"""Two-hand displacement DP for recognized sweep families."""

from dataclasses import dataclass
import math

from .sweep import ScoredSweepFamily, ScoredSweepGroup, circular_key_distance


REFERENCE_STEP_SECONDS = 1 / 12  # 180 BPM sixteenth.


@dataclass(frozen=True)
class HandAssignment:
    time_s: float
    lanes: tuple[int, ...]
    left_lanes: tuple[int, ...]
    right_lanes: tuple[int, ...]
    left_position: int | None
    right_position: int | None
    active_distance: int
    idle_reposition_distance: int
    free_hand_takeover: int
    fast_jump_violations: int
    left_idle_reposition_distance: int = 0
    right_idle_reposition_distance: int = 0


@dataclass(frozen=True)
class HandMotionResult:
    total_distance: int
    active_distance: int
    idle_reposition_distance: int
    free_hand_takeovers: int
    fast_jump_violations: int
    batch_count: int
    attack_count: int
    duration_s: float
    assignments: tuple[HandAssignment, ...]

    @property
    def distance_per_second(self) -> float:
        return self.total_distance / self.duration_s if self.duration_s > 0 else 0.0

    @property
    def distance_per_attack(self) -> float:
        return self.total_distance / self.attack_count if self.attack_count else 0.0


@dataclass(frozen=True)
class _MotionState:
    left_position: int | None
    right_position: int | None
    left_last_batch: int | None
    right_last_batch: int | None


@dataclass(frozen=True)
class _MotionRecord:
    fast_jump_violations: int = 0
    total_distance: int = 0
    active_distance: int = 0
    idle_reposition_distance: int = 0
    free_hand_takeovers: int = 0
    assignments: tuple[HandAssignment, ...] = ()

    @property
    def objective(self) -> tuple:
        return (
            self.fast_jump_violations,
            self.free_hand_takeovers,
            self.total_distance,
            self.active_distance,
            tuple(
                (
                    assignment.left_position or 0,
                    assignment.right_position or 0,
                )
                for assignment in self.assignments
            ),
        )


def _assignment_options(lanes: tuple[int, ...]):
    if len(lanes) == 1:
        lane = lanes[0]
        return (((lane,), (), lane, None), ((), (lane,), None, lane))
    if len(lanes) == 2:
        left, right = lanes
        return (
            ((left,), (right,), left, right),
            ((right,), (left,), right, left),
        )
    if len(lanes) == 3:
        output = []
        for single_index in range(3):
            single = lanes[single_index]
            pair = tuple(lane for index, lane in enumerate(lanes) if index != single_index)
            if circular_key_distance(*pair) != 1:
                continue
            for pair_position in pair:
                output.append((pair, (single,), pair_position, single))
                output.append(((single,), pair, single, pair_position))
        return tuple(output)
    return ()


def _move_cost(
    previous_position: int | None,
    target_position: int | None,
    last_batch: int | None,
    batch_index: int,
    time_s: float,
    previous_time_s: float | None,
) -> tuple[int, int, int, int]:
    if target_position is None or previous_position is None:
        return 0, 0, 0, 0
    distance = circular_key_distance(previous_position, target_position)
    active = last_batch == batch_index - 1
    active_distance = distance if active else 0
    idle_distance = distance if not active else 0
    fast_violation = 0
    if active and previous_time_s is not None:
        gap = time_s - previous_time_s
        allowed_steps = max(1, math.floor(gap / REFERENCE_STEP_SECONDS + 1e-9))
        fast_violation = int(distance > allowed_steps)
    return distance, active_distance, idle_distance, fast_violation


def two_hand_motion(
    times_s: tuple[float, ...],
    lanes_by_batch: tuple[tuple[int, ...], ...],
) -> HandMotionResult:
    """Minimize fast jumps first, then both hands' total circular displacement."""
    if len(times_s) != len(lanes_by_batch) or not times_s:
        raise ValueError("times_s and lanes_by_batch must have the same positive length")
    if any(
        not math.isfinite(time_s)
        or (index and time_s <= times_s[index - 1])
        for index, time_s in enumerate(times_s)
    ):
        raise ValueError("batch times must be finite and strictly increasing")
    normalized_lanes = tuple(tuple(sorted(set(lanes))) for lanes in lanes_by_batch)
    if any(
        not lanes
        or len(lanes) > 3
        or any(type(lane) is not int or not 1 <= lane <= 8 for lane in lanes)
        for lanes in normalized_lanes
    ):
        raise ValueError("each hand-motion batch needs one to three outer lanes")

    initial_state = _MotionState(None, None, None, None)
    states = {initial_state: _MotionRecord()}
    for batch_index, (time_s, lanes) in enumerate(zip(times_s, normalized_lanes)):
        options = _assignment_options(lanes)
        if not options:
            raise ValueError(f"batch {batch_index} cannot be covered by two hands")
        next_states = {}
        previous_time_s = times_s[batch_index - 1] if batch_index else None
        for state, record in states.items():
            previous_used_left = state.left_last_batch == batch_index - 1
            previous_used_right = state.right_last_batch == batch_index - 1
            for left_lanes, right_lanes, left_target, right_target in options:
                left_cost = _move_cost(
                    state.left_position,
                    left_target,
                    state.left_last_batch,
                    batch_index,
                    time_s,
                    previous_time_s,
                )
                right_cost = _move_cost(
                    state.right_position,
                    right_target,
                    state.right_last_batch,
                    batch_index,
                    time_s,
                    previous_time_s,
                )
                used_left = left_target is not None
                used_right = right_target is not None
                takeover = int(
                    len(lanes) == 1
                    and (
                        (used_left and not previous_used_left and previous_used_right)
                        or (used_right and not previous_used_right and previous_used_left)
                    )
                )
                next_state = _MotionState(
                    left_target if used_left else state.left_position,
                    right_target if used_right else state.right_position,
                    batch_index if used_left else state.left_last_batch,
                    batch_index if used_right else state.right_last_batch,
                )
                assignment = HandAssignment(
                    time_s=time_s,
                    lanes=lanes,
                    left_lanes=left_lanes,
                    right_lanes=right_lanes,
                    left_position=next_state.left_position,
                    right_position=next_state.right_position,
                    active_distance=left_cost[1] + right_cost[1],
                    idle_reposition_distance=left_cost[2] + right_cost[2],
                    free_hand_takeover=takeover,
                    fast_jump_violations=left_cost[3] + right_cost[3],
                    left_idle_reposition_distance=left_cost[2],
                    right_idle_reposition_distance=right_cost[2],
                )
                candidate = _MotionRecord(
                    fast_jump_violations=(
                        record.fast_jump_violations + left_cost[3] + right_cost[3]
                    ),
                    total_distance=record.total_distance + left_cost[0] + right_cost[0],
                    active_distance=(
                        record.active_distance + left_cost[1] + right_cost[1]
                    ),
                    idle_reposition_distance=(
                        record.idle_reposition_distance + left_cost[2] + right_cost[2]
                    ),
                    free_hand_takeovers=record.free_hand_takeovers + takeover,
                    assignments=record.assignments + (assignment,),
                )
                previous = next_states.get(next_state)
                if previous is None or candidate.objective < previous.objective:
                    next_states[next_state] = candidate
        states = next_states

    best = min(states.values(), key=lambda record: record.objective)
    return HandMotionResult(
        total_distance=best.total_distance,
        active_distance=best.active_distance,
        idle_reposition_distance=best.idle_reposition_distance,
        free_hand_takeovers=best.free_hand_takeovers,
        fast_jump_violations=best.fast_jump_violations,
        batch_count=len(times_s),
        attack_count=sum(len(lanes) for lanes in normalized_lanes),
        duration_s=times_s[-1] - times_s[0],
        assignments=best.assignments,
    )


def sweep_family_hand_motion(
    family: ScoredSweepFamily,
    groups: tuple[ScoredSweepGroup, ...],
) -> HandMotionResult:
    """Combine a scored family's groups and run the two-hand displacement DP."""
    by_id = {group.group_id: group for group in groups}
    batches: dict[float, set[int]] = {}
    for group_id in family.group_ids:
        group = by_id.get(group_id)
        if group is None:
            raise ValueError(f"Unknown sweep group id in family: {group_id}")
        for time_s, lanes in zip(
            group.sequence.times_s,
            group.sequence.lanes_by_batch,
        ):
            batches.setdefault(time_s, set()).update(lanes)
    ordered = sorted(batches.items())
    return two_hand_motion(
        tuple(time_s for time_s, _ in ordered),
        tuple(tuple(sorted(lanes)) for _, lanes in ordered),
    )
