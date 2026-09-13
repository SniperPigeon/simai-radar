"""Portable summary CSV and artwork, independent of bundle discovery."""

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
import csv
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import unicodedata

from mairadar.analysis import AnalysisIssue
from mairadar.io import COVER_EXTENSIONS, bundle_directory_name
from mairadar.reporting import AnalysisRecord

FIXED_COLUMNS = (
    "title", "artist", "designer", "difficulty_index", "level", "chart_type",
    "cover_path", "status", "diagnostics",
)


@dataclass(frozen=True)
class ExportResult:
    csv_path: Path
    failed_records: int

    @property
    def exit_code(self) -> int:
        return int(self.failed_records > 0)


class CsvExporter:
    def export(
        self, records: Iterable[AnalysisRecord], output: str | Path,
        *, feature_names: Sequence[str], include_scores: bool = False,
    ) -> ExportResult:
        """Write into a new or empty directory. Existing reports are never replaced."""
        names = tuple(feature_names)
        if not names or len(set(names)) != len(names):
            raise ValueError("Export requires unique configured feature names")
        records = list(records)
        if not records:
            raise ValueError("No bundle records to export")
        for record in records:
            if record.analysis is not None and tuple(record.analysis.features) != names:
                raise ValueError("Analysis features must match the configured export order")
            if record.scores is not None and set(record.scores.features) != set(names):
                raise ValueError("Score features must match the configured export features")

        output = Path(output).absolute()
        if output.is_symlink() or (output.exists() and (
            not output.is_dir() or any(output.iterdir())
        )):
            raise FileExistsError(f"Output must be a new or empty directory: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
        emptied = False
        try:
            (staging / "covers").mkdir()
            score_columns = include_scores or any(record.scores is not None for record in records)
            columns = list(FIXED_COLUMNS) + [f"{name}_raw" for name in names]
            if score_columns:
                columns += [f"{name}_score" for name in names]
            used_covers: dict[str, Path] = {}
            failures = 0
            with (staging / "charts.csv").open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=columns)
                writer.writeheader()
                for record in records:
                    row = self._row(record, names, score_columns)
                    issues = []
                    if record.cover_path is not None:
                        try:
                            row["cover_path"] = self._copy_cover(record, staging, used_covers)
                        except (OSError, ValueError) as exc:
                            issues.append(AnalysisIssue("COVER_EXPORT_FAILED", str(exc)))
                    if issues:
                        row["diagnostics"]["export"] = [asdict(issue) for issue in issues]
                    failures += row["status"] != "ok"
                    row["diagnostics"] = json.dumps(
                        row["diagnostics"], ensure_ascii=False, allow_nan=False,
                    )
                    writer.writerow(row)
            if output.exists():
                output.rmdir()  # Only succeeds if it is still empty.
                emptied = True
            os.rename(staging, output)
            return ExportResult(output / "charts.csv", failures)
        except Exception:
            if emptied and not output.exists():
                output.mkdir()
            raise
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    @staticmethod
    def _copy_cover(record: AnalysisRecord, staging: Path, used: dict[str, Path]) -> str:
        if record.chart is None:
            raise ValueError("Artwork export requires chart metadata")
        source = Path(record.cover_path)
        suffix = source.suffix.lower()
        if suffix not in COVER_EXTENSIONS:
            raise ValueError(f"Unsupported cover extension: {suffix}")
        filename = bundle_directory_name(record.chart) + suffix
        key = unicodedata.normalize("NFC", filename).casefold()
        if key in used:
            existing = used[key]
            if source.read_bytes() == existing.read_bytes():
                return existing.relative_to(staging).as_posix()
            raise ValueError(f"Conflicting cover filename: {filename}")
        target = staging / "covers" / filename
        try:
            shutil.copyfile(source, target)
        except OSError:
            target.unlink(missing_ok=True)
            raise
        used[key] = target
        return f"covers/{filename}"

    @staticmethod
    def _row(record: AnalysisRecord, names: tuple[str, ...], scores: bool) -> dict:
        chart = record.chart
        row = {key: None for key in FIXED_COLUMNS}
        if chart is not None:
            row.update(
                title=chart.title, artist=chart.artist, designer=chart.designer,
                difficulty_index=chart.difficulty_index, level=chart.level_text,
                chart_type=chart.chart_type,
            )
        row["status"] = record.status
        detail = {"source": record.source_name, "input": [asdict(d) for d in record.diagnostics]}
        if record.analysis is not None:
            detail["parser"] = [asdict(d) for d in record.analysis.parser_diagnostics]
            detail["analysis"] = [asdict(d) for d in record.analysis.diagnostics]
            detail["features"] = {
                name: item.success
                for name, item in record.analysis.features.items()
            }
        for name in names:
            item = record.analysis.features[name] if record.analysis is not None else None
            row[f"{name}_raw"] = item.data if item is not None and item.success else None
            if scores:
                score = record.scores.features[name] if record.scores is not None else None
                if score is not None and score.status == "ok" and (
                    isinstance(score.value, bool) or not isinstance(score.value, (int, float))
                    or not math.isfinite(score.value)
                ):
                    raise ValueError(f"Non-finite or missing standard score: {name}")
                row[f"{name}_score"] = score.value if score is not None and score.status == "ok" else None
        if record.scores is not None:
            detail["scoring"] = asdict(record.scores)
        row["diagnostics"] = detail
        return row
