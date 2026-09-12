"""Note semantics and inline Slide paths, independent of chart scanning."""

from fractions import Fraction
import re

from .durations import hold_duration, slide_duration
from .source import SyntaxProblem, Token, seconds, split_top

SHAPES = "-^v<>Vpqszw"


def _flags(token: Token) -> tuple[Token, dict]:
    chars, offsets = [], []
    flags = dict(is_break=False, is_ex=False, is_mine=False,
                 slide_break=False, slide_mine=False, extra={})
    in_duration = False
    saw_shape = False
    stars = 0
    for i, char in enumerate(token.text):
        if char == "[":
            in_duration = True
        if not in_duration:
            if char in SHAPES or char == "K":
                saw_shape = True
            if char in "bm":
                name = "break" if char == "b" else "mine"
                if saw_shape:
                    if i != len(token.text) - 1 and token.text[i + 1] != "[":
                        raise SyntaxProblem("INVALID_MODIFIER", "Slide b/m must precede a duration or end the path")
                    flags["slide_" + name] = True
                else:
                    flags["is_" + name] = True
                continue
            if char == "x":
                flags["is_ex"] = True
                continue
            if char == "c":
                flags["extra"]["using_sv"] = False  # Pinned NoteFlag: c disables SV.
                continue
            if char in "!?":
                if "no_head_marker" in flags["extra"]:
                    raise SyntaxProblem("INVALID_MODIFIER", "Multiple no-head markers")
                flags["extra"]["no_head_marker"] = char
                continue
            if char == "@":
                flags["extra"]["tap_head"] = True
                continue
            if char == "$":
                stars += 1
                continue
            if char == "f":
                flags["extra"]["hanabi"] = True
                continue
        chars.append(char)
        offsets.append(token.offsets[i])
        if char == "]":
            in_duration = False
    if stars:
        flags["extra"]["force_star"] = True
        if stars > 1:
            flags["extra"]["fake_rotate"] = True
    return Token("".join(chars), tuple(offsets)), flags


def _shape_is_valid(shape: str, start: str, via: str | None, end: str) -> None:
    """Endpoint constraints from the pinned player's NoteCreateHelper."""
    relative = (int(end) - int(start)) % 8
    valid = True
    if shape == "-":
        valid = 2 <= relative <= 6
    elif shape == "^":
        valid = relative not in (0, 4)
    elif shape == "v":
        valid = relative != 4
    elif shape in ("s", "z", "w"):
        valid = relative == 4
    elif shape == "V":
        turn = (int(via) - int(start)) % 8
        valid = (turn == 6 and 1 <= relative <= 4) or (turn == 2 and 4 <= relative <= 7)
    if not valid:
        raise SyntaxProblem("INVALID_SLIDE_GEOMETRY", f"Invalid endpoints for {start}{shape}{via or ''}{end}")


def _path(clean: Token, bpm: Fraction, declare: Fraction, source: str) -> tuple[list, Fraction, Fraction]:
    if not clean.text or clean.text[0] not in "12345678":
        raise SyntaxProblem("INVALID_POSITION", "Slide must begin at a button from 1 to 8")
    start = clean.text[0]
    cursor = 1
    segments, lengths = [], []
    wait_override = None
    while cursor < len(clean.text):
        begin = cursor
        shape_match = re.match(r"pp|qq|[-^v<>Vpqszw]", clean.text[cursor:])
        if not shape_match:
            raise SyntaxProblem("INVALID_SLIDE", f"Unknown Slide path at {clean.text[cursor:]!r}")
        shape = shape_match[0]
        cursor += len(shape)
        count = 2 if shape == "V" else 1
        positions = clean.text[cursor:cursor + count]
        if len(positions) != count or any(c not in "12345678" for c in positions):
            raise SyntaxProblem("INVALID_POSITION", "Slide endpoints must be buttons 1–8")
        via = positions[0] if count == 2 else None
        end = positions[-1]
        cursor += count
        _shape_is_valid(shape, start, via, end)
        length = None
        if cursor < len(clean.text) and clean.text[cursor] == "[":
            close = clean.text.find("]", cursor)
            if close < 0:
                raise SyntaxProblem("INVALID_DURATION", "Unclosed Slide duration")
            wait, length = slide_duration(clean.text[cursor + 1:close], bpm)
            if wait_override is None and wait is not None:
                wait_override = wait
            cursor = close + 1
        lengths.append(length)
        segments.append(dict(shape=shape, start_position=start, via_position=via,
                             end_position=end, start_time_s=None, end_time_s=None,
                             raw_segment=source[clean.offsets[begin]:clean.offsets[cursor - 1] + 1],
                             time_resolution="needs_geometry"))
        start = end
    if not segments:
        raise SyntaxProblem("INVALID_SLIDE", "Slide has no path")
    all_explicit = all(length is not None for length in lengths)
    total_only = lengths[-1] is not None and all(length is None for length in lengths[:-1])
    if not (all_explicit or total_only):
        raise SyntaxProblem("INVALID_SLIDE_DURATION", "Specify every segment duration, or one total duration on the last segment")
    if len(segments) > 1 and any(segment["shape"] == "w" for segment in segments):
        raise SyntaxProblem("INVALID_SLIDE_GEOMETRY", "Wifi cannot be part of a connected Slide")
    start_time = declare + (60 / bpm if wait_override is None else wait_override)
    end_time = start_time + sum((length for length in lengths if length is not None), Fraction(0))
    # Whole-path timing is the parsing contract. Derived segment timings are optional.
    if len(segments) == 1:
        segments[0].update(start_time_s=seconds(start_time), end_time_s=seconds(end_time),
                           time_resolution="explicit_duration")
    return segments, start_time, end_time


def parse_note(token: Token, at: Fraction, bpm: Fraction, source: str) -> list[dict]:
    """Parse one simultaneous member atomically into event specs."""
    if "K" in token.text:
        raise SyntaxProblem("UNSUPPORTED_SLIDE", "K custom Slide geometry is not supported")
    branches = split_top(token, "*")
    if any(not branch.text for branch in branches):
        raise SyntaxProblem("EMPTY_SLIDE_BRANCH", "Empty same-head Slide branch")
    first, first_flags = _flags(branches[0])
    is_slide = any(c in first.text.split("[")[0] for c in SHAPES)
    if not is_slide:
        if len(branches) > 1:
            raise SyntaxProblem("INVALID_SLIDE", "Same-head branches require a Slide")
        match = re.fullmatch(r"([1-8]|[ABDE][1-8]|C[12]?)(h)?(?:\[([^\[\]]*)\])?", first.text)
        if not match:
            raise SyntaxProblem("INVALID_NOTE", f"Unknown or malformed note: {token.text!r}")
        position, held, duration = match.groups()
        if position.startswith("C"):
            position = "C"  # C1/C2 are source aliases of the same central sensor.
        extra = first_flags["extra"]
        if any(key in extra for key in ("no_head_marker", "tap_head")):
            raise SyntaxProblem("INVALID_MODIFIER", "No-head and tap-head markers require a Slide")
        touch = position[0] in "ABCDE"
        if extra.get("hanabi") and not touch:
            raise SyntaxProblem("INVALID_MODIFIER", "Hanabi is only supported on Touch notes")
        if touch and (extra.get("force_star") or extra.get("fake_rotate")):
            raise SyntaxProblem("INVALID_MODIFIER", "Star display modifiers require a button note")
        if duration is not None and not held:
            raise SyntaxProblem("INVALID_DURATION", "A non-Slide duration requires h")
        end = at + (hold_duration(duration, bpm) if held else Fraction(0))
        kind = ("touch_hold" if held else "touch") if touch else ("hold" if held else "tap")
        return [dict(kind=kind, position=position, is_slide_head=False,
                     is_break=first_flags["is_break"], is_ex=first_flags["is_ex"],
                     is_mine=first_flags["is_mine"], flags_json=extra,
                     _suffix="note", _start=at, _end=end)]

    output = []
    no_head = "no_head_marker" in first_flags["extra"]
    if no_head and first_flags["extra"].get("tap_head"):
        raise SyntaxProblem("INVALID_MODIFIER", "A Slide cannot be both no-head and tap-head")
    head_extra = {key: value for key, value in first_flags["extra"].items()
                  if key not in ("no_head_marker",)}
    if head_extra.get("hanabi"):
        raise SyntaxProblem("INVALID_MODIFIER", "Hanabi is only supported on Touch notes")
    if not no_head:
        output.append(dict(kind="tap", position=first.text[0], is_slide_head=True,
                           is_break=first_flags["is_break"], is_ex=first_flags["is_ex"],
                           is_mine=first_flags["is_mine"], flags_json=head_extra,
                           _suffix="head", _start=at, _end=at))
    for i, branch in enumerate(branches):
        # Later * branches inherit only the initial button, not the first path's flags.
        if i:
            branch = Token(first.text[0] + branch.text, (first.start,) + branch.offsets)
        clean, flags = _flags(branch)
        if flags["extra"].get("hanabi"):
            raise SyntaxProblem("INVALID_MODIFIER", "Hanabi is only supported on Touch notes")
        segments, start, end = _path(clean, bpm, at, source)
        extra = {key: value for key, value in flags["extra"].items()
                 if key in ("no_head_marker", "using_sv")}
        if no_head:
            extra.setdefault("no_head_marker", first_flags["extra"]["no_head_marker"])
        if i or no_head:
            suppressed = {key: flags[key] for key in ("is_break", "is_ex", "is_mine") if flags[key]}
            suppressed.update({key: value for key, value in flags["extra"].items()
                               if key not in ("no_head_marker", "using_sv")})
            if suppressed:
                extra["suppressed_head_flags"] = suppressed
        output.append(dict(kind="slide", position=first.text[0], is_slide_head=False,
                           is_break=flags["slide_break"], is_ex=False,
                           is_mine=flags["slide_mine"], flags_json=extra,
                           slide_path_json=segments, _suffix=f"slide:{i}",
                           _head_suffix=None if no_head else "head", _declare=at,
                           _start=start, _end=end))
    return output
