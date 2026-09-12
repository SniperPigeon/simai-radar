"""The events-0.3 interchange model; no analysis or scoring policies."""

from dataclasses import dataclass, field
from typing import Any

from .constant_reference import MAJDATAPLAY_PIN as MAJDATAPLAY_PIN
from .constant_reference import MAJSIMAI_PIN as MAJSIMAI_PIN

SCHEMA_VERSION = "events-0.3"


@dataclass
class Event:
    event_id: int
    kind: str
    is_slide_head: bool | None = None
    timing_type: str | None = None
    slide_declare_time_s: float | None = None
    start_time_s: float = 0.0
    end_time_s: float = 0.0
    start_beat: str | None = None
    end_beat: str | None = None
    bpm: float | None = None
    position: str | None = None
    head_event_id: int | None = None
    slide_path_json: list[dict[str, Any]] | None = None
    is_break: bool | None = None
    is_ex: bool | None = None
    is_mine: bool | None = None
    flags_json: dict[str, Any] | None = None
    raw_token: str = ""
    source_start: int = 0
    source_end: int = 0
    source_line: int = 1
    source_column: int = 1


@dataclass
class Chart:
    source_name: str | None = None
    chart_type: str | None = None
    difficulty_index: int | None = None
    difficulty_label: str | None = None
    level_text: str | None = None
    title: str | None = None
    artist: str | None = None
    designer: str | None = None
    offset_s: float | None = 0.0
    chart_end_time_s: float | None = None
    last_event_end_s: float | None = None
    audio_duration_s: float | None = None
    metadata_json: dict[str, Any] = field(default_factory=dict)


@dataclass
class Diagnostic:
    severity: str
    code: str
    message: str
    source_start: int
    source_end: int
    source_line: int
    source_column: int
    raw_text: str
    recovery_json: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParseResult:
    """One chart's text semantics, with no song identity or filesystem metadata."""

    events: list[Event] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    complete: bool = True
    chart_end_time_s: float | None = None
    last_event_end_s: float | None = None


@dataclass
class ChartBundle:
    chart: Chart
    events: list[Event] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    complete: bool = True
