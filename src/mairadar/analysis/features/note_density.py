"""Corrected note density over fixed, non-overlapping chart-time windows."""

from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
import math
from types import MappingProxyType

from mairadar.analysis.model import AnalysisContext, FeatureResult
from mairadar.model import Event


WINDOW_SECONDS = 1.5
SLIDE_LENGTH_UNIT = 64
BURST_WEIGHT = 0.3
EIGHTH_NOTE_BEATS = Fraction(1, 2)
SIXTEENTH_NOTE_BEATS = Fraction(1, 4)


def _ring(index: int) -> int:
    return (index - 1) % 8 + 1


def _build_touch_adjacency() -> dict[str, frozenset[str]]:
    """Build the shared-edge graph from the maimai DX sensor layout.

    C1 and C2 are deliberately represented as one logical C sensor. Regions
    which meet only at a corner (for example D1 and E1) are not adjacent.
    """
    mutable = {"C": set()}
    for family in "ABDE":
        for index in range(1, 9):
            mutable[f"{family}{index}"] = set()

    def connect(left: str, right: str) -> None:
        mutable[left].add(right)
        mutable[right].add(left)

    for index in range(1, 9):
        previous = _ring(index - 1)
        following = _ring(index + 1)
        a = f"A{index}"
        b = f"B{index}"
        d = f"D{index}"
        e = f"E{index}"

        # D_i lies between A_(i-1) and A_i. E_i lies between those
        # same A regions and the corresponding pair of B regions.
        connect(d, f"A{previous}")
        connect(d, a)
        connect(e, f"A{previous}")
        connect(e, a)
        connect(e, f"B{previous}")
        connect(e, b)

        # The aligned A/B regions share an edge. B is the continuous inner
        # ring and every B region borders the logical central C region.
        connect(a, b)
        connect(b, f"B{previous}")
        connect(b, f"B{following}")
        connect(b, "C")

    return {position: frozenset(neighbors) for position, neighbors in mutable.items()}


TOUCH_ADJACENCY = MappingProxyType(_build_touch_adjacency())


@dataclass(frozen=True)
class _TouchComponent:
    component_id: int
    beat: Fraction
    time_s: float
    positions: tuple[str, ...]


def _positions_are_adjacent(left: str, right: str) -> bool:
    # Equal positions are separate declarations, not an implicit deduplication.
    return left != right and right in TOUCH_ADJACENCY[left]


def _component_positions_are_adjacent(
    left: _TouchComponent,
    right: _TouchComponent,
) -> bool:
    return any(
        _positions_are_adjacent(left_position, right_position)
        for left_position in left.positions
        for right_position in right.positions
    )


def _graph_components(nodes, connected) -> list[list]:
    """Return deterministic connected components for a small note-time graph."""
    remaining = set(range(len(nodes)))
    output = []
    while remaining:
        first = min(remaining)
        remaining.remove(first)
        stack = [first]
        indexes = []
        while stack:
            current = stack.pop()
            indexes.append(current)
            neighbors = [
                candidate for candidate in sorted(remaining)
                if connected(nodes[current], nodes[candidate])
            ]
            for candidate in neighbors:
                remaining.remove(candidate)
                stack.append(candidate)
        output.append([nodes[index] for index in sorted(indexes)])
    return output


def _simultaneous_touch_components(events: list[Event]) -> dict[Fraction, list[_TouchComponent]]:
    by_beat = defaultdict(list)
    for event in events:
        if event.start_beat is None:
            raise ValueError("Touch event is missing its beat position")
        by_beat[Fraction(event.start_beat)].append(event)

    output = {}
    component_id = 0
    for beat in sorted(by_beat):
        simultaneous = by_beat[beat]
        groups = _graph_components(
            simultaneous,
            lambda left, right: _positions_are_adjacent(left.position, right.position),
        )
        components = []
        for group in groups:
            component_id += 1
            components.append(_TouchComponent(
                component_id=component_id,
                beat=beat,
                time_s=min(event.start_time_s for event in group),
                positions=tuple(event.position for event in group),
            ))
        output[beat] = components
    return output


def _cross_time_components(
    left: list[_TouchComponent],
    right: list[_TouchComponent],
) -> list[list[_TouchComponent]]:
    """Find only components containing an adjacency edge across two timestamps."""
    nodes = left + right
    left_ids = {component.component_id for component in left}
    right_ids = {component.component_id for component in right}

    def connected(first: _TouchComponent, second: _TouchComponent) -> bool:
        crosses_time = (
            first.component_id in left_ids and second.component_id in right_ids
        ) or (
            first.component_id in right_ids and second.component_id in left_ids
        )
        return crosses_time and _component_positions_are_adjacent(first, second)

    return [
        group for group in _graph_components(nodes, connected)
        if len(group) > 1
    ]


def _touch_workload_points(events: list[Event]) -> list[tuple[float, int]]:
    """Collapse spatial neighbors, then pair neighbors across at most two beats."""
    if not events:
        return []
    by_beat = _simultaneous_touch_components(events)
    beats = sorted(by_beat)
    used = set()
    output = []

    # Earliest eligible pair wins. A component is removed as soon as it joins
    # one cross-time group, so no resulting group can span three timestamps.
    for left_index, left_beat in enumerate(beats):
        for right_beat in beats[left_index + 1:]:
            if right_beat - left_beat > SIXTEENTH_NOTE_BEATS:
                break
            left = [item for item in by_beat[left_beat] if item.component_id not in used]
            right = [item for item in by_beat[right_beat] if item.component_id not in used]
            if not left or not right:
                continue
            for group in _cross_time_components(left, right):
                used.update(item.component_id for item in group)
                output.append((min(item.time_s for item in group), 1))

    for components in by_beat.values():
        output.extend(
            (component.time_s, 1)
            for component in components
            if component.component_id not in used
        )
    return sorted(output)


def _beat_duration(event: Event) -> Fraction:
    if event.start_beat is None or event.end_beat is None:
        raise ValueError("Hold event is missing its beat interval")
    duration = Fraction(event.end_beat) - Fraction(event.start_beat)
    if duration < 0:
        raise ValueError("Hold event has a negative beat interval")
    return duration


def _slide_workload_points(events: list[Event]) -> list[tuple[float, int]]:
    groups = defaultdict(list)
    for event in events:
        if event.head_event_id is not None:
            key = ("head", event.head_event_id)
        else:
            # No-head branches from one declaration retain the same source span.
            key = ("source", event.source_start, event.source_end, event.slide_declare_time_s)
        groups[key].append(event)

    output = []
    for group in groups.values():
        total_length = sum(
            segment["bar_count"]
            for event in group
            for segment in event.slide_path_json or ()
        )
        if total_length <= 0:
            raise ValueError("Slide group has no positive geometric length")
        output.append((
            min(event.start_time_s for event in group),
            (total_length + SLIDE_LENGTH_UNIT - 1) // SLIDE_LENGTH_UNIT,
        ))
    return output


def _workload_points(events: tuple[Event, ...]) -> list[tuple[float, int]]:
    output = []
    slides = []
    touches = []
    for event in events:
        if event.kind == "tap":
            output.append((event.start_time_s, 1))
        elif event.kind == "hold":
            weight = 1 if _beat_duration(event) <= EIGHTH_NOTE_BEATS else 2
            output.append((event.start_time_s, weight))
        elif event.kind == "slide":
            slides.append(event)
        elif event.kind in {"touch", "touch_hold"}:
            touches.append(event)
    output.extend(_slide_workload_points(slides))
    output.extend(_touch_workload_points(touches))
    return output


def _window_count(duration_s: float) -> int:
    ratio = duration_s / WINDOW_SECONDS
    nearest = round(ratio)
    if math.isclose(ratio, nearest, rel_tol=1e-12, abs_tol=1e-12):
        ratio = float(nearest)
    return max(1, math.ceil(ratio))


class NoteDensityAnalyzer:
    """Compute mean density with a population-variation burst correction."""

    def analyze(self, context: AnalysisContext) -> FeatureResult:
        duration = context.duration_s
        if duration <= 0:
            return FeatureResult(None, success=False)

        count = _window_count(duration)
        workload = [0] * count
        for time_s, weight in _workload_points(context.events):
            # The final window includes an event exactly at the chart endpoint.
            index = min(math.floor(time_s / WINDOW_SECONDS), count - 1)
            workload[index] += weight

        densities = [value / WINDOW_SECONDS for value in workload]
        mean = math.fsum(densities) / count
        if mean == 0:
            return FeatureResult(0.0)
        variance = math.fsum((value - mean) ** 2 for value in densities) / count
        coefficient_of_variation = math.sqrt(variance) / mean
        return FeatureResult(mean * (1 + BURST_WEIGHT * coefficient_of_variation))
