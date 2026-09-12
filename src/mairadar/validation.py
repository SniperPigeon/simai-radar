"""Pure model validation, usable without file I/O or export metadata."""

from fractions import Fraction
import math
import re

from .model import ChartBundle, ParseResult

BOOL_FIELDS = {"is_slide_head", "is_break", "is_ex", "is_mine"}


def validate_result(result: ParseResult) -> None:
    """Validate per-call IDs, timing, positions and shared Slide references."""
    if any(type(e.event_id) is not int for e in result.events) or [e.event_id for e in result.events] != list(range(1, len(result.events) + 1)):
        raise ValueError("event_id must be consecutive integers starting at 1 in output order")
    for e in result.events:
        if e.head_event_id is not None and (type(e.head_event_id) is not int or e.head_event_id < 1):
            raise ValueError("head_event_id must be a positive integer or null")
    events = {e.event_id: e for e in result.events}
    if len(events) != len(result.events):
        raise ValueError("Duplicate event_id")
    def finite(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    for value in (result.chart_end_time_s, result.last_event_end_s):
        if value is not None and (not finite(value) or value < 0):
            raise ValueError("Invalid result time metadata")
    for event in result.events:
        if event.kind not in {"tap", "hold", "touch", "touch_hold", "slide", "timing"}:
            raise ValueError("Invalid event identity/type")
        if not finite(event.start_time_s) or not finite(event.end_time_s) or not 0 <= event.start_time_s <= event.end_time_s:
            raise ValueError("Invalid event interval")
        for beat in (event.start_beat, event.end_beat):
            if beat is not None and (not isinstance(beat, str) or str(Fraction(beat)) != beat):
                raise ValueError("Beat must be a canonical rational string")
        if event.kind in {"tap", "touch", "timing"} and event.start_time_s != event.end_time_s:
            raise ValueError("Point event has nonzero duration")
        if not (0 <= event.source_start < event.source_end and event.source_line >= 1 and event.source_column >= 1):
            raise ValueError("Invalid source span")
        if len(event.raw_token) != event.source_end - event.source_start:
            raise ValueError("Source span length differs from raw_token")
        if event.kind == "timing":
            if event.timing_type != "bpm" or not finite(event.bpm) or event.bpm <= 0:
                raise ValueError("Invalid BPM event")
            if any(getattr(event, key) is not None for key in BOOL_FIELDS | {"position", "head_event_id", "slide_path_json", "flags_json", "slide_declare_time_s"}):
                raise ValueError("BPM event has note-only fields")
            continue
        if event.timing_type is not None or event.bpm is not None:
            raise ValueError("Note has BPM-only fields")
        if any(type(getattr(event, key)) is not bool for key in BOOL_FIELDS) or not isinstance(event.flags_json, dict):
            raise ValueError("Note flags must be booleans and a JSON object")
        touch = event.kind in {"touch", "touch_hold"}
        pattern = r"(?:[ABDE][1-8]|C)" if touch else r"[1-8]"
        if not isinstance(event.position, str) or not re.fullmatch(pattern, event.position):
            raise ValueError("Invalid string position")
        if event.is_slide_head and event.kind != "tap":
            raise ValueError("Only a Tap can be a Slide head")
        if event.kind != "slide":
            if any(getattr(event, key) is not None for key in ("slide_declare_time_s", "head_event_id", "slide_path_json")):
                raise ValueError("Non-Slide has path fields")
            continue
        if not finite(event.slide_declare_time_s) or not 0 <= event.slide_declare_time_s <= event.start_time_s:
            raise ValueError("Every Slide requires a valid declaration time")
        if event.head_event_id:
            head = events.get(event.head_event_id)
            if head is None or not head.is_slide_head or head.position != event.position or head.start_time_s != event.slide_declare_time_s:
                raise ValueError("Invalid shared Slide head reference")
        path = event.slide_path_json
        if not isinstance(path, list) or not path:
            if result.complete:
                raise ValueError("Complete Slide has no path")
            continue
        previous = event.position
        previous_time = event.start_time_s
        for segment in path:
            if set(segment) != {"shape", "start_position", "via_position", "end_position", "start_time_s", "end_time_s", "raw_segment", "time_resolution"}:
                raise ValueError("Unexpected Slide segment fields")
            if segment["start_position"] != previous:
                raise ValueError("Disconnected Slide segments")
            if segment["shape"] not in {"-", "^", "v", "<", ">", "V", "p", "q", "pp", "qq", "s", "z", "w"}:
                raise ValueError("Unknown Slide shape")
            for key in ("start_position", "end_position"):
                if not isinstance(segment[key], str) or not re.fullmatch(r"[1-8]", segment[key]):
                    raise ValueError("Invalid segment position")
            via = segment["via_position"]
            if (segment["shape"] == "V") != (via is not None):
                raise ValueError("Only V requires an explicit via position")
            if via is not None and (not isinstance(via, str) or not re.fullmatch(r"[1-8]", via)):
                raise ValueError("Invalid via position")
            start, end = segment["start_time_s"], segment["end_time_s"]
            if start is None or end is None:
                if start is not None or end is not None or segment["time_resolution"] != "needs_geometry" or result.complete:
                    raise ValueError("Unresolved segment must mark bundle incomplete")
            else:
                if segment["time_resolution"] != "explicit_duration":
                    raise ValueError("Unexpected segment time resolution")
                if not finite(start) or not finite(end) or not event.start_time_s <= start <= end <= event.end_time_s or start != previous_time:
                    raise ValueError("Invalid segment interval")
                previous_time = end
            previous = segment["end_position"]
        if all(s["end_time_s"] is not None for s in path) and previous_time != event.end_time_s:
            raise ValueError("Segments do not cover Slide duration")
    for diagnostic in result.diagnostics:
        if diagnostic.severity not in {"error", "warning", "info"}:
            raise ValueError("Invalid diagnostic")
        if not isinstance(diagnostic.recovery_json, dict):
            raise ValueError("Diagnostic recovery must be a JSON object")
    if result.complete and any(d.severity == "error" for d in result.diagnostics):
        raise ValueError("Complete bundle contains errors")
    if result.complete and result.chart_end_time_s is None:
        raise ValueError("Complete chart lacks time metadata")
    last = max((e.end_time_s for e in result.events if e.kind != "timing"), default=None)
    if result.last_event_end_s != last:
        raise ValueError("last_event_end_s differs from note endpoints")



def validate_bundle(bundle: ChartBundle) -> None:
    chart = bundle.chart
    if chart.chart_type not in {None, "dx", "sd"}:
        raise ValueError("chart_type must be dx, sd or null")
    if chart.difficulty_index is not None and (type(chart.difficulty_index) is not int or chart.difficulty_index < 1):
        raise ValueError("difficulty_index must be a positive integer or null")
    for key in ("offset_s", "chart_end_time_s", "last_event_end_s", "audio_duration_s"):
        value = getattr(chart, key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise ValueError(f"Non-finite chart field: {key}")
    if bundle.complete and chart.offset_s is None:
        raise ValueError("Complete chart lacks offset metadata")
    validate_result(ParseResult(bundle.events, bundle.diagnostics, bundle.complete,
                                chart.chart_end_time_s, chart.last_event_end_s))
