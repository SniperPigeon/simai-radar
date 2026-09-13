"""Global Slide pressure from motion, launch interference, and dense sequences."""

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
import math

from mairadar.analysis.model import AnalysisContext, FeatureResult
from mairadar.model import Event

from .note_density import _simultaneous_touch_components


LENGTH_REFERENCE_BARS = 20.0
CADENCE_REFERENCE_SECONDS = 0.5
SIMULTANEOUS_ONSET_SECONDS = 1 / 60
FULL_SEQUENCE_LENGTH = 8
INTERFERENCE_SCALE = 0.5
TOUCH_INTERFERENCE_WEIGHT = 1.5
SECONDS_PER_MINUTE = 60.0
COMPARISON_TOLERANCE = 1e-9


@dataclass(frozen=True)
class _SlideGroup:
    key: tuple
    events: tuple[Event, ...]
    declaration_time_s: float
    declaration_beat: Fraction
    launch_time_s: float
    movement_pressure: float
    head_event_id: int | None
    source_start: int


@dataclass(frozen=True)
class _SlideOnsetCluster:
    declaration_time_s: float
    declaration_beat: Fraction
    groups: tuple[_SlideGroup, ...]


@dataclass(frozen=True)
class _WorkloadPoint:
    point_id: tuple
    time_s: float
    weight: float
    owner_group_key: tuple | None = None


@dataclass(frozen=True)
class SlideSectionPressure:
    """Explainable intermediate values for one maximal Slide sequence."""

    slide_count: int
    effective_slide_count: float
    onset_count: int
    mean_movement_pressure: float
    context_per_slide: float
    cadence_factor: float
    intensity: float
    pressure: float


@dataclass(frozen=True)
class SlidePressureBreakdown:
    """Chart-level Slide pressure plus its independently inspectable parts."""

    global_pressure: float
    active_intensity: float
    slide_density_per_minute: float
    effective_slide_density_per_minute: float
    sections: tuple[SlideSectionPressure, ...]


def _time_in_closed_interval(value: float, start: float, end: float) -> bool:
    return (
        value >= start - COMPARISON_TOLERANCE
        and value <= end + COMPARISON_TOLERANCE
    )


def _tempo_points(events: tuple[Event, ...]) -> list[tuple[float, Fraction, Fraction]]:
    points = []
    for event in events:
        if event.kind != "timing":
            continue
        if event.start_beat is None or event.bpm is None:
            raise ValueError("BPM event is missing its beat or tempo")
        points.append((
            event.start_time_s,
            Fraction(event.start_beat),
            Fraction(str(event.bpm)),
        ))
    return points


def _beat_at_seconds(
    time_s: float,
    tempo: list[tuple[float, Fraction, Fraction]],
) -> Fraction:
    times = [point[0] for point in tempo]
    index = bisect_right(times, time_s + COMPARISON_TOLERANCE) - 1
    if index < 0:
        raise ValueError("Slide declaration precedes the first BPM event")
    origin_time, origin_beat, bpm = tempo[index]
    delta_s = Fraction(str(time_s)) - Fraction(str(origin_time))
    return origin_beat + delta_s * bpm / 60


def _slide_group_key(event: Event) -> tuple:
    if event.head_event_id is not None:
        return ("head", event.head_event_id)
    return (
        "source",
        event.source_start,
        event.source_end,
        event.slide_declare_time_s,
    )


def _build_slide_groups(events: tuple[Event, ...]) -> list[_SlideGroup]:
    grouped = defaultdict(list)
    for event in events:
        if event.kind == "slide":
            grouped[_slide_group_key(event)].append(event)
    if not grouped:
        return []

    by_id = {event.event_id: event for event in events}
    tempo = _tempo_points(events)
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
            # events-0.3 stores the no-head declaration in seconds. Rebuild its
            # beat from timing events without rescanning the Simai source.
            declaration_beat = _beat_at_seconds(declaration_time, tempo)

        movement = 0.0
        valid_paths = []
        for path in paths:
            duration = path.end_time_s - path.start_time_s
            if duration <= 0:
                # A zero-duration path has no finite execution speed. It is
                # retained by the parser but contributes no Slide pressure.
                continue
            length = sum(
                segment["bar_count"]
                for segment in path.slide_path_json or ()
            )
            if length <= 0:
                raise ValueError("Slide path must have a positive geometric length")
            path_pressure = math.sqrt(length / LENGTH_REFERENCE_BARS)
            if not math.isfinite(path_pressure):
                continue
            movement += path_pressure
            valid_paths.append(path)

        if not valid_paths:
            continue

        output.append(_SlideGroup(
            key=key,
            events=tuple(valid_paths),
            declaration_time_s=declaration_time,
            declaration_beat=declaration_beat,
            # Branches may launch independently. The inclusive interference
            # window stays open until the final branch has launched.
            launch_time_s=max(path.start_time_s for path in valid_paths),
            movement_pressure=movement,
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


def _same_onset(left: _SlideGroup, right: _SlideGroup) -> bool:
    return (
        right.declaration_time_s - left.declaration_time_s
        <= SIMULTANEOUS_ONSET_SECONDS + COMPARISON_TOLERANCE
    )


def _build_onset_clusters(groups: list[_SlideGroup]) -> list[_SlideOnsetCluster]:
    if not groups:
        return []
    buckets = [[groups[0]]]
    for group in groups[1:]:
        if _same_onset(buckets[-1][0], group):
            buckets[-1].append(group)
        else:
            buckets.append([group])
    return [
        _SlideOnsetCluster(
            declaration_time_s=bucket[0].declaration_time_s,
            declaration_beat=bucket[0].declaration_beat,
            groups=tuple(bucket),
        )
        for bucket in buckets
    ]


def _build_sections(
    clusters: list[_SlideOnsetCluster],
) -> list[list[_SlideOnsetCluster]]:
    if not clusters:
        return []
    output = []
    current = [clusters[0]]
    for cluster in clusters[1:]:
        delta = float(cluster.declaration_beat - current[-1].declaration_beat)
        if delta > COMPARISON_TOLERANCE and delta <= 1 + COMPARISON_TOLERANCE:
            current.append(cluster)
        else:
            output.append(current)
            current = [cluster]
    output.append(current)
    return output


def _workload_points(
    events: tuple[Event, ...],
    groups: list[_SlideGroup],
) -> list[_WorkloadPoint]:
    head_owners = {
        group.head_event_id: group.key
        for group in groups
        if group.head_event_id is not None
    }
    output = []
    touches = []
    for event in events:
        if event.kind in {"tap", "hold"}:
            output.append(_WorkloadPoint(
                ("event", event.event_id),
                event.start_time_s,
                1.0,
                head_owners.get(event.event_id),
            ))
        elif event.kind in {"touch", "touch_hold"}:
            touches.append(event)

    # Only simultaneous spatial components are collapsed here. Pairing Touches
    # across time would move an onset away from a Slide launch boundary.
    for components in _simultaneous_touch_components(touches).values():
        for component in components:
            output.append(_WorkloadPoint(
                ("touch", component.component_id),
                component.time_s,
                TOUCH_INTERFERENCE_WEIGHT,
            ))

    for group in groups:
        # A shared-head group can contain branches with independent launches.
        # Equal launch times are one simultaneous Slide action for this purpose.
        for launch_index, launch_time in enumerate(sorted({
            event.start_time_s for event in group.events
        })):
            output.append(_WorkloadPoint(
                ("slide_launch", group.key, launch_index),
                launch_time,
                1.0,
                group.key,
            ))
    return sorted(output, key=lambda point: (point.time_s, point.point_id))


def _launch_interference(
    group: _SlideGroup,
    points: list[_WorkloadPoint],
) -> tuple[float, set[tuple]]:
    total = 0.0
    counted = set()
    for point in points:
        if not _time_in_closed_interval(
            point.time_s,
            group.declaration_time_s,
            group.launch_time_s,
        ):
            continue
        if point.owner_group_key == group.key:
            continue
        total += point.weight
        counted.add(point.point_id)
    return total, counted


def _cadence_factor(section: list[_SlideOnsetCluster]) -> float:
    if len(section) == 1:
        return 1.0
    factors = []
    for left, right in zip(section, section[1:]):
        interval = right.declaration_time_s - left.declaration_time_s
        if interval <= 0:
            raise ValueError("Distinct Slide onsets must advance in time")
        factors.append(CADENCE_REFERENCE_SECONDS / interval)
    return math.fsum(factors) / len(factors)


def _effective_slide_count(slide_count: int) -> float:
    if slide_count <= 0:
        raise ValueError("A Slide section must contain at least one group")
    if slide_count <= FULL_SEQUENCE_LENGTH:
        return float(slide_count)
    excess = (slide_count - FULL_SEQUENCE_LENGTH) / FULL_SEQUENCE_LENGTH
    return FULL_SEQUENCE_LENGTH * (1 + math.log1p(excess))


def _section_pressure(
    section: list[_SlideOnsetCluster],
    points: list[_WorkloadPoint],
) -> SlideSectionPressure:
    groups = [group for cluster in section for group in cluster.groups]
    member_keys = {group.key for group in groups}
    launch_total = 0.0
    covered_points = set()
    for group in groups:
        pressure, counted = _launch_interference(group, points)
        launch_total += pressure
        covered_points.update(counted)

    first_declaration = section[0].declaration_time_s
    last_declaration = section[-1].declaration_time_s
    gap_total = math.fsum(
        point.weight
        for point in points
        if _time_in_closed_interval(
            point.time_s,
            first_declaration,
            last_declaration,
        )
        and point.point_id not in covered_points
        and point.owner_group_key not in member_keys
    )
    slide_count = len(groups)
    mean_movement = math.fsum(
        group.movement_pressure for group in groups
    ) / slide_count
    context_per_slide = (launch_total + gap_total) / slide_count
    cadence = _cadence_factor(section)
    effective_slide_count = _effective_slide_count(slide_count)
    # Cadence scales Slide execution. Interference remains an independent
    # weighted addition instead of being multiplied by cadence again.
    intensity = (
        mean_movement * cadence
        + INTERFERENCE_SCALE * context_per_slide
    )
    return SlideSectionPressure(
        slide_count=slide_count,
        effective_slide_count=effective_slide_count,
        onset_count=len(section),
        mean_movement_pressure=mean_movement,
        context_per_slide=context_per_slide,
        cadence_factor=cadence,
        intensity=intensity,
        pressure=math.sqrt(effective_slide_count) * intensity,
    )


def slide_pressure_breakdown(
    events: tuple[Event, ...],
    duration_s: float,
) -> SlidePressureBreakdown:
    """Calculate chart, section, and density values without mutating events."""
    if duration_s <= 0:
        raise ValueError("Slide pressure requires a positive chart duration")
    groups = _build_slide_groups(events)
    if not groups:
        return SlidePressureBreakdown(
            global_pressure=0.0,
            active_intensity=0.0,
            slide_density_per_minute=0.0,
            effective_slide_density_per_minute=0.0,
            sections=(),
        )
    clusters = _build_onset_clusters(groups)
    points = _workload_points(events, groups)
    sections = tuple(
        _section_pressure(section, points)
        for section in _build_sections(clusters)
    )
    slide_count = sum(section.slide_count for section in sections)
    effective_slide_count = math.fsum(
        section.effective_slide_count for section in sections
    )
    energy = math.fsum(section.pressure ** 2 for section in sections)
    active_intensity = math.sqrt(energy / effective_slide_count)
    density = SECONDS_PER_MINUTE * slide_count / duration_s
    effective_density = SECONDS_PER_MINUTE * effective_slide_count / duration_s
    return SlidePressureBreakdown(
        global_pressure=active_intensity * math.sqrt(effective_density),
        active_intensity=active_intensity,
        slide_density_per_minute=density,
        effective_slide_density_per_minute=effective_density,
        sections=sections,
    )


class SlidePressureAnalyzer:
    """Return the global pressure contributed by every Slide section."""

    def analyze(self, context: AnalysisContext) -> FeatureResult:
        if context.duration_s <= 0:
            return FeatureResult(None, success=False)
        breakdown = slide_pressure_breakdown(context.events, context.duration_s)
        return FeatureResult(breakdown.global_pressure)
