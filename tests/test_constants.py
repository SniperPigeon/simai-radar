"""Source labels and exact matching must not invent or merge chart identities."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mairadar.constants import (
    ConstantCatalog, ConstantTable, fetch_snapshot, main, read_rows,
    write_json, write_rows,
)


def snapshot(songs):
    return {"schema_version": "otoge-constants-1", "source_url": "https://example.invalid/data.json",
            "region": "jp", "fetched_at": "2026-09-17T00:00:00Z", "songs": songs}


class ConstantsTests(unittest.TestCase):
    def test_exact_unicode_title_and_unique_title_ignores_type(self):
        catalog = ConstantCatalog(snapshot([
            {"title": "Ａ & 曲", "dx_lev_mas": "13+", "dx_lev_mas_i": "13.8"},
        ]))
        for kind in (None, "sd", "dx"):
            result = catalog.match("Ａ & 曲", 5, kind)
            self.assertEqual(result["official_constant"], 13.8)
            self.assertEqual(result["matched_chart_type"], "dx")
        for title in ("A & 曲", "Ａ & 曲 ", "Ａ & 曲 [DX]", "Ａ &amp; 曲"):
            self.assertEqual(catalog.match(title, 5)["match_status"], "title_not_found")

    def test_title_conflict_resolves_type_before_difficulty(self):
        catalog = ConstantCatalog(snapshot([
            {"title": "Same", "lev_mas": "12", "lev_mas_i": "12.4",
             "lev_remas": "13+", "lev_remas_i": "13.8",
             "dx_lev_mas": "14", "dx_lev_mas_i": "14.4"},
        ]))
        self.assertEqual(catalog.match("Same", 5, "std")["official_constant"], 12.4)
        self.assertEqual(catalog.match("Same", 5, "DX")["official_constant"], 14.4)
        self.assertEqual(catalog.match("Same", 5)["match_status"], "ambiguous_type")
        self.assertEqual(catalog.match("Same", 6, "dx")["match_status"], "difficulty_not_found")

    def test_duplicate_source_rows_are_ambiguous_even_with_equal_values(self):
        song = {"title": "Link", "lev_mas": "12", "lev_mas_i": "12.4"}
        catalog = ConstantCatalog(snapshot([song, dict(song)]))
        self.assertEqual(catalog.match("Link", 5, "sd")["match_status"], "ambiguous_title")
        self.assertEqual(catalog.match("Link", 5, "dx")["match_status"], "type_not_found")

    def test_never_uses_display_levels_or_approximate_constants(self):
        for raw, status in ((None, "constant_missing"), ("", "constant_missing"),
                            ("13.6?", "invalid_constant"), ("NaN", "invalid_constant"),
                            (True, "invalid_constant"), (0, "invalid_constant")):
            with self.subTest(raw=raw):
                catalog = ConstantCatalog(snapshot([
                    {"title": "X", "lev_mas": "13+", "lev_mas_i": raw},
                ]))
                self.assertEqual(catalog.match("X", 5)["match_status"], status)
                self.assertIsNone(catalog.match("X", 5)["official_constant"])
                self.assertEqual(catalog.match("X", 7)["match_status"], "unsupported_difficulty")
                self.assertEqual(catalog.match("X", 5.5)["match_status"], "unsupported_difficulty")

    def test_cli_preserves_all_rows_and_reports_partial_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_rows(root / "charts.csv", [
                {"title": "X", "difficulty_index": 5, "chart_type": "sd", "note_raw": 3},
                {"title": "X", "difficulty_index": 5, "chart_type": "sd", "note_raw": 4},
                {"title": "Y", "difficulty_index": 5, "chart_type": "dx", "note_raw": 5},
            ])
            write_json(root / "source.json", snapshot([
                {"title": "X", "lev_mas": "13+", "lev_mas_i": "13.8"},
            ]))
            args = ["match", "--input", str(root), "--snapshot", str(root / "source.json"),
                    "--output", str(root / "constants.csv")]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 1)
            rows = read_rows(root / "constants.csv")
            self.assertEqual(len(rows), 3)
            self.assertEqual([r["note_raw"] for r in rows], ["3", "4", "5"])
            self.assertEqual(rows[-1]["match_status"], "title_not_found")
            before = (root / "constants.csv").read_bytes()
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(args), 1)
            self.assertEqual((root / "constants.csv").read_bytes(), before)

    def test_table_does_not_attach_another_variant_or_collapse_duplicate_rows(self):
        row = {"title": "X", "difficulty_index": 5, "chart_type": "sd",
               "official_constant": 13.8, "match_status": "matched"}
        self.assertEqual(ConstantTable([row]).lookup("X", 5, "ST")["officialConstant"], 13.8)
        self.assertIsNone(ConstantTable([row]).lookup("X", 5, "dx")["officialConstant"])
        self.assertEqual(ConstantTable([row, row]).lookup("X", 5, "sd")["constantMatchStatus"], "table_ambiguous")

    def test_visualizer_and_metadata_bundle_adapters(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "data").mkdir()
            write_json(root / "data/songs.json", {
                "schemaVersion": "mairadar-visualizer-1", "songs": [
                    {"title": "X", "charts": [{"difficulty": 6, "kind": "ST", "status": "ok",
                                                "rawScores": {"note": 3.5}}]},
                ],
            })
            [row] = read_rows(root)
            self.assertEqual(row["chart_type"], "sd")
            self.assertEqual(row["note_raw"], 3.5)
            bundle = root / "parsed" / "X-6-sd"
            write_rows(bundle / "charts.csv", [{"title": "X", "difficulty_index": 6, "chart_type": "sd"}])
            self.assertEqual(read_rows(root / "parsed")[0]["title"], "X")

    def test_fetch_uses_region_endpoint_and_saves_provenance(self):
        response = io.StringIO(json.dumps([{"title": "X", "lev_mas": "13", "lev_mas_i": "13.4"}]))
        response.headers = {"Last-Modified": "Thu, 17 Sep 2026 00:00:00 GMT"}
        with patch("mairadar.constants.urlopen", return_value=response) as fetch:
            result = fetch_snapshot("intl")
        self.assertTrue(fetch.call_args.args[0].full_url.endswith("music-ex-intl.json"))
        self.assertEqual(result["region"], "intl")
        self.assertIn("2026", result["last_modified"])


if __name__ == "__main__":
    unittest.main()
