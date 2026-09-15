"""Same-button Tap/Hold sequences with short off-button interruptions."""

from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
import math

from mairadar.analysis.model import AnalysisContext, FeatureResult
from mairadar.model import Event


EIGHTH_NOTE_BEATS = Fraction(1, 2)
DEFAULT_TOP_K = 5
DEFAULT_MAX_INTERRUPTING_TAPS = 4
INTERRUPTING_TAP_WEIGHT = 1.5
DEFAULT_SPEED_REFERENCE_SIXTEENTH_BPM = 180.0
DEFAULT_SPEED_EXPONENT = 1.5
TOP_ONE_RANK_MULTIPLIER = 1.3
TOP_RANK_DECAY = 0.645


@dataclass(frozen=True)
class JackSequence:
    """One maximal same-button sequence under the configured interruption cap."""

    position: str
    start_beat: Fraction
    end_beat: Fraction
    anchor_count: int
    interrupting_tap_count: int
    interrupting_span_beats: Fraction
    equivalent_sixteenth_bpm: float
    speed_factor: float

    @property
    def strength(self) -> float:
        return self.anchor_count + INTERRUPTING_TAP_WEIGHT * self.interrupting_tap_count

    @property
    def weighted_strength(self) -> float:
        return self.strength * self.speed_factor


def _button_batches(
    events: tuple[Event, ...],
) -> list[tuple[Fraction, float, tuple[Event, ...]]]:
    by_beat = defaultdict(list)
    for event in events:
        if event.kind not in {"tap", "hold"}:
            continue
        if event.start_beat is None:
            raise ValueError("Button event is missing its beat position")
        by_beat[Fraction(event.start_beat)].append(event)
    return [
        (
            beat,
            min(event.start_time_s for event in by_beat[beat]),
            tuple(sorted(by_beat[beat], key=lambda event: event.event_id)),
        )
        for beat in sorted(by_beat)
    ]


def _position_sequences(
    batches: list[tuple[Fraction, float, tuple[Event, ...]]],
    position: str,
    max_interrupting_taps: int,
    speed_reference_sixteenth_bpm: float,
    speed_exponent: float,
) -> list[JackSequence]:
    output = []
    start_beat = None
    end_beat = None
    start_time_s = None
    end_time_s = None
    previous_beat = None
    anchor_count = 0
    anchor_time_count = 0
    anchors_since_interruption = 0
    pending_interruptions = 0
    pending_start_beat = None
    committed_interruptions = 0
    committed_interruption_span = Fraction(0)

    def finish() -> None:
        nonlocal start_beat, end_beat, start_time_s, end_time_s, previous_beat
        nonlocal anchor_count, anchor_time_count, anchors_since_interruption
        nonlocal pending_interruptions, pending_start_beat, committed_interruptions
        nonlocal committed_interruption_span
        # Simultaneous duplicate declarations remain counted, but do not by
        # themselves constitute repeated presses on one button.
        if anchor_time_count >= 2:
            span_s = end_time_s - start_time_s
            if span_s <= 0:
                raise ValueError("Jack sequence must advance in chart time")
            equivalent_sixteenth_bpm = 15 * (anchor_time_count - 1) / span_s
            speed_factor = (
                equivalent_sixteenth_bpm / speed_reference_sixteenth_bpm
            ) ** speed_exponent
            output.append(JackSequence(
                position=position,
                start_beat=start_beat,
                end_beat=end_beat,
                anchor_count=anchor_count,
                interrupting_tap_count=committed_interruptions,
                interrupting_span_beats=committed_interruption_span,
                equivalent_sixteenth_bpm=equivalent_sixteenth_bpm,
                speed_factor=speed_factor,
            ))
        start_beat = end_beat = previous_beat = None
        start_time_s = end_time_s = None
        anchor_count = anchor_time_count = anchors_since_interruption = 0
        pending_interruptions = committed_interruptions = 0
        pending_start_beat = None
        committed_interruption_span = Fraction(0)

    for beat, time_s, events in batches:
        anchors = [event for event in events if event.position == position]
        if previous_beat is not None and beat - previous_beat > EIGHTH_NOTE_BEATS:
            finish()

        if anchors:
            returned_from_interruption = pending_start_beat is not None
            if returned_from_interruption:
                interruption_span = beat - pending_start_beat
                if interruption_span > EIGHTH_NOTE_BEATS:
                    finish()
                    returned_from_interruption = False
                else:
                    committed_interruptions += pending_interruptions
                    committed_interruption_span += interruption_span
            if start_beat is None:
                start_beat = beat
                start_time_s = time_s
            end_beat = beat
            end_time_s = time_s
            anchor_count += len(anchors)
            anchor_time_count += 1
            anchors_since_interruption = (
                1 if returned_from_interruption
                else anchors_since_interruption + 1
            )
            pending_interruptions = 0
            pending_start_beat = None
            previous_beat = beat
            continue

        if start_beat is None:
            continue
        # Only ordinary off-button Taps are eligible interruptions. An
        # off-button Hold is a new sustained action and ends this candidate.
        if any(event.kind != "tap" for event in events):
            finish()
            continue
        # A flight is only evidence inside an already-established jack. After
        # returning, two main-button onsets are required before another one.
        if pending_start_beat is None and anchors_since_interruption < 2:
            finish()
            continue
        if pending_interruptions + len(events) > max_interrupting_taps:
            finish()
            continue
        if pending_start_beat is None:
            pending_start_beat = beat
        pending_interruptions += len(events)
        previous_beat = beat

    finish()
    return output


def jack_sequences(
    events: tuple[Event, ...],
    *,
    max_interrupting_taps: int = DEFAULT_MAX_INTERRUPTING_TAPS,
    speed_reference_sixteenth_bpm: float = DEFAULT_SPEED_REFERENCE_SIXTEENTH_BPM,
    speed_exponent: float = DEFAULT_SPEED_EXPONENT,
) -> tuple[JackSequence, ...]:
    """Return deterministic maximal candidates for all eight outer buttons."""
    if (
        isinstance(max_interrupting_taps, bool)
        or not isinstance(max_interrupting_taps, int)
        or max_interrupting_taps < 0
    ):
        raise ValueError("max_interrupting_taps must be a non-negative integer")
    _validate_speed_parameters(speed_reference_sixteenth_bpm, speed_exponent)
    batches = _button_batches(events)
    sequences = [
        sequence
        for position in map(str, range(1, 9))
        for sequence in _position_sequences(
            batches,
            position,
            max_interrupting_taps,
            speed_reference_sixteenth_bpm,
            speed_exponent,
        )
    ]
    return tuple(sorted(
        sequences,
        key=lambda sequence: (
            -sequence.weighted_strength,
            -sequence.strength,
            -sequence.anchor_count,
            sequence.start_beat,
            int(sequence.position),
        ),
    ))


def jack_score(
    events: tuple[Event, ...],
    *,
    top_k: int = DEFAULT_TOP_K,
    max_interrupting_taps: int = DEFAULT_MAX_INTERRUPTING_TAPS,
    speed_reference_sixteenth_bpm: float = DEFAULT_SPEED_REFERENCE_SIXTEENTH_BPM,
    speed_exponent: float = DEFAULT_SPEED_EXPONENT,
) -> float:
    """Sum the strongest K sequences with geometric rank decay."""
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
        raise ValueError("top_k must be a positive integer")
    sequences = jack_sequences(
        events,
        max_interrupting_taps=max_interrupting_taps,
        speed_reference_sixteenth_bpm=speed_reference_sixteenth_bpm,
        speed_exponent=speed_exponent,
    )
    return math.fsum(
        sequence.weighted_strength
        * TOP_ONE_RANK_MULTIPLIER
        * TOP_RANK_DECAY ** (rank - 1)
        for rank, sequence in enumerate(sequences[:top_k], start=1)
    )


def _validate_speed_parameters(
    speed_reference_sixteenth_bpm: float,
    speed_exponent: float,
) -> None:
    for name, value in (
        ("speed_reference_sixteenth_bpm", speed_reference_sixteenth_bpm),
        ("speed_exponent", speed_exponent),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"{name} must be a positive finite number")


@dataclass(frozen=True)
class JackSequenceAnalyzer:
    """Measure the strongest repeated-button sequences in one chart."""

    top_k: int = DEFAULT_TOP_K
    max_interrupting_taps: int = DEFAULT_MAX_INTERRUPTING_TAPS
    speed_reference_sixteenth_bpm: float = DEFAULT_SPEED_REFERENCE_SIXTEENTH_BPM
    speed_exponent: float = DEFAULT_SPEED_EXPONENT

    def __post_init__(self) -> None:
        if isinstance(self.top_k, bool) or not isinstance(self.top_k, int) or self.top_k <= 0:
            raise ValueError("top_k must be a positive integer")
        if (
            isinstance(self.max_interrupting_taps, bool)
            or not isinstance(self.max_interrupting_taps, int)
            or self.max_interrupting_taps < 0
        ):
            raise ValueError("max_interrupting_taps must be a non-negative integer")
        _validate_speed_parameters(
            self.speed_reference_sixteenth_bpm,
            self.speed_exponent,
        )

    def analyze(self, context: AnalysisContext) -> FeatureResult:
        if context.duration_s <= 0:
            return FeatureResult(None, success=False)
        return FeatureResult(jack_score(
            context.events,
            top_k=self.top_k,
            max_interrupting_taps=self.max_interrupting_taps,
            speed_reference_sixteenth_bpm=self.speed_reference_sixteenth_bpm,
            speed_exponent=self.speed_exponent,
        ))
