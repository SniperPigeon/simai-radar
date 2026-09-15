"""Maximal adjacent-button sweep recognition and speed-weighted aggregation."""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
import math

from mairadar.analysis.model import AnalysisContext, FeatureResult
from mairadar.model import Event


MAX_STEP_BEATS = Fraction(1)
TIMING_TOLERANCE_BEATS = Fraction(1, 16)
MIN_DISTINCT_LANES = 3
MIN_DIRECTION_RUN_STEPS = 2
MAX_INTERVENING_ATTACKS = 1
DEFAULT_MAX_STATES = 100_000
DEFAULT_REFERENCE_INTERVAL_SECONDS = 0.1
DEFAULT_SPEED_EXPONENT = 1.0
DEFAULT_COUNT_DISCOUNT_EXPONENT = 0.5
DETECTOR_VERSION = "uniform_v5_events_0_3"
SCORING_VERSION = "sweep_weighted_v5_per_second"


@dataclass(frozen=True)
class ButtonAttack:
    """One physical outer-button onset, retaining every source declaration."""

    attack_id: int
    lane: int
    beat: Fraction
    time_s: float
    event_ids: tuple[int, ...]
    kinds: tuple[str, ...]

    @property
    def declaration_count(self) -> int:
        return len(self.event_ids)


@dataclass(frozen=True)
class SweepSequence:
    """One selected maximal sweep candidate."""

    lanes: tuple[int, ...]
    event_ids_by_attack: tuple[tuple[int, ...], ...]
    start_beat: Fraction
    end_beat: Fraction
    start_time_s: float
    end_time_s: float
    gaps_beats: tuple[Fraction, ...]
    period_beats: Fraction
    turn_note_indexes: tuple[int, ...]

    @property
    def attack_count(self) -> int:
        return len(self.lanes)

    @property
    def declaration_count(self) -> int:
        return sum(len(event_ids) for event_ids in self.event_ids_by_attack)

    @property
    def strength(self) -> float:
        return float(self.attack_count)

    @property
    def interval_seconds(self) -> float:
        return (self.end_time_s - self.start_time_s) / (self.attack_count - 1)


@dataclass(frozen=True)
class SweepScoringConfig:
    """Numeric sweep policy supplied by sweep_weighted_v5."""

    reference_interval_seconds: float = DEFAULT_REFERENCE_INTERVAL_SECONDS
    speed_exponent: float = DEFAULT_SPEED_EXPONENT
    count_discount_exponent: float = DEFAULT_COUNT_DISCOUNT_EXPONENT

    def validate(self) -> None:
        for name, value in (
            ("reference_interval_seconds", self.reference_interval_seconds),
            ("speed_exponent", self.speed_exponent),
            ("count_discount_exponent", self.count_discount_exponent),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be a positive finite number")


@dataclass(frozen=True)
class ScoredSweepGroup:
    """One recognized group after continuation and rank weighting."""

    group_id: int
    sequence: SweepSequence
    speed_factor: float
    base_weight: float
    continuation_multiplier: float
    follower_group_id: int | None
    follower_gap_units: float | None
    start_key_distance: int | None
    simultaneous_group_ids: tuple[int, ...]
    simultaneous_multiplier: float
    total_multiplier: float
    boosted_weight: float
    contribution_rank: int
    count_discount: float
    contribution: float


@dataclass(frozen=True)
class SweepScore:
    """Detailed score result; value is normalized when duration_s is supplied."""

    value: float
    weighted_total: float
    undiscounted_score: float
    duration_s: float | None
    groups: tuple[ScoredSweepGroup, ...]


def button_attacks(events: tuple[Event, ...]) -> tuple[ButtonAttack, ...]:
    """Build physical Tap/Hold onsets without discarding duplicate declarations."""
    grouped: dict[tuple[Fraction, int], list[Event]] = {}
    for event in events:
        if event.kind not in {"tap", "hold"}:
            continue
        if event.start_beat is None:
            raise ValueError("Button event is missing its beat position")
        beat = Fraction(event.start_beat)
        lane = int(event.position)
        grouped.setdefault((beat, lane), []).append(event)

    attacks = []
    for attack_id, ((beat, lane), declarations) in enumerate(sorted(grouped.items())):
        declarations.sort(key=lambda event: event.event_id)
        first_time = declarations[0].start_time_s
        if any(
            not math.isclose(
                event.start_time_s, first_time, rel_tol=0, abs_tol=1e-12
            )
            for event in declarations[1:]
        ):
            raise ValueError("Same-beat duplicate attacks disagree on chart time")
        attacks.append(ButtonAttack(
            attack_id=attack_id,
            lane=lane,
            beat=beat,
            time_s=min(event.start_time_s for event in declarations),
            event_ids=tuple(event.event_id for event in declarations),
            kinds=tuple(
                "slide_head" if event.is_slide_head else event.kind
                for event in declarations
            ),
        ))
    return tuple(attacks)


def _indexes(mask: int):
    while mask:
        bit = mask & -mask
        yield bit.bit_length() - 1
        mask ^= bit


def _select_disjoint_paths(
    paths: list[tuple[int, ...]],
    *,
    max_states: int,
) -> list[tuple[int, ...]]:
    """Select the most groups, then the most physical attacks."""
    owners: dict[int, list[int]] = {}
    conflicts = [1 << index for index in range(len(paths))]
    for index, path in enumerate(paths):
        for attack_id in path:
            owners.setdefault(attack_id, []).append(index)
    for attack_id, path_indexes in owners.items():
        for left in path_indexes:
            for right in path_indexes:
                if paths[left][-1] != attack_id or paths[right][-1] != attack_id:
                    conflicts[left] |= 1 << right

    states = 0

    @lru_cache(None)
    def solve(mask: int) -> tuple[int, ...]:
        nonlocal states
        states += 1
        if states > max_states:
            raise ValueError(
                "Sweep group selection limit exceeded; no partial result returned"
            )
        if not mask:
            return ()

        component = mask & -mask
        frontier = component
        while frontier:
            neighbors = 0
            for index in _indexes(frontier):
                neighbors |= conflicts[index]
            frontier = neighbors & mask & ~component
            component |= frontier
        if component != mask:
            return tuple(sorted(solve(component) + solve(mask ^ component)))

        pivot = max(
            _indexes(mask),
            key=lambda index: (conflicts[index] & mask).bit_count(),
        )
        included = tuple(sorted((pivot,) + solve(mask & ~conflicts[pivot])))
        excluded = solve(mask & ~(1 << pivot))

        def score(chosen: tuple[int, ...]) -> tuple[int, int]:
            return (
                len(chosen),
                sum(len(paths[index]) for index in chosen),
            )
        if score(included) == score(excluded):
            return min(included, excluded)
        return max(included, excluded, key=score)

    chosen = solve((1 << len(paths)) - 1)
    return [paths[index] for index in chosen]


def _candidate_paths(
    attacks: tuple[ButtonAttack, ...],
    *,
    max_states: int,
) -> list[tuple[int, ...]]:
    all_beats = [attack.beat for attack in attacks]
    by_lane = {lane: [] for lane in range(1, 9)}
    for attack in attacks:
        by_lane[attack.lane].append(attack.attack_id)
    lane_beats = {
        lane: [attacks[attack_id].beat for attack_id in attack_ids]
        for lane, attack_ids in by_lane.items()
    }

    def next_attack(lane: int, beat: Fraction) -> int | None:
        index = bisect_right(lane_beats[lane], beat)
        return by_lane[lane][index] if index < len(by_lane[lane]) else None

    edges: list[list[tuple[int, int]]] = [[] for _ in attacks]
    for attack in attacks:
        repeat = next_attack(attack.lane, attack.beat)
        for direction in (-1, 1):
            lane = (attack.lane - 1 + direction) % 8 + 1
            following = next_attack(lane, attack.beat)
            if following is None:
                continue
            target = attacks[following]
            intervening = (
                bisect_left(all_beats, target.beat)
                - bisect_right(all_beats, attack.beat)
            )
            if intervening > MAX_INTERVENING_ATTACKS:
                continue
            if repeat is not None and attacks[repeat].beat < target.beat:
                continue
            if 0 < target.beat - attack.beat <= MAX_STEP_BEATS:
                edges[attack.attack_id].append((following, direction))

    candidates: set[tuple[int, ...]] = set()
    visited_states = 0
    for attack in attacks:
        stack = [((attack.attack_id,), ())]
        while stack:
            path, directions = stack.pop()
            visited_states += 1
            if visited_states > max_states:
                raise ValueError(
                    "Sweep candidate limit exceeded; no partial result returned"
                )

            run_length = 0
            for previous_direction in reversed(directions):
                if previous_direction != directions[-1]:
                    break
                run_length += 1
            if (
                run_length >= MIN_DIRECTION_RUN_STEPS
                and len({attacks[index].lane for index in path}) >= MIN_DISTINCT_LANES
            ):
                candidates.add(path)

            for target, direction in edges[path[-1]]:
                if len(path) >= 2:
                    period = attacks[path[1]].beat - attacks[path[0]].beat
                    gap = attacks[target].beat - attacks[path[-1]].beat
                    if abs(gap - period) > TIMING_TOLERANCE_BEATS:
                        continue
                if (
                    directions
                    and direction != directions[-1]
                    and run_length < MIN_DIRECTION_RUN_STEPS
                ):
                    continue
                stack.append((path + (target,), directions + (direction,)))

    contained = set()
    for path in candidates:
        for start in range(len(path) - 2):
            for end in range(start + MIN_DISTINCT_LANES, len(path) + 1):
                subpath = path[start:end]
                if subpath != path and subpath in candidates:
                    contained.add(subpath)
    return sorted(candidates - contained)


def _validate_parameters(
    *,
    max_states: int,
) -> None:
    if isinstance(max_states, bool) or not isinstance(max_states, int) or max_states <= 0:
        raise ValueError("max_states must be a positive integer")


def sweep_sequences(
    events: tuple[Event, ...],
    *,
    max_states: int = DEFAULT_MAX_STATES,
) -> tuple[SweepSequence, ...]:
    """Return selected maximal sweeps in deterministic chart order."""
    _validate_parameters(max_states=max_states)
    attacks = button_attacks(events)
    paths = _candidate_paths(attacks, max_states=max_states)
    selected = _select_disjoint_paths(paths, max_states=max_states)
    sequences = []
    for path in selected:
        notes = [attacks[index] for index in path]
        span_s = notes[-1].time_s - notes[0].time_s
        if span_s <= 0:
            raise ValueError("Sweep sequence must advance in chart time")
        directions = tuple(
            1 if (right.lane - left.lane) % 8 == 1 else -1
            for left, right in zip(notes, notes[1:])
        )
        sequences.append(SweepSequence(
            lanes=tuple(note.lane for note in notes),
            event_ids_by_attack=tuple(note.event_ids for note in notes),
            start_beat=notes[0].beat,
            end_beat=notes[-1].beat,
            start_time_s=notes[0].time_s,
            end_time_s=notes[-1].time_s,
            gaps_beats=tuple(
                right.beat - left.beat for left, right in zip(notes, notes[1:])
            ),
            period_beats=notes[1].beat - notes[0].beat,
            turn_note_indexes=tuple(
                index
                for index in range(1, len(directions))
                if directions[index] != directions[index - 1]
            ),
        ))
    return tuple(sorted(
        sequences,
        key=lambda sequence: (
            sequence.start_beat,
            sequence.lanes,
            sequence.event_ids_by_attack,
        ),
    ))


def circular_key_distance(left: int, right: int) -> int:
    distance = abs(left - right)
    return min(distance, 8 - distance)


def score_sweep_sequences(
    sequences: tuple[SweepSequence, ...],
    *,
    config: SweepScoringConfig | None = None,
    duration_s: float | None = None,
) -> SweepScore:
    """Apply the supplied sweep_weighted_v5 numeric policy."""
    config = config or SweepScoringConfig()
    config.validate()
    if duration_s is not None and (
        isinstance(duration_s, bool)
        or not isinstance(duration_s, (int, float))
        or not math.isfinite(duration_s)
        or duration_s <= 0
    ):
        raise ValueError("duration_s must be a positive finite number")

    prepared = []
    ordered_sequences = sorted(
        sequences,
        key=lambda sequence: (
            sequence.start_time_s,
            sequence.lanes,
            sequence.event_ids_by_attack,
        ),
    )
    for group_id, sequence in enumerate(ordered_sequences, start=1):
        if (
            sequence.attack_count < MIN_DISTINCT_LANES
            or any(type(lane) is not int or not 1 <= lane <= 8 for lane in sequence.lanes)
        ):
            raise ValueError(f"Invalid lanes for sweep group {group_id}")
        if (
            not math.isfinite(sequence.start_time_s)
            or not math.isfinite(sequence.end_time_s)
            or sequence.end_time_s <= sequence.start_time_s
        ):
            raise ValueError(f"Invalid time range for sweep group {group_id}")
        interval = sequence.interval_seconds
        speed_factor = (
            config.reference_interval_seconds / interval
        ) ** config.speed_exponent
        prepared.append({
            "group_id": group_id,
            "sequence": sequence,
            "speed_factor": speed_factor,
            "base_weight": sequence.attack_count * speed_factor,
        })

    for group in prepared:
        sequence = group["sequence"]
        simultaneous_ids = tuple(
            other["group_id"]
            for other in prepared
            if other["group_id"] != group["group_id"]
            and math.isclose(
                other["sequence"].start_time_s,
                sequence.start_time_s,
                rel_tol=0,
                abs_tol=1e-9,
            )
        )
        simultaneous_multiplier = 1.6 if simultaneous_ids else 1.0
        matches = []
        for follower in prepared:
            if follower["group_id"] == group["group_id"]:
                continue
            follower_sequence = follower["sequence"]
            units = (
                follower_sequence.start_time_s - sequence.end_time_s
            ) / sequence.interval_seconds
            if units < -1e-9 or units > 4 + 1e-9:
                continue
            units = max(0.0, units)
            distance = circular_key_distance(
                sequence.lanes[0], follower_sequence.lanes[0]
            )
            if units <= 1 + 1e-9:
                multiplier = 4.0 if distance <= 1 else 2.0
                if (
                    distance <= 1
                    and math.isclose(
                        follower_sequence.start_time_s,
                        sequence.end_time_s,
                        rel_tol=0,
                        abs_tol=1e-9,
                    )
                ):
                    multiplier = 8.0
            elif 2 - 1e-9 <= units <= 4 + 1e-9:
                multiplier = 1.2
            else:
                continue
            matches.append((
                -multiplier,
                follower_sequence.start_time_s,
                follower["group_id"],
                units,
                distance,
            ))
        if matches:
            negative_multiplier, _, follower_id, units, distance = min(matches)
            continuation_multiplier = -negative_multiplier
        else:
            continuation_multiplier = 1.0
            follower_id = units = distance = None
        total_multiplier = max(continuation_multiplier, simultaneous_multiplier)
        group.update(
            continuation_multiplier=continuation_multiplier,
            follower_group_id=follower_id,
            follower_gap_units=units,
            start_key_distance=distance,
            simultaneous_group_ids=simultaneous_ids,
            simultaneous_multiplier=simultaneous_multiplier,
            total_multiplier=total_multiplier,
            boosted_weight=group["base_weight"] * total_multiplier,
        )

    ranked = sorted(
        prepared,
        key=lambda group: (
            -group["boosted_weight"],
            group["sequence"].start_time_s,
            group["group_id"],
        ),
    )
    rank_by_id = {}
    for rank, group in enumerate(ranked, start=1):
        discount = rank ** -config.count_discount_exponent
        rank_by_id[group["group_id"]] = (
            rank,
            discount,
            group["boosted_weight"] * discount,
        )

    scored_groups = []
    for group in prepared:
        rank, discount, contribution = rank_by_id[group["group_id"]]
        scored_groups.append(ScoredSweepGroup(
            group_id=group["group_id"],
            sequence=group["sequence"],
            speed_factor=group["speed_factor"],
            base_weight=group["base_weight"],
            continuation_multiplier=group["continuation_multiplier"],
            follower_group_id=group["follower_group_id"],
            follower_gap_units=group["follower_gap_units"],
            start_key_distance=group["start_key_distance"],
            simultaneous_group_ids=group["simultaneous_group_ids"],
            simultaneous_multiplier=group["simultaneous_multiplier"],
            total_multiplier=group["total_multiplier"],
            boosted_weight=group["boosted_weight"],
            contribution_rank=rank,
            count_discount=discount,
            contribution=contribution,
        ))
    weighted_total = math.fsum(group.contribution for group in scored_groups)
    return SweepScore(
        value=weighted_total / duration_s if duration_s is not None else weighted_total,
        weighted_total=weighted_total,
        undiscounted_score=math.fsum(
            group.boosted_weight for group in scored_groups
        ),
        duration_s=duration_s,
        groups=tuple(scored_groups),
    )


def sweep_score(
    events: tuple[Event, ...],
    *,
    max_states: int = DEFAULT_MAX_STATES,
    config: SweepScoringConfig | None = None,
    duration_s: float | None = None,
) -> float:
    """Return total or per-second raw sweep intensity."""
    sequences = sweep_sequences(
        events,
        max_states=max_states,
    )
    return score_sweep_sequences(
        sequences,
        config=config,
        duration_s=duration_s,
    ).value


@dataclass(frozen=True)
class SweepAnalyzer:
    """Measure maximal adjacent-button sweeps as one numeric dimension."""

    max_states: int = DEFAULT_MAX_STATES
    reference_interval_seconds: float = DEFAULT_REFERENCE_INTERVAL_SECONDS
    speed_exponent: float = DEFAULT_SPEED_EXPONENT
    count_discount_exponent: float = DEFAULT_COUNT_DISCOUNT_EXPONENT

    def __post_init__(self) -> None:
        _validate_parameters(max_states=self.max_states)
        self.scoring_config.validate()

    @property
    def scoring_config(self) -> SweepScoringConfig:
        return SweepScoringConfig(
            reference_interval_seconds=self.reference_interval_seconds,
            speed_exponent=self.speed_exponent,
            count_discount_exponent=self.count_discount_exponent,
        )

    def analyze(self, context: AnalysisContext) -> FeatureResult:
        if context.duration_s <= 0:
            return FeatureResult(None, success=False)
        return FeatureResult(sweep_score(
            context.events,
            max_states=self.max_states,
            config=self.scoring_config,
            duration_s=context.duration_s,
        ))
