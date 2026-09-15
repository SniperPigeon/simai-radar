"""Independent Slide misalignment and sequence-strength features."""

from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from fractions import Fraction
import math
from statistics import median

from mairadar.analysis.model import AnalysisContext, FeatureResult
from mairadar.model import Event

from .note_density import _simultaneous_touch_components


CADENCE_REFERENCE_SECONDS = 0.5
SIMULTANEOUS_ONSET_SECONDS = 1 / 60
SEQUENCE_FULL_ONSETS = 3
CONCURRENCY_WEIGHT = 0.5
TRICKY_OBJECT_CAP = 16
TRICKY_TOP_COUNT = 5
TRICKY_SPEED_REFERENCE_EIGHTH_BPM = 180.0
TRICKY_SPEED_EXPONENT = 0.5
TRICKY_SPEED_MAX_FACTOR = 1.5
TRICKY_UNIQUE_COUNT = 5
TRICKY_LOAD_BUCKET_DECIMALS = 6
TOUCH_INTERFERENCE_WEIGHT = 1.5
TOUCH_INTERFERENCE_CAP = 2
SAME_POSITION_MULTIPLIER = 1.5
PENDING_SLIDE_HEAD_MULTIPLIER = 2.0
MULTI_SLIDE_UPLIFT = 0.15
COMPARISON_TOLERANCE = 1e-9


@dataclass(frozen=True)
class _SlideGroup:
    key: tuple
    events: tuple[Event, ...]
    declaration_time_s: float
    declaration_beat: Fraction
    launch_time_s: float
    launch_times_s: tuple[float, ...]
    active_intervals: tuple[tuple[float, float], ...]
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
    kind: str
    beat: Fraction | None = None
    position: str | None = None
    owner_group_key: tuple | None = None
    slide_head_body_weight: float = 0.0


@dataclass(frozen=True)
class _AssignedPoint:
    point: _WorkloadPoint
    phase: str


@dataclass(frozen=True)
class _ClusterTricky:
    internal: float
    launch: float
    logical_object_count: int = 0
    object_cap_factor: float = 1.0
    ordinary_button_speed_factor: float = 1.0
    configuration_multiplier: float = 1.0

    @property
    def intensity(self) -> float:
        return (
            (self.internal + self.launch)
            * self.object_cap_factor
            * self.configuration_multiplier
        )


@dataclass(frozen=True)
class SlideTrickyPoint:
    """One de-duplicated Slide onset configuration in the burst timeline."""

    time_s: float
    internal: float
    launch: float
    load: float
    slide_count: int
    head_count: int = 1
    logical_object_count: int = 0
    object_cap_factor: float = 1.0
    ordinary_button_speed_factor: float = 1.0
    configuration_multiplier: float = 1.0


@dataclass(frozen=True)
class SlideSectionMetrics:
    """Explainable intermediate values for one maximal Slide sequence."""

    slide_count: int
    onset_count: int
    internal_interference: float
    launch_interference: float
    tricky_effective_length: float
    tricky_intensity: float
    tricky_load: float
    tricky_cluster_values: tuple[float, ...]
    cadence_factor: float
    concurrency_pressure: float
    sequence_length_factor: float
    sequence_intensity: float


@dataclass(frozen=True)
class SlideFeatureBreakdown:
    """The independent Slide raw features and their shared section details."""

    tricky: float
    tricky_total_load: float
    tricky_unique_loads: tuple[float, ...]
    tricky_peak_time_s: float | None
    tricky_points: tuple[SlideTrickyPoint, ...]
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


def _seconds_at_beat(
    beat: Fraction,
    tempo: list[tuple[float, Fraction, Fraction]],
) -> float:
    beats = [point[1] for point in tempo]
    index = bisect_right(beats, beat) - 1
    if index < 0:
        raise ValueError("Beat precedes the first BPM event")
    origin_time, origin_beat, bpm = tempo[index]
    return origin_time + float((beat - origin_beat) * 60 / bpm)


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
        active_intervals = []
        for path in valid_paths:
            if path.start_beat is None:
                raise ValueError("Slide path is missing its launch beat")
            one_beat_end = _seconds_at_beat(
                Fraction(path.start_beat) + 1,
                tempo,
            )
            active_intervals.append((
                path.start_time_s,
                min(path.end_time_s, one_beat_end),
            ))
        output.append(_SlideGroup(
            key=key,
            events=valid_paths,
            declaration_time_s=declaration_time,
            declaration_beat=declaration_beat,
            launch_time_s=max(path.start_time_s for path in valid_paths),
            launch_times_s=tuple(sorted({
                path.start_time_s for path in valid_paths
            })),
            active_intervals=tuple(active_intervals),
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
    head_body_weights = {
        group.head_event_id: float(len(group.events))
        for group in groups
        if group.head_event_id is not None
    }
    output = []
    touches = []
    for event in events:
        if event.kind in {"tap", "hold"}:
            if event.start_beat is None:
                raise ValueError("Button event is missing its beat position")
            output.append(_WorkloadPoint(
                ("event", event.event_id),
                event.start_time_s,
                1.0,
                "button",
                Fraction(event.start_beat),
                event.position,
                head_owners.get(event.event_id),
                head_body_weights.get(event.event_id, 0.0),
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
                "touch",
            ))

    for group in groups:
        launches = defaultdict(int)
        for event in group.events:
            launches[event.start_time_s] += 1
        for launch_index, (launch_time, path_count) in enumerate(
            sorted(launches.items())
        ):
            output.append(_WorkloadPoint(
                ("slide_launch", group.key, launch_index),
                launch_time,
                float(path_count),
                "slide_launch",
                owner_group_key=group.key,
            ))
    return sorted(output, key=lambda point: (point.time_s, point.point_id))


def _adjacent_direction(left: Counter[int], right: Counter[int]) -> int | None:
    clockwise = Counter({position % 8 + 1: count for position, count in left.items()})
    if clockwise == right:
        return 1
    counterclockwise = Counter({(position - 2) % 8 + 1: count for position, count in left.items()})
    if counterclockwise == right:
        return -1
    return None


def _button_point_weight(
    point: _WorkloadPoint,
    target_positions: set[str],
    same_position_point_ids: set[tuple] | None = None,
    ordinary_button_speed_factor: float = 1.0,
) -> float:
    multiplier = 1.0
    same_position_eligible = (
        same_position_point_ids is None
        or point.point_id in same_position_point_ids
    )
    if same_position_eligible and point.position in target_positions:
        multiplier = max(multiplier, SAME_POSITION_MULTIPLIER)
    # is_slide_head alone also covers display-only star modifiers. Requiring an
    # attached, valid Slide body keeps those syntax-only stars at Tap weight.
    if point.slide_head_body_weight > 0:
        multiplier = max(multiplier, PENDING_SLIDE_HEAD_MULTIPLIER)
    speed_factor = (
        ordinary_button_speed_factor
        if point.slide_head_body_weight == 0 else 1.0
    )
    return point.weight * multiplier * speed_factor


def _sweep_adjusted_button_total(
    points: list[_WorkloadPoint],
    target_positions: set[str],
    same_position_point_ids: set[tuple] | None = None,
    ordinary_button_speed_factor: float = 1.0,
) -> float:
    """Sum Tap/Hold load, decaying equal-rhythm one- and two-lane sweeps."""
    if not points:
        return 0.0
    by_beat = defaultdict(list)
    for point in points:
        if point.beat is None or point.position is None:
            raise ValueError("Button workload point lacks beat or position")
        by_beat[point.beat].append(point)

    run_length = 0
    previous_positions = None
    previous_beat = None
    interval = None
    direction = None
    total = 0.0
    for beat in sorted(by_beat):
        batch = by_beat[beat]
        width = len(batch)
        batch_weight = math.fsum(
            _button_point_weight(
                point,
                target_positions,
                same_position_point_ids,
                ordinary_button_speed_factor,
            )
            for point in batch
        )
        if width not in {1, 2}:
            total += batch_weight
            run_length = 0
            previous_positions = previous_beat = interval = direction = None
            continue

        positions = Counter(int(point.position) for point in batch)
        current_interval = beat - previous_beat if previous_beat is not None else None
        current_direction = (
            _adjacent_direction(previous_positions, positions)
            if previous_positions is not None and sum(previous_positions.values()) == width
            else None
        )
        continues = (
            previous_beat is not None
            and current_direction is not None
            and current_interval is not None
            and current_interval > 0
            and (
                run_length == 1
                or (current_direction == direction and current_interval == interval)
            )
        )
        if continues:
            run_length += 1
            if run_length == 2:
                direction = current_direction
                interval = current_interval
        elif (
            previous_beat is not None
            and current_direction is not None
            and current_interval is not None
            and current_interval > 0
        ):
            run_length = 2
            direction = current_direction
            interval = current_interval
        else:
            run_length = 1
            direction = interval = None

        # The third timestamp has divisor log2(2)=1; actual attenuation begins
        # at the fourth, exactly following 1/log2(n-1).
        divisor = math.log2(max(2, run_length - 1))
        total += batch_weight / divisor
        previous_positions = positions
        previous_beat = beat
    return total


def _ordinary_button_speed_factor(
    cluster: _SlideOnsetCluster,
    assigned: list[_AssignedPoint],
) -> float:
    """Scale ordinary buttons around 180-BPM eighth-note speed."""
    ordinary = [
        item.point
        for item in assigned
        if (
            item.point.kind == "button"
            and item.point.slide_head_body_weight == 0
        )
    ]
    if not ordinary:
        return 1.0
    times = sorted({point.time_s for point in ordinary})
    timeline = [cluster.declaration_time_s, *times]
    gaps = [
        right - left
        for left, right in zip(timeline, timeline[1:])
        if right - left > COMPARISON_TOLERANCE
    ]
    if not gaps:
        return 1.0
    equivalent_eighth_bpm = 30 / median(gaps)
    ratio = equivalent_eighth_bpm / TRICKY_SPEED_REFERENCE_EIGHTH_BPM
    if ratio < 1:
        return ratio ** TRICKY_SPEED_EXPONENT
    return min(TRICKY_SPEED_MAX_FACTOR, ratio)


def _assign_points_to_onsets(
    clusters: list[_SlideOnsetCluster],
    points: list[_WorkloadPoint],
) -> list[list[_AssignedPoint]]:
    """Assign each point to one launch, pending, or active configuration."""
    assigned = [[] for _ in clusters]
    for point in points:
        candidates = []
        for index, cluster in enumerate(clusters):
            cluster_keys = {group.key for group in cluster.groups}
            if point.owner_group_key in cluster_keys:
                continue
            launch = any(
                _same_time(point.time_s, launch_time)
                for group in cluster.groups
                for launch_time in group.launch_times_s
            )
            waiting = any(
                point.time_s >= group.declaration_time_s - COMPARISON_TOLERANCE
                and point.time_s < group.launch_time_s - COMPARISON_TOLERANCE
                for group in cluster.groups
            )
            active = any(
                point.time_s > start + COMPARISON_TOLERANCE
                and point.time_s <= end + COMPARISON_TOLERANCE
                for group in cluster.groups
                for start, end in group.active_intervals
            )
            phase = (
                "launch" if launch
                else "waiting" if waiting
                else "active" if active
                else None
            )
            if phase is not None:
                if phase == "launch":
                    priority = 0
                    distance = 0.0
                elif phase == "waiting":
                    priority = 1
                    distance = min(
                        group.launch_time_s - point.time_s
                        for group in cluster.groups
                        if (
                            point.time_s
                            >= group.declaration_time_s - COMPARISON_TOLERANCE
                            and point.time_s
                            < group.launch_time_s - COMPARISON_TOLERANCE
                        )
                    )
                else:
                    priority = 2
                    distance = min(
                        point.time_s - start
                        for group in cluster.groups
                        for start, end in group.active_intervals
                        if (
                            point.time_s > start + COMPARISON_TOLERANCE
                            and point.time_s <= end + COMPARISON_TOLERANCE
                        )
                    )
                candidates.append((priority, distance, index, phase))
        if candidates:
            _, _, index, phase = min(candidates)
            assigned[index].append(
                _AssignedPoint(point=point, phase=phase)
            )
    return assigned


def _cluster_tricky(
    cluster: _SlideOnsetCluster,
    assigned: list[_AssignedPoint],
) -> _ClusterTricky:
    target_positions = {
        event.position
        for group in cluster.groups
        for event in group.events
        if event.position is not None
    }
    touch_points = [
        item.point
        for item in sorted(
            assigned,
            key=lambda item: (item.point.time_s, item.point.point_id),
        )
        if item.point.kind == "touch"
    ]
    touch_ids = {
        point.point_id for point in touch_points[:TOUCH_INTERFERENCE_CAP]
    }
    counted = [
        item for item in assigned
        if item.point.kind != "touch" or item.point.point_id in touch_ids
    ]
    launch_points = [
        item.point for item in counted if item.phase == "launch"
    ]
    internal = [
        item for item in counted if item.phase != "launch"
    ]

    internal_buttons = [
        item.point for item in internal if item.point.kind == "button"
    ]
    internal_other = [
        item.point for item in internal if item.point.kind != "button"
    ]
    waiting_button_ids = {
        item.point.point_id
        for item in internal
        if item.phase == "waiting" and item.point.kind == "button"
    }
    speed_factor = _ordinary_button_speed_factor(cluster, counted)
    internal_total = _sweep_adjusted_button_total(
        internal_buttons,
        target_positions,
        waiting_button_ids,
        speed_factor,
    ) + math.fsum(point.weight for point in internal_other)
    launch_head_owners = {
        point.owner_group_key
        for point in launch_points
        if point.kind == "button" and point.slide_head_body_weight > 0
    }
    deduplicated_launch_points = [
        point for point in launch_points
        if not (
            point.kind == "slide_launch"
            and point.owner_group_key in launch_head_owners
        )
    ]
    launch = math.fsum(
        point.weight + point.slide_head_body_weight
        for point in deduplicated_launch_points
    ) / 2
    logical_object_count = sum(
        int(point.weight) if point.kind == "slide_launch" else 1
        for point in internal_other
    ) + len(internal_buttons) + sum(
        (
            int(point.weight)
            if point.kind == "slide_launch"
            else 1 + int(point.slide_head_body_weight)
            if point.kind == "button"
            else 1
        )
        for point in deduplicated_launch_points
    )
    object_cap_factor = (
        min(1.0, TRICKY_OBJECT_CAP / logical_object_count)
        if logical_object_count > 0 else 1.0
    )
    concurrency = math.fsum(
        math.sqrt(len(group.events)) for group in cluster.groups
    )
    multiplier = 1 + MULTI_SLIDE_UPLIFT * max(0.0, concurrency - 1)
    return _ClusterTricky(
        internal=internal_total,
        launch=launch,
        logical_object_count=logical_object_count,
        object_cap_factor=object_cap_factor,
        ordinary_button_speed_factor=speed_factor,
        configuration_multiplier=multiplier,
    )


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
    assigned_points: dict[int, list[_AssignedPoint]],
) -> SlideSectionMetrics:
    groups = [group for cluster in section for group in cluster.groups]
    cluster_tricky = [
        _cluster_tricky(cluster, assigned_points[id(cluster)])
        for cluster in section
    ]
    internal_total = math.fsum(item.internal for item in cluster_tricky)
    launch_total = math.fsum(item.launch for item in cluster_tricky)
    onset_count = len(section)
    tricky_values = tuple(item.intensity for item in cluster_tricky)
    slide_count = len(groups)
    tricky_mean = math.fsum(tricky_values) / onset_count
    # These section fields remain useful diagnostics for Slide sequence output,
    # but no section boundary or length bonus contributes to slide_tricky.
    tricky_intensity = tricky_mean
    tricky_effective_length = float(onset_count)
    tricky_load = math.fsum(tricky_values)

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
        tricky_effective_length=tricky_effective_length,
        tricky_intensity=tricky_intensity,
        tricky_load=tricky_load,
        tricky_cluster_values=tricky_values,
        cadence_factor=cadence,
        concurrency_pressure=concurrency,
        sequence_length_factor=sequence_length,
        sequence_intensity=sequence_intensity,
    )


def _top_unique_tricky_loads(loads: list[float]) -> tuple[float, ...]:
    buckets = {}
    for load in loads:
        key = round(load, TRICKY_LOAD_BUCKET_DECIMALS)
        buckets[key] = max(load, buckets.get(key, 0.0))
    return tuple(sorted(buckets.values(), reverse=True)[:TRICKY_UNIQUE_COUNT])


def _top_unique_tricky_load(loads: list[float]) -> float:
    return math.fsum(_top_unique_tricky_loads(loads))


def slide_feature_breakdown(
    events: tuple[Event, ...],
    duration_s: float,
) -> SlideFeatureBreakdown:
    """Calculate Slide features without mutating canonical events."""
    if duration_s <= 0:
        raise ValueError("Slide features require a positive chart duration")
    groups = _build_slide_groups(events)
    if not groups:
        return SlideFeatureBreakdown(
            tricky=0.0,
            tricky_total_load=0.0,
            tricky_unique_loads=(),
            tricky_peak_time_s=None,
            tricky_points=(),
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

    cluster_tricky = [
        _cluster_tricky(cluster, assigned_points[id(cluster)])
        for cluster in clusters
    ]
    tricky_points = tuple(
        SlideTrickyPoint(
            time_s=max(group.launch_time_s for group in cluster.groups),
            internal=value.internal,
            launch=value.launch,
            load=value.intensity,
            slide_count=sum(len(group.events) for group in cluster.groups),
            head_count=len(cluster.groups),
            logical_object_count=value.logical_object_count,
            object_cap_factor=value.object_cap_factor,
            ordinary_button_speed_factor=value.ordinary_button_speed_factor,
            configuration_multiplier=value.configuration_multiplier,
        )
        for cluster, value in zip(clusters, cluster_tricky)
    )
    tricky_unique_loads = _top_unique_tricky_loads(
        [point.load for point in tricky_points]
    )
    peak = max(
        tricky_points,
        key=lambda point: (point.load, -point.time_s),
    )
    top_loads = sorted(
        (point.load for point in tricky_points),
        reverse=True,
    )[:TRICKY_TOP_COUNT]
    tricky_total_load = math.fsum(top_loads)
    tricky = tricky_total_load / TRICKY_TOP_COUNT

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
        tricky_unique_loads=tricky_unique_loads,
        tricky_peak_time_s=peak.time_s,
        tricky_points=tricky_points,
        sequence=sequence,
        sections=sections,
    )


class SlideTrickyAnalyzer:
    """Return the zero-padded mean of the five strongest Slide configurations."""

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
