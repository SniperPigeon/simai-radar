"""Sweep-family recognition over one- or two-strand button fronts."""

from bisect import bisect_left
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
import math

from mairadar.analysis.model import AnalysisContext, FeatureResult
from mairadar.model import Event


SHORT_HOLD_MAX_BEATS = Fraction(1, 4)
BASE_MAX_UNIT_BEATS = Fraction(1, 3)
EIGHTH_NOTE_BEATS = Fraction(1, 2)
DEFAULT_MAX_STATES = 100_000
DEFAULT_REFERENCE_INTERVAL_SECONDS = Fraction(1, 12)  # 180 BPM sixteenth.
DEFAULT_SPEED_EXPONENT = 0.5
DEFAULT_SPEED_RELATIVE_TOLERANCE = 0.005
DEFAULT_PROTECTED_WEIGHT = 0.3
DEFAULT_CHORD_NOTE_MULTIPLIER = 1.3
DEFAULT_MINIMUM_FAMILY_ATTACK_COUNT = 9
DEFAULT_MEAN_WEIGHT = 0.6
DEFAULT_PEAK_WEIGHT = 0.4
DEFAULT_DURATION_REFERENCE_SECONDS = 150.0
DEFAULT_DURATION_CORRECTION_EXPONENT = 0.5
DEFAULT_SINGLE_CONNECTION_INCREMENT = 0.2
DEFAULT_DOUBLE_CONNECTION_INCREMENT = 0.0
DEFAULT_WIDTH_CHANGE_INCREMENT = 0.0
DEFAULT_SPEED_CHANGE_INCREMENT = 0.2
DEFAULT_SAME_DIRECTION_INCREMENT = 0.1
DEFAULT_REVERSAL_INCREMENT = 0.2
FAMILY_LIMIT = 5
DETECTOR_VERSION = "main_spine_v2_chord_handoff"
SCORING_VERSION = "sweep_family_blend_v7_family_mean_duration_sqrt"


@dataclass(frozen=True)
class ButtonAttack:
    """One physical outer-button onset, retaining every source declaration."""

    attack_id: int
    lane: int
    beat: Fraction
    time_s: float
    event_ids: tuple[int, ...]
    kinds: tuple[str, ...]
    normal_declaration_count: int
    protected_declaration_count: int

    @property
    def declaration_count(self) -> int:
        return len(self.event_ids)


@dataclass(frozen=True)
class _HoldOccupancy:
    lane: int
    start_beat: Fraction
    end_beat: Fraction


@dataclass(frozen=True)
class _AttackBatch:
    beat: Fraction
    time_s: float
    attack_ids: tuple[int, ...]


@dataclass(frozen=True)
class SweepStrand:
    """One hand's ordered path inside a possibly variable-width family."""

    attack_ids: tuple[int, ...]
    lanes: tuple[int, ...]
    start_beat: Fraction
    end_beat: Fraction
    initial_direction: int
    final_direction: int
    turn_count: int


@dataclass(frozen=True)
class SweepSequence:
    """One maximal family candidate with one or two active strands per batch."""

    lanes_by_batch: tuple[tuple[int, ...], ...]
    attack_ids_by_batch: tuple[tuple[int, ...], ...]
    event_ids_by_batch: tuple[tuple[int, ...], ...]
    event_ids_by_attack_batch: tuple[tuple[tuple[int, ...], ...], ...]
    extra_event_ids_by_batch: tuple[tuple[int, ...], ...]
    beats: tuple[Fraction, ...]
    times_s: tuple[float, ...]
    unit_intervals_beats: tuple[Fraction, ...]
    unit_intervals_seconds: tuple[float, ...]
    speed_switch_indexes: tuple[int, ...]
    direction_switch_indexes: tuple[int, ...]
    width_switch_indexes: tuple[int, ...]
    double_handoff_indexes: tuple[int, ...]
    normal_declaration_counts: tuple[int, ...]
    protected_declaration_counts: tuple[int, ...]
    strands: tuple[SweepStrand, ...]
    paired_sweep: bool = False

    @property
    def lanes(self) -> tuple[int, ...]:
        return tuple(lane for batch in self.lanes_by_batch for lane in batch)

    @property
    def event_ids_by_attack(self) -> tuple[tuple[int, ...], ...]:
        return tuple(
            event_ids for batch in self.event_ids_by_attack_batch for event_ids in batch
        )

    @property
    def start_beat(self) -> Fraction:
        return self.beats[0]

    @property
    def end_beat(self) -> Fraction:
        return self.beats[-1]

    @property
    def start_time_s(self) -> float:
        return self.times_s[0]

    @property
    def end_time_s(self) -> float:
        return self.times_s[-1]

    @property
    def gaps_beats(self) -> tuple[Fraction, ...]:
        return tuple(right - left for left, right in zip(self.beats, self.beats[1:]))

    @property
    def period_beats(self) -> Fraction:
        return self.unit_intervals_beats[0]

    @property
    def turn_note_indexes(self) -> tuple[int, ...]:
        return self.direction_switch_indexes

    @property
    def attack_count(self) -> int:
        return sum(len(batch) for batch in self.attack_ids_by_batch)

    @property
    def declaration_count(self) -> int:
        return sum(self.normal_declaration_counts) + sum(self.protected_declaration_counts)

    @property
    def strength(self) -> float:
        return float(self.attack_count)

    @property
    def interval_seconds(self) -> float:
        ordered = sorted(self.unit_intervals_seconds)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / 2

    @property
    def widths(self) -> tuple[int, ...]:
        return tuple(len(batch) for batch in self.lanes_by_batch)


@dataclass(frozen=True)
class SweepScoringConfig:
    reference_interval_seconds: float = float(DEFAULT_REFERENCE_INTERVAL_SECONDS)
    speed_exponent: float = DEFAULT_SPEED_EXPONENT
    speed_relative_tolerance: float = DEFAULT_SPEED_RELATIVE_TOLERANCE
    protected_note_weight: float = DEFAULT_PROTECTED_WEIGHT
    chord_note_multiplier: float = DEFAULT_CHORD_NOTE_MULTIPLIER
    minimum_family_attack_count: int = DEFAULT_MINIMUM_FAMILY_ATTACK_COUNT
    mean_weight: float = DEFAULT_MEAN_WEIGHT
    peak_weight: float = DEFAULT_PEAK_WEIGHT
    duration_reference_seconds: float = DEFAULT_DURATION_REFERENCE_SECONDS
    duration_correction_exponent: float = DEFAULT_DURATION_CORRECTION_EXPONENT
    single_connection_increment: float = DEFAULT_SINGLE_CONNECTION_INCREMENT
    double_connection_increment: float = DEFAULT_DOUBLE_CONNECTION_INCREMENT
    width_change_increment: float = DEFAULT_WIDTH_CHANGE_INCREMENT
    speed_change_increment: float = DEFAULT_SPEED_CHANGE_INCREMENT
    same_direction_increment: float = DEFAULT_SAME_DIRECTION_INCREMENT
    reversal_increment: float = DEFAULT_REVERSAL_INCREMENT
    eighth_gap_family_bridge: bool = False

    def validate(self) -> None:
        if not isinstance(self.eighth_gap_family_bridge, bool):
            raise ValueError("eighth_gap_family_bridge must be a boolean")
        positive = {
            "reference_interval_seconds": self.reference_interval_seconds,
            "speed_exponent": self.speed_exponent,
            "chord_note_multiplier": self.chord_note_multiplier,
            "duration_reference_seconds": self.duration_reference_seconds,
        }
        non_negative = {
            "speed_relative_tolerance": self.speed_relative_tolerance,
            "protected_note_weight": self.protected_note_weight,
            "single_connection_increment": self.single_connection_increment,
            "double_connection_increment": self.double_connection_increment,
            "width_change_increment": self.width_change_increment,
            "speed_change_increment": self.speed_change_increment,
            "same_direction_increment": self.same_direction_increment,
            "reversal_increment": self.reversal_increment,
            "mean_weight": self.mean_weight,
            "peak_weight": self.peak_weight,
            "duration_correction_exponent": self.duration_correction_exponent,
        }
        for name, value in positive.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be a positive finite number")
        for name, value in non_negative.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative finite number")
        if self.speed_relative_tolerance >= 1:
            raise ValueError("speed_relative_tolerance must be less than one")
        if (
            isinstance(self.minimum_family_attack_count, bool)
            or not isinstance(self.minimum_family_attack_count, int)
            or self.minimum_family_attack_count <= 0
        ):
            raise ValueError("minimum_family_attack_count must be a positive integer")
        if not math.isclose(self.mean_weight + self.peak_weight, 1.0):
            raise ValueError("mean_weight and peak_weight must sum to one")


@dataclass(frozen=True)
class ScoredSweepGroup:
    group_id: int
    sequence: SweepSequence
    base_weight: float
    internal_weighted_load: float
    family_multiplier: float
    parent_group_id: int | None
    connection_increment: float
    contribution: float


@dataclass(frozen=True)
class ScoredSweepFamily:
    family_id: int
    group_ids: tuple[int, ...]
    start_time_s: float
    end_time_s: float
    duration_s: float
    attack_count: int
    load: float
    density: float
    strength: float
    eligible: bool
    rank: int | None
    discount: float
    contribution: float


@dataclass(frozen=True)
class SweepScore:
    value: float
    weighted_total: float
    undiscounted_score: float
    mean_load: float | None
    family_mean: float | None
    duration_factor: float | None
    peak_score: float
    duration_s: float | None
    groups: tuple[ScoredSweepGroup, ...]
    families: tuple[ScoredSweepFamily, ...]


@dataclass(frozen=True)
class _MainSpineState:
    batch_indexes: tuple[int, ...]
    entry_attack_ids: tuple[int, ...]
    exit_attack_ids: tuple[int, ...]
    direction: int | None = None
    run_steps: int = 0
    turn_count: int = 0
    unit_intervals_beats: tuple[Fraction, ...] = ()
    unit_intervals_seconds: tuple[float, ...] = ()
    speed_switches: tuple[bool, ...] = ()
    direction_switches: tuple[bool, ...] = ()


def _beat_duration(event: Event) -> Fraction:
    if event.start_beat is None or event.end_beat is None:
        raise ValueError("Hold event is missing its beat interval")
    return Fraction(event.end_beat) - Fraction(event.start_beat)


def button_attacks(events: tuple[Event, ...]) -> tuple[ButtonAttack, ...]:
    """Build Tap/short-Hold onsets and preserve declaration-level EX weights."""
    grouped: dict[tuple[Fraction, int], list[Event]] = {}
    for event in events:
        if event.kind not in {"tap", "hold"}:
            continue
        if event.kind == "hold" and _beat_duration(event) > SHORT_HOLD_MAX_BEATS:
            continue
        if event.start_beat is None:
            raise ValueError("Button event is missing its beat position")
        grouped.setdefault((Fraction(event.start_beat), int(event.position)), []).append(event)

    attacks = []
    for attack_id, ((beat, lane), declarations) in enumerate(sorted(grouped.items())):
        declarations.sort(key=lambda event: event.event_id)
        first_time = declarations[0].start_time_s
        if any(
            not math.isclose(event.start_time_s, first_time, rel_tol=0, abs_tol=1e-12)
            for event in declarations[1:]
        ):
            raise ValueError("Same-beat duplicate attacks disagree on chart time")
        attacks.append(ButtonAttack(
            attack_id=attack_id,
            lane=lane,
            beat=beat,
            time_s=first_time,
            event_ids=tuple(event.event_id for event in declarations),
            kinds=tuple(
                "slide_head" if event.is_slide_head else event.kind
                for event in declarations
            ),
            normal_declaration_count=sum(not event.is_ex for event in declarations),
            protected_declaration_count=sum(event.is_ex for event in declarations),
        ))
    return tuple(attacks)


def _long_holds(events: tuple[Event, ...]) -> tuple[_HoldOccupancy, ...]:
    output = []
    for event in events:
        if event.kind != "hold" or _beat_duration(event) <= SHORT_HOLD_MAX_BEATS:
            continue
        output.append(_HoldOccupancy(
            int(event.position), Fraction(event.start_beat), Fraction(event.end_beat)
        ))
    return tuple(output)


def _attack_batches(attacks: tuple[ButtonAttack, ...]) -> tuple[_AttackBatch, ...]:
    grouped: dict[Fraction, list[ButtonAttack]] = {}
    for attack in attacks:
        grouped.setdefault(attack.beat, []).append(attack)
    output = []
    for beat, members in sorted(grouped.items()):
        members.sort(key=lambda attack: (attack.lane, attack.attack_id))
        first_time = members[0].time_s
        if any(
            not math.isclose(attack.time_s, first_time, rel_tol=0, abs_tol=1e-12)
            for attack in members[1:]
        ):
            raise ValueError("Same-beat attacks disagree on chart time")
        output.append(_AttackBatch(
            beat, first_time, tuple(attack.attack_id for attack in members)
        ))
    return tuple(output)


def _ring(lane: int) -> int:
    return (lane - 1) % 8 + 1


def _move(
    left: ButtonAttack,
    right: ButtonAttack,
    holds: tuple[_HoldOccupancy, ...],
) -> tuple[int, int] | None:
    clockwise = (right.lane - left.lane) % 8
    counterclockwise = (left.lane - right.lane) % 8
    if clockwise == 1:
        return 1, 1
    if counterclockwise == 1:
        return -1, 1
    if clockwise == 2:
        direction = 1
    elif counterclockwise == 2:
        direction = -1
    else:
        return None
    skipped_lane = _ring(left.lane + direction)
    occupied = any(
        hold.lane == skipped_lane
        and hold.start_beat <= left.beat
        and hold.end_beat >= right.beat
        for hold in holds
    )
    return (direction, 2) if occupied else None


def _same_speed(left: float, right: float, relative_tolerance: float) -> bool:
    return math.isclose(
        left,
        right,
        rel_tol=relative_tolerance,
        abs_tol=1e-9,
    )


def _advance_main_spine(
    state: _MainSpineState,
    current_batch_index: int,
    current_entry_attack_id: int,
    current_exit_attack_id: int,
    attacks: tuple[ButtonAttack, ...],
    batches: tuple[_AttackBatch, ...],
    holds: tuple[_HoldOccupancy, ...],
    speed_relative_tolerance: float,
) -> _MainSpineState | None:
    previous = attacks[state.exit_attack_ids[-1]]
    current = attacks[current_entry_attack_id]
    move = _move(previous, current, holds)
    if move is None:
        return None
    direction, step_units = move
    previous_batch = batches[state.batch_indexes[-1]]
    current_batch = batches[current_batch_index]
    gap_beats = current_batch.beat - previous_batch.beat
    gap_seconds = current_batch.time_s - previous_batch.time_s
    if gap_beats <= 0 or gap_seconds <= 0:
        return None
    unit_beat = gap_beats / step_units
    unit_second = gap_seconds / step_units

    if not state.unit_intervals_seconds:
        if unit_beat > BASE_MAX_UNIT_BEATS:
            return None
        speed_switch = False
    else:
        previous_unit_second = state.unit_intervals_seconds[-1]
        speed_switch = not _same_speed(
            unit_second,
            previous_unit_second,
            speed_relative_tolerance,
        )
        ordinary_speed = unit_beat <= BASE_MAX_UNIT_BEATS
        continues_eighth = (
            unit_beat == EIGHTH_NOTE_BEATS
            and state.unit_intervals_beats[-1] == EIGHTH_NOTE_BEATS
            and not speed_switch
        )
        decelerates_to_eighth = (
            unit_beat == EIGHTH_NOTE_BEATS
            and speed_switch
            and unit_second > previous_unit_second
        )
        if not (ordinary_speed or continues_eighth or decelerates_to_eighth):
            return None

    direction_switched = False
    if state.direction is None:
        run_steps = 1
        turn_count = state.turn_count
    elif state.direction == direction:
        run_steps = state.run_steps + 1
        turn_count = state.turn_count
    else:
        if state.run_steps < 2:
            return None
        run_steps = 1
        turn_count = state.turn_count + 1
        direction_switched = True
    return _MainSpineState(
        batch_indexes=state.batch_indexes + (current_batch_index,),
        entry_attack_ids=state.entry_attack_ids + (current_entry_attack_id,),
        exit_attack_ids=state.exit_attack_ids + (current_exit_attack_id,),
        direction=direction,
        run_steps=run_steps,
        turn_count=turn_count,
        unit_intervals_beats=state.unit_intervals_beats + (unit_beat,),
        unit_intervals_seconds=state.unit_intervals_seconds + (unit_second,),
        speed_switches=state.speed_switches + (speed_switch,),
        direction_switches=state.direction_switches + (direction_switched,),
    )


def _main_spine_complete(
    state: _MainSpineState,
    attacks: tuple[ButtonAttack, ...],
) -> bool:
    return (
        len(state.entry_attack_ids) >= 3
        and state.direction is not None
        and (state.turn_count == 0 or state.run_steps >= 2)
        and len({
            attacks[attack_id].lane
            for attack_id in state.entry_attack_ids + state.exit_attack_ids
        }) >= 3
    )


def _main_state_to_sequence(
    state: _MainSpineState,
    attacks: tuple[ButtonAttack, ...],
    batches: tuple[_AttackBatch, ...],
) -> SweepSequence:
    included_by_batch = tuple(
        batches[index].attack_ids for index in state.batch_indexes
    )
    extras_by_batch = tuple(
        tuple(
            attack_id for attack_id in included
            if attack_id not in {entry_attack_id, exit_attack_id}
        )
        for included, entry_attack_id, exit_attack_id in zip(
            included_by_batch,
            state.entry_attack_ids,
            state.exit_attack_ids,
        )
    )
    event_ids_by_batch = tuple(
        tuple(event_id for attack_id in included for event_id in attacks[attack_id].event_ids)
        for included in included_by_batch
    )
    main_attacks = tuple(
        attacks[attack_id] for attack_id in state.entry_attack_ids
    )
    directions = tuple(
        1 if (right.lane - left.lane) % 8 in {1, 2} else -1
        for left, right in zip(
            (attacks[attack_id] for attack_id in state.exit_attack_ids[:-1]),
            (attacks[attack_id] for attack_id in state.entry_attack_ids[1:]),
        )
    )
    widths = tuple(len(included) for included in included_by_batch)
    return SweepSequence(
        lanes_by_batch=tuple(
            tuple(attacks[attack_id].lane for attack_id in included)
            for included in included_by_batch
        ),
        attack_ids_by_batch=included_by_batch,
        event_ids_by_batch=event_ids_by_batch,
        event_ids_by_attack_batch=tuple(
            tuple(attacks[attack_id].event_ids for attack_id in included)
            for included in included_by_batch
        ),
        extra_event_ids_by_batch=tuple(
            tuple(event_id for attack_id in extras for event_id in attacks[attack_id].event_ids)
            for extras in extras_by_batch
        ),
        beats=tuple(batches[index].beat for index in state.batch_indexes),
        times_s=tuple(batches[index].time_s for index in state.batch_indexes),
        unit_intervals_beats=state.unit_intervals_beats,
        unit_intervals_seconds=state.unit_intervals_seconds,
        speed_switch_indexes=tuple(
            index + 1 for index, changed in enumerate(state.speed_switches) if changed
        ),
        direction_switch_indexes=tuple(
            index for index, changed in enumerate(state.direction_switches) if changed
        ),
        width_switch_indexes=tuple(
            index for index, (left, right) in enumerate(zip(widths, widths[1:]), start=1)
            if left != right
        ),
        double_handoff_indexes=tuple(
            index for index, (entry_attack_id, exit_attack_id) in enumerate(zip(
                state.entry_attack_ids,
                state.exit_attack_ids,
            ))
            if entry_attack_id != exit_attack_id
        ),
        normal_declaration_counts=tuple(
            sum(attacks[attack_id].normal_declaration_count for attack_id in included)
            for included in included_by_batch
        ),
        protected_declaration_counts=tuple(
            sum(attacks[attack_id].protected_declaration_count for attack_id in included)
            for included in included_by_batch
        ),
        strands=(SweepStrand(
            attack_ids=state.entry_attack_ids,
            lanes=tuple(attack.lane for attack in main_attacks),
            start_beat=main_attacks[0].beat,
            end_beat=main_attacks[-1].beat,
            initial_direction=directions[0],
            final_direction=directions[-1],
            turn_count=state.turn_count,
        ),),
    )


def _candidate_sequences(
    attacks: tuple[ButtonAttack, ...],
    batches: tuple[_AttackBatch, ...],
    holds: tuple[_HoldOccupancy, ...],
    *,
    max_states: int,
    speed_relative_tolerance: float,
) -> list[SweepSequence]:
    candidates = []
    active: dict[tuple, _MainSpineState] = {}
    visited = 0
    for batch_index, batch in enumerate(batches):
        if len(batch.attack_ids) > 3:
            active = {}
            continue
        next_states = [
            _MainSpineState((batch_index,), (attack_id,), (attack_id,))
            for attack_id in batch.attack_ids
        ]
        for state in active.values():
            for entry_attack_id in batch.attack_ids:
                for exit_attack_id in batch.attack_ids:
                    advanced = _advance_main_spine(
                        state,
                        batch_index,
                        entry_attack_id,
                        exit_attack_id,
                        attacks,
                        batches,
                        holds,
                        speed_relative_tolerance,
                    )
                    if advanced is not None:
                        next_states.append(advanced)
        deduplicated: dict[tuple, _MainSpineState] = {}
        for state in next_states:
            visited += 1
            if visited > max_states:
                raise ValueError(
                    "Sweep main-spine candidate limit exceeded; no partial result returned"
                )
            last_interval = (
                round(state.unit_intervals_seconds[-1], 9)
                if state.unit_intervals_seconds else None
            )
            key = (
                state.exit_attack_ids[-1],
                state.direction,
                min(state.run_steps, 2),
                last_interval,
                state.unit_intervals_beats[-1] if state.unit_intervals_beats else None,
            )
            previous = deduplicated.get(key)
            quality = (
                len(state.entry_attack_ids),
                -sum(state.speed_switches),
                -state.turn_count,
                -sum(
                    entry_attack_id != exit_attack_id
                    for entry_attack_id, exit_attack_id in zip(
                        state.entry_attack_ids,
                        state.exit_attack_ids,
                    )
                ),
                tuple(-attack_id for attack_id in state.entry_attack_ids),
                tuple(-attack_id for attack_id in state.exit_attack_ids),
            )
            if previous is None:
                deduplicated[key] = state
                continue
            previous_quality = (
                len(previous.entry_attack_ids),
                -sum(previous.speed_switches),
                -previous.turn_count,
                -sum(
                    entry_attack_id != exit_attack_id
                    for entry_attack_id, exit_attack_id in zip(
                        previous.entry_attack_ids,
                        previous.exit_attack_ids,
                    )
                ),
                tuple(-attack_id for attack_id in previous.entry_attack_ids),
                tuple(-attack_id for attack_id in previous.exit_attack_ids),
            )
            if quality > previous_quality:
                deduplicated[key] = state
        active = deduplicated
        candidates.extend(
            _main_state_to_sequence(state, attacks, batches)
            for state in active.values()
            if _main_spine_complete(state, attacks)
        )

    unique: dict[frozenset[int], SweepSequence] = {}
    for candidate in candidates:
        event_ids = frozenset(
            event_id for batch in candidate.event_ids_by_batch for event_id in batch
        )
        previous = unique.get(event_ids)
        quality = (
            sum(len(batch) for batch in candidate.lanes_by_batch),
            -len(candidate.speed_switch_indexes),
            max(len(strand.attack_ids) for strand in candidate.strands),
            -len(candidate.direction_switch_indexes),
            -len(candidate.width_switch_indexes),
        )
        if previous is None:
            unique[event_ids] = candidate
            continue
        previous_quality = (
            sum(len(batch) for batch in previous.lanes_by_batch),
            -len(previous.speed_switch_indexes),
            max(len(strand.attack_ids) for strand in previous.strands),
            -len(previous.direction_switch_indexes),
            -len(previous.width_switch_indexes),
        )
        if quality > previous_quality:
            unique[event_ids] = candidate

    items = list(unique.items())
    contained = set()
    for index, (event_ids, _) in enumerate(items):
        if any(
            event_ids < other_ids
            for other_index, (other_ids, _) in enumerate(items)
            if other_index != index
        ):
            contained.add(index)
    return [candidate for index, (_, candidate) in enumerate(items) if index not in contained]


def _paired_sweep_sequence(
    attacks: tuple[ButtonAttack, ...],
    batches: tuple[_AttackBatch, ...],
    start: int,
    pair_count: int,
) -> SweepSequence:
    members = tuple(attacks[batches[index].attack_ids[0]] for index in range(
        start, start + 2 * pair_count
    ))
    pairs = tuple((members[index], members[index + 1]) for index in range(
        0, len(members), 2
    ))
    strands = []
    for phase in range(2):
        strand_attacks = tuple(
            attack for pair in pairs[phase::2] for attack in pair
        )
        if not strand_attacks:
            continue
        directions = tuple(
            1 if (right.lane - left.lane) % 8 == 1 else -1
            for left, right in pairs[phase::2]
        )
        strands.append(SweepStrand(
            attack_ids=tuple(attack.attack_id for attack in strand_attacks),
            lanes=tuple(attack.lane for attack in strand_attacks),
            start_beat=strand_attacks[0].beat,
            end_beat=strand_attacks[-1].beat,
            initial_direction=directions[0],
            final_direction=directions[-1],
            turn_count=sum(left != right for left, right in zip(
                directions, directions[1:]
            )),
        ))
    return SweepSequence(
        lanes_by_batch=tuple((attack.lane,) for attack in members),
        attack_ids_by_batch=tuple((attack.attack_id,) for attack in members),
        event_ids_by_batch=tuple(attack.event_ids for attack in members),
        event_ids_by_attack_batch=tuple((attack.event_ids,) for attack in members),
        extra_event_ids_by_batch=((),) * len(members),
        beats=tuple(attack.beat for attack in members),
        times_s=tuple(attack.time_s for attack in members),
        unit_intervals_beats=tuple(
            right.beat - left.beat for left, right in zip(members, members[1:])
        ),
        unit_intervals_seconds=tuple(
            right.time_s - left.time_s for left, right in zip(members, members[1:])
        ),
        speed_switch_indexes=(),
        direction_switch_indexes=(),
        width_switch_indexes=(),
        double_handoff_indexes=(),
        normal_declaration_counts=tuple(
            attack.normal_declaration_count for attack in members
        ),
        protected_declaration_counts=tuple(
            attack.protected_declaration_count for attack in members
        ),
        strands=tuple(strands),
        paired_sweep=True,
    )


def _paired_sweep_candidates(
    attacks: tuple[ButtonAttack, ...],
    batches: tuple[_AttackBatch, ...],
    covered_attack_ids: set[int],
    speed_relative_tolerance: float,
) -> tuple[SweepSequence, ...]:
    """Find repeated, separated adjacent-key pairs without accepting trills."""
    candidates = []
    for start in range(len(batches) - 7):
        signatures = []
        unit_beat = None
        unit_second = None
        previous = None
        for pair_index in range((len(batches) - start) // 2):
            left_batch = batches[start + 2 * pair_index]
            right_batch = batches[start + 2 * pair_index + 1]
            if len(left_batch.attack_ids) != 1 or len(right_batch.attack_ids) != 1:
                break
            left = attacks[left_batch.attack_ids[0]]
            right = attacks[right_batch.attack_ids[0]]
            if left.attack_id in covered_attack_ids or right.attack_id in covered_attack_ids:
                break
            if circular_key_distance(left.lane, right.lane) != 1:
                break
            beat_gap = right.beat - left.beat
            second_gap = right.time_s - left.time_s
            if beat_gap <= 0 or beat_gap > BASE_MAX_UNIT_BEATS or second_gap <= 0:
                break
            if unit_beat is None:
                unit_beat, unit_second = beat_gap, second_gap
            elif (
                beat_gap != unit_beat
                or not _same_speed(
                    second_gap, unit_second, speed_relative_tolerance
                )
            ):
                break
            if previous is not None:
                boundary_beat_gap = left.beat - previous.beat
                boundary_second_gap = left.time_s - previous.time_s
                if (
                    boundary_beat_gap != unit_beat
                    or not _same_speed(
                        boundary_second_gap, unit_second, speed_relative_tolerance
                    )
                    or circular_key_distance(previous.lane, left.lane) <= 1
                ):
                    break
            signatures.append((left.lane, right.lane))
            previous = right
        best_pairs = 0
        if len(signatures) >= 4:
            directions = tuple(
                1 if (right - left) % 8 == 1 else -1
                for left, right in signatures
            )
            if directions[0] == -directions[1]:
                length = 2
                while (
                    length < len(signatures)
                    and directions[length] == directions[length - 2]
                    and circular_key_distance(
                        signatures[length][0], signatures[length - 2][0]
                    ) <= 1
                ):
                    length += 1
                if length >= 4:
                    best_pairs = length
        for period in (2, 4):
            minimum = 4 if period == 2 else 6
            if len(signatures) < minimum:
                continue
            length = period
            while (
                length < len(signatures)
                and signatures[length] == signatures[length - period]
            ):
                length += 1
            if length >= minimum:
                best_pairs = max(best_pairs, length)
        if best_pairs:
            candidates.append(_paired_sweep_sequence(
                attacks, batches, start, best_pairs
            ))
    ordered = sorted(
        candidates,
        key=lambda item: (item.end_beat, item.start_beat, -item.attack_count),
    )
    ends = [item.end_beat for item in ordered]
    # Candidates occupy contiguous, single-attack batches. Weighted interval
    # scheduling maximizes covered attacks without exponential conflict search.
    best: list[tuple[int, int, tuple[int, ...]]] = [(0, 0, ())]
    for index, candidate in enumerate(ordered):
        previous_count = bisect_left(ends, candidate.start_beat, 0, index)
        previous = best[previous_count]
        included = (
            previous[0] + candidate.attack_count,
            previous[1] - 1,
            previous[2] + (index,),
        )
        excluded = best[-1]
        include_quality = (included[0], included[1], tuple(
            -item for item in included[2]
        ))
        exclude_quality = (excluded[0], excluded[1], tuple(
            -item for item in excluded[2]
        ))
        best.append(included if include_quality > exclude_quality else excluded)
    selected = [ordered[index] for index in best[-1][2]]
    return tuple(sorted(
        selected,
        key=lambda item: (item.start_beat, item.end_beat, item.lanes_by_batch),
    ))


def _select_disjoint_sequences(
    sequences: list[SweepSequence],
    *,
    max_states: int,
) -> tuple[SweepSequence, ...]:
    event_sets = [
        frozenset(event_id for batch in sequence.event_ids_by_batch for event_id in batch)
        for sequence in sequences
    ]
    conflicts = [1 << index for index in range(len(sequences))]
    for left in range(len(sequences)):
        for right in range(left + 1, len(sequences)):
            if event_sets[left] & event_sets[right]:
                conflicts[left] |= 1 << right
                conflicts[right] |= 1 << left
    states = 0

    @lru_cache(None)
    def solve(mask: int) -> tuple[int, ...]:
        nonlocal states
        states += 1
        if states > max_states:
            raise ValueError(
                "Sweep family selection limit exceeded; no partial result returned"
            )
        if not mask:
            return ()
        bit = mask & -mask
        pivot = bit.bit_length() - 1
        included = (pivot,) + solve(mask & ~conflicts[pivot])
        excluded = solve(mask ^ bit)

        def score(chosen):
            return (
                sum(len(event_sets[index]) for index in chosen),
                -len(chosen),
                sum(len(sequences[index].beats) for index in chosen),
            )

        if score(included) == score(excluded):
            return min(tuple(sorted(included)), tuple(sorted(excluded)))
        return max((included, excluded), key=score)

    chosen = solve((1 << len(sequences)) - 1)
    return tuple(sorted(
        (sequences[index] for index in chosen),
        key=lambda sequence: (sequence.start_beat, sequence.end_beat, sequence.lanes_by_batch),
    ))


def _validate_recognition_parameters(
    max_states: int,
    speed_relative_tolerance: float,
) -> None:
    if isinstance(max_states, bool) or not isinstance(max_states, int) or max_states <= 0:
        raise ValueError("max_states must be a positive integer")
    if (
        isinstance(speed_relative_tolerance, bool)
        or not isinstance(speed_relative_tolerance, (int, float))
        or not math.isfinite(speed_relative_tolerance)
        or not 0 <= speed_relative_tolerance < 1
    ):
        raise ValueError("speed_relative_tolerance must be finite and in [0, 1)")


def sweep_sequences(
    events: tuple[Event, ...],
    *,
    max_states: int = DEFAULT_MAX_STATES,
    speed_relative_tolerance: float = DEFAULT_SPEED_RELATIVE_TOLERANCE,
    include_paired_sweeps: bool = False,
) -> tuple[SweepSequence, ...]:
    """Recognize maximal variable-width sweep families from canonical events."""
    _validate_recognition_parameters(max_states, speed_relative_tolerance)
    if not isinstance(include_paired_sweeps, bool):
        raise ValueError("include_paired_sweeps must be a boolean")
    attacks = button_attacks(events)
    batches = _attack_batches(attacks)
    candidates = _candidate_sequences(
        attacks,
        batches,
        _long_holds(events),
        max_states=max_states,
        speed_relative_tolerance=speed_relative_tolerance,
    )
    selected = _select_disjoint_sequences(candidates, max_states=max_states)
    if not include_paired_sweeps:
        return selected
    covered = {
        attack_id for sequence in selected
        for batch in sequence.attack_ids_by_batch for attack_id in batch
    }
    paired = _paired_sweep_candidates(
        attacks, batches, covered, speed_relative_tolerance
    )
    return tuple(sorted(
        (*selected, *paired),
        key=lambda sequence: (
            sequence.start_beat, sequence.end_beat, sequence.lanes_by_batch
        ),
    ))


def circular_key_distance(left: int, right: int) -> int:
    distance = abs(left - right)
    return min(distance, 8 - distance)


def _internal_load(
    sequence: SweepSequence,
    config: SweepScoringConfig,
) -> tuple[float, float]:
    batch_count = len(sequence.beats)
    speed_by_batch = (sequence.unit_intervals_seconds[0], *sequence.unit_intervals_seconds)
    increments = [0.0] * batch_count
    for index in sequence.speed_switch_indexes:
        increments[index] += config.speed_change_increment
    for index in sequence.width_switch_indexes:
        increments[index] += (
            config.double_connection_increment
            if index in sequence.double_handoff_indexes
            else config.width_change_increment
        )
    # A reversal pivot belongs to the following directional section.
    for pivot_index in sequence.direction_switch_indexes:
        increments[pivot_index] += config.reversal_increment

    multiplier = 1.0
    base_weight = 0.0
    weighted_load = 0.0
    for index in range(batch_count):
        multiplier += increments[index]
        note_weight = (
            sequence.normal_declaration_counts[index]
            + sequence.protected_declaration_counts[index] * config.protected_note_weight
        )
        if sequence.widths[index] >= 2:
            note_weight *= config.chord_note_multiplier
        speed_factor = (
            config.reference_interval_seconds / speed_by_batch[index]
        ) ** config.speed_exponent
        base = note_weight * speed_factor
        base_weight += base
        weighted_load += base * multiplier
    return base_weight, weighted_load


def _external_connection(
    parent: SweepSequence,
    child: SweepSequence,
    config: SweepScoringConfig,
) -> float | None:
    gap = child.start_time_s - parent.end_time_s
    repeated_eighth_bridge = (
        config.eighth_gap_family_bridge
        and child.start_beat - parent.end_beat == EIGHTH_NOTE_BEATS
        and len(parent.beats) >= 4
        and len(child.beats) >= 4
        and sum(width >= 2 for width in parent.widths) >= 3
        and sum(width >= 2 for width in child.widths) >= 3
        and parent.lanes_by_batch == child.lanes_by_batch
        and _same_speed(
            parent.interval_seconds,
            child.interval_seconds,
            config.speed_relative_tolerance,
        )
    )
    if gap < -1e-9 or (
        gap > parent.interval_seconds + 1e-9
        and not repeated_eighth_bridge
    ):
        return None
    double_connection = abs(gap) <= 1e-9
    speed_changed = not _same_speed(
        parent.interval_seconds,
        child.interval_seconds,
        config.speed_relative_tolerance,
    )
    parent_directions = {strand.final_direction for strand in parent.strands}
    child_directions = {strand.initial_direction for strand in child.strands}
    near_starts = any(
        circular_key_distance(left, right) <= 1
        for left in parent.lanes_by_batch[0]
        for right in child.lanes_by_batch[0]
    )
    same_direction = bool(parent_directions & child_directions) or repeated_eighth_bridge
    fold = any(
        left == -right for left in parent_directions for right in child_directions
    ) and any(
        circular_key_distance(left, right) <= 1
        for left in parent.lanes_by_batch[-1]
        for right in child.lanes_by_batch[0]
    )
    same_direction_bonus = same_direction and near_starts
    if double_connection:
        increment = config.double_connection_increment
    elif same_direction_bonus or fold:
        increment = config.single_connection_increment
    else:
        # Keep the chronological family edge, but a directionless single
        # handoff no longer raises every later group's multiplier.
        increment = 0.0
    if speed_changed:
        increment += config.speed_change_increment
    if same_direction_bonus:
        increment += config.same_direction_increment
    elif fold:
        increment += config.reversal_increment
    return increment


def score_sweep_sequences(
    sequences: tuple[SweepSequence, ...],
    *,
    config: SweepScoringConfig | None = None,
    duration_s: float | None = None,
) -> SweepScore:
    """Filter undersized families, then rank five square-root-duration strengths."""
    config = config or SweepScoringConfig()
    config.validate()
    if duration_s is not None and (
        isinstance(duration_s, bool)
        or not isinstance(duration_s, (int, float))
        or not math.isfinite(duration_s)
        or duration_s <= 0
    ):
        raise ValueError("duration_s must be a positive finite number")

    ordered = tuple(sorted(
        sequences,
        key=lambda sequence: (
            sequence.start_time_s, sequence.end_time_s, sequence.lanes_by_batch
        ),
    ))
    base_and_internal = [_internal_load(sequence, config) for sequence in ordered]
    family_multipliers = [1.0] * len(ordered)
    parents: list[int | None] = [None] * len(ordered)
    connection_increments = [0.0] * len(ordered)
    for child_index, child in enumerate(ordered):
        choices = []
        for parent_index in range(child_index):
            increment = _external_connection(ordered[parent_index], child, config)
            if increment is None:
                continue
            choices.append((
                family_multipliers[parent_index] + increment,
                -abs(child.start_time_s - ordered[parent_index].end_time_s),
                -parent_index,
                parent_index,
                increment,
            ))
        if choices:
            multiplier, _, _, parent_index, increment = max(choices)
            family_multipliers[child_index] = multiplier
            parents[child_index] = parent_index
            connection_increments[child_index] = increment

    groups = []
    for index, sequence in enumerate(ordered):
        base, internal = base_and_internal[index]
        contribution = internal * family_multipliers[index]
        groups.append(ScoredSweepGroup(
            group_id=index + 1,
            sequence=sequence,
            base_weight=base,
            internal_weighted_load=internal,
            family_multiplier=family_multipliers[index],
            parent_group_id=None if parents[index] is None else parents[index] + 1,
            connection_increment=connection_increments[index],
            contribution=contribution,
        ))
    members_by_root: dict[int, list[ScoredSweepGroup]] = {}
    for index, group in enumerate(groups):
        root = index
        while parents[root] is not None:
            root = parents[root]
        members_by_root.setdefault(root, []).append(group)
    family_rows = []
    for family_id, root in enumerate(sorted(members_by_root), start=1):
        members = members_by_root[root]
        start_time_s = min(group.sequence.start_time_s for group in members)
        end_time_s = max(group.sequence.end_time_s for group in members)
        family_duration_s = end_time_s - start_time_s
        if family_duration_s <= 0:
            raise ValueError("Sweep family must have positive duration")
        load = math.fsum(group.contribution for group in members)
        attack_count = sum(group.sequence.attack_count for group in members)
        density = load / family_duration_s
        strength = density
        family_rows.append((
            family_id,
            tuple(group.group_id for group in members),
            start_time_s,
            end_time_s,
            family_duration_s,
            attack_count,
            load,
            density,
            strength,
        ))
    eligible_rows = [
        item for item in family_rows
        if item[5] >= config.minimum_family_attack_count
    ]
    ranked_families = sorted(
        eligible_rows,
        key=lambda item: (-item[8], item[0]),
    )
    rank_by_id = {family_id: rank for rank, (family_id, *_) in enumerate(
        ranked_families, start=1
    )}
    families = []
    for (
        family_id,
        group_ids,
        start_time_s,
        end_time_s,
        family_duration_s,
        attack_count,
        load,
        density,
        strength,
    ) in family_rows:
        rank = rank_by_id.get(family_id)
        eligible = rank is not None
        discount = rank ** -0.5 if rank is not None and rank <= FAMILY_LIMIT else 0.0
        families.append(ScoredSweepFamily(
            family_id=family_id,
            group_ids=group_ids,
            start_time_s=start_time_s,
            end_time_s=end_time_s,
            duration_s=family_duration_s,
            attack_count=attack_count,
            load=load,
            density=density,
            strength=strength,
            eligible=eligible,
            rank=rank,
            discount=discount,
            contribution=strength * discount,
        ))
    weighted_total = math.fsum(family.contribution for family in families)
    undiscounted = math.fsum(
        family.strength for family in families if family.eligible
    )
    family_mean = (
        math.fsum(family.density for family in families) / len(families)
        if families else 0.0
    )
    duration_factor = (
        (duration_s / config.duration_reference_seconds)
        ** config.duration_correction_exponent
        if duration_s is not None else None
    )
    mean_load = (
        family_mean * duration_factor if duration_factor is not None else None
    )
    value = (
        config.mean_weight * mean_load + config.peak_weight * weighted_total
        if mean_load is not None else weighted_total
    )
    return SweepScore(
        value=value,
        weighted_total=weighted_total,
        undiscounted_score=undiscounted,
        mean_load=mean_load,
        family_mean=family_mean if duration_s is not None else None,
        duration_factor=duration_factor,
        peak_score=weighted_total,
        duration_s=duration_s,
        groups=tuple(groups),
        families=tuple(families),
    )


def sweep_score(
    events: tuple[Event, ...],
    *,
    max_states: int = DEFAULT_MAX_STATES,
    config: SweepScoringConfig | None = None,
    duration_s: float | None = None,
) -> float:
    resolved_config = config or SweepScoringConfig()
    resolved_config.validate()
    return score_sweep_sequences(
        sweep_sequences(
            events,
            max_states=max_states,
            speed_relative_tolerance=resolved_config.speed_relative_tolerance,
        ),
        config=resolved_config,
        duration_s=duration_s,
    ).value


@dataclass(frozen=True)
class SweepAnalyzer:
    """Combine the five strongest eligible sweep-family densities."""

    max_states: int = DEFAULT_MAX_STATES
    reference_interval_seconds: float = float(DEFAULT_REFERENCE_INTERVAL_SECONDS)
    speed_exponent: float = DEFAULT_SPEED_EXPONENT
    speed_relative_tolerance: float = DEFAULT_SPEED_RELATIVE_TOLERANCE
    protected_note_weight: float = DEFAULT_PROTECTED_WEIGHT
    chord_note_multiplier: float = DEFAULT_CHORD_NOTE_MULTIPLIER
    minimum_family_attack_count: int = DEFAULT_MINIMUM_FAMILY_ATTACK_COUNT
    mean_weight: float = DEFAULT_MEAN_WEIGHT
    peak_weight: float = DEFAULT_PEAK_WEIGHT
    duration_reference_seconds: float = DEFAULT_DURATION_REFERENCE_SECONDS
    duration_correction_exponent: float = DEFAULT_DURATION_CORRECTION_EXPONENT

    def __post_init__(self) -> None:
        _validate_recognition_parameters(
            self.max_states,
            self.speed_relative_tolerance,
        )
        self.scoring_config.validate()

    @property
    def scoring_config(self) -> SweepScoringConfig:
        return SweepScoringConfig(
            reference_interval_seconds=self.reference_interval_seconds,
            speed_exponent=self.speed_exponent,
            speed_relative_tolerance=self.speed_relative_tolerance,
            protected_note_weight=self.protected_note_weight,
            chord_note_multiplier=self.chord_note_multiplier,
            minimum_family_attack_count=self.minimum_family_attack_count,
            mean_weight=self.mean_weight,
            peak_weight=self.peak_weight,
            duration_reference_seconds=self.duration_reference_seconds,
            duration_correction_exponent=self.duration_correction_exponent,
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
