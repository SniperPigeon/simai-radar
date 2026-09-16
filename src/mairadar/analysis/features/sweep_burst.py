"""Experimental three-second sweep burst load with two-hand motion costs."""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, replace
import math

from mairadar.analysis.model import AnalysisContext, FeatureResult
from mairadar.model import Event

from .hand_motion import sweep_family_hand_motion
from .sweep import (
    DEFAULT_MAX_STATES,
    SweepScoringConfig,
    score_sweep_sequences,
    sweep_sequences,
)


DEFAULT_WINDOW_SECONDS = 3.0
DEFAULT_IDLE_DISTANCE_WEIGHT = 0.5
DEFAULT_TAKEOVER_WEIGHT = 1.0
DEFAULT_FAST_JUMP_WEIGHT = 2.0
DEFAULT_SIMPLE_RUN_FULL_ATTACKS = 16
DEFAULT_SIMPLE_RUN_DECAY_EXPONENT = 0.5
DEFAULT_SAME_DIRECTION_CONNECTION_BONUS = 0.2
DEFAULT_SAME_DIRECTION_HANDOFF_BONUS = 0.2


@dataclass(frozen=True)
class SweepBurstScore:
    value: float
    base_density: float
    motion_density: float
    window_start_s: float
    window_end_s: float


def score_sweep_burst(
    events: tuple[Event, ...],
    *,
    duration_s: float,
    config: SweepScoringConfig | None = None,
    max_states: int = DEFAULT_MAX_STATES,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
    idle_distance_weight: float = DEFAULT_IDLE_DISTANCE_WEIGHT,
    takeover_weight: float = DEFAULT_TAKEOVER_WEIGHT,
    fast_jump_weight: float = DEFAULT_FAST_JUMP_WEIGHT,
    simple_run_full_attacks: int = DEFAULT_SIMPLE_RUN_FULL_ATTACKS,
    simple_run_decay_exponent: float = DEFAULT_SIMPLE_RUN_DECAY_EXPONENT,
    same_direction_connection_bonus: float = (
        DEFAULT_SAME_DIRECTION_CONNECTION_BONUS
    ),
    same_direction_handoff_bonus: float = DEFAULT_SAME_DIRECTION_HANDOFF_BONUS,
) -> SweepBurstScore:
    """Return the strongest fixed window without family multiplier carry-over."""
    numeric = {
        "duration_s": duration_s,
        "window_seconds": window_seconds,
        "idle_distance_weight": idle_distance_weight,
        "takeover_weight": takeover_weight,
        "fast_jump_weight": fast_jump_weight,
        "simple_run_decay_exponent": simple_run_decay_exponent,
        "same_direction_connection_bonus": same_direction_connection_bonus,
        "same_direction_handoff_bonus": same_direction_handoff_bonus,
    }
    for name, value in numeric.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            or (name in {"duration_s", "window_seconds"} and value <= 0)
        ):
            qualifier = (
                "positive"
                if name in {"duration_s", "window_seconds"}
                else "non-negative"
            )
            raise ValueError(f"{name} must be a {qualifier} finite number")
    if (
        isinstance(simple_run_full_attacks, bool)
        or not isinstance(simple_run_full_attacks, int)
        or simple_run_full_attacks <= 0
    ):
        raise ValueError("simple_run_full_attacks must be a positive integer")

    resolved = replace(config or SweepScoringConfig(), chord_note_multiplier=1.0)
    resolved.validate()
    sequences = sweep_sequences(
        events,
        max_states=max_states,
        speed_relative_tolerance=resolved.speed_relative_tolerance,
    )
    scored = score_sweep_sequences(sequences, config=resolved, duration_s=duration_s)

    points: dict[float, list[float]] = {}

    def add_point(time_s: float, base: float = 0.0, motion: float = 0.0) -> None:
        row = points.setdefault(time_s, [0.0, 0.0])
        row[0] += base
        row[1] += motion

    first_batch_base: dict[int, float] = {}
    groups_by_id = {group.group_id: group for group in scored.groups}
    for group in scored.groups:
        sequence = group.sequence
        speed_by_batch = (
            sequence.unit_intervals_seconds[0],
            *sequence.unit_intervals_seconds,
        )
        simple_uninterrupted_run = (
            len(sequence.times_s) > simple_run_full_attacks
            and all(width == 1 for width in sequence.widths)
            and not sequence.speed_switch_indexes
            and not sequence.direction_switch_indexes
        )
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
                first_batch_base[group.group_id] = raw_base
            decay = 1.0
            if simple_uninterrupted_run and index >= simple_run_full_attacks:
                excess_rank = index - simple_run_full_attacks + 2
                decay = excess_rank ** -simple_run_decay_exponent
            base = raw_base * decay
            if (
                index in sequence.double_handoff_indexes
                and index not in sequence.direction_switch_indexes
            ):
                base += raw_base * same_direction_handoff_bonus
            add_point(time_s, base=base)

    for group in scored.groups:
        if group.parent_group_id is None:
            continue
        parent = groups_by_id[group.parent_group_id]
        parent_directions = {
            strand.final_direction for strand in parent.sequence.strands
        }
        child_directions = {
            strand.initial_direction for strand in group.sequence.strands
        }
        if parent_directions & child_directions:
            add_point(
                group.sequence.start_time_s,
                base=(
                    first_batch_base[group.group_id]
                    * same_direction_connection_bonus
                ),
            )

    for family in scored.families:
        motion = sweep_family_hand_motion(family, scored.groups)
        for assignment in motion.assignments:
            bonus = (
                assignment.idle_reposition_distance * idle_distance_weight
                + assignment.free_hand_takeover * takeover_weight
                + assignment.fast_jump_violations * fast_jump_weight
            )
            if bonus:
                add_point(assignment.time_s, motion=bonus)

    max_start = max(0.0, duration_s - window_seconds)
    candidates = {0.0, max_start}
    for time_s in points:
        candidates.add(min(max_start, max(0.0, time_s)))
        candidates.add(min(max_start, max(0.0, time_s - window_seconds)))

    ordered = sorted(points.items())
    times = [time_s for time_s, _ in ordered]
    base_prefix = [0.0]
    motion_prefix = [0.0]
    for _, (base, motion) in ordered:
        base_prefix.append(base_prefix[-1] + base)
        motion_prefix.append(motion_prefix[-1] + motion)

    best = SweepBurstScore(0.0, 0.0, 0.0, 0.0, window_seconds)
    for start in sorted(candidates):
        left = bisect_left(times, start - 1e-9)
        right = bisect_right(times, start + window_seconds + 1e-9)
        base = base_prefix[right] - base_prefix[left]
        motion = motion_prefix[right] - motion_prefix[left]
        base_density = base / window_seconds
        motion_density = motion / window_seconds
        value = base_density + motion_density
        candidate = SweepBurstScore(
            value,
            base_density,
            motion_density,
            start,
            start + window_seconds,
        )
        if (candidate.value, -candidate.window_start_s) > (
            best.value,
            -best.window_start_s,
        ):
            best = candidate
    return best


@dataclass(frozen=True)
class SweepBurstAnalyzer:
    """Expose the experimental strongest three-second sweep burst as raw."""

    def analyze(self, context: AnalysisContext) -> FeatureResult:
        if context.duration_s <= 0:
            return FeatureResult(None, success=False)
        return FeatureResult(score_sweep_burst(
            context.events,
            duration_s=context.duration_s,
        ).value)
