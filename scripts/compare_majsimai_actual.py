#!/usr/bin/env python3
"""Migration-only differential check: MajSimai versus the Python parser.

This intentionally excludes Slide bar_count and per-segment timing. They are not
part of MajSimai's typed output and are covered by the C# adapter's fixed geometry.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mairadar.parser import parse_chart


FIELD = re.compile(r"(?m)^[ \t]*&([A-Za-z][A-Za-z0-9_]*)=")
SHAPE = re.compile(r"pp|qq|[-^v<>Vpqszw]")
TIME_TOLERANCE = 1e-6
MAX_BEAT_DENOMINATOR = 4096
BEAT_SNAP_TOLERANCE = 1e-7


@dataclass(frozen=True)
class ComparableEvent:
    kind: str
    position: str | None
    start_time: float
    end_time: float
    start_beat: str
    end_beat: str
    is_slide_head: bool
    is_break: bool
    is_ex: bool
    is_mine: bool
    path: tuple[tuple[str, int, int | None, int], ...] = ()


def maidata_field(text: str, key: str) -> str | None:
    matches = list(FIELD.finditer(text.removeprefix("\ufeff")))
    values = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        values[match[1]] = text[match.end():end].strip()
    return values.get(key)


def snap_beat(value: float) -> Fraction:
    best = Fraction(0)
    best_error = math.inf
    for denominator in range(1, MAX_BEAT_DENOMINATOR + 1):
        numerator = round(value * denominator)
        candidate = Fraction(numerator, denominator)
        error = abs(float(candidate) - value)
        if error <= BEAT_SNAP_TOLERANCE:
            return candidate
        if error < best_error:
            best, best_error = candidate, error
    return best


def beat_text(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else str(value)


def majsimai_timeline(comma_points: list[dict]) -> list[tuple[float, Fraction, float]]:
    output = []
    beat_value = 0.0
    for index, point in enumerate(comma_points):
        if index:
            previous = comma_points[index - 1]
            beat_value += (point["Timing"] - previous["Timing"]) * previous["Bpm"] / 60.0
        output.append((point["Timing"], snap_beat(beat_value), point["Bpm"]))
    return output


def beat_at(time: float, timeline: list[tuple[float, Fraction, float]]) -> Fraction:
    for point_time, point_beat, bpm in reversed(timeline):
        if math.isclose(time, point_time, abs_tol=TIME_TOLERANCE):
            return point_beat
        if time > point_time:
            return snap_beat(float(point_beat) + (time - point_time) * bpm / 60.0)
    raise ValueError(f"time {time} precedes MajSimai timeline")


def slide_path(raw: str) -> tuple[tuple[str, int, int | None, int], ...]:
    if not raw or raw[0] not in "12345678" or "K" in raw:
        raise ValueError(f"unsupported Slide path {raw!r}")
    start, cursor, output = int(raw[0]), 1, []
    while cursor < len(raw):
        match = SHAPE.match(raw, cursor)
        if not match:
            raise ValueError(f"unknown Slide path at {raw[cursor:]!r}")
        shape = match[0]
        cursor = match.end()
        via = int(raw[cursor]) if shape == "V" else None
        cursor += int(shape == "V")
        end = int(raw[cursor])
        cursor += 1
        while cursor < len(raw) and raw[cursor] in "bmc":
            cursor += 1
        if cursor < len(raw) and raw[cursor] == "[":
            close = raw.find("]", cursor + 1)
            if close < 0:
                raise ValueError(f"unclosed duration in {raw!r}")
            cursor = close + 1
        output.append((shape, start, via, end))
        start = end
    return tuple(output)


def maj_position(note: dict) -> str:
    area = note["TouchArea"]
    if area == "C":
        return "C"
    if area.strip():
        return f"{area}{note['StartPosition']}"
    return str(note["StartPosition"])


def project_majsimai(payload: dict) -> tuple[list[ComparableEvent], float, float | None]:
    chart = payload["chart"]
    timeline = majsimai_timeline(chart["commaTimings"])
    pending = []
    order = 0
    for timing in chart["noteTimings"]:
        declare_time = timing["Timing"]
        declare_beat = beat_at(declare_time, timeline)
        for note in timing["Notes"]:
            order += 1
            note_type = note["Type"]
            if note_type == "Slide":
                position = str(note["StartPosition"])
                if not note["IsSlideNoHead"]:
                    pending.append((declare_time, timing["RawTextPosition"], order, ComparableEvent(
                        "tap", position, declare_time, declare_time,
                        beat_text(declare_beat), beat_text(declare_beat), True,
                        note["IsBreak"], note["IsEx"], note["IsMine"],
                    )))
                start, end = note["SlideStartTime"], note["SlideStartTime"] + note["SlideTime"]
                pending.append((start, timing["RawTextPosition"], order, ComparableEvent(
                    "slide", position, start, end,
                    beat_text(beat_at(start, timeline)), beat_text(beat_at(end, timeline)), False,
                    note["IsSlideBreak"], False, note["IsMineSlide"], slide_path(note["RawContent"]),
                )))
                continue
            kind = {
                "Tap": "tap", "Hold": "hold", "Touch": "touch", "TouchHold": "touch_hold",
            }[note_type]
            end = declare_time + note["HoldTime"]
            pending.append((declare_time, timing["RawTextPosition"], order, ComparableEvent(
                kind, maj_position(note), declare_time, end,
                beat_text(declare_beat), beat_text(beat_at(end, timeline)), False,
                note["IsBreak"], note["IsEx"], note["IsMine"],
            )))
    pending.sort(key=lambda item: item[:3])
    events = [item[-1] for item in pending]
    last_end = max((event.end_time for event in events), default=None)
    return events, chart["commaTimings"][-1]["Timing"], last_end


def project_python(body: str) -> tuple[list[ComparableEvent], float | None, float | None, list[str]]:
    result = parse_chart(body)
    diagnostics = [f"{item.severity}:{item.code}" for item in result.diagnostics]
    events = []
    for event in result.events:
        if event.kind == "timing":
            continue
        path = tuple(
            (item["shape"], int(item["start_position"]),
             int(item["via_position"]) if item["via_position"] is not None else None,
             int(item["end_position"]))
            for item in event.slide_path_json or ()
        )
        events.append(ComparableEvent(
            event.kind, event.position, event.start_time_s, event.end_time_s,
            beat_text(snap_beat(float(Fraction(event.start_beat or "0")))),
            beat_text(snap_beat(float(Fraction(event.end_beat or "0")))),
            bool(event.is_slide_head),
            bool(event.is_break), bool(event.is_ex), bool(event.is_mine), path,
        ))
    if not result.complete:
        diagnostics.insert(0, "error:PYTHON_INCOMPLETE")
    return events, result.chart_end_time_s, result.last_event_end_s, diagnostics


def close(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is right
    return math.isclose(left, right, rel_tol=0, abs_tol=TIME_TOLERANCE)


def event_key(event: ComparableEvent) -> tuple:
    return (
        event.kind, event.position, event.start_beat, event.end_beat, event.is_slide_head,
        event.is_break, event.is_ex, event.is_mine, event.path,
    )


def grouped_events(events: list[ComparableEvent]) -> dict[tuple, list[ComparableEvent]]:
    output = defaultdict(list)
    for event in events:
        output[event_key(event)].append(event)
    for members in output.values():
        members.sort(key=lambda item: (item.start_time, item.end_time))
    return dict(output)


def probe(probe_dll: Path, body: str) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8") as stream:
        stream.write(body)
        stream.flush()
        completed = subprocess.run(
            ["dotnet", str(probe_dll), stream.name],
            check=False, capture_output=True, text=True,
        )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or f"probe exited {completed.returncode}")
    return json.loads(completed.stdout)


def compare(path: Path, difficulty: int, probe_dll: Path) -> tuple[bool, list[str]]:
    text = path.read_text(encoding="utf-8-sig")
    body = maidata_field(text, f"inote_{difficulty}")
    if body is None:
        return False, [f"missing inote_{difficulty}"]
    python_events, python_end, python_last, diagnostics = project_python(body)
    if "error:PYTHON_INCOMPLETE" in diagnostics:
        return False, diagnostics
    try:
        maj_events, maj_end, maj_last = project_majsimai(probe(probe_dll, body))
    except (RuntimeError, ValueError, KeyError, IndexError) as exception:
        return False, [f"MajSimai projection failed: {exception}"]
    differences, notes = [], []
    if len(maj_events) != len(python_events):
        differences.append(f"event_count: MajSimai={len(maj_events)} Python={len(python_events)}")
    maj_groups, python_groups = grouped_events(maj_events), grouped_events(python_events)
    max_time_delta = 0.0
    for key in sorted(set(maj_groups) | set(python_groups), key=repr):
        maj_members, python_members = maj_groups.get(key, []), python_groups.get(key, [])
        if len(maj_members) != len(python_members):
            differences.append(
                f"event semantic count {key!r}: MajSimai={len(maj_members)} Python={len(python_members)}"
            )
            if len(differences) >= 8:
                break
            continue
        for maj, python in zip(maj_members, python_members):
            delta = max(
                abs(maj.start_time - python.start_time),
                abs(maj.end_time - python.end_time),
            )
            max_time_delta = max(max_time_delta, delta)
            if not close(maj.start_time, python.start_time) or not close(maj.end_time, python.end_time):
                differences.append(
                    f"event time {key!r}: MajSimai=({maj.start_time!r},{maj.end_time!r}) "
                    f"Python=({python.start_time!r},{python.end_time!r})"
                )
                if len(differences) >= 8:
                    break
    if not close(maj_end, python_end):
        differences.append(f"chart_end: MajSimai={maj_end!r} Python={python_end!r}")
    if not close(maj_last, python_last):
        differences.append(f"last_event_end: MajSimai={maj_last!r} Python={python_last!r}")
    notes.append(f"events={len(maj_events)}, max_event_time_delta={max_time_delta:.3g}s")
    notes.append(f"chart_end_delta={abs(maj_end - python_end):.3g}s")
    return not differences, differences or notes + diagnostics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--difficulty", type=int, default=5)
    parser.add_argument(
        "--probe-dll", type=Path,
        default=Path("integrations/majsimai-probe/bin/Debug/net10.0/MajSimaiProbe.dll"),
    )
    args = parser.parse_args()
    failures = 0
    for path in args.paths:
        passed, details = compare(path, args.difficulty, args.probe_dll)
        print(f"{'PASS' if passed else 'FAIL'} {path}")
        for detail in details[:8]:
            print(f"  {detail}")
        failures += int(not passed)
    print(f"summary: {len(args.paths) - failures} passed, {failures} failed, {len(args.paths)} total")
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
