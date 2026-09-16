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
from mairadar.analysis.features.sweep_burst import score_sweep_burst
from mairadar.cli import main
from mairadar.exporters import ExportResult
from mairadar.io import parse_file, read_bundle, write_bundle
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
        "&inote_5=(180){16}1h[4:1],1,E\n&inote_6=(120){4}1h[4:1],,E\n"
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
    def test_cli_default_uses_two_second_top_three_sweep(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "maidata.txt"
            body = ",".join("12345678" * 3)
            source.write_text(
                f"&title=扫键入口测试\n&cabinet=DX\n"
                f"&inote_5=(180){{16}}{body},E\n",
                encoding="utf-8",
            )
            [bundle] = parse_file(source, difficulties=[5])
            write_bundle(bundle, root / "bundles")

            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = main([
                    "--mode", "analysis_score",
                    "--input", str(root / "bundles"),
                    "--output", str(root / "scored"),
                ])

            self.assertEqual(code, 0, stderr.getvalue())
            with (root / "scored" / "charts.csv").open(
                encoding="utf-8-sig", newline="",
            ) as stream:
                [row] = list(csv.DictReader(stream))
            expected = score_sweep_burst(
                tuple(bundle.events),
                duration_s=bundle.chart.chart_end_time_s,
            ).value
            self.assertAlmostEqual(float(row["sweep_raw"]), expected)
            self.assertEqual(row["sweep_raw"], row["sweep_score"])
            self.assertEqual(
                json.loads(row["diagnostics"])["scoring"]["mapping_version"],
                "provisional-sweep-2s-top3-cumulate-star8-20260916-v50",
            )

    def test_cli_loads_frozen_mapping_profile_for_scoring(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixtures(root)
            profile = root / "mapping_profile.json"
            profile.write_text(json.dumps({
                "schemaVersion": "mairadar-mapping-profile-1",
                "mappingVersion": "cli-profile-v1",
                "scoreAnchors": [50, 100, 150, 200],
                "maximumScore": 220,
                "dimensions": {
                    name: {"rawAnchors": [1, 2, 3, 4], "t4Max": 5}
                    for name in (
                        "note", "peak", "sweep", "slide_tricky", "slide_sequence",
                        "jack", "slide_cumulate",
                    )
                },
            }), encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = main([
                    "--mode", "analysis_score",
                    "--input", str(root / "bundles"),
                    "--output", str(root / "profile-scored"),
                    "--mapping-profile", str(profile),
                ])

            self.assertEqual(code, 0, stderr.getvalue())
            with (root / "profile-scored" / "charts.csv").open(
                encoding="utf-8-sig", newline="",
            ) as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(
                json.loads(row["diagnostics"])["scoring"]["mapping_version"]
                == "cli-profile-v1"
                for row in rows
            ))

    def test_parse_only_writes_bundles_without_analysis_mapping_or_report_export(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = fixtures(root)
            analyzer, transformer, exporter = Mock(), Mock(), Mock()
            parsed, report = run_pipeline(
                "parse_only",
                source,
                output=root / "parsed-only",
                analyzer=analyzer,
                transformer=transformer,
                exporter=exporter,
            )

            self.assertIsNone(report)
            self.assertEqual(parsed.exported_bundles, 2)
            self.assertEqual(parsed.failed_records, 0)
            self.assertEqual(parsed.exit_code, 0)
            analyzer.analyze.assert_not_called()
            transformer.transform.assert_not_called()
            exporter.export.assert_not_called()
            targets = sorted((root / "parsed-only").iterdir())
            self.assertEqual([target.name for target in targets], ["测试-5-dx", "测试-6-dx"])
            self.assertTrue(all({path.name for path in target.iterdir()} == {
                "manifest.json", "charts.csv", "events.csv", "diagnostics.csv", "cover.png",
            } for target in targets))
            bundles = [read_bundle(target) for target in targets]
            self.assertEqual([bundle.chart.difficulty_index for bundle in bundles], [5, 6])
            self.assertTrue(all(bundle.chart.chart_type == "dx" for bundle in bundles))
            self.assertTrue(all((target / "cover.png").is_file() for target in targets))

    def test_parse_only_filters_difficulty_and_keeps_partial_bundle_visible(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "partial.simai"
            source.write_text("(120){4}1,invalid,E")
            parsed, report = run_pipeline(
                "parse_only",
                source,
                output=root / "parsed",
                difficulties=[5],
                chart_type="sd",
            )

            self.assertIsNone(report)
            self.assertEqual(parsed.exported_bundles, 1)
            self.assertEqual(parsed.failed_records, 1)
            self.assertEqual(parsed.records[0].status, "partial")
            self.assertEqual(parsed.exit_code, 1)
            restored = read_bundle(parsed.records[0].bundle_path)
            self.assertFalse(restored.complete)
            self.assertEqual(restored.chart.chart_type, "sd")

    def test_complete_chain_proceeds_but_incomplete_parse_never_reaches_analysis_or_mapping(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "chart.simai"
            source.write_text("(180){16}1h[4:1]/2-4-6[4:1],1,E")
            mapper = Mock(wraps=TestMapper())
            batch, report = run_pipeline("full", source, output=root / "complete", transformer=mapper)
            self.assertEqual(batch.exit_code, 0)
            self.assertEqual(batch.records[0].analysis.features["jack"].data, 2.6)
            mapper.transform.assert_called_once()
            self.assertEqual(read_csv(report)[0]["status"], "ok")

            source.write_text("(180){16}1h[4:1]/2-4-6[4:1],1,invalid,E")
            mapper.reset_mock()
            with patch("mairadar.analysis.features.JackSequenceAnalyzer.analyze") as feature:
                batch, report = run_pipeline("full", source, output=root / "incomplete", transformer=mapper)
                feature.assert_not_called()
            mapper.transform.assert_not_called()
            self.assertEqual(batch.exit_code, 1)
            row = read_csv(report)[0]
            self.assertEqual((row["jack_raw"], row["jack_score"]), ("", ""))

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
            self.assertEqual([r.analysis.features["jack"].data for r in batch.records], [2.6, 0])
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
            self.assertEqual([row["jack_score"] for row in left], ["27.0", "1.0"])
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
                if result.features["jack"].data == 2.6:
                    raise ValueError("test mapping failed")
                return super().transform(result)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixtures(root)
            batch, report = run_pipeline(
                "analysis_score", root / "bundles", output=root / "out", transformer=SometimesFails(),
            )
            rows = read_csv(report)
            self.assertEqual([r["jack_raw"] for r in rows], ["2.6", "0.0"])
            self.assertEqual([r["jack_score"] for r in rows], ["", "1.0"])
            self.assertEqual([r["status"] for r in rows], ["partial", "ok"])
            self.assertEqual((batch.exit_code, report.exit_code), (1, 1))

    def test_all_failed_mapping_still_exports_score_columns_and_raw_values(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixtures(root)
            mapper = Mock()
            mapper.transform.return_value = ScoreResult({"jack": FeatureScore(float("nan"))}, "invalid")
            batch, report = run_pipeline(
                "analysis_score", root / "bundles", output=root / "out", transformer=mapper,
            )
            self.assertEqual(batch.exit_code, 1)
            self.assertTrue(all(row["jack_score"] == "" for row in read_csv(report)))
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
                ("analysis", {"chart_type": "dx"}),
                ("analysis_score", {}),
                ("parse_only", {}),
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

    def test_cli_parse_only_does_not_construct_default_mapper_and_rejects_format(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = fixtures(root)
            configured = Mock(side_effect=AssertionError("mapper must not be constructed"))
            stdout = io.StringIO()
            with patch("mairadar.scoring.config.TRANSFORMER", configured), \
                 contextlib.redirect_stdout(stdout):
                code = main([
                    "--mode", "parse_only", "--input", str(source),
                    "--output", str(root / "parsed"), "--difficulty", "6",
                ])
            self.assertEqual(code, 0)
            configured.assert_not_called()
            self.assertIn("bundles=1 failed_or_partial=0", stdout.getvalue())
            self.assertEqual([path.name for path in (root / "parsed").iterdir()], ["测试-6-dx"])

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main([
                    "--mode", "parse_only", "--input", str(source),
                    "--output", str(root / "rejected"), "--format", "visualizer",
                ])
            self.assertEqual(code, 1)
            self.assertIn("--format is only available in scoring modes", stderr.getvalue())
            self.assertFalse((root / "rejected").exists())

    def test_parse_only_adapter_import_has_no_analysis_or_scoring_dependency(self):
        process = subprocess.run([
            sys.executable,
            "-c",
            "import mairadar.parse_export, sys; "
            "assert 'mairadar.analysis' not in sys.modules; "
            "assert 'mairadar.scoring' not in sys.modules",
        ], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)

    def test_unified_script_and_module_analysis_print_json_and_default_mapping_completes_mvp(self):
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
                self.assertEqual(rows[0]["analysis"]["features"]["jack"], {"data": 2.6, "success": True})
                self.assertTrue(rows[0]["analysis"]["features"]["note"]["success"])
            for mode, input_dir in (("full", "raw"), ("analysis_score", "bundles")):
                process = subprocess.run([
                    sys.executable, "-m", "mairadar", "--mode", mode, "-i", str(root / input_dir),
                    "-o", str(root / mode),
                ], capture_output=True, text=True)
                self.assertEqual(process.returncode, 0, process.stderr)
                with (root / mode / "charts.csv").open(encoding="utf-8-sig") as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual([float(row["jack_raw"]) for row in rows], [2.6, 0])
                self.assertEqual([float(row["jack_score"]) for row in rows], [2.6, 0])
                self.assertEqual(
                    [float(row["slide_tricky_score"]) for row in rows],
                    [float(row["slide_tricky_raw"]) for row in rows],
                )
                self.assertTrue(all(
                    row["note_raw"] and row["note_score"]
                    and row["sweep_raw"] and row["sweep_score"]
                    and row["peak_raw"] and row["peak_score"]
                    and row["slide_tricky_raw"] and row["slide_tricky_score"]
                    and row["slide_sequence_raw"] and row["slide_sequence_score"]
                    and row["slide_cumulate_raw"] and row["slide_cumulate_score"]
                    for row in rows
                ))
                self.assertTrue(all(json.loads(row["diagnostics"])["scoring"]["mapping_version"]
                                    == "provisional-sweep-2s-top3-cumulate-star8-20260916-v50"
                                    for row in rows))
                self.assertTrue(all((root / mode / row["cover_path"]).is_file() for row in rows))


if __name__ == "__main__":
    unittest.main()
