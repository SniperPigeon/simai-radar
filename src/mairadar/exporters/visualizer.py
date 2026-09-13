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

from .artwork import export_song_covers


SCHEMA_VERSION = "mairadar-visualizer-1"
TEMPLATE_DIRECTORY = Path(__file__).resolve().parents[3] / "res" / "visualizer"
TEMPLATE_FILES = ("index.html", "app.js", "styles.css")
DEFAULT_COLORS = (
    "#ef476f", "#ff9f1c", "#2a9d8f", "#3a86ff",
    "#8338ec", "#d1495b", "#00a6a6", "#6c757d",
)


@dataclass(frozen=True)
class DimensionPresentation:
    label: str
    short_label: str | None = None
    color: str | None = None


# Edit this mapping to configure labels and colors for known analysis feature keys.
# Unlisted keys are exported unchanged so a newly added feature remains visible.
DIMENSION_PRESENTATION = {
    "hold": DimensionPresentation("Hold频率", "Hold"),
    "note": DimensionPresentation("总体物量", "Note"),
    "peak": DimensionPresentation("Peak爆发", "Peak"),
    "slide": DimensionPresentation("Slide压力", "Slide"),
}


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
    ) -> None:
        self._presentation = dict(DIMENSION_PRESENTATION)
        if dimension_presentation is not None:
            self._presentation.update(dimension_presentation)

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
            if record.scores is not None and set(record.scores.features) != set(names):
                raise ValueError("Score features must match the configured export features")
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
                raw_scores = self._raw_scores(record, names)
                scores = self._scores(record, names)
                dominant = self._dominant(scores, names)
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
            "displayRange": [0, 200],
            "dimensions": self._dimensions(names),
            "stats": {
                "songCount": len(songs),
                "chartCount": sum(len(song["charts"]) for song in songs),
                "coverCount": cover_count,
                "failedChartCount": len(failed_indexes),
                "skippedRecordCount": skipped,
            },
            "songs": songs,
        }
        return payload, len(failed_indexes)

    @staticmethod
    def _raw_scores(record: AnalysisRecord, names: tuple[str, ...]) -> dict[str, float | None]:
        values = {}
        for name in names:
            item = record.analysis.features.get(name) if record.analysis is not None else None
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
