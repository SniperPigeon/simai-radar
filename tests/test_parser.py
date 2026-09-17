"""Semantic goldens and invariants, using only synthetic chart text."""

from dataclasses import asdict
from pathlib import Path
import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mairadar.io import bundle_directory_name, parse_file, read_bundle, write_bundle
from mairadar.model import Chart
from mairadar.validation import validate_bundle, validate_result
from mairadar.parser import parse_chart, parse_text
from mairadar.parser.source import number

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/parser"


def chart(text, **kwargs):
    result = parse_text(text, **kwargs)[0]
    validate_bundle(result)
    return result


def notes(bundle):
    return [event for event in bundle.events if event.kind != "timing"]


def exportable(text, *, title="Test", difficulty=5, chart_type="sd"):
    bundle = chart(text)
    bundle.chart.title = title
    bundle.chart.difficulty_index = difficulty
    bundle.chart.chart_type = chart_type
    return bundle


class TimelineTests(unittest.TestCase):
    def test_manual_prototype_semantics(self):
        p = ROOT / "res/examples/schema_v0.3/Schema Prototype-5-sd"
        bundle = parse_file(p / "maidata.txt")[0]
        self.assertTrue(bundle.complete)
        validate_bundle(bundle)
        with (p / "events.csv").open(newline="") as f:
            expected = list(csv.DictReader(f))
        self.assertEqual(len(bundle.events), len(expected))
        # Compare semantic identities rather than the hand-authored e001 IDs.
        actual_heads = {e.event_id: e for e in bundle.events}
        for actual, gold in zip(bundle.events, expected):
            for key in ("kind", "position", "raw_token", "timing_type"):
                self.assertEqual(getattr(actual, key) or "", gold[key], key)
            for key in ("start_time_s", "end_time_s", "slide_declare_time_s", "bpm"):
                self.assertEqual(getattr(actual, key), float(gold[key]) if gold[key] else None, key)
            for key in ("start_beat", "end_beat"):
                self.assertEqual(getattr(actual, key), gold[key])
            if actual.head_event_id:
                self.assertTrue(actual_heads[actual.head_event_id].is_slide_head)
            if actual.slide_path_json:
                self.assertEqual(actual.slide_path_json, json.loads(gold["slide_path_json"]))
        self.assertEqual(bundle.chart.offset_s, 0.25)

    def test_cross_measure_regression(self):
        bundle = parse_file(FIXTURES / "cross_measure_slot.txt")[0]
        self.assertEqual([e.start_time_s for e in notes(bundle)], [0, 0.5, 1, 1.5])
        self.assertEqual(bundle.chart.chart_end_time_s, 2.5)

    def test_comments_do_not_advance_time(self):
        left = parse_file(FIXTURES / "normal_four_taps.txt")[0]
        right = parse_file(FIXTURES / "comment_with_comma.txt")[0]
        self.assertEqual([(e.position, e.start_time_s) for e in notes(left)],
                         [(e.position, e.start_time_s) for e in notes(right)])
        self.assertEqual(right.chart.chart_end_time_s, 2)

    def test_redundant_bpm_preserves_notes(self):
        left = parse_file(FIXTURES / "bpm_base.txt")[0]
        right = parse_file(FIXTURES / "repeated_same_bpm.txt")[0]
        self.assertEqual([(e.position, e.start_time_s, e.start_beat) for e in notes(left)],
                         [(e.position, e.start_time_s, e.start_beat) for e in notes(right)])
        self.assertEqual(right.chart.chart_end_time_s, 4)
        self.assertEqual(sum(e.kind == "timing" for e in right.events), 8)

    def test_bpm_mapping_for_hold_crossing_change(self):
        b = chart("(120){4}1h[1:1],(240)2,3,E")
        held = notes(b)[0]
        self.assertEqual((held.start_time_s, held.end_time_s, held.end_beat), (0, 2, "7"))
        self.assertEqual(b.chart.chart_end_time_s, 1)
        self.assertEqual(b.chart.last_event_end_s, 2)

    def test_source_decimal_bpm_is_exact_on_beat_axis(self):
        b = chart("(123.45){7.5}1,2,3,E")
        self.assertEqual([e.start_beat for e in notes(b)], ["0", "8/15", "16/15"])

    def test_absolute_slot_uses_pinned_derived_division(self):
        b = chart("(120){#1}1,(240)2,E")
        self.assertEqual([e.start_time_s for e in notes(b)], [0, 1])
        self.assertEqual(b.chart.chart_end_time_s, 1.5)
        self.assertEqual([e.start_beat for e in notes(b)], ["0", "2"])

    def test_no_large_divider_repair(self):
        b = chart("(120){10000}1,2,E")
        self.assertTrue(b.complete)
        self.assertEqual(notes(b)[1].start_beat, "1/2500")
        self.assertAlmostEqual(b.chart.chart_end_time_s, 0.0004)

    def test_initial_and_trailing_rests(self):
        b = chart("(120){4},,1,,,E")
        self.assertEqual(notes(b)[0].start_time_s, 1)
        self.assertEqual(b.chart.chart_end_time_s, 2.5)
        self.assertEqual(b.chart.last_event_end_s, 1)

    def test_pseudo_each_and_same_key_duplicates(self):
        b = chart("(120){4}1/1`2/3`4,5,E")
        self.assertEqual([e.start_time_s for e in notes(b)], [0, 0, 1/64, 1/64, 1/32, 0.5])
        self.assertEqual(len({e.event_id for e in notes(b)}), 6)
        self.assertEqual(notes(b)[2].start_beat, "1/32")

    def test_compact_each_is_exactly_two_digits(self):
        self.assertEqual([e.position for e in notes(chart("(120)12/3,E"))], ["1", "2", "3"])
        b = chart("(120)123,E")
        self.assertFalse(b.complete)
        self.assertEqual(notes(b), [])

    def test_same_time_bpm_uses_final_declaration(self):
        b = chart("(120)(240){4}1,2,E")
        self.assertEqual([e.bpm for e in b.events if e.kind == "timing"], [120, 240])
        self.assertEqual(notes(b)[1].start_time_s, 0.25)

    def test_eof_has_no_synthetic_interval(self):
        for text, end in [("(120)1,2", 0.5), ("(120)1,2,", 1)]:
            with self.subTest(text=text):
                b = chart(text)
                self.assertTrue(b.complete)
                self.assertEqual(b.chart.chart_end_time_s, end)
                self.assertEqual([d.code for d in b.diagnostics], ["EOF_TERMINATOR"])
        self.assertTrue(chart("(120)1,E,2,").complete)

    def test_unknown_note_is_local_but_invalid_bpm_stops_timeline(self):
        b = chart("(120){4}1,Z,2,E")
        self.assertFalse(b.complete)
        self.assertEqual([(e.position, e.start_time_s) for e in notes(b)], [("1", 0), ("2", 1)])
        b = chart("(120){4}1,(oops)2,3,E")
        self.assertFalse(b.complete)
        self.assertEqual(len(notes(b)), 1)
        self.assertIsNone(b.chart.chart_end_time_s)

    def test_invalid_timing_is_never_guessed(self):
        for text in ["{4}1,E", "{#1}1,E", "(0)1,E", "(NaN)1,E", "(120){0}1,E", "(120){-2}1,E", "(120)1(240)2,E", "(1e308){1e308}1,E"]:
            with self.subTest(text=text):
                b = chart(text)
                self.assertFalse(b.complete)
                self.assertIsNone(b.chart.chart_end_time_s)

    def test_representable_subnormal_number_is_not_rejected_by_exponent(self):
        self.assertGreater(number("1e-309"), 0)
        self.assertEqual(number("0e-400", zero=True), 0)
        with self.assertRaises(ValueError):
            number("1e-400", zero=True)

    def test_default_subdivision_is_upstream_four(self):
        b = chart("(120)1,2,E")
        self.assertTrue(b.complete)
        self.assertEqual(notes(b)[1].start_time_s, 0.5)

    def test_extensions_are_explicitly_incomplete(self):
        for text, code in [("(120)||s 3/4\n1,E", "UNSUPPORTED_METER"),
                           ("(120)<HS*2>{4}1,E", "UNSUPPORTED_SPEED"),
                           ("(120)1Kfoo[4:1],E", "UNSUPPORTED_SLIDE")]:
            with self.subTest(text=text):
                b = chart(text)
                self.assertFalse(b.complete)
                self.assertIn(code, [d.code for d in b.diagnostics])


class NoteTests(unittest.TestCase):
    def test_repeated_boolean_modifiers_preserve_note_semantics_and_source(self):
        for repeated, canonical in (("2hh[12:1]", "2h[12:1]"),
                                    ("3xhh[2:1]", "3xh[2:1]"),
                                    ("Chxhhxx[4:1]", "Chx[4:1]")):
            with self.subTest(token=repeated):
                text = f"(120){repeated},E"
                result = parse_chart(text)
                expected = parse_chart(f"(120){canonical},E")
                self.assertTrue(result.complete, result.diagnostics)
                actual_notes = [e for e in result.events if e.kind != "timing"]
                expected_notes = [e for e in expected.events if e.kind != "timing"]
                self.assertEqual(len(actual_notes), 1)
                actual, target = actual_notes[0], expected_notes[0]
                self.assertEqual((actual.kind, actual.position, actual.is_ex, actual.end_time_s),
                                 (target.kind, target.position, target.is_ex, target.end_time_s))
                self.assertEqual(actual.raw_token, repeated)
                self.assertEqual(text[actual.source_start:actual.source_end], repeated)
        for token in ("2hhjunk[12:1]", "2hh[0:1]", "3xhh[2:1]junk"):
            self.assertFalse(parse_chart(f"(120){token},E").complete)

    def test_only_standalone_terminal_lowercase_e_is_an_end_alias(self):
        result = parse_chart("(120){4}Cf/E1,e")
        expected = parse_chart("(120){4}Cf/E1,E")
        self.assertTrue(result.complete)
        self.assertEqual(result.events, expected.events)
        self.assertEqual(result.chart_end_time_s, expected.chart_end_time_s)
        self.assertEqual([(d.code, d.severity) for d in result.diagnostics], [("LOWERCASE_TERMINATOR", "info")])
        for text in ("(120)e,1,E", "(120)1e,E", "(120)1/e"):
            self.assertFalse(parse_chart(text).complete)

    def test_hold_forms_and_zero_duration(self):
        cases = {"1h": 0, "1h[4:1]": 0.5, "1h[#1.25]": 1.25,
                 "1h[240#4:2]": 0.5, "Ch": 0, "Chf[4:2]": 1,
                 "A1h[4:0]": 0}
        for token, duration in cases.items():
            with self.subTest(token=token):
                b = chart(f"(120){{4}}{token},E")
                self.assertTrue(b.complete)
                self.assertEqual(notes(b)[0].end_time_s, duration)

    def test_touch_locations_and_flags(self):
        b = chart("(120){4}A1f/B2x/C/D3m/E8b,Chf[#2],E")
        self.assertTrue(b.complete)
        self.assertEqual([e.position for e in notes(b)], ["A1", "B2", "C", "D3", "E8", "C"])
        self.assertTrue(notes(b)[0].flags_json["hanabi"])
        self.assertTrue(notes(b)[1].is_ex)
        self.assertTrue(notes(b)[3].is_mine)
        self.assertTrue(notes(b)[4].is_break)

    def test_center_aliases_keep_source_but_use_one_position(self):
        b = chart("(120)C/C1/C2,C1fh[4:1],C2h,E")
        self.assertTrue(b.complete)
        self.assertEqual([e.position for e in notes(b)], ["C"] * 5)
        self.assertEqual([e.raw_token for e in notes(b)][:3], ["C", "C1", "C2"])
        self.assertEqual(notes(b)[3].kind, "touch_hold")
        self.assertEqual(notes(b)[3].end_time_s, 1)

    def test_force_star_and_sv_flag(self):
        b = chart("(120)1$$c/2bxm,E")
        first, second = notes(b)
        self.assertEqual(first.flags_json, {"force_star": True, "fake_rotate": True, "using_sv": False})
        self.assertTrue(second.is_break and second.is_ex and second.is_mine)

    def test_no_head_keeps_declare_time_and_wait(self):
        for marker in ("?", "!"):
            b = chart(f"(120){{4}},1{marker}-5[4:1],E")
            self.assertTrue(b.complete)
            [event] = notes(b)
            self.assertEqual(event.kind, "slide")
            self.assertIsNone(event.head_event_id)
            self.assertEqual((event.slide_declare_time_s, event.start_time_s, event.end_time_s), (0.5, 1, 1.5))
            self.assertEqual(event.flags_json["no_head_marker"], marker)

    def test_no_head_branches_keep_independent_durations(self):
        b = chart("(120){4},1?-5[4:1]*!-7[4:2],E")
        self.assertTrue(b.complete)
        a, c = notes(b)
        self.assertEqual([e.kind for e in notes(b)], ["slide", "slide"])
        self.assertIsNone(a.head_event_id)
        self.assertIsNone(c.head_event_id)
        self.assertEqual(a.slide_declare_time_s, c.slide_declare_time_s)
        self.assertEqual((a.end_time_s, c.end_time_s), (1.5, 2))
        self.assertEqual((a.flags_json["no_head_marker"], c.flags_json["no_head_marker"]), ("?", "!"))

    def test_slide_duration_forms(self):
        cases = {"4:1": (0.5, 0.5), "240#4:2": (0.25, 0.5), "240#1.5": (0.25, 1.5),
                 "0.2##4:1": (0.2, 0.5), "0.2##1.5": (0.2, 1.5),
                 "0.2##240#4:2": (0.2, 0.5), "0##0": (0, 0)}
        for duration, (wait, length) in cases.items():
            with self.subTest(duration=duration):
                b = chart(f"(120){{4}}1-5[{duration}],E")
                self.assertTrue(b.complete)
                event = next(e for e in b.events if e.kind == "slide")
                self.assertEqual(event.start_time_s, wait)
                self.assertAlmostEqual(event.end_time_s, wait + length)

    def test_shared_head_and_independent_path_flags(self):
        b = chart("(120){4}1bx-5[4:1]b*-7[4:2]m,E")
        self.assertTrue(b.complete)
        head, a, c = notes(b)
        self.assertTrue(head.is_slide_head and head.is_break and head.is_ex)
        self.assertEqual(a.head_event_id, c.head_event_id)
        self.assertEqual(a.head_event_id, head.event_id)
        self.assertTrue(a.is_break)
        self.assertFalse(a.is_mine or a.is_ex)
        self.assertTrue(c.is_mine)
        self.assertFalse(c.is_break or c.is_ex)
        self.assertEqual(a.raw_token, c.raw_token)

    def test_tap_head_slide(self):
        b = chart("(120)1@-5[4:1],E")
        self.assertTrue(notes(b)[0].flags_json["tap_head"])
        self.assertTrue(notes(b)[0].is_slide_head)

    def test_all_basic_shapes(self):
        cases = {
            "1-5": (None, 20), "1^3": (None, 16), "1v3": (None, 20),
            "1<1": (None, 64), "1>5": (None, 32), "1p4": (None, 25),
            "1q4": (None, 43), "1pp4": (None, 50), "1qq4": (None, 49),
            "1s5": (None, 31), "1z5": (None, 31), "1w5": (None, 12),
            "1V75": ("7", 29), "1v1": (None, 21),
        }
        for token, (via, bar_count) in cases.items():
            with self.subTest(token=token):
                b = chart(f"(120){token}[4:1],E")
                self.assertTrue(b.complete, b.diagnostics)
                path = next(e for e in b.events if e.kind == "slide").slide_path_json
                self.assertEqual(len(path), 1)
                self.assertEqual(path[0]["via_position"], via)
                self.assertEqual(path[0]["bar_count"], bar_count)
                self.assertNotIn("time_resolution", path[0])

    def test_left_right_circle_bar_counts_follow_player_view(self):
        cases = {"1>2": 8, "1<2": 56, "5<6": 8, "5>6": 56}
        for token, bar_count in cases.items():
            with self.subTest(token=token):
                b = chart(f"(120){token}[4:1],E")
                path = next(e for e in b.events if e.kind == "slide").slide_path_json
                self.assertEqual(path[0]["bar_count"], bar_count)

    def test_capital_v_uses_l_prefab_and_mirrored_bar_counts(self):
        cases = {
            "1V72": 33, "1V73": 35, "1V74": 33, "1V75": 29,
            "1V35": 29, "1V36": 33, "1V37": 35, "1V38": 33,
        }
        for token, bar_count in cases.items():
            with self.subTest(token=token):
                b = chart(f"(120){token}[4:1],E")
                path = next(e for e in b.events if e.kind == "slide").slide_path_json
                self.assertEqual(path[0]["bar_count"], bar_count)

    def test_chain_distributes_total_time_by_player_bar_counts(self):
        cases = [
            ("1-3-5[4:3]", 2, [0.5, 1.25, 2]),
            ("1-3[4:1]-5[4:2]", 2, [0.5, 1.25, 2]),
            ("1-3[4:1]-5[240#4:2]", 1.25, [0.25, 0.75, 1.25]),
            ("1-5>1[4:3]", 2, [0.5, 14 / 13, 2]),
            ("1-5[4:1]>1[4:2]", 2, [0.5, 14 / 13, 2]),
        ]
        for token, end, boundaries in cases:
            with self.subTest(token=token):
                b = chart(f"(120){token},E")
                self.assertTrue(b.complete)
                event = next(e for e in b.events if e.kind == "slide")
                self.assertEqual(event.end_time_s, end)
                self.assertEqual(len(event.slide_path_json), 2)
                actual_boundaries = [event.slide_path_json[0]["start_time_s"]]
                actual_boundaries.extend(s["end_time_s"] for s in event.slide_path_json)
                for actual, expected in zip(actual_boundaries, boundaries, strict=True):
                    self.assertAlmostEqual(actual, expected)
                self.assertEqual(
                    [s["bar_count"] for s in event.slide_path_json],
                    [14, 14] if token.startswith("1-3") else [20, 32],
                )
                self.assertNotIn("SLIDE_GEOMETRY_PENDING", [d.code for d in b.diagnostics])

    def test_invalid_geometry_duration_and_suffixes_are_diagnosed(self):
        tokens = ["0", "9", "C3", "A9", "1junk", "1[4:1]", "1h[]", "1h[0:1]", "1h[#-1]",
                  "1-2[4:1]", "1^5[4:1]", "1w3[4:1]", "1V25[4:1]", "1-5[#1]",
                  "1-3[4:1]-5", "1-3-5[4:1]-7[4:1]", "1w5-7[4:1]", "1-5[4:1]*",
                  "1?-5[4:1]@", "1f", "1?", "1h[4:1", "1h[4:1]]", "1-5[4:1]oops"]
        for token in tokens:
            with self.subTest(token=token):
                b = chart(f"(120){token},E")
                self.assertFalse(b.complete)
                self.assertEqual(notes(b), [])


class EnvelopeAndIOTests(unittest.TestCase):
    def test_raw_chart_api_needs_no_metadata_files_or_hashes(self):
        text = "(120){4}1?-5[4:1],2,E"
        with patch("builtins.open", side_effect=AssertionError("unexpected file I/O")), patch(
            "hashlib.sha256", side_effect=AssertionError("unexpected identity hash")
        ):
            result = parse_chart(text)
        validate_result(result)
        self.assertTrue(result.complete)
        self.assertNotIn("chart", asdict(result))
        self.assertEqual(result.events, parse_text(text)[0].events)
        self.assertEqual(result.events, parse_chart(text).events)
        self.assertTrue(all("chart_id" not in asdict(e) for e in result.events))

    def test_sequential_ids_after_sorting_and_partial_token_failure(self):
        result = parse_chart("(120){4}1-5[2##1],2-6[0##1]/3,1-5[1e308##1e308],4,E")
        validate_result(result)
        self.assertFalse(result.complete)
        self.assertEqual([e.event_id for e in result.events], list(range(1, len(result.events) + 1)))
        by_id = {e.event_id: e for e in result.events}
        for e in result.events:
            if e.head_event_id is not None:
                head = by_id[e.head_event_id]
                self.assertTrue(head.is_slide_head)
                self.assertEqual(e.position, head.position)
                self.assertEqual(e.slide_declare_time_s, head.start_time_s)
        self.assertEqual(result.events[-1].kind, "slide")

    def test_directory_names_distinguish_dx_sd_without_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            for kind in ("dx", "sd"):
                bundle = exportable("(120)1,E", title="同名曲", difficulty=6, chart_type=kind)
                target = write_bundle(bundle, temp)
                self.assertEqual(target.name, f"同名曲-6-{kind}")
                self.assertEqual(read_bundle(target), bundle)
                with (target / "events.csv").open(newline="") as f:
                    rows = list(csv.DictReader(f))
                self.assertEqual(rows[0]["event_id"], "1")
                self.assertNotIn("chart_id", rows[0])
            self.assertEqual(len(list(Path(temp).iterdir())), 2)

    def test_export_keeps_missing_metadata_empty(self):
        result = parse_chart("(120)1,E")
        self.assertTrue(result.complete)
        b = exportable("(120)1,E", chart_type=None)
        with tempfile.TemporaryDirectory() as temp:
            target = write_bundle(b, temp)
            self.assertIsNone(read_bundle(target).chart.chart_type)
        metadata = Chart(title="A/B:C", difficulty_index=5, chart_type="dx")
        self.assertEqual(bundle_directory_name(metadata), "A_B_C-5-dx")
        self.assertEqual(metadata.title, "A/B:C")
        self.assertEqual(bundle_directory_name(Chart()), "--")

    def test_export_type_comes_from_explicit_metadata(self):
        for name in ("cabinet", "cabinate"):
            b = chart(f"&title=Test\n&{name}=DX\n|| comment\n&inote_5=(120)1,E")
            self.assertTrue(b.complete)
            self.assertEqual(b.chart.chart_type, "dx")
        b = chart("&title=[DX]Just a title\n&inote_5=(120)A1,E")
        self.assertIsNone(b.chart.chart_type)

    def test_scalar_metadata_comments_do_not_change_values(self):
        b = chart("&first=0.25\n|| comment\n&title=T\n|| comment\n&inote_5=(120)1,E")
        self.assertTrue(b.complete)
        self.assertEqual(b.chart.offset_s, 0.25)
        self.assertEqual(b.chart.title, "T")
        b = chart("&title=T\nunexpected\n&inote_5=(120)1,E")
        self.assertFalse(b.complete)
        self.assertIn("UNEXPECTED_METADATA_TEXT", [d.code for d in b.diagnostics])

    def test_metadata_selection_unknown_values_and_offset(self):
        text = "&title=名字🎵\n&artist=A\n&des=common\n&des_6=special\n&first=-0.25\n&first_6=0.5\n&custom=kept\n&lv_5=13.7\n&inote_5=(120)1,E\n&lv_6=14+\n&inote_6=(120)2,E\n"
        a, b = parse_text(text)
        self.assertEqual(a.chart.level_text, "13.7")
        self.assertEqual(a.chart.offset_s, -0.25)
        self.assertEqual(b.chart.offset_s, 0.5)
        self.assertEqual(b.chart.designer, "special")
        self.assertEqual(a.chart.metadata_json["custom"], "kept")
        self.assertEqual(notes(a)[0].start_time_s, 0)
        self.assertNotEqual(a.chart.difficulty_index, b.chart.difficulty_index)
        self.assertEqual(parse_text(text, difficulties=[6])[0].chart.difficulty_index, 6)

    def test_missing_empty_duplicate_and_invalid_metadata(self):
        for text, kwargs, code in [
            ("&title=T", {}, "MISSING_CHART"),
            ("&inote_5=(120)1,E", {"difficulties": [6]}, "MISSING_CHART"),
            ("&inote_5=\n", {}, "EMPTY_CHART"),
            ("&first=NaN\n&inote_5=(120)1,E", {}, "INVALID_OFFSET"),
            ("&title=A\n&title=B\n&inote_5=(120)1,E", {}, "DUPLICATE_FIELD"),
        ]:
            with self.subTest(text=text):
                b = chart(text, **kwargs)
                self.assertFalse(b.complete)
                self.assertIn(code, [d.code for d in b.diagnostics])

    def test_source_locations_unicode_crlf_and_whitespace(self):
        text = "&title=🎵中文\r\n&inote_5=\r\n(120){4}1 b,|| comment , 🎵\r\n C h [#1],E\r\n"
        b = chart(text)
        self.assertTrue(b.complete)
        self.assertEqual([e.raw_token for e in notes(b)], ["1 b", "C h [#1]"])
        for e in b.events:
            self.assertEqual(text[e.source_start:e.source_end], e.raw_token)
            self.assertEqual(e.source_line, text[:e.source_start].count("\n") + 1)
            self.assertEqual(e.source_column, e.source_start - text.rfind("\n", 0, e.source_start))
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "maidata.txt"
            p.write_bytes(b"\xef\xbb\xbf" + text.encode())
            from_file = parse_file(p)[0]
            self.assertEqual([e.source_start for e in b.events], [e.source_start for e in from_file.events])

    def test_ids_are_local_consecutive_and_independent_of_file_path(self):
        text = "(120)1/1,1-5[4:1]*-7[4:1],E"
        a = chart(text)
        self.assertEqual(a, chart(text))
        self.assertEqual([e.event_id for e in a.events], list(range(1, len(a.events) + 1)))
        with tempfile.TemporaryDirectory() as temp:
            for name in ("a", "b"):
                p = Path(temp) / name / "maidata.txt"
                p.parent.mkdir()
                p.write_text(text)
            x = parse_file(Path(temp) / "a/maidata.txt", source_name="a/maidata.txt")[0]
            y = parse_file(Path(temp) / "b/maidata.txt", source_name="b/maidata.txt")[0]
            self.assertEqual(x.events, y.events)
            self.assertNotEqual(x.chart.source_name, y.chart.source_name)
        self.assertNotIn("chart_id", asdict(a.events[0]))
        self.assertNotIn("chart_id", asdict(a.chart))

    def test_round_trip_complete_and_partial(self):
        for text in ["(120)1bx/Chf[#1],1?-5[4:1],E", "(120)1-3-5[4:1],Z,E"]:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as temp:
                original = exportable(text)
                target = write_bundle(original, temp)
                self.assertEqual({p.name for p in target.iterdir()}, {"events.csv", "charts.csv", "diagnostics.csv", "manifest.json"})
                self.assertEqual(read_bundle(target), original)
                self.assertEqual(len(asdict(original.events[0])), 22)

    def test_corrupt_csv_fails_checksum(self):
        with tempfile.TemporaryDirectory() as temp:
            target = write_bundle(exportable("(120)1,E"), temp)
            p = target / "events.csv"
            p.write_text(p.read_text().replace(",tap,", ",hold,"))
            with self.assertRaisesRegex(ValueError, "Checksum"):
                read_bundle(target)

    def test_safe_overwrite_and_rollback(self):
        with tempfile.TemporaryDirectory() as temp:
            b = exportable("(120)1,E")
            target = write_bundle(b, temp)
            with self.assertRaises(FileExistsError):
                write_bundle(b, temp)
            original_rename = os.rename
            calls = 0
            def interrupted(src, dst):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated install failure")
                return original_rename(src, dst)
            with patch("mairadar.io.os.rename", side_effect=interrupted):
                with self.assertRaises(OSError):
                    write_bundle(b, temp, overwrite=True)
            self.assertEqual(read_bundle(target), b)
            self.assertEqual(write_bundle(b, temp, overwrite=True), target)
            (target / "my-notes.txt").write_text("keep")
            with self.assertRaisesRegex(ValueError, "extra files"):
                write_bundle(b, temp, overwrite=True)
            self.assertEqual((target / "my-notes.txt").read_text(), "keep")


class CLITests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "scripts/mairadar.py"), "--mode", "full", *map(str, args)],
                              text=True, capture_output=True, cwd=ROOT)

    def test_directory_discovery_partial_exit_and_summary_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, text in [("good", "(120)1,E"), ("bad", "(120)Z,E")]:
                p = root / "input" / name / "maidata.txt"
                p.parent.mkdir(parents=True)
                p.write_text(f"&title={name}\n&cabinet=SD\n&inote_5=" + text)
            result = self.run_cli("-i", root / "input", "-o", root / "output")
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("charts=2 failed_or_partial=1", result.stdout)
            with (root / "output/charts.csv").open(encoding="utf-8-sig") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 2)
            self.assertEqual(sum(row["status"] == "ok" for row in rows), 1)

    def test_difficulty_option_and_existing_report_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            p = root / "maidata.txt"
            p.write_text("&title=Test\n&cabinet=DX\n&inote_5=(120)1,E\n&inote_6=(120)2,E")
            args = ["-i", p, "-o", root / "out", "-d", "6"]
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = root / "out/charts.csv"
            with report.open(encoding="utf-8-sig") as stream:
                [row] = list(csv.DictReader(stream))
            self.assertEqual(row["difficulty_index"], "6")
            original = report.read_bytes()
            self.assertEqual(self.run_cli(*args).returncode, 1)
            self.assertEqual(report.read_bytes(), original)

    def test_invalid_usage_and_unreadable_utf8_fail(self):
        self.assertEqual(self.run_cli("-i", "no-such-file", "-o", "unused").returncode, 1)
        self.assertEqual(self.run_cli("--unknown").returncode, 2)
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "maidata.txt"
            p.write_bytes(b"\xff\xfe")
            self.assertEqual(self.run_cli("-i", p, "-o", Path(temp) / "out").returncode, 1)

    def test_raw_file_export_type_override(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "Example.simai"
            path.write_text("(120)1,E")
            result = self.run_cli("-i", path, "-o", root / "out", "-d", "5", "--chart-type", "dx")
            self.assertEqual(result.returncode, 0, result.stderr)
            with (root / "out/charts.csv").open(encoding="utf-8-sig") as stream:
                [row] = list(csv.DictReader(stream))
            self.assertEqual((row["title"], row["chart_type"], row["difficulty_index"]),
                             ("", "dx", "5"))
            self.assertEqual(row["status"], "ok")

    def test_importing_parser_does_not_import_export_or_cli(self):
        result = subprocess.run(
            [sys.executable, "-c", "import sys; from mairadar.parser import parse_chart; assert 'mairadar.io' not in sys.modules; assert 'mairadar.cli' not in sys.modules; assert parse_chart('(120)1,E').complete"],
            cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
