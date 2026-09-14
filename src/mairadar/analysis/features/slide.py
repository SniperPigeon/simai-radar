"""Independent Slide misalignment and sequence-strength features."""

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
import math

from mairadar.analysis.model import AnalysisContext, FeatureResult
from mairadar.model import Event

from .note_density import _simultaneous_touch_components


CADENCE_REFERENCE_SECONDS = 0.5
SIMULTANEOUS_ONSET_SECONDS = 1 / 60
INTERNAL_GROWTH_ALPHA = 0.35
TRICKY_REFERENCE_SECONDS = 0.5
MAX_TIME_DENSITY_FACTOR = 4.0
TRICKY_FULL_ONSETS = 8
SEQUENCE_FULL_ONSETS = 3
CONCURRENCY_WEIGHT = 0.5
TOUCH_INTERFERENCE_WEIGHT = 1.5
COMPARISON_TOLERANCE = 1e-9


@dataclass(frozen=True)
class _SlideGroup:
    key: tuple
    events: tuple[Event, ...]
    declaration_time_s: float
    declaration_beat: Fraction
    launch_time_s: float
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
class _ClusterTricky:
    internal: float
    launch: float
    time_density_factor: float

    @property
    def intensity(self) -> float:
        return (self.internal + self.launch) * self.time_density_factor


@dataclass(frozen=True)
class SlideSectionMetrics:
    """Explainable intermediate values for one maximal Slide sequence."""

    slide_count: int
    onset_count: int
    internal_interference: float
    launch_interference: float
    mean_time_density_factor: float
    tricky_length_factor: float
    tricky_intensity: float
    tricky_load: float
    tricky_cluster_values: tuple[float, ...]
    cadence_factor: float
    concurrency_pressure: float
    sequence_length_factor: float
    sequence_intensity: float


@dataclass(frozen=True)
class SlideFeatureBreakdown:
    """The two independent Slide raw features and their section details."""

    tricky: float
    tricky_total_load: float
    tricky_time_units: float
    sequence: float
    sections: tuple[SlideSectionMetrics, ...]


def _time_in_closed_interval(value: float, start: float, end: float) -> bool:
    return (
        value >= start - COMPARISON_TOLERANCE
        and value <= end + COMPARISON_TOLERANCE
    )


def _same_time(left: float, right: float) -> bool:
    return math.isclose(
        left,
        right,
        rel_tol=0.0,
        abs_tol=COMPARISON_TOLERANCE,
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
            declaration_beat = _beat_at_seconds(declaration_time, tempo)

        # Zero-duration paths are retained by the parser but do not represent a
        # valid Slide action for either derived feature.
        valid_paths = tuple(
            path for path in paths
            if path.end_time_s > path.start_time_s
        )
        if not valid_paths:
            continue
        output.append(_SlideGroup(
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
        # Compare against the first onset so one cluster cannot grow without
        # bound through a transitive chain of sub-frame gaps.
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

    # Cross-time Touch grouping is deliberately not used because it would move
    # an onset across the strict Slide launch boundary.
    for components in _simultaneous_touch_components(touches).values():
        for component in components:
            output.append(_WorkloadPoint(
                ("touch", component.component_id),
                component.time_s,
                TOUCH_INTERFERENCE_WEIGHT,
            ))

    for group in groups:
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


def _internal_total(count: int) -> float:
    if count < 0:
        raise ValueError("Internal object count cannot be negative")
    if count == 0:
        return 0.0
    return count + INTERNAL_GROWTH_ALPHA * math.lgamma(count + 1) / math.log(2)


def _internal_batches(points: list[_WorkloadPoint]) -> list[list[_WorkloadPoint]]:
    if not points:
        return []
    batches = [[points[0]]]
    for point in points[1:]:
        if _same_time(batches[-1][0].time_s, point.time_s):
            batches[-1].append(point)
        else:
            batches.append([point])
    return batches


def _assign_points_to_onsets(
    clusters: list[_SlideOnsetCluster],
    points: list[_WorkloadPoint],
) -> list[list[_WorkloadPoint]]:
    """Assign each external workload point to at most one pending onset."""
    member_keys = {
        group.key
        for cluster in clusters
        for group in cluster.groups
    }
    assigned = [[] for _ in clusters]
    for point in points:
        if point.owner_group_key in member_keys:
            continue
        candidates = []
        for index, cluster in enumerate(clusters):
            pending = [
                group for group in cluster.groups
                if _time_in_closed_interval(
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


def _cluster_tricky(
    cluster: _SlideOnsetCluster,
    points: list[_WorkloadPoint],
) -> _ClusterTricky:
    launch_times = [group.launch_time_s for group in cluster.groups]
    launch_points = [
        point for point in points
        if any(_same_time(point.time_s, launch) for launch in launch_times)
    ]
    internal_points = [point for point in points if point not in launch_points]

    internal = 0.0
    previous_count = 0
    for batch in _internal_batches(internal_points):
        next_count = previous_count + len(batch)
        average_marginal = (
            _internal_total(next_count) - _internal_total(previous_count)
        ) / len(batch)
        internal += average_marginal * math.fsum(point.weight for point in batch)
        previous_count = next_count
    launch = math.fsum(point.weight for point in launch_points)
    interval = max(launch_times) - min(
        group.declaration_time_s for group in cluster.groups
    )
    # A quarter of the 0.5-second reference is the minimum effective wait, so
    # positive micro-waits and zero-wait launch pressure never exceed 4x.
    time_density = (
        MAX_TIME_DENSITY_FACTOR
        if interval <= COMPARISON_TOLERANCE
        else min(
            MAX_TIME_DENSITY_FACTOR,
            TRICKY_REFERENCE_SECONDS / interval,
        )
    )
    return _ClusterTricky(internal, launch, time_density)


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


def _log_length(count: int, full_count: int) -> float:
    if count <= 0 or full_count <= 0:
        raise ValueError("Length counts must be positive")
    if count <= full_count:
        return float(count)
    excess = (count - full_count) / full_count
    return full_count * (1 + math.log1p(excess))


def _tricky_length_factor(onset_count: int) -> float:
    if onset_count <= TRICKY_FULL_ONSETS:
        return 1.0
    effective = _log_length(onset_count, TRICKY_FULL_ONSETS)
    return math.sqrt(effective / TRICKY_FULL_ONSETS)


def _sequence_length_factor(onset_count: int) -> float:
    if onset_count <= SEQUENCE_FULL_ONSETS:
        return 1.0
    effective = _log_length(onset_count, SEQUENCE_FULL_ONSETS)
    return math.sqrt(effective / SEQUENCE_FULL_ONSETS)


def _cluster_concurrency(cluster: _SlideOnsetCluster) -> float:
    # Legal ordinary charts have U=1 (one path), U=2 (two heads), or
    # U=sqrt(2) (one head with two paths). The generic sum also keeps unusual
    # Utage structures defined without special cases.
    workload = math.fsum(math.sqrt(len(group.events)) for group in cluster.groups)
    return max(0.0, workload - 1)


def _section_metrics(
    section: list[_SlideOnsetCluster],
    assigned_points: dict[int, list[_WorkloadPoint]],
) -> SlideSectionMetrics:
    groups = [group for cluster in section for group in cluster.groups]
    cluster_tricky = [
        _cluster_tricky(cluster, assigned_points[id(cluster)])
        for cluster in section
    ]
    internal_total = math.fsum(item.internal for item in cluster_tricky)
    launch_total = math.fsum(item.launch for item in cluster_tricky)
    mean_time_density = math.fsum(
        item.time_density_factor for item in cluster_tricky
    ) / len(cluster_tricky)
    onset_count = len(section)
    tricky_length = _tricky_length_factor(onset_count)
    tricky_values = tuple(
        item.intensity * tricky_length for item in cluster_tricky
    )
    slide_count = len(groups)
    tricky_load = math.fsum(tricky_values)
    tricky_intensity = tricky_load / onset_count

    cadence = _cadence_factor(section)
    concurrency = math.fsum(
        _cluster_concurrency(cluster) for cluster in section
    ) / onset_count
    sequence_length = _sequence_length_factor(onset_count)
    continuous = cadence * sequence_length if onset_count >= 2 else 0.0
    sequence_intensity = continuous + CONCURRENCY_WEIGHT * concurrency
    return SlideSectionMetrics(
        slide_count=slide_count,
        onset_count=onset_count,
        internal_interference=internal_total,
        launch_interference=launch_total,
        mean_time_density_factor=mean_time_density,
        tricky_length_factor=tricky_length,
        tricky_intensity=tricky_intensity,
        tricky_load=tricky_load,
        tricky_cluster_values=tricky_values,
        cadence_factor=cadence,
        concurrency_pressure=concurrency,
        sequence_length_factor=sequence_length,
        sequence_intensity=sequence_intensity,
    )


def slide_feature_breakdown(
    events: tuple[Event, ...],
    duration_s: float,
) -> SlideFeatureBreakdown:
    """Calculate both Slide features without mutating canonical events."""
    if duration_s <= 0:
        raise ValueError("Slide features require a positive chart duration")
    groups = _build_slide_groups(events)
    if not groups:
        return SlideFeatureBreakdown(
            tricky=0.0,
            tricky_total_load=0.0,
            tricky_time_units=duration_s / TRICKY_REFERENCE_SECONDS,
            sequence=0.0,
            sections=(),
        )
    clusters = _build_onset_clusters(groups)
    points = _workload_points(events, groups)
    assigned = _assign_points_to_onsets(clusters, points)
    assigned_points = {
        id(cluster): cluster_points
        for cluster, cluster_points in zip(clusters, assigned)
    }
    sections = tuple(
        _section_metrics(section, assigned_points)
        for section in _build_sections(clusters)
    )

    tricky_total_load = math.fsum(section.tricky_load for section in sections)
    tricky_time_units = duration_s / TRICKY_REFERENCE_SECONDS
    tricky = tricky_total_load / tricky_time_units

    sequence_sections = [
        section for section in sections
        if section.sequence_intensity > 0
    ]
    sequence = (
        math.sqrt(math.fsum(
            section.sequence_intensity ** 2 for section in sequence_sections
        ) / len(sequence_sections))
        if sequence_sections else 0.0
    )
    return SlideFeatureBreakdown(
        tricky=tricky,
        tricky_total_load=tricky_total_load,
        tricky_time_units=tricky_time_units,
        sequence=sequence,
        sections=sections,
    )


class SlideTrickyAnalyzer:
    """Return Slide-head-to-launch interference intensity."""

    def analyze(self, context: AnalysisContext) -> FeatureResult:
        if context.duration_s <= 0:
            return FeatureResult(None, success=False)
        result = slide_feature_breakdown(context.events, context.duration_s)
        return FeatureResult(result.tricky)


class SlideSequenceAnalyzer:
    """Return continuous and concurrent Slide-pattern intensity."""

    def analyze(self, context: AnalysisContext) -> FeatureResult:
        if context.duration_s <= 0:
            return FeatureResult(None, success=False)
        result = slide_feature_breakdown(context.events, context.duration_s)
        return FeatureResult(result.sequence)
