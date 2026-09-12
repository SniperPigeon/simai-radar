"""Fallback DX/SD classification after parsing, using the configured product rules."""

from .model import Event, ParseResult


def _is_dx_event(event: Event) -> bool:
    if event.kind == "timing":
        return False
    if event.kind in {"touch", "touch_hold"} or event.is_ex:
        return True
    if event.kind == "slide":
        return bool(event.is_break or len(event.slide_path_json or ()) > 1)
    if event.is_break:
        if event.kind == "hold":
            return True
        flags = event.flags_json or {}
        if event.kind == "tap" and (
            flags.get("force_star") or (event.is_slide_head and not flags.get("tap_head"))
        ):
            return True
    return False


def detect_chart_type(result: ParseResult) -> str | None:
    """Return DX on a matching event, otherwise SD; incomplete input stays unknown.

    This fallback classifies chart contents, not the song's official release type.
    Explicit caller or metadata types take priority in the surrounding adapter.
    """
    if not result.complete:
        return None
    return "dx" if any(_is_dx_event(event) for event in result.events) else "sd"
