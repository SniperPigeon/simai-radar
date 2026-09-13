"""Local burst density over a weighted three-window neighborhood."""

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
import math

from mairadar.analysis.model import AnalysisContext, FeatureResult
from mairadar.model import Event

from .note_density import (
    EIGHTH_NOTE_BEATS,
    WINDOW_SECONDS,
    _beat_duration,
    _slide_workload_points,
    _touch_workload_points,
)


PEAK_STEP_SECONDS = 0.5
LEFT_WEIGHT = 0.2
CENTER_WEIGHT = 0.6
RIGHT_WEIGHT = 0.2
TOUCH_GROUP_WEIGHT = 0.5
PEAK_COUNT = 3
PEAK_WEIGHTS = (0.5, 0.3, 0.2)
PEAK_NEIGHBORHOOD_SECONDS = 3 * WINDOW_SECONDS


@dataclass(frozen=True)
class _ButtonOnset:
    time_s: float
    beat: Fraction
    position: int
    weight: float


def _direction(left: int, right: int) -> int | None:
    if right == left % 8 + 1:
        return 1
    if right == (left - 2) % 8 + 1:
        return -1
    return None


def _decay_sweep_buttons(buttons: list[_ButtonOnset]) -> list[tuple[float, float]]:
    """Reduce the fifth and later notes in equal-interval adjacent-key sweeps."""
    if not buttons:
        return []
    by_beat = defaultdict(list)
    for button in buttons:
        by_beat[button.beat].append(button)

    run_length = 0
    previous = None
    interval = None
    direction = None
    output = []
    for beat in sorted(by_beat):
        simultaneous = by_beat[beat]
        if len(simultaneous) != 1:
            output.extend((button.time_s, button.weight) for button in simultaneous)
            run_length = 0
            previous = interval = direction = None
            continue

        current = simultaneous[0]
        current_direction = (
            _direction(previous.position, current.position)
            if previous is not None
            else None
        )
        current_interval = current.beat - previous.beat if previous is not None else None
        continues = (
            previous is not None
            and current_direction is not None
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
        else:
            if previous is not None and current_direction is not None and current_interval > 0:
                # The failed transition can still be the first edge of a new sweep.
                run_length = 2
                direction = current_direction
                interval = current_interval
            else:
                run_length = 1
                direction = interval = None

        divisor = math.log2(max(2, run_length - 2))
        output.append((current.time_s, current.weight / divisor))
        previous = current
    return output


def _peak_workload_points(events: tuple[Event, ...]) -> list[tuple[float, float]]:
    buttons = []
    slides = []
    touches = []
    for event in events:
        if event.kind in {"tap", "hold"}:
            if event.start_beat is None:
                raise ValueError("Button event is missing its beat position")
            weight = 1.0
            if event.kind == "hold" and _beat_duration(event) > EIGHTH_NOTE_BEATS:
                weight = 2.0
            buttons.append(_ButtonOnset(
                time_s=event.start_time_s,
                beat=Fraction(event.start_beat),
                position=int(event.position),
                weight=weight,
            ))
        elif event.kind == "slide":
            slides.append(event)
        elif event.kind in {"touch", "touch_hold"}:
            touches.append(event)

    output = _decay_sweep_buttons(buttons)
    output.extend(_slide_workload_points(slides, length_unit=None))
    output.extend(_touch_workload_points(touches, group_weight=TOUCH_GROUP_WEIGHT))
    return output


def _peak_density(points: list[tuple[float, float]], duration_s: float) -> float:
    if not points:
        return 0.0
    points = sorted(points)
    times = [time_s for time_s, _ in points]
    prefix = [0]
    for _, weight in points:
        prefix.append(prefix[-1] + weight)

    def density(start_s: float) -> float:
        end_s = start_s + WINDOW_SECONDS
        begin = bisect_left(times, start_s)
        end = bisect_left(times, end_s)
        return (prefix[end] - prefix[begin]) / WINDOW_SECONDS

    ratio = duration_s / PEAK_STEP_SECONDS
    nearest = round(ratio)
    if math.isclose(ratio, nearest, rel_tol=1e-12, abs_tol=1e-12):
        ratio = float(nearest)
    candidate_count = math.floor(ratio) + 1

    candidates = []
    for index in range(candidate_count):
        center_start = index * PEAK_STEP_SECONDS
        score = (
            LEFT_WEIGHT * density(center_start - WINDOW_SECONDS)
            + CENTER_WEIGHT * density(center_start)
            + RIGHT_WEIGHT * density(center_start + WINDOW_SECONDS)
        )
        candidates.append((score, center_start))

    selected = []
    for score, center_start in sorted(candidates, key=lambda item: (-item[0], item[1])):
        if all(
            abs(center_start - selected_start) >= PEAK_NEIGHBORHOOD_SECONDS
            for _, selected_start in selected
        ):
            selected.append((score, center_start))
            if len(selected) == PEAK_COUNT:
                break
    return math.fsum(
        weight * selected[index][0]
        for index, weight in enumerate(PEAK_WEIGHTS)
        if index < len(selected)
    )


class PeakDensityAnalyzer:
    """Combine the three strongest non-overlapping burst neighborhoods."""

    def analyze(self, context: AnalysisContext) -> FeatureResult:
        duration = context.duration_s
        if duration <= 0:
            return FeatureResult(None, success=False)
        points = _peak_workload_points(context.events)
        return FeatureResult(_peak_density(points, duration))
