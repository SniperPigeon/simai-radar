"""Parse source text once into canonical chart bundles."""

from bisect import bisect_right
from fractions import Fraction
import math
import re

from mairadar.model import Chart, ChartBundle, Diagnostic, Event, ParseResult
from .notes import parse_note
from .source import Source, SyntaxProblem, Token, number, seconds, split_top

FIELD = re.compile(r"^[ \t]*&([^=\s]+)=[ \t]*", re.MULTILINE)
LABELS = {1: "Easy", 2: "Basic", 3: "Advanced", 4: "Expert", 5: "Master", 6: "Re:Master", 7: "Utage"}


class ChartParser:
    def __init__(self, source: Source):
        self.source = source
        self.result = ParseResult()
        self._next_id = 1
        self._local_ids: dict[tuple[int, int, str], int] = {}
        self.tempo: list[tuple[Fraction, Fraction, Fraction]] = []
        self.pending: list[tuple[Event, Fraction, Fraction]] = []
        self.time = Fraction(0)
        self.beat = Fraction(0)
        self.bpm: Fraction | None = None
        self.division = Fraction(4)  # Verified upstream default, not guessed BPM.
        self.time_valid = True

    def diagnose(self, code: str, message: str, start: int, end: int, *,
                 severity: str = "error", incomplete: bool = True, action: str = "none") -> None:
        self.result.diagnostics.append(Diagnostic(
            severity=severity, code=code, message=message,
            raw_text=self.source.text[start:end], recovery_json={"action": action},
            **self.source.location(start, end)))
        if incomplete:
            self.result.complete = False

    def event(self, token: Token, spec: dict) -> tuple[Event, Fraction, Fraction]:
        spec = dict(spec)
        start, end = spec.pop("_start"), spec.pop("_end")
        declare = spec.pop("_declare", None)
        suffix = spec.pop("_suffix")
        head_suffix = spec.pop("_head_suffix", None)
        # Temporary local IDs are remapped after final time ordering. Invalid
        # tokens may reserve IDs internally, but never leave gaps in the result.
        key = (token.start, token.end, suffix)
        local_id = self._next_id
        self._next_id += 1
        self._local_ids[key] = local_id
        if head_suffix:
            spec["head_event_id"] = self._local_ids[(token.start, token.end, head_suffix)]
        event = Event(event_id=local_id,
                      start_time_s=seconds(start), end_time_s=seconds(end),
                      slide_declare_time_s=seconds(declare) if declare is not None else None,
                      raw_token=self.source.text[token.start:token.end],
                      **self.source.location(token.start, token.end), **spec)
        return event, start, end

    def masked_token(self, begin: int, end: int) -> Token:
        """Remove comments/whitespace while retaining original code-point offsets."""
        chars, offsets = [], []
        i = begin
        text = self.source.text
        while i < end:
            if text.startswith("||", i):
                finish = text.find("\n", i, end)
                finish = end if finish < 0 else finish
                if text.startswith("||s", i):
                    self.diagnose("UNSUPPORTED_METER", "Player ||s meter extension is not implemented",
                                  i, finish, action="omit_unsupported_command")
                i = finish
            elif text[i].isspace():
                i += 1
            else:
                chars.append(text[i])
                offsets.append(i)
                i += 1
        return Token("".join(chars), tuple(offsets))

    def directives(self, slot: Token) -> Token:
        index = 0
        while index < len(slot.text):
            char = slot.text[index]
            if char in "({":
                close_char = ")" if char == "(" else "}"
                close = slot.text.find(close_char, index + 1)
                if close < 0:
                    raise SyntaxProblem("INVALID_TIMING", f"Unclosed {char} timing directive")
                token = slot.slice(index, close + 1)
                value = slot.text[index + 1:close]
                if char == "(":
                    bpm = number(value)
                    point = self.event(token, dict(kind="timing", timing_type="bpm", bpm=seconds(bpm),
                                                  _suffix="bpm", _start=self.time, _end=self.time))
                    self.bpm = bpm
                    self.tempo.append((self.time, self.beat, bpm))
                    self.pending.append(point)
                elif value.startswith("#"):
                    if self.bpm is None:
                        raise SyntaxProblem("MISSING_BPM", "Absolute subdivision requires a preceding BPM in the pinned dialect")
                    self.division = 240 / (self.bpm * number(value[1:]))
                else:
                    self.division = number(value)
                index = close + 1
            elif slot.text.startswith("<", index) and re.match(r"<[A-Z]", slot.text[index:]):
                close = slot.text.find(">", index + 1)
                if close < 0:
                    raise SyntaxProblem("INVALID_TIMING", "Unclosed player speed directive")
                token = slot.slice(index, close + 1)
                self.diagnose("UNSUPPORTED_SPEED", "Player speed directives are not implemented",
                              token.start, token.end, action="omit_unsupported_command")
                index = close + 1
            else:
                break
        return slot.slice(index)

    def notes(self, slot: Token) -> None:
        if not slot.text:
            return
        if "(" in slot.text or "{" in slot.text:
            raise SyntaxProblem("MISPLACED_TIMING", "Timing directives must precede the notes in their comma slot")
        try:
            groups = split_top(slot, "`")
            if any(not group.text for group in groups):
                raise SyntaxProblem("EMPTY_PSEUDO_EACH", "Empty pseudo-EACH group")
        except SyntaxProblem as exc:
            self.diagnose(exc.code, str(exc), slot.start, slot.end, action="omit_invalid_slot_notes")
            return
        for group_index, group in enumerate(groups):
            at = self.time + group_index * Fraction(15, 8) / self.bpm
            try:
                members = split_top(group, "/")
            except SyntaxProblem as exc:
                self.diagnose(exc.code, str(exc), group.start, group.end, action="omit_invalid_group")
                continue
            for member in members:
                if not member.text:
                    self.diagnose("EMPTY_EACH", "Empty simultaneous note member", group.start, group.end,
                                  action="omit_empty_member")
                    continue
                # Exactly two bare digits; never silently truncate a longer string.
                atoms = [member.slice(0, 1), member.slice(1)] if re.fullmatch(r"[1-8]{2}", member.text) else [member]
                for atom in atoms:
                    try:
                        specs = parse_note(atom, at, self.bpm, self.source.text)
                        events = [self.event(atom, spec) for spec in specs]
                    except SyntaxProblem as exc:
                        self.diagnose(exc.code, str(exc), atom.start, atom.end, action="omit_invalid_token")
                        continue
                    self.pending.extend(events)

    def parse(self, begin: int, end: int) -> ParseResult:
        token = self.masked_token(begin, end)
        if not token.text:
            self.diagnose("EMPTY_CHART", "Chart is empty", begin, end)
            return self.result
        # No supported duration or timing directive contains a comma. Treat each
        # comma as a source boundary even when a note itself has malformed brackets.
        boundaries = [i for i, char in enumerate(token.text) if char == ","]
        boundaries.append(len(token.text))
        cursor = 0
        terminated = False
        for boundary in boundaries:
            slot = token.slice(cursor, boundary)
            has_comma = boundary < len(token.text)
            try:
                notes = self.directives(slot)
                lowercase_end = notes.text == "e" and not has_comma and boundary == len(token.text)
                if lowercase_end:
                    self.diagnose("LOWERCASE_TERMINATOR", "Terminal e accepted as E",
                                  notes.start, notes.end, severity="info", incomplete=False)
                if notes.text == "E" or lowercase_end:
                    terminated = True
                    if has_comma or boundary != len(token.text):
                        start = token.offsets[boundary] if has_comma else notes.start
                        self.diagnose("TRAILING_CONTENT", "Content follows the E terminator", start, token.end,
                                      action="ignore_after_terminator")
                    break
                if notes.text or has_comma:
                    if self.bpm is None:
                        raise SyntaxProblem("MISSING_BPM", "A BPM is required before notes or time advancement")
                    self.notes(notes)
                if has_comma:
                    delta_beat = 4 / self.division
                    next_time = self.time + 60 * delta_beat / self.bpm
                    seconds(next_time)  # Reject overflow before accepting a new time origin.
                    self.time = next_time
                    self.beat += delta_beat
            except SyntaxProblem as exc:
                start, finish = (slot.start, slot.end) if slot.text else (begin, end)
                self.diagnose(exc.code, str(exc), start, finish, action="stop_chart_timeline")
                self.time_valid = False
                break
            cursor = boundary + 1
        if self.time_valid:
            self.result.chart_end_time_s = seconds(self.time)
            if not terminated:
                self.diagnose("EOF_TERMINATOR", "EOF accepted as the chart end; no comma or time interval was inserted",
                              end, end, severity="info", incomplete=False)
        self.finish()
        return self.result

    def finish(self) -> None:
        times = [point[0] for point in self.tempo]

        def beat_at(time: Fraction) -> str | None:
            if not self.tempo or (not self.time_valid and time >= self.time):
                return None
            i = bisect_right(times, time) - 1
            if i < 0:
                return None
            origin, beat, bpm = self.tempo[i]
            return str(beat + (time - origin) * bpm / 60)

        # Stable sort retains derivation order within a token (head, then branches).
        self.pending.sort(key=lambda item: (item[1], item[0].source_start))
        id_map = {event.event_id: index for index, (event, _, _) in enumerate(self.pending, 1)}
        for event, start, end in self.pending:
            event.event_id = id_map[event.event_id]
            if event.head_event_id is not None:
                event.head_event_id = id_map[event.head_event_id]
            event.start_beat = beat_at(start)
            event.end_beat = beat_at(end)
            self.result.events.append(event)
        ends = [event.end_time_s for event in self.result.events if event.kind != "timing"]
        self.result.last_event_end_s = max(ends, default=None)


def parse_chart(text: str) -> ParseResult:
    """Parse one raw inote body. No paths, song IDs, metadata or file I/O needed."""
    text = text.removeprefix("\ufeff")
    parser = ChartParser(Source(text))
    return parser.parse(0, len(text))


def parse_text(text: str, *, difficulties: list[int] | None = None) -> list[ChartBundle]:
    """Parse raw text or a maidata envelope in memory; attach only its metadata.

    For a player's raw inote text, parse_chart returns a single ParseResult.
    This convenience adapter handles multi-difficulty envelope text as well.
    """
    text = text.removeprefix("\ufeff")
    if difficulties is not None and any(type(index) is not int or index < 1 for index in difficulties):
        raise ValueError("difficulty indexes must be positive integers")
    source = Source(text)
    matches = list(FIELD.finditer(text))
    fields, spans, duplicate_spans, metadata_tails = {}, {}, [], []
    for i, match in enumerate(matches):
        begin = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        key = match[1]
        if key in fields:
            duplicate_spans.append((key, match.start(), end))
        value = text[begin:end]
        if re.fullmatch(r"(?:title|artist|cabinet|cabinate|des(?:_[0-9]+)?|first(?:_[0-9]+)?|lv_[0-9]+)", key):
            lines = value.splitlines(keepends=True)
            fields[key] = lines[0].strip() if lines else ""
            cursor = begin + len(lines[0]) if lines else begin
            for line in lines[1:]:
                if line.strip() and not line.lstrip().startswith("||"):
                    metadata_tails.append((key, cursor, cursor + len(line)))
                cursor += len(line)
        else:
            fields[key] = value.strip()
        spans[key] = (begin, end)
    if matches:
        indexes = sorted(int(match[1]) for key in fields if (match := re.fullmatch(r"inote_([1-9][0-9]*)", key)))
        selected = sorted(set(difficulties)) if difficulties is not None else indexes
        if not selected:
            selected = [None]
    else:
        # A raw chart has no metadata index to filter; an explicit single index
        # supplies its identity, while multiple indexes would duplicate objects.
        if difficulties is not None and len(set(difficulties)) != 1:
            raise ValueError("raw Simai accepts at most one --difficulty index")
        selected = list(set(difficulties)) if difficulties else [None]
    bundles = []
    for index in selected:
        key = f"inote_{index}" if index is not None else ""
        cabinet = fields.get("cabinet", fields.get("cabinate", "")).strip().lower()
        chart = Chart(chart_type=cabinet if cabinet in {"dx", "sd"} else None,
                      difficulty_index=index, difficulty_label=LABELS.get(index),
                      level_text=fields.get(f"lv_{index}") or None,
                      title=fields.get("title") or None, artist=fields.get("artist") or None,
                      designer=fields.get(f"des_{index}") or fields.get("des") or None,
                      metadata_json={k: v for k, v in fields.items() if not re.fullmatch(r"inote_[0-9]+", k)})
        parser = ChartParser(source)
        for metadata_key, start, end in metadata_tails:
            parser.diagnose("UNEXPECTED_METADATA_TEXT", f"Unexpected continuation of scalar &{metadata_key}",
                            start, end, action="retain_in_diagnostic")
        for duplicate, start, end in duplicate_spans:
            parser.diagnose("DUPLICATE_FIELD", f"Duplicate &{duplicate}; last value used", start, end,
                            action="use_last_field")
        if matches and text[:matches[0].start()].strip():
            preamble = parser.masked_token(0, matches[0].start())
            if preamble.text:
                parser.diagnose("UNEXPECTED_PREAMBLE", "Unexpected text before metadata", preamble.start, preamble.end)
        offset_key = f"first_{index}" if fields.get(f"first_{index}") else "first"
        if fields.get(offset_key):
            try:
                chart.offset_s = float(fields[offset_key])
                if not math.isfinite(chart.offset_s):
                    raise ValueError()
            except ValueError:
                chart.offset_s = None
                parser.diagnose("INVALID_OFFSET", "Audio offset must be a finite number", *spans[offset_key])
        if matches and key not in fields:
            parser.diagnose("MISSING_CHART", f"No &{key or 'inote_N'} field matches the request", 0, 0)
            result = parser.result
        else:
            begin, end = spans[key] if matches else (0, len(text))
            result = parser.parse(begin, end)
        chart.chart_end_time_s = result.chart_end_time_s
        chart.last_event_end_s = result.last_event_end_s
        bundles.append(ChartBundle(chart, result.events, result.diagnostics, result.complete))
    return bundles
