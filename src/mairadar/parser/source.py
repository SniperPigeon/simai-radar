"""Source coordinates and strict numeric/grammar helpers."""

from bisect import bisect_right
from dataclasses import dataclass
from fractions import Fraction
import math
import re


class SyntaxProblem(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def number(text: str, *, zero: bool = False) -> Fraction:
    if len(text) > 100 or not re.fullmatch(
        r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", text
    ):
        raise SyntaxProblem("INVALID_NUMBER", f"Invalid number: {text!r}")
    try:
        if not math.isfinite(float(text)):
            raise ValueError()
        # Bound exponents before Fraction allocation, including huge negative exponents.
        if "e" in text.lower() and abs(int(text.lower().split("e")[1])) > 308:
            raise ValueError()
        value = Fraction(text)
        if value < 0 or (not zero and value == 0):
            raise ValueError()
        if value and float(value) == 0:
            raise ValueError()
    except (ValueError, OverflowError) as exc:
        raise SyntaxProblem("INVALID_NUMBER", f"Expected a finite {'nonnegative' if zero else 'positive'} number: {text!r}") from exc
    return value


def seconds(value: Fraction) -> float:
    try:
        result = float(value)
        if not math.isfinite(result) or (value and result == 0):
            raise OverflowError()
        return result
    except OverflowError as exc:
        raise SyntaxProblem("TIME_OVERFLOW", "Time is outside representable float64 range") from exc


@dataclass(frozen=True)
class Token:
    text: str
    offsets: tuple[int, ...]

    @property
    def start(self) -> int:
        return self.offsets[0]

    @property
    def end(self) -> int:
        return self.offsets[-1] + 1

    def slice(self, start: int, end: int | None = None) -> "Token":
        return Token(self.text[start:end], self.offsets[start:end])


class Source:
    def __init__(self, text: str):
        self.text = text
        self.line_starts = [0] + [i + 1 for i, c in enumerate(text) if c == "\n"]

    def location(self, start: int, end: int) -> dict:
        line = bisect_right(self.line_starts, start)
        return dict(source_start=start, source_end=end, source_line=line,
                    source_column=start - self.line_starts[line - 1] + 1)


def split_top(token: Token, separator: str) -> list[Token]:
    """Split note groups without treating punctuation inside durations as separators."""
    out = []
    begin = depth = 0
    for i, char in enumerate(token.text):
        if char == "[":
            depth += 1
            if depth > 1:
                raise SyntaxProblem("INVALID_BRACKETS", "Nested duration brackets are invalid")
        elif char == "]":
            depth -= 1
            if depth < 0:
                raise SyntaxProblem("INVALID_BRACKETS", "Unexpected closing duration bracket")
        elif char == separator and depth == 0:
            out.append(token.slice(begin, i))
            begin = i + 1
    if depth:
        raise SyntaxProblem("INVALID_BRACKETS", "Unclosed duration bracket")
    out.append(token.slice(begin))
    return out
