"""Export one shared artwork file per song without creating song identities."""

from collections import OrderedDict, defaultdict
from pathlib import Path
import re
import shutil
import unicodedata

from mairadar.io import COVER_EXTENSIONS
from mairadar.reporting import AnalysisRecord


def _song_key(record: AnalysisRecord) -> tuple:
    chart = record.chart
    if chart is None:
        return (record.source_name, None, None)
    return (
        chart.source_name or record.source_name,
        chart.title,
        chart.artist,
    )


def _cover_stem(record: AnalysisRecord) -> str:
    title = record.chart.title if record.chart is not None else None
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title or "").strip().rstrip(".")
    return stem or "cover"


def export_song_covers(
    records: list[AnalysisRecord],
    staging: Path,
    relative_directory: Path,
) -> tuple[dict[int, str], dict[int, list[str]], int]:
    """Copy one canonical cover per export-time song group.

    The grouping key is scoped to this report and is not persisted as song
    identity. Conflicting artwork is diagnosed and never overwrites the first
    successfully exported file.
    """
    groups: OrderedDict[tuple, list[tuple[int, AnalysisRecord]]] = OrderedDict()
    for index, record in enumerate(records):
        if record.chart is not None:
            groups.setdefault(_song_key(record), []).append((index, record))

    paths = {}
    issues = defaultdict(list)
    used: dict[str, Path] = {}
    target_directory = staging / relative_directory

    def source_bytes(record: AnalysisRecord) -> tuple[Path, str, bytes]:
        source = Path(record.cover_path)
        suffix = source.suffix.lower()
        if suffix not in COVER_EXTENSIONS:
            raise ValueError(f"Unsupported cover extension: {suffix}")
        return source, suffix, source.read_bytes()

    def export_one(record: AnalysisRecord) -> tuple[str, bytes]:
        source, suffix, content = source_bytes(record)
        filename = _cover_stem(record) + suffix
        filenames = [filename]
        chart = record.chart
        if chart.difficulty_index is not None and chart.chart_type in {"dx", "sd"}:
            filenames.append(f"{_cover_stem(record)}-{chart.difficulty_index}-{chart.chart_type}{suffix}")
        for candidate in filenames:
            key = unicodedata.normalize("NFC", candidate).casefold()
            if key in used:
                existing = used[key]
                if content == existing.read_bytes():
                    return existing.relative_to(staging).as_posix(), content
                continue
            target = target_directory / candidate
            try:
                shutil.copyfile(source, target)
            except OSError:
                target.unlink(missing_ok=True)
                raise
            used[key] = target
            return target.relative_to(staging).as_posix(), content
        raise ValueError(f"Conflicting cover filename: {filename}")

    for grouped in groups.values():
        candidates = [(index, record) for index, record in grouped if record.cover_path is not None]
        exported_path = None
        canonical_content = None
        canonical_index = None
        failed_candidates = set()
        for index, record in candidates:
            try:
                exported_path, canonical_content = export_one(record)
                canonical_index = index
                break
            except (OSError, ValueError) as exc:
                issues[index].append(str(exc))
                failed_candidates.add(index)
        if exported_path is None:
            continue

        for index, _ in grouped:
            paths[index] = exported_path
        for index, record in candidates:
            if index == canonical_index or index in failed_candidates:
                continue
            try:
                _, _, content = source_bytes(record)
                if content != canonical_content:
                    issues[index].append("Conflicting cover content within one song")
            except (OSError, ValueError) as exc:
                issues[index].append(str(exc))

    return paths, dict(issues), len(used)
