"""Standalone cumulative Slide pressure using the reviewed historical policy."""

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
import math

from mairadar.analysis.model import AnalysisContext, FeatureResult
from mairadar.model import Event

from .note_density import _simultaneous_touch_components


CUMULATE_ALPHA = 0.35
CUMULATE_REFERENCE_SECONDS = 0.5
CUMULATE_SIMULTANEOUS_SECONDS = 1 / 60
CUMULATE_EFFECTIVE_LENGTH_BASE = 5
CUMULATE_TOUCH_WEIGHT = 1.5
CUMULATE_TOLERANCE = 1e-9


@dataclass(frozen=True)
class _CumulateSlideGroup:
    key: tuple
    events: tuple[Event, ...]
    declaration_time_s: float
    declaration_beat: Fraction
    launch_time_s: float
    head_event_id: int | None
    source_start: int


@dataclass(frozen=True)
class _CumulateOnset:
    declaration_time_s: float
    declaration_beat: Fraction
    groups: tuple[_CumulateSlideGroup, ...]


@dataclass(frozen=True)
class _CumulatePoint:
    point_id: tuple
    time_s: float
    weight: float
    owner_group_key: tuple | None = None


@dataclass(frozen=True)
class SlideCumulateSection:
    onset_count: int
    cluster_loads: tuple[float, ...]
    mean_load: float
    rms_load: float
    intensity: float
    effective_length: float
    total_load: float


@dataclass(frozen=True)
class SlideCumulateBreakdown:
    value: float
    total_load: float
    time_units: float
    sections: tuple[SlideCumulateSection, ...]


def _cumulate_same_time(left: float, right: float) -> bool:
    return math.isclose(
        left,
        right,
        rel_tol=0.0,
        abs_tol=CUMULATE_TOLERANCE,
    )


def _cumulate_in_closed_interval(value: float, start: float, end: float) -> bool:
    return (
        value >= start - CUMULATE_TOLERANCE
        and value <= end + CUMULATE_TOLERANCE
    )


def _cumulate_tempo(
    events: tuple[Event, ...],
) -> list[tuple[float, Fraction, Fraction]]:
    output = []
    for event in events:
        if event.kind != "timing":
            continue
        if event.start_beat is None or event.bpm is None:
            raise ValueError("BPM event is missing its beat or tempo")
        output.append((
            event.start_time_s,
            Fraction(event.start_beat),
            Fraction(str(event.bpm)),
        ))
    return output


def _cumulate_beat_at_seconds(
    time_s: float,
    tempo: list[tuple[float, Fraction, Fraction]],
) -> Fraction:
    times = [point[0] for point in tempo]
    index = bisect_right(times, time_s + CUMULATE_TOLERANCE) - 1
    if index < 0:
        raise ValueError("Slide declaration precedes the first BPM event")
    origin_time, origin_beat, bpm = tempo[index]
    delta_s = Fraction(str(time_s)) - Fraction(str(origin_time))
    return origin_beat + delta_s * bpm / 60


def _cumulate_group_key(event: Event) -> tuple:
    if event.head_event_id is not None:
        return ("head", event.head_event_id)
    return (
        "source",
        event.source_start,
        event.source_end,
        event.slide_declare_time_s,
    )


def _cumulate_groups(events: tuple[Event, ...]) -> list[_CumulateSlideGroup]:
    grouped = defaultdict(list)
    for event in events:
        if event.kind == "slide":
            grouped[_cumulate_group_key(event)].append(event)
    if not grouped:
        return []

    by_id = {event.event_id: event for event in events}
    tempo = _cumulate_tempo(events)
    output = []
    for key, paths in grouped.items():
        declaration_times = {path.slide_declare_time_s for path in paths}
        if len(declaration_times) != 1 or None in declaration_times:
            raise ValueError("A Slide group must have one declaration time")
        declaration_time = declaration_times.pop()

        head_ids = {path.head_event_id for path in paths}
        if len(head_ids) != 1:
            raise ValueError("A Slide group must have one shared head identity")
        head_id = head_ids.pop()
        if head_id is not None:
            head = by_id[head_id]
            if head.start_beat is None:
                raise ValueError("Slide head is missing its declaration beat")
            declaration_beat = Fraction(head.start_beat)
        else:
            declaration_beat = _cumulate_beat_at_seconds(declaration_time, tempo)

        valid_paths = tuple(
            path for path in paths
            if path.end_time_s > path.start_time_s
        )
        if not valid_paths:
            continue
        output.append(_CumulateSlideGroup(
            key=key,
            events=valid_paths,
            declaration_time_s=declaration_time,
            declaration_beat=declaration_beat,
            launch_time_s=max(path.start_time_s for path in valid_paths),
            head_event_id=head_id,
            source_start=min(path.source_start for path in paths),
        ))
    return sorted(
        output,
        key=lambda group: (
            group.declaration_beat,
            group.declaration_time_s,
            group.source_start,
        ),
    )


def _cumulate_same_onset(
    first: _CumulateSlideGroup,
    candidate: _CumulateSlideGroup,
) -> bool:
    return (
        candidate.declaration_time_s - first.declaration_time_s
        <= CUMULATE_SIMULTANEOUS_SECONDS + CUMULATE_TOLERANCE
    )


def _cumulate_onsets(groups: list[_CumulateSlideGroup]) -> list[_CumulateOnset]:
    if not groups:
        return []
    buckets = [[groups[0]]]
    for group in groups[1:]:
        if _cumulate_same_onset(buckets[-1][0], group):
            buckets[-1].append(group)
        else:
            buckets.append([group])
    return [
        _CumulateOnset(
            declaration_time_s=bucket[0].declaration_time_s,
            declaration_beat=bucket[0].declaration_beat,
            groups=tuple(bucket),
        )
        for bucket in buckets
    ]


def _cumulate_sections(onsets: list[_CumulateOnset]) -> list[list[_CumulateOnset]]:
    if not onsets:
        return []
    output = []
    current = [onsets[0]]
    for onset in onsets[1:]:
        delta = float(onset.declaration_beat - current[-1].declaration_beat)
        if delta > CUMULATE_TOLERANCE and delta <= 1 + CUMULATE_TOLERANCE:
            current.append(onset)
        else:
            output.append(current)
            current = [onset]
    output.append(current)
    return output


def _cumulate_points(
    events: tuple[Event, ...],
    groups: list[_CumulateSlideGroup],
) -> list[_CumulatePoint]:
    head_owners = {
        group.head_event_id: group.key
        for group in groups
        if group.head_event_id is not None
    }
    output = []
    touches = []
    for event in events:
        if event.kind in {"tap", "hold"}:
            output.append(_CumulatePoint(
                ("event", event.event_id),
                event.start_time_s,
                1.0,
                head_owners.get(event.event_id),
            ))
        elif event.kind in {"touch", "touch_hold"}:
            touches.append(event)

    for components in _simultaneous_touch_components(touches).values():
        for component in components:
            output.append(_CumulatePoint(
                ("touch", component.component_id),
                component.time_s,
                CUMULATE_TOUCH_WEIGHT,
            ))

    for group in groups:
        for launch_index, launch_time in enumerate(sorted({
            event.start_time_s for event in group.events
        })):
            output.append(_CumulatePoint(
                ("slide_launch", group.key, launch_index),
                launch_time,
                1.0,
                group.key,
            ))
    return sorted(output, key=lambda point: (point.time_s, point.point_id))


def _cumulate_assign(
    onsets: list[_CumulateOnset],
    points: list[_CumulatePoint],
) -> list[list[_CumulatePoint]]:
    """Assign each external point once, to the closest pending launch."""
    member_keys = {
        group.key
        for onset in onsets
        for group in onset.groups
    }
    assigned = [[] for _ in onsets]
    for point in points:
        if point.owner_group_key in member_keys:
            continue
        candidates = []
        for index, onset in enumerate(onsets):
            pending = [
                group for group in onset.groups
                if _cumulate_in_closed_interval(
                    point.time_s,
                    group.declaration_time_s,
                    group.launch_time_s,
                )
            ]
            if pending:
                distance = min(
                    max(0.0, group.launch_time_s - point.time_s)
                    for group in pending
                )
                candidates.append((distance, index))
        if candidates:
            _, index = min(candidates)
            assigned[index].append(point)
    return assigned


def _cumulate_internal_total(count: int) -> float:
    if count < 0:
        raise ValueError("Internal object count cannot be negative")
    if count == 0:
        return 0.0
    return count + CUMULATE_ALPHA * math.lgamma(count + 1) / math.log(2)


def _cumulate_batches(points: list[_CumulatePoint]) -> list[list[_CumulatePoint]]:
    if not points:
        return []
    batches = [[points[0]]]
    for point in points[1:]:
        if _cumulate_same_time(batches[-1][0].time_s, point.time_s):
            batches[-1].append(point)
        else:
            batches.append([point])
    return batches


def _cumulate_onset_load(
    onset: _CumulateOnset,
    points: list[_CumulatePoint],
) -> float:
    launch_times = [group.launch_time_s for group in onset.groups]
    launch_points = [
        point for point in points
        if any(_cumulate_same_time(point.time_s, launch) for launch in launch_times)
    ]
    launch_ids = {point.point_id for point in launch_points}
    internal_points = [point for point in points if point.point_id not in launch_ids]

    internal = 0.0
    previous_count = 0
    for batch in _cumulate_batches(internal_points):
        next_count = previous_count + len(batch)
        marginal = (
            _cumulate_internal_total(next_count)
            - _cumulate_internal_total(previous_count)
        ) / len(batch)
        internal += marginal * math.fsum(point.weight for point in batch)
        previous_count = next_count
    return internal + math.fsum(point.weight for point in launch_points)


def _cumulate_effective_length(onset_count: int) -> float:
    if onset_count <= 0:
        raise ValueError("A cumulative section must contain at least one onset")
    if onset_count <= CUMULATE_EFFECTIVE_LENGTH_BASE:
        return float(onset_count)
    scaled = 1 + (
        onset_count - CUMULATE_EFFECTIVE_LENGTH_BASE
    ) / CUMULATE_EFFECTIVE_LENGTH_BASE
    return CUMULATE_EFFECTIVE_LENGTH_BASE * (
        1 + math.log(scaled, CUMULATE_EFFECTIVE_LENGTH_BASE)
    )


def _cumulate_section_intensity(loads: tuple[float, ...]) -> float:
    if not loads:
        raise ValueError("A cumulative section must contain at least one load")
    mean = math.fsum(loads) / len(loads)
    rms = math.sqrt(math.fsum(load ** 2 for load in loads) / len(loads))
    return 0.8 * mean + 0.2 * rms


def _cumulate_section(
    section: list[_CumulateOnset],
    assigned: dict[int, list[_CumulatePoint]],
) -> SlideCumulateSection:
    loads = tuple(
        _cumulate_onset_load(onset, assigned[id(onset)])
        for onset in section
    )
    onset_count = len(loads)
    mean = math.fsum(loads) / onset_count
    rms = math.sqrt(math.fsum(load ** 2 for load in loads) / onset_count)
    intensity = _cumulate_section_intensity(loads)
    effective_length = _cumulate_effective_length(onset_count)
    return SlideCumulateSection(
        onset_count=onset_count,
        cluster_loads=loads,
        mean_load=mean,
        rms_load=rms,
        intensity=intensity,
        effective_length=effective_length,
        total_load=effective_length * intensity,
    )


def slide_cumulate_breakdown(
    events: tuple[Event, ...],
    duration_s: float,
) -> SlideCumulateBreakdown:
    """Calculate cumulative pressure without using the current Slide analyzer."""
    if duration_s <= 0:
        raise ValueError("Slide cumulative pressure requires a positive chart duration")
    groups = _cumulate_groups(events)
    time_units = duration_s / CUMULATE_REFERENCE_SECONDS
    if not groups:
        return SlideCumulateBreakdown(0.0, 0.0, time_units, ())

    onsets = _cumulate_onsets(groups)
    points = _cumulate_points(events, groups)
    assigned_lists = _cumulate_assign(onsets, points)
    assigned = {
        id(onset): onset_points
        for onset, onset_points in zip(onsets, assigned_lists)
    }
    sections = tuple(
        _cumulate_section(section, assigned)
        for section in _cumulate_sections(onsets)
    )
    total_load = math.fsum(section.total_load for section in sections)
    return SlideCumulateBreakdown(
        value=total_load / time_units,
        total_load=total_load,
        time_units=time_units,
        sections=sections,
    )


class SlideCumulateAnalyzer:
    """Return sustained cumulative Slide pressure over the whole chart."""

    def analyze(self, context: AnalysisContext) -> FeatureResult:
        if context.duration_s <= 0:
            return FeatureResult(None, success=False)
        result = slide_cumulate_breakdown(context.events, context.duration_s)
        return FeatureResult(result.value)
