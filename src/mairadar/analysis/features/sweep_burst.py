"""Ranked two-second sweep bursts with two-hand motion costs."""

from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass, replace
import math

from mairadar.analysis.model import AnalysisContext, FeatureResult
from mairadar.model import Event

from .hand_motion import (
    REFERENCE_STEP_SECONDS,
    HandMotionResult,
    sweep_family_hand_motion,
)
from .sweep import (
    DEFAULT_MAX_STATES,
    ScoredSweepFamily,
    ScoredSweepGroup,
    SweepScoringConfig,
    score_sweep_sequences,
    sweep_sequences,
)


DEFAULT_WINDOW_SECONDS = 2.0
DEFAULT_WINDOW_COUNT = 3
DEFAULT_WINDOW_RANK_DECAY_EXPONENT = 0.5
DEFAULT_IDLE_DISTANCE_WEIGHT = 0.5
DEFAULT_TAKEOVER_WEIGHT = 1.0
DEFAULT_FAST_JUMP_WEIGHT = 2.0
DEFAULT_IDLE_SPEED_REFERENCE_KEYS_PER_SECOND = 1 / (2 * REFERENCE_STEP_SECONDS)
DEFAULT_SIMPLE_RUN_FULL_ATTACKS = 16
DEFAULT_SIMPLE_RUN_DECAY_EXPONENT = 0.5
DEFAULT_SAME_DIRECTION_CONNECTION_BONUS = 0.2
DEFAULT_SAME_DIRECTION_HANDOFF_BONUS = 0.2
DEFAULT_PATTERN_MOTION_FLOOR = 0.1
PATTERN_MIN_GROUPS = 6
PATTERN_MAX_PERIOD = 4
PATTERN_MIN_MATCH_RATIO = 0.8


@dataclass(frozen=True)
class SweepBurstWindow:
    value: float
    base_density: float
    motion_density: float
    raw_motion_density: float
    window_start_s: float
    window_end_s: float


@dataclass(frozen=True)
class SweepBurstScore:
    value: float
    base_density: float
    motion_density: float
    raw_motion_density: float
    window_start_s: float
    window_end_s: float
    windows: tuple[SweepBurstWindow, ...]


def _same_direction_connection(
    parent: ScoredSweepGroup,
    child: ScoredSweepGroup,
) -> bool:
    return bool(
        {strand.final_direction for strand in parent.sequence.strands}
        & {strand.initial_direction for strand in child.sequence.strands}
    )


def _idle_distance_pressure(
    distance: int,
    elapsed_s: float,
    reference_keys_per_second: float,
) -> float:
    """Charge idle travel above the reference key-per-second movement rate."""
    if distance == 0:
        return 0.0
    if elapsed_s <= 0:
        raise ValueError("Idle hand travel requires positive elapsed time")
    speed_ratio = distance / (elapsed_s * reference_keys_per_second)
    return distance * math.sqrt(max(1.0, speed_ratio))


def _simple_group_token(
    group: ScoredSweepGroup,
    motion: HandMotionResult,
) -> tuple[str, int] | None:
    sequence = group.sequence
    if (
        any(width != 1 for width in sequence.widths)
        or sequence.speed_switch_indexes
        or sequence.direction_switch_indexes
        or sequence.double_handoff_indexes
    ):
        return None
    directions = {
        direction
        for strand in sequence.strands
        for direction in (strand.initial_direction, strand.final_direction)
    }
    if len(directions) != 1:
        return None
    assignments_by_time = {
        assignment.time_s: assignment for assignment in motion.assignments
    }
    hands = set()
    for time_s in sequence.times_s:
        assignment = assignments_by_time.get(time_s)
        if assignment is None:
            return None
        if assignment.left_lanes and not assignment.right_lanes:
            hands.add("L")
        elif assignment.right_lanes and not assignment.left_lanes:
            hands.add("R")
        else:
            return None
    if len(hands) != 1:
        return None
    return next(iter(hands)), next(iter(directions))


def _mark_regular_segment(
    segment: list[tuple[int, tuple[str, int]]],
) -> set[int]:
    if len(segment) < PATTERN_MIN_GROUPS:
        return set()
    best: tuple[float, int, tuple[tuple[str, int], ...]] | None = None
    for period in range(1, min(PATTERN_MAX_PERIOD, len(segment) // 3) + 1):
        template = tuple(
            Counter(
                token
                for index, (_, token) in enumerate(segment)
                if index % period == phase
            ).most_common(1)[0][0]
            for phase in range(period)
        )
        matched = sum(
            token == template[index % period]
            for index, (_, token) in enumerate(segment)
        )
        ratio = matched / len(segment)
        if ratio < PATTERN_MIN_MATCH_RATIO:
            continue
        quality = ratio - 0.02 * (period - 1)
        candidate = quality, -period, template
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    if best is None:
        return set()
    template = best[2]
    period = len(template)
    return {
        group_id
        for index, (group_id, token) in enumerate(segment)
        if token == template[index % period]
    }


def _regular_pattern_group_ids(
    family: ScoredSweepFamily,
    groups_by_id: dict[int, ScoredSweepGroup],
    motion: HandMotionResult,
    protected_group_ids: set[int],
) -> set[int]:
    regular = set()
    segment: list[tuple[int, tuple[str, int]]] = []
    members = sorted(
        (groups_by_id[group_id] for group_id in family.group_ids),
        key=lambda group: group.sequence.start_time_s,
    )
    for group in members:
        token = _simple_group_token(group, motion)
        if token is None:
            regular.update(_mark_regular_segment(segment))
            segment = []
        else:
            segment.append((group.group_id, token))
    regular.update(_mark_regular_segment(segment))
    return regular - protected_group_ids


def score_sweep_burst(
    events: tuple[Event, ...],
    *,
    duration_s: float,
    config: SweepScoringConfig | None = None,
    max_states: int = DEFAULT_MAX_STATES,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
    window_count: int = DEFAULT_WINDOW_COUNT,
    window_rank_decay_exponent: float = DEFAULT_WINDOW_RANK_DECAY_EXPONENT,
    idle_distance_weight: float = DEFAULT_IDLE_DISTANCE_WEIGHT,
    takeover_weight: float = DEFAULT_TAKEOVER_WEIGHT,
    fast_jump_weight: float = DEFAULT_FAST_JUMP_WEIGHT,
    idle_speed_reference_keys_per_second: float = (
        DEFAULT_IDLE_SPEED_REFERENCE_KEYS_PER_SECOND
    ),
    simple_run_full_attacks: int = DEFAULT_SIMPLE_RUN_FULL_ATTACKS,
    simple_run_decay_exponent: float = DEFAULT_SIMPLE_RUN_DECAY_EXPONENT,
    same_direction_connection_bonus: float = (
        DEFAULT_SAME_DIRECTION_CONNECTION_BONUS
    ),
    same_direction_handoff_bonus: float = DEFAULT_SAME_DIRECTION_HANDOFF_BONUS,
    pattern_motion_floor: float = DEFAULT_PATTERN_MOTION_FLOOR,
) -> SweepBurstScore:
    """Return the strongest fixed window without family multiplier carry-over."""
    numeric = {
        "duration_s": duration_s,
        "window_seconds": window_seconds,
        "window_rank_decay_exponent": window_rank_decay_exponent,
        "idle_distance_weight": idle_distance_weight,
        "takeover_weight": takeover_weight,
        "fast_jump_weight": fast_jump_weight,
        "idle_speed_reference_keys_per_second": idle_speed_reference_keys_per_second,
        "simple_run_decay_exponent": simple_run_decay_exponent,
        "same_direction_connection_bonus": same_direction_connection_bonus,
        "same_direction_handoff_bonus": same_direction_handoff_bonus,
        "pattern_motion_floor": pattern_motion_floor,
    }
    for name, value in numeric.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            or (
                name in {
                    "duration_s",
                    "window_seconds",
                    "idle_speed_reference_keys_per_second",
                }
                and value <= 0
            )
        ):
            qualifier = (
                "positive"
                if name in {
                    "duration_s",
                    "window_seconds",
                    "idle_speed_reference_keys_per_second",
                }
                else "non-negative"
            )
            raise ValueError(f"{name} must be a {qualifier} finite number")
    if (
        isinstance(simple_run_full_attacks, bool)
        or not isinstance(simple_run_full_attacks, int)
        or simple_run_full_attacks <= 0
    ):
        raise ValueError("simple_run_full_attacks must be a positive integer")
    if (
        isinstance(window_count, bool)
        or not isinstance(window_count, int)
        or window_count <= 0
    ):
        raise ValueError("window_count must be a positive integer")
    if pattern_motion_floor > 1:
        raise ValueError("pattern_motion_floor must be at most one")

    resolved = replace(config or SweepScoringConfig(), chord_note_multiplier=1.0)
    resolved.validate()
    sequences = sweep_sequences(
        events,
        max_states=max_states,
        speed_relative_tolerance=resolved.speed_relative_tolerance,
    )
    scored = score_sweep_sequences(sequences, config=resolved, duration_s=duration_s)

    points: dict[float, list[float]] = {}

    def add_point(
        time_s: float,
        base: float = 0.0,
        motion: float = 0.0,
        raw_motion: float = 0.0,
    ) -> None:
        row = points.setdefault(time_s, [0.0, 0.0, 0.0])
        row[0] += base
        row[1] += motion
        row[2] += raw_motion

    first_batch_physical_base: dict[int, float] = {}
    groups_by_id = {group.group_id: group for group in scored.groups}
    for group in scored.groups:
        sequence = group.sequence
        speed_by_batch = (
            sequence.unit_intervals_seconds[0],
            *sequence.unit_intervals_seconds,
        )
        simple_run_length = 0
        for index, time_s in enumerate(sequence.times_s):
            note_weight = (
                sequence.normal_declaration_counts[index]
                + sequence.protected_declaration_counts[index]
                * resolved.protected_note_weight
            )
            speed_factor = (
                resolved.reference_interval_seconds / speed_by_batch[index]
            ) ** resolved.speed_exponent
            raw_base = note_weight * speed_factor
            if index == 0:
                first_batch_physical_base[group.group_id] = (
                    sequence.widths[index] * speed_factor
                )
            decay = 1.0
            if sequence.widths[index] >= 2:
                simple_run_length = 0
            else:
                if (
                    index in sequence.speed_switch_indexes
                    or index in sequence.direction_switch_indexes
                ):
                    simple_run_length = 0
                simple_run_length += 1
                if simple_run_length > simple_run_full_attacks:
                    excess_rank = simple_run_length - simple_run_full_attacks + 1
                    decay = excess_rank ** -simple_run_decay_exponent
            base = raw_base * decay
            if (
                index in sequence.double_handoff_indexes
                and index not in sequence.direction_switch_indexes
            ):
                base += (
                    sequence.widths[index]
                    * speed_factor
                    * same_direction_handoff_bonus
                )
            add_point(time_s, base=base)

    same_direction_group_ids = set()
    for group in scored.groups:
        if group.parent_group_id is None:
            continue
        parent = groups_by_id[group.parent_group_id]
        if _same_direction_connection(parent, group):
            same_direction_group_ids.add(group.group_id)
            add_point(
                group.sequence.start_time_s,
                base=(
                    first_batch_physical_base[group.group_id]
                    * same_direction_connection_bonus
                ),
            )

    for family in scored.families:
        motion = sweep_family_hand_motion(family, scored.groups)
        last_hand_use: dict[str, float | None] = {"L": None, "R": None}
        regular_group_ids = _regular_pattern_group_ids(
            family,
            groups_by_id,
            motion,
            same_direction_group_ids,
        )
        regular_start_times = {
            groups_by_id[group_id].sequence.start_time_s
            for group_id in regular_group_ids
        }
        for assignment in motion.assignments:
            weighted_idle_distance = 0.0
            for hand, lanes, distance in (
                (
                    "L",
                    assignment.left_lanes,
                    assignment.left_idle_reposition_distance,
                ),
                (
                    "R",
                    assignment.right_lanes,
                    assignment.right_idle_reposition_distance,
                ),
            ):
                if distance:
                    previous_time = last_hand_use[hand]
                    if previous_time is None:
                        raise ValueError("Idle displacement has no prior hand use")
                    weighted_idle_distance += _idle_distance_pressure(
                        distance,
                        assignment.time_s - previous_time,
                        idle_speed_reference_keys_per_second,
                    )
                if lanes:
                    last_hand_use[hand] = assignment.time_s
            raw_bonus = (
                weighted_idle_distance * idle_distance_weight
                + assignment.free_hand_takeover * takeover_weight
                + assignment.fast_jump_violations * fast_jump_weight
            )
            if raw_bonus:
                factor = (
                    pattern_motion_floor
                    if assignment.time_s in regular_start_times
                    else 1.0
                )
                add_point(
                    assignment.time_s,
                    motion=raw_bonus * factor,
                    raw_motion=raw_bonus,
                )

    max_start = max(0.0, duration_s - window_seconds)
    candidates = {0.0, max_start}
    for time_s in points:
        candidates.add(min(max_start, max(0.0, time_s)))
        candidates.add(min(max_start, max(0.0, time_s - window_seconds)))

    ordered = sorted(points.items())
    times = [time_s for time_s, _ in ordered]
    base_prefix = [0.0]
    motion_prefix = [0.0]
    raw_motion_prefix = [0.0]
    for _, (base, motion, raw_motion) in ordered:
        base_prefix.append(base_prefix[-1] + base)
        motion_prefix.append(motion_prefix[-1] + motion)
        raw_motion_prefix.append(raw_motion_prefix[-1] + raw_motion)

    candidates_by_start = []
    for start in sorted(candidates):
        left = bisect_left(times, start - 1e-9)
        right = bisect_left(times, start + window_seconds - 1e-9)
        base = base_prefix[right] - base_prefix[left]
        motion = motion_prefix[right] - motion_prefix[left]
        raw_motion = raw_motion_prefix[right] - raw_motion_prefix[left]
        base_density = base / window_seconds
        motion_density = motion / window_seconds
        raw_motion_density = raw_motion / window_seconds
        value = base_density + motion_density
        candidates_by_start.append(SweepBurstWindow(
            value,
            base_density,
            motion_density,
            raw_motion_density,
            start,
            start + window_seconds,
        ))

    selected = []
    for candidate in sorted(
        candidates_by_start,
        key=lambda item: (-item.value, item.window_start_s),
    ):
        if candidate.value <= 0:
            continue
        if any(
            candidate.window_start_s < other.window_end_s - 1e-9
            and other.window_start_s < candidate.window_end_s - 1e-9
            for other in selected
        ):
            continue
        selected.append(candidate)
        if len(selected) == window_count:
            break

    if not selected:
        empty = SweepBurstWindow(0.0, 0.0, 0.0, 0.0, 0.0, window_seconds)
        return SweepBurstScore(0.0, 0.0, 0.0, 0.0, 0.0, window_seconds, (empty,))

    weights = tuple(
        rank ** -window_rank_decay_exponent
        for rank in range(1, len(selected) + 1)
    )
    weight_total = math.fsum(weights)

    def weighted(attribute: str) -> float:
        return math.fsum(
            weight * getattr(window, attribute)
            for weight, window in zip(weights, selected)
        ) / weight_total

    strongest = selected[0]
    return SweepBurstScore(
        weighted("value"),
        weighted("base_density"),
        weighted("motion_density"),
        weighted("raw_motion_density"),
        strongest.window_start_s,
        strongest.window_end_s,
        tuple(selected),
    )


@dataclass(frozen=True)
class SweepBurstAnalyzer:
    """Expose the ranked two-second sweep burst aggregate as raw."""

    def analyze(self, context: AnalysisContext) -> FeatureResult:
        if context.duration_s <= 0:
            return FeatureResult(None, success=False)
        return FeatureResult(score_sweep_burst(
            context.events,
            duration_s=context.duration_s,
        ).value)
