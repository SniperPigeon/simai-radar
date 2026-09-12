"""Duration expressions from the fixed MajSimai pin, using exact arithmetic."""

from fractions import Fraction
import re

from .source import SyntaxProblem, number


def ratio(text: str, bpm: Fraction) -> Fraction:
    match = re.fullmatch(r"([0-9]+):([0-9]+)", text)
    if not match:
        raise SyntaxProblem("INVALID_DURATION", f"Expected integer division:count, got {text!r}")
    division = number(match[1])
    count = number(match[2], zero=True)
    return 240 * count / (bpm * division)


def hold_duration(text: str | None, bpm: Fraction) -> Fraction:
    if text is None:
        return Fraction(0)  # The pinned parser's short Hold/TouchHold behavior.
    parts = text.split("#")
    if len(parts) == 1:
        return ratio(text, bpm)
    if len(parts) == 2:
        if parts[0] == "":
            return number(parts[1], zero=True)
        return ratio(parts[1], number(parts[0]))
    raise SyntaxProblem("INVALID_DURATION", f"Unsupported Hold duration: [{text}]")


def slide_duration(text: str, bpm: Fraction) -> tuple[Fraction | None, Fraction]:
    """Return an optional wait override and this bracket's slide duration.

    In a chain, the first explicit wait/custom-BPM override wins. A plain ratio
    does not override the wait; all lengths use declaration BPM unless explicit.
    """
    parts = text.split("#")
    if len(parts) == 1:
        return None, ratio(text, bpm)
    if len(parts) == 2 and all(parts):
        custom = number(parts[0])
        length = ratio(parts[1], custom) if ":" in parts[1] else number(parts[1], zero=True)
        return 60 / custom, length
    if len(parts) in (3, 4) and parts[1] == "":
        wait = number(parts[0], zero=True)
        if len(parts) == 3:
            length = ratio(parts[2], bpm) if ":" in parts[2] else number(parts[2], zero=True)
        else:
            length = ratio(parts[3], number(parts[2]))
        return wait, length
    raise SyntaxProblem("INVALID_DURATION", f"Unsupported Slide duration: [{text}]")
