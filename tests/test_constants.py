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
            "fetched_at": "2026-09-17T00:00:00Z", "songs": songs}


def with_sources(first, second):
    return {
        "schema_version": "otoge-constants-2", "fetched_at": "2026-09-17T00:00:00Z",
        "sources": [
            {"source_url": "https://example.invalid/first.json", "songs": first},
            {"source_url": "https://example.invalid/second.json", "songs": second},
        ],
    }


class ConstantsTests(unittest.TestCase):
    def test_normalized_title_and_unique_title_ignore_type(self):
        catalog = ConstantCatalog(snapshot([
            {"title": "Ａ & 曲", "dx_lev_mas": "13+", "dx_lev_mas_i": "13.8"},
        ]))
        for kind in (None, "sd", "dx"):
            result = catalog.match("Ａ & 曲", 5, kind)
            self.assertEqual(result["official_constant"], 13.8)
            self.assertEqual(result["matched_chart_type"], "dx")
        for title in ("A & 曲", "Ａ & 曲 ", "Ａ & 曲 [DX]", "A曲[ST]", "Ａ & 曲 [STD]", "Ａ & 曲 [SD]"):
            self.assertEqual(catalog.match(title, 5)["official_constant"], 13.8)
            self.assertEqual(catalog.match(title, 5)["title_match_method"], "normalized")
        for title in ("Ａ &amp; 曲", "Ａ別曲", "Ａ & 曲 (Remix)"):
            self.assertEqual(catalog.match(title, 5)["match_status"], "title_not_found")

    def test_artist_disambiguates_same_title_and_type_without_fuzzy_fallback(self):
        catalog = ConstantCatalog(snapshot([
            {"title": "Link", "artist": "Artist A", "lev_mas": "12", "lev_mas_i": "12.4"},
            {"title": "Link", "artist": "Artist B", "lev_mas": "13", "lev_mas_i": "13.4"},
        ]))
        self.assertEqual(catalog.match("Link", 5, "sd", "Artist・B")["official_constant"], 13.4)
        self.assertEqual(catalog.match("Link", 5, None, "Artist A")["official_constant"], 12.4)
        self.assertEqual(catalog.match("Link", 5, "sd")["match_status"], "ambiguous_title")
        self.assertEqual(catalog.match("Link", 5, "sd", "Artist C")["match_status"], "artist_not_found")

    def test_symbol_collisions_remain_ambiguous_and_exact_matches_win(self):
        catalog = ConstantCatalog(snapshot([
            {"title": "ECHO", "artist": "A", "lev_mas": "12", "lev_mas_i": "12.4"},
            {"title": "ECHO,", "artist": "B", "lev_mas": "13", "lev_mas_i": "13.4"},
            {"title": "「」", "artist": "C", "lev_mas": "14", "lev_mas_i": "14.4"},
        ]))
        self.assertEqual(catalog.match("ECHO", 5)["official_constant"], 12.4)
        self.assertEqual(catalog.match("ECHO!", 5, "sd")["match_status"], "ambiguous_title")
        self.assertEqual(catalog.match("ECHO!", 5, "sd", "B")["official_constant"], 13.4)
        self.assertEqual(catalog.match("「」", 5)["official_constant"], 14.4)
        self.assertEqual(catalog.match("", 5)["match_status"], "title_not_found")
        self.assertEqual(catalog.match("!!!", 5)["match_status"], "title_not_found")

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
        self.assertEqual(catalog.match("Link", 5, "dx")["match_status"], "ambiguous_title")

    def test_never_uses_display_levels_or_approximate_constants(self):
        for raw, status in ((None, "constant_estimated"), ("", "constant_estimated"),
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

    def test_web_estimate_is_separate_from_explicit_training_label(self):
        for level, expected in (("13", 13.0), ("13+", 13.6)):
            catalog = ConstantCatalog(snapshot([{"title": "X", "lev_mas": level, "lev_mas_i": ""}]))
            match = catalog.match("X", 5)
            self.assertIsNone(match["official_constant"])
            self.assertEqual(match["display_constant"], expected)
            self.assertEqual(match["constant_value_kind"], "display_estimate")
            row = {"title": "X", "difficulty_index": 5, "chart_type": "sd", **match}
            self.assertEqual(ConstantTable([row]).lookup("X", 5, "sd")["displayConstant"], expected)

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
        table = ConstantTable([{**row, "artist": "A"}, {**row, "artist": "B", "official_constant": 14.2}])
        self.assertEqual(table.lookup("X", 5, "sd", "B")["officialConstant"], 14.2)

    def test_visualizer_and_metadata_bundle_adapters(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "data").mkdir()
            write_json(root / "data/songs.json", {
                "schemaVersion": "mairadar-visualizer-2", "songs": [
                    {"title": "X", "artist": "Test artist", "charts": [{"difficulty": 6, "kind": "ST", "status": "ok",
                                                "rawFeatures": {"note": 3.5}}]},
                ],
            })
            [row] = read_rows(root)
            self.assertEqual(row["chart_type"], "sd")
            self.assertEqual(row["note_raw"], 3.5)
            self.assertEqual(row["artist"], "Test artist")
            bundle = root / "parsed" / "X-6-sd"
            write_rows(bundle / "charts.csv", [{"title": "X", "difficulty_index": 6, "chart_type": "sd"}])
            self.assertEqual(read_rows(root / "parsed")[0]["title"], "X")

    def test_sources_supply_values_without_region_or_availability_fields(self):
        catalog = ConstantCatalog(with_sources(
            [{"title": "Current", "lev_mas": "14", "lev_mas_i": "14.2"}],
            [{"title": "Current", "lev_mas": "13", "lev_mas_i": "13.2"},
             {"title": "Old", "artist": "A", "lev_mas": "13", "lev_mas_i": "13.4",
              "deleted_date": "20240912"}],
        ))
        self.assertEqual(catalog.match("Current", 5)["official_constant"], 14.2)
        result = catalog.match("Old [ST]", 5, "sd", "A")
        self.assertEqual(result["official_constant"], 13.4)
        self.assertEqual(result["constant_source"], "https://example.invalid/second.json")
        self.assertNotIn("constant_region", result)
        self.assertNotIn("constant_deleted_date", result)
        self.assertNotIn("constant_source_kind", result)
        self.assertEqual(catalog.match("Missing", 5)["match_status"], "title_not_found")

    def test_any_source_can_supply_missing_or_invalid_values_and_difficulties(self):
        second = {"title": "X", "lev_mas": "13", "lev_mas_i": "13.2",
                  "lev_remas": "14", "lev_remas_i": "14.2"}
        for first, difficulty, expected in (
            ([{"title": "X", "lev_mas": "13"}], 5, 13.2),
            ([{"title": "X", "lev_mas": "13", "lev_mas_i": "NaN"}], 5, 13.2),
            ([{"title": "X", "lev_mas": "13"}], 6, 14.2),
        ):
            with self.subTest(first=first, difficulty=difficulty):
                result = ConstantCatalog(with_sources(first, [second])).match("X", difficulty, "sd")
                self.assertEqual(result["official_constant"], expected)
                self.assertEqual(result["match_status"], "matched")

    def test_exact_source_title_wins_over_another_sources_normalized_title(self):
        catalog = ConstantCatalog(with_sources(
            [{"title": "Echo!", "lev_mas": "14", "lev_mas_i": "14.2"}],
            [{"title": "Echo", "lev_mas": "13", "lev_mas_i": "13.2"}],
        ))
        self.assertEqual(catalog.match("Echo", 5)["official_constant"], 13.2)

    def test_type_and_artist_disambiguate_across_sources(self):
        catalog = ConstantCatalog(with_sources(
            [{"title": "X", "artist": "A", "lev_mas": "14", "lev_mas_i": "14.2"}],
            [{"title": "X", "artist": "A", "dx_lev_mas": "13", "dx_lev_mas_i": "13.2"}],
        ))
        self.assertEqual(catalog.match("X", 5, "dx", "A")["official_constant"], 13.2)
        self.assertEqual(catalog.match("X", 5)["match_status"], "ambiguous_type")
        catalog = ConstantCatalog(with_sources(
            [{"title": "X", "artist": "A", "lev_mas": "14", "lev_mas_i": "14.2"}],
            [{"title": "X", "artist": "B", "lev_mas": "13", "lev_mas_i": "13.2"}],
        ))
        self.assertEqual(catalog.match("X", 5, "sd", "B")["official_constant"], 13.2)
        self.assertEqual(catalog.match("X", 5, "sd")["match_status"], "ambiguous_title")

    def test_estimates_stay_out_of_official_labels_across_sources(self):
        result = ConstantCatalog(with_sources(
            [{"title": "Current", "lev_mas": "13"}],
            [{"title": "Old", "lev_mas": "12+", "deleted_date": "20240912"}],
        )).match("Old", 5)
        self.assertEqual(result["match_status"], "constant_estimated")
        self.assertEqual(result["display_constant"], 12.6)
        self.assertIsNone(result["official_constant"])

    def test_existing_downloaded_snapshots_remain_usable(self):
        data = snapshot([{"title": "Current", "lev_mas": "13", "lev_mas_i": "13.4"}])
        data["deleted_snapshot"] = snapshot([{"title": "Old", "lev_mas": "12", "lev_mas_i": "12.4"}])
        data["region"] = "jp"
        data["deleted_snapshot"]["source_kind"] = "deleted"
        result = ConstantCatalog(data).match("Old", 5)
        self.assertEqual(result["official_constant"], 12.4)
        self.assertNotIn("constant_region", result)
        with self.assertRaisesRegex(ValueError, "requires sources"):
            ConstantCatalog({"schema_version": "otoge-constants-2", "sources": []})

    def test_fetch_always_uses_both_japanese_sources_and_propagates_download_errors(self):
        def response(title):
            result = io.StringIO(json.dumps([{"title": title, "lev_mas": "13", "lev_mas_i": "13.4"}]))
            result.headers = {"Last-Modified": "Thu, 17 Sep 2026 00:00:00 GMT"}
            return result

        with patch("mairadar.constants.urlopen", side_effect=[response("Current"), response("Old")]) as request:
            data = fetch_snapshot()
        self.assertEqual(request.call_count, 2)
        self.assertEqual(data["schema_version"], "otoge-constants-2")
        self.assertEqual(len(data["sources"]), 2)
        self.assertTrue(data["sources"][0]["source_url"].endswith("music-ex.json"))
        self.assertTrue(data["sources"][1]["source_url"].endswith("music-ex-deleted.json"))
        self.assertNotIn("region", data)
        self.assertNotIn("source_kind", data["sources"][1])
        with patch("mairadar.constants.urlopen", side_effect=[response("Current"), OSError("offline")]):
            with self.assertRaisesRegex(OSError, "offline"):
                fetch_snapshot()

    def test_cli_uses_combined_sources_and_discards_legacy_columns(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_rows(root / "charts.csv", [{"title": "Old", "difficulty_index": 5, "chart_type": "sd",
                                            "constant_region": "jp", "constant_source_kind": "deleted"}])
            write_json(root / "source.json", with_sources(
                [{"title": "Current", "lev_mas": "14", "lev_mas_i": "14.2"}],
                [{"title": "Old", "lev_mas": "13", "lev_mas_i": "13.4", "deleted_date": "20240912"}],
            ))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["match", "--input", str(root / "charts.csv"),
                                       "--snapshot", str(root / "source.json"),
                                       "--output", str(root / "constants.csv")]), 0)
            chart = ConstantTable.load(root / "constants.csv").lookup("Old", 5, "ST")
            self.assertEqual(chart["officialConstant"], 13.4)
            self.assertNotIn("constantRegion", chart)
            self.assertNotIn("constantSourceKind", chart)
            self.assertNotIn("constantDeletedDate", chart)
            header = (root / "constants.csv").read_text(encoding="utf-8-sig").splitlines()[0]
            self.assertNotIn("constant_region", header)
            self.assertNotIn("constant_source_kind", header)


if __name__ == "__main__":
    unittest.main()
