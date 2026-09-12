"""Mode composition using an injected test mapper, never production calibration."""

import contextlib
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from mairadar.analysis import AnalysisResult, FeatureResult
from mairadar.cli import main
from mairadar.exporters import ExportResult
from mairadar.io import parse_file, write_bundle
from mairadar.pipeline import run_pipeline
from mairadar.scoring import FeatureScore, ScoreResult

ROOT = Path(__file__).resolve().parents[1]


class TestMapper:
    """Test-only arbitrary transform; deliberately not an identity mapping."""

    def transform(self, result):
        return ScoreResult({
            name: FeatureScore(feature.data * 10 + 1) if feature.success
            else FeatureScore(None, "unavailable")
            for name, feature in result.features.items()
        }, "test-only")


def fixtures(root):
    song = root / "raw" / "song"
    song.mkdir(parents=True)
    source = song / "maidata.txt"
    source.write_text(
        "&title=测试\n&artist=曲师\n&des=谱师\n&cabinet=DX\n"
        "&inote_5=(120){4}1h[4:1],E\n&inote_6=(120){4}1h[4:1],,E\n"
    )
    cover = song / "bg.png"
    cover.write_bytes(b"synthetic attachment")
    for bundle in parse_file(source):
        write_bundle(bundle, root / "bundles", cover_path=cover)
    return source


def read_csv(report):
    with report.csv_path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


class PipelineTests(unittest.TestCase):
    def test_complete_chain_proceeds_but_incomplete_parse_never_reaches_analysis_or_mapping(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "chart.simai"
            source.write_text("(120){4}1h[4:1]/2-4-6[4:1],E")
            mapper = Mock(wraps=TestMapper())
            batch, report = run_pipeline("full", source, output=root / "complete", transformer=mapper)
            self.assertEqual(batch.exit_code, 0)
            self.assertEqual(batch.records[0].analysis.features["hold"].data, 1)
            mapper.transform.assert_called_once()
            self.assertEqual(read_csv(report)[0]["status"], "ok")

            source.write_text("(120){4}1h[4:1]/2-4-6[4:1],invalid,E")
            mapper.reset_mock()
            with patch("mairadar.analysis.features.HoldFrequencyAnalyzer.analyze") as feature:
                batch, report = run_pipeline("full", source, output=root / "incomplete", transformer=mapper)
                feature.assert_not_called()
            mapper.transform.assert_not_called()
            self.assertEqual(batch.exit_code, 1)
            row = read_csv(report)[0]
            self.assertEqual((row["hold_raw"], row["hold_score"]), ("", ""))

    def test_custom_analyzer_mapper_and_exporter_compose_without_output_files(self):
        class FixedAnalyzer:
            feature_names = ("custom",)

            def analyze(self, parsed):
                return AnalysisResult({"custom": FeatureResult(4.0)})

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = fixtures(root)
            exporter = Mock()
            exporter.export.return_value = ExportResult(root / "virtual" / "charts.csv", 0)
            batch, report = run_pipeline(
                "full", source, output=root / "virtual", analyzer=FixedAnalyzer(),
                transformer=TestMapper(), exporter=exporter,
            )
            self.assertEqual(batch.feature_names, ("custom",))
            self.assertTrue(all(record.scores.features["custom"].value == 41 for record in batch.records))
            exporter.export.assert_called_once_with(
                batch.records, root / "virtual", feature_names=("custom",), include_scores=True,
            )
            self.assertIs(report, exporter.export.return_value)
            self.assertFalse((root / "virtual").exists())

    def test_analysis_only_never_parses_maps_or_exports(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixtures(root)
            mapper, exporter = Mock(), Mock()
            before = sorted(root.rglob("*"))
            with patch("mairadar.batch.parse_file", side_effect=AssertionError("must not parse")), \
                 patch("mairadar.batch.read_cover_path", side_effect=AssertionError("no artwork needed")):
                batch, report = run_pipeline(
                    "analysis", root / "bundles", transformer=mapper, exporter=exporter,
                )
            self.assertIsNone(report)
            self.assertEqual(batch.exit_code, 0)
            self.assertEqual([r.analysis.features["hold"].data for r in batch.records], [2, 1])
            mapper.transform.assert_not_called()
            exporter.export.assert_not_called()
            self.assertEqual(sorted(root.rglob("*")), before)

    def test_full_and_analysis_score_produce_same_results_without_intermediate_io(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = fixtures(root)
            with patch("mairadar.batch.read_bundle", side_effect=AssertionError("full must use memory")), \
                 patch("mairadar.io.write_bundle", side_effect=AssertionError("no intermediate export")), \
                 patch("mairadar.batch.parse_file", wraps=parse_file) as parser:
                full, full_report = run_pipeline(
                    "full", root / "raw", output=root / "full", transformer=TestMapper(),
                )
                self.assertEqual(parser.call_count, 1)
                self.assertEqual(parser.call_args.args[0], source)
            with patch("mairadar.batch.parse_file", side_effect=AssertionError("must not reparse")):
                scored, scored_report = run_pipeline(
                    "analysis_score", root / "bundles", output=root / "scored", transformer=TestMapper(),
                )
            left, right = read_csv(full_report), read_csv(scored_report)
            for a, b in zip(left, right):
                a.pop("diagnostics")
                b.pop("diagnostics")
                self.assertEqual(a, b)
                self.assertEqual((root / "full" / a["cover_path"]).read_bytes(), b"synthetic attachment")
            self.assertEqual([row["hold_score"] for row in left], ["21.0", "11.0"])
            self.assertEqual((full.exit_code, scored.exit_code), (0, 0))

    def test_missing_mapper_fails_before_reading_or_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for mode in ("full", "analysis_score"):
                with self.subTest(mode=mode), \
                     patch("mairadar.pipeline.analyze_source") as raw, \
                     patch("mairadar.pipeline.analyze_directory") as bundles:
                    with self.assertRaisesRegex(ValueError, "No score transformer"):
                        run_pipeline(mode, root / "input", output=root / "report")
                    raw.assert_not_called()
                    bundles.assert_not_called()
                    self.assertFalse((root / "report").exists())

    def test_mapper_failure_preserves_raw_and_continues_other_charts(self):
        class SometimesFails(TestMapper):
            def transform(self, result):
                if result.features["hold"].data == 2:
                    raise ValueError("test mapping failed")
                return super().transform(result)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixtures(root)
            batch, report = run_pipeline(
                "analysis_score", root / "bundles", output=root / "out", transformer=SometimesFails(),
            )
            rows = read_csv(report)
            self.assertEqual([r["hold_raw"] for r in rows], ["2.0", "1.0"])
            self.assertEqual([r["hold_score"] for r in rows], ["", "11.0"])
            self.assertEqual([r["status"] for r in rows], ["partial", "ok"])
            self.assertEqual((batch.exit_code, report.exit_code), (1, 1))

    def test_all_failed_mapping_still_exports_score_columns_and_raw_values(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixtures(root)
            mapper = Mock()
            mapper.transform.return_value = ScoreResult({"hold": FeatureScore(float("nan"))}, "invalid")
            batch, report = run_pipeline(
                "analysis_score", root / "bundles", output=root / "out", transformer=mapper,
            )
            self.assertEqual(batch.exit_code, 1)
            self.assertTrue(all(row["hold_score"] == "" for row in read_csv(report)))
            self.assertTrue(all(record.diagnostics[0].code == "MAPPING_FAILED" for record in batch.records))

    def test_full_partial_parse_and_unreadable_file_return_nonzero_with_good_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixtures(root)
            (root / "raw" / "bad.simai").write_bytes(b"\xff")
            (root / "raw" / "partial.simai").write_text("(120){4}1h[4:1],invalid,E")
            batch, report = run_pipeline(
                "full", root / "raw", output=root / "out", transformer=TestMapper(),
            )
            rows = read_csv(report)
            self.assertEqual(len(rows), 4)
            self.assertEqual(sum(row["status"] == "ok" for row in rows), 2)
            self.assertEqual((batch.exit_code, report.exit_code), (1, 1))

    def test_invalid_mode_options_and_output_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixtures(root)
            for mode, options in (
                ("unknown", {}),
                ("analysis", {"output": root / "out"}),
                ("analysis", {"difficulties": [5]}),
                ("analysis_score", {}),
                ("full", {"output": root / "raw" / "out"}),
            ):
                with self.subTest(mode=mode, options=options), self.assertRaises(ValueError):
                    run_pipeline(mode, root / "raw", transformer=TestMapper(), **options)
            self.assertFalse((root / "out").exists())
            with self.assertRaises(FileExistsError):
                run_pipeline("full", root / "raw", output=root / "bundles", transformer=TestMapper())

    def test_cli_accepts_configured_or_injected_mapper_and_full_difficulty_filter(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = fixtures(root)
            with contextlib.redirect_stdout(io.StringIO()), patch("mairadar.scoring.config.TRANSFORMER", TestMapper):
                code = main(["--mode", "full", "-i", str(source), "-o", str(root / "out"), "-d", "6"])
            self.assertEqual(code, 0)
            with (root / "out" / "charts.csv").open(encoding="utf-8-sig") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["difficulty_index"], "6")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([
                    "--mode", "analysis_score", "-i", str(root / "bundles"), "-o", str(root / "out2"),
                ], transformer=TestMapper()), 0)

    def test_unified_script_and_module_analysis_print_json_and_default_dummy_completes_mvp(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixtures(root)
            for command in (
                [sys.executable, str(ROOT / "scripts/mairadar.py")],
                [sys.executable, "-m", "mairadar"],
            ):
                process = subprocess.run(command + ["--mode", "analysis", "-i", str(root / "bundles")],
                                         capture_output=True, text=True)
                self.assertEqual(process.returncode, 0, process.stderr)
                rows = [json.loads(line) for line in process.stdout.splitlines()]
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0]["analysis"]["features"]["hold"], {"data": 2.0, "success": True})
            for mode, input_dir in (("full", "raw"), ("analysis_score", "bundles")):
                process = subprocess.run([
                    sys.executable, "-m", "mairadar", "--mode", mode, "-i", str(root / input_dir),
                    "-o", str(root / mode),
                ], capture_output=True, text=True)
                self.assertEqual(process.returncode, 0, process.stderr)
                with (root / mode / "charts.csv").open(encoding="utf-8-sig") as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual([float(row["hold_raw"]) for row in rows], [2, 1])
                self.assertEqual([float(row["hold_score"]) for row in rows], [200, 50])
                self.assertTrue(all(json.loads(row["diagnostics"])["scoring"]["mapping_version"]
                                    == "dummy-pn-v1" for row in rows))
                self.assertTrue(all((root / mode / row["cover_path"]).is_file() for row in rows))


if __name__ == "__main__":
    unittest.main()
