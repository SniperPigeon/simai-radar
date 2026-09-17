"""Self-contained static visualizer export, independent of parsing and analysis."""

from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import tempfile

from mairadar.reporting import AnalysisRecord
from mairadar.analysis import flatten_features

from .artwork import export_song_covers


SCHEMA_VERSION = "mairadar-visualizer-2"
TEMPLATE_DIRECTORY = Path(__file__).resolve().parents[3] / "res" / "visualizer"
TEMPLATE_FILES = ("index.html", "app.js", "styles.css")
DEFAULT_COLORS = (
    "#ef476f", "#ff9f1c", "#2a9d8f", "#3a86ff",
    "#8338ec", "#d1495b", "#00a6a6", "#6c757d",
)

# Edit this tuple to choose radar axes and order, independently of ML inputs.
# None keeps the default axes (or the current axes when repackaging a site).
# Example: ("note", "peak", "sweep", "slide_tricky", "jack", "fitted_constant")
RADAR_FEATURES: tuple[str, ...] | None = None


@dataclass(frozen=True)
class DimensionPresentation:
    label: str
    short_label: str | None = None
    color: str | None = None


# Edit this mapping to configure labels and colors for known analysis feature keys.
# Unlisted keys are exported unchanged so a newly added feature remains visible.
DIMENSION_PRESENTATION = {
    "note": DimensionPresentation("Note", "Note"),
    "peak": DimensionPresentation("Peak", "Peak"),
    "sweep": DimensionPresentation("扫键", "扫键"),
    "slide_tricky": DimensionPresentation("错位压力", "错位压力"),
    "slide_sequence": DimensionPresentation("星星阵", "星星阵"),
    "jack": DimensionPresentation("纵连", "纵连"),
    "slide_cumulate": DimensionPresentation("持续星星压力", "持续星星"),
    "fitted_constant": DimensionPresentation("拟合定数", "拟合定数"),
}


def select_dimensions(payload, names=None):
    """Project mapped values onto radar axes, retaining the complete raw inputs."""
    if names is None:
        names = RADAR_FEATURES
    if names is None:
        return
    names = tuple(names)
    if not names or len(set(names)) != len(names):
        raise ValueError("Radar dimensions must be nonempty and unique")
    charts = [chart for song in payload["songs"] for chart in song["charts"]]
    available = {name for chart in charts for name in chart["mappedFeatures"]}
    missing = set(names) - available
    if missing:
        raise ValueError(f"Radar features need a score mapping: {', '.join(sorted(missing))}")
    previous = {item["key"]: item for item in payload["dimensions"]}
    payload["dimensions"] = [previous.get(name, {
        "key": name, "label": DIMENSION_PRESENTATION.get(name, DimensionPresentation(name)).label,
        "shortLabel": DIMENSION_PRESENTATION.get(name, DimensionPresentation(name)).short_label or name,
        "color": DEFAULT_COLORS[index % len(DEFAULT_COLORS)],
    }) for index, name in enumerate(names)]
    for chart in charts:
        raw = chart["rawFeatures"]
        scores = chart["mappedFeatures"]
        chart["rawScores"] = {name: raw.get(name) for name in names}
        chart["scores"] = {name: scores.get(name) for name in names}
        chart["dominantDimension"] = VisualizerExporter._dominant(chart["scores"], names)


@dataclass(frozen=True)
class VisualizerExportResult:
    site_path: Path
    data_path: Path
    failed_records: int

    @property
    def exit_code(self) -> int:
        return int(self.failed_records > 0)


class VisualizerExporter:
    """Export scored records into the repository's single bundled UI template."""

    def __init__(
        self,
        dimension_presentation: Mapping[str, DimensionPresentation] | None = None,
        *,
        constants_table: str | Path | None = None,
        constant_model: str | Path | None = None,
        radar_features: Sequence[str] | None = None,
    ) -> None:
        self._presentation = dict(DIMENSION_PRESENTATION)
        if dimension_presentation is not None:
            self._presentation.update(dimension_presentation)
        self._constants = None
        self._radar_features = radar_features
        self._model = None
        if constant_model is not None:
            from mairadar.regression import PolynomialModel
            self._model = PolynomialModel.load(constant_model)
        if constants_table is not None:
            from .constants import ConstantAnnotations
            self._constants = ConstantAnnotations(constants_table)

    def export(
        self,
        records: Iterable[AnalysisRecord],
        output: str | Path,
        *,
        feature_names: Sequence[str],
        include_scores: bool = False,
    ) -> VisualizerExportResult:
        names = tuple(feature_names)
        if not names or len(set(names)) != len(names):
            raise ValueError("Visualizer export requires unique configured feature names")
        if not include_scores:
            raise ValueError("Visualizer export requires mapped scores")
        records = list(records)
        if not records:
            raise ValueError("No bundle records to export")
        for record in records:
            if record.analysis is not None and tuple(record.analysis.features) != names:
                raise ValueError("Analysis features must match the configured export order")
            if record.scores is not None and record.analysis is not None and not (
                set(record.scores.features) <= set(flatten_features(record.analysis))
            ):
                raise ValueError("Score features must refer to available raw features")
            if self._model is not None and record.analysis is not None and "fitted_constant" not in record.analysis.features:
                raise ValueError("Compute fitted_constant before scoring; pass constant_model to run_pipeline")
        self._validate_template()

        output = Path(output).absolute()
        if output.is_symlink() or (output.exists() and (
            not output.is_dir() or any(output.iterdir())
        )):
            raise FileExistsError(f"Output must be a new or empty directory: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
        emptied = False
        try:
            for filename in TEMPLATE_FILES:
                shutil.copyfile(TEMPLATE_DIRECTORY / filename, staging / filename)
            (staging / "data").mkdir()
            covers = staging / "assets" / "covers"
            covers.mkdir(parents=True)
            payload, failures = self._payload(records, names, staging)
            if self._constants is not None:
                self._constants.payload(payload)
            if self._model is not None:
                payload["constantModel"] = self._model.to_dict()
            select_dimensions(payload, self._radar_features)
            data_path = staging / "data" / "songs.json"
            data_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            if output.exists():
                output.rmdir()
                emptied = True
            os.rename(staging, output)
            return VisualizerExportResult(output, output / "data" / "songs.json", failures)
        except Exception:
            if emptied and not output.exists():
                output.mkdir()
            raise
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    @staticmethod
    def _validate_template() -> None:
        missing = [name for name in TEMPLATE_FILES if not (TEMPLATE_DIRECTORY / name).is_file()]
        if missing:
            raise FileNotFoundError(
                f"Visualizer template is incomplete at {TEMPLATE_DIRECTORY}: {', '.join(missing)}"
            )

    def _dimensions(self, names: tuple[str, ...]) -> list[dict]:
        dimensions = []
        for index, name in enumerate(names):
            configured = self._presentation.get(name)
            label = configured.label if configured is not None else name
            short_label = configured.short_label if configured is not None else None
            color = configured.color if configured is not None else None
            dimensions.append({
                "key": name,
                "label": label,
                "shortLabel": short_label or label,
                "color": color or DEFAULT_COLORS[index % len(DEFAULT_COLORS)],
            })
        return dimensions

    def _payload(
        self,
        records: list[AnalysisRecord],
        names: tuple[str, ...],
        staging: Path,
    ) -> tuple[dict, int]:
        mapped_names = tuple(dict.fromkeys(
            name for record in records if record.scores is not None for name in record.scores.features
        )) or names
        axes = tuple(name for name in names if name != "fitted_constant" and name in mapped_names) or mapped_names
        groups: OrderedDict[tuple, list[tuple[int, AnalysisRecord]]] = OrderedDict()
        skipped = 0
        for index, record in enumerate(records):
            chart = record.chart
            if chart is None:
                skipped += 1
                continue
            source = chart.source_name or record.source_name
            key = (source, chart.chart_type, chart.title, chart.artist)
            groups.setdefault(key, []).append((index, record))

        cover_paths, cover_issues, cover_count = export_song_covers(
            records, staging, Path("assets/covers"),
        )
        failed_indexes = {index for index, record in enumerate(records) if record.status != "ok"}
        songs = []
        chart_number = 0
        mapping_versions = []
        for song_number, (_, grouped) in enumerate(groups.items(), start=1):
            first = grouped[0][1].chart
            charts = []
            for record_index, record in grouped:
                chart_number += 1
                chart = record.chart
                cover = cover_paths.get(record_index)
                export_issues = cover_issues.get(record_index, [])
                raw_names = tuple(flatten_features(record.analysis)) if record.analysis is not None else names
                raw_features = self._raw_scores(record, raw_names)
                mapped_features = self._scores(record, mapped_names)
                raw_scores = {name: raw_features.get(name) for name in axes}
                scores = {name: mapped_features.get(name) for name in axes}
                dominant = self._dominant(scores, axes)
                metadata = chart.metadata_json if isinstance(chart.metadata_json, dict) else {}
                duration = max(chart.chart_end_time_s or 0, chart.last_event_end_s or 0)
                if not math.isfinite(duration) or duration <= 0:
                    duration = None
                status = record.status
                charts.append({
                    "id": f"chart-{chart_number}",
                    "kind": self._kind(chart.chart_type),
                    "kindLabel": self._kind_label(chart.chart_type),
                    "difficulty": chart.difficulty_index,
                    "difficultyLabel": chart.difficulty_label or str(chart.difficulty_index or ""),
                    "level": chart.level_text or "",
                    "charter": chart.designer or "",
                    "bpm": metadata.get("wholebpm") or metadata.get("bpm") or "",
                    "version": metadata.get("version") or "",
                    "chartVersion": metadata.get("chartVersion") or "",
                    "cover": cover,
                    "scores": scores,
                    "rawScores": raw_scores,
                    "rawFeatures": raw_features,
                    "mappedFeatures": mapped_features,
                    **({"fittedConstant": raw_features["fitted_constant"],
                        "constantPredictionStatus": "ok" if raw_features["fitted_constant"] is not None else "unavailable"}
                       if "fitted_constant" in raw_features else {}),
                    "dominantDimension": dominant,
                    "totalNotes": None,
                    "durationSeconds": duration,
                    "sourceRef": chart.source_name or record.source_name,
                    "status": status,
                    "exportIssues": export_issues,
                })
                if record.scores is not None and record.scores.mapping_version not in mapping_versions:
                    mapping_versions.append(record.scores.mapping_version)
            song_cover = next((item["cover"] for item in charts if item["cover"]), None)
            kinds = list(dict.fromkeys(item["kind"] for item in charts))
            difficulties = list(dict.fromkeys(
                item["difficulty"] for item in charts if item["difficulty"] is not None
            ))
            versions = list(dict.fromkeys(item["version"] for item in charts if item["version"]))
            metadata = first.metadata_json if isinstance(first.metadata_json, dict) else {}
            songs.append({
                "id": f"song-{song_number}",
                "title": first.title or "",
                "artist": first.artist or "",
                "genre": metadata.get("genre") or "",
                "cover": song_cover,
                "charts": charts,
                "versions": versions,
                "chartKinds": kinds,
                "difficulties": difficulties,
            })

        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "mappingVersions": mapping_versions,
            "scoreScale": 100,
            "displayRange": [0, 220],
            "dimensions": self._dimensions(axes),
            "stats": {
                "songCount": len(songs),
                "chartCount": sum(len(song["charts"]) for song in songs),
                "coverCount": cover_count,
                "failedChartCount": len(failed_indexes),
                "skippedRecordCount": skipped,
            },
            "songs": songs,
        }
        predicted = [chart for song in songs for chart in song["charts"] if "fittedConstant" in chart]
        if predicted:
            payload["stats"]["constantPredictionFailedCount"] = sum(
                chart["constantPredictionStatus"] != "ok" for chart in predicted
            )
        return payload, len(failed_indexes)

    @staticmethod
    def _raw_scores(record: AnalysisRecord, names: tuple[str, ...]) -> dict[str, float | None]:
        values = {}
        raw = flatten_features(record.analysis) if record.analysis is not None else {}
        for name in names:
            item = raw.get(name)
            value = item.data if item is not None and item.success else None
            values[name] = value if VisualizerExporter._finite(value) else None
        return values

    @staticmethod
    def _scores(record: AnalysisRecord, names: tuple[str, ...]) -> dict[str, float | None]:
        values = {}
        for name in names:
            item = record.scores.features.get(name) if record.scores is not None else None
            value = item.value if item is not None and item.status == "ok" else None
            values[name] = value if VisualizerExporter._finite(value) else None
        return values

    @staticmethod
    def _finite(value) -> bool:
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)

    @staticmethod
    def _dominant(scores: dict[str, float | None], names: tuple[str, ...]) -> str | None:
        available = [name for name in names if scores[name] is not None]
        return max(available, key=lambda name: scores[name]) if available else None

    @staticmethod
    def _kind(chart_type: str | None) -> str:
        return {"dx": "DX", "sd": "ST"}.get(chart_type, "UNKNOWN")

    @staticmethod
    def _kind_label(chart_type: str | None) -> str:
        return {"dx": "DX谱", "sd": "标谱"}.get(chart_type, "类型未提供")
