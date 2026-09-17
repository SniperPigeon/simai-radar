"""Synthetic bundle-to-report integration, portable covers, and failure reporting."""

import contextlib
import csv
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mairadar.analysis import ChartAnalyzer
from mairadar.analysis.features import JackSequenceAnalyzer
from mairadar.cli import main
from mairadar.batch import analyze_directory, analyze_source
from mairadar.exporters import CsvExporter
from mairadar.io import parse_file, write_bundle
from mairadar.parser import parse_text

ROOT = Path(__file__).resolve().parents[1]


def bundle(root, title="曲名", *, text="(180){16}1h[4:1],1,E", cover=True, offset=0):
    parsed = parse_text(
        f"&title={title}\n&artist=曲师\n&des_5=谱师\n&cabinet=DX\n"
        f"&lv_5=13\n&first={offset}\n&inote_5={text}"
    )[0]
    artwork = root / "art.png"
    if cover:
        artwork.write_bytes(b"synthetic opaque image attachment")
    return write_bundle(parsed, root / "input", cover_path=artwork if cover else None)


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        return reader.fieldnames, list(reader)


class BatchTests(unittest.TestCase):
    def test_csv_exports_one_shared_cover_for_all_difficulties_of_a_song(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            song = root / "raw" / "song"
            song.mkdir(parents=True)
            source = song / "maidata.txt"
            source.write_text(
                "&title=Shared Cover\n&inote_5=(120){4}1,2,E\n"
                "&inote_6=(120){4}3,4,E\n"
            )
            (song / "bg.png").write_bytes(b"one cover")

            batch = analyze_source(source)
            report = CsvExporter().export(
                batch.records, root / "report", feature_names=batch.feature_names,
            )
            _, rows = read_rows(report.csv_path)
            self.assertEqual([row["cover_path"] for row in rows], [
                "covers/Shared Cover.png", "covers/Shared Cover.png",
            ])
            self.assertEqual([path.name for path in (root / "report" / "covers").iterdir()], [
                "Shared Cover.png",
            ])

    def test_distribution_excludes_utage_by_default_and_explicit_index_includes_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "maidata.txt"
            source.write_text(
                "&title=Selection\n&inote_4=(120){4}7,8,E\n"
                "&inote_5=(120){4}1,2,E\n&inote_6=(120){4}5,6,E\n"
                "&inote_7=(120){4}3,4,E\n"
            )

            default_raw = analyze_source(source)
            explicit_raw = analyze_source(source, difficulties=[7])
            included_raw = analyze_source(source, include_utage=True)
            self.assertEqual([r.chart.difficulty_index for r in default_raw.records], [5, 6])
            self.assertEqual([r.chart.difficulty_index for r in explicit_raw.records], [7])
            self.assertEqual(
                [r.chart.difficulty_index for r in included_raw.records], [5, 6, 7],
            )

            for parsed in parse_file(source):
                write_bundle(parsed, root / "bundles")
            default_bundles = analyze_directory(root / "bundles", include_cover=False)
            explicit_bundles = analyze_directory(
                root / "bundles", include_cover=False, difficulties=[7],
            )
            included_bundles = analyze_directory(
                root / "bundles", include_cover=False, include_utage=True,
            )
            self.assertEqual(
                [r.chart.difficulty_index for r in default_bundles.records], [5, 6],
            )
            self.assertEqual([r.chart.difficulty_index for r in explicit_bundles.records], [7])
            self.assertEqual(
                [r.chart.difficulty_index for r in included_bundles.records], [5, 6, 7],
            )

    def test_metadata_raw_columns_cover_relative_path_and_offset(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = bundle(root, title='曲名,"quoted"', offset=10)
            before = (source / "events.csv").read_bytes()
            analyzer = ChartAnalyzer({"second": JackSequenceAnalyzer, "first": JackSequenceAnalyzer})
            batch = analyze_directory(root / "input", analyzer=analyzer)
            report = CsvExporter().export(batch.records, root / "report", feature_names=batch.feature_names)
            columns, rows = read_rows(report.csv_path)
            self.assertEqual(columns[-2:], ["second_raw", "first_raw"])
            self.assertFalse(any(name.endswith("_score") for name in columns))
            self.assertEqual(rows[0]["title"], '曲名,"quoted"')
            self.assertEqual(rows[0]["artist"], "曲师")
            self.assertEqual(rows[0]["designer"], "谱师")
            self.assertEqual(rows[0]["difficulty_index"], "5")
            self.assertEqual(rows[0]["chart_type"], "dx")
            self.assertEqual(float(rows[0]["first_raw"]), 2.6)
            self.assertEqual(
                json.loads(rows[0]["diagnostics"])["features"], {"second": True, "first": True},
            )
            cover = report.csv_path.parent / rows[0]["cover_path"]
            self.assertEqual(cover.read_bytes(), (root / "art.png").read_bytes())
            self.assertEqual((source / "events.csv").read_bytes(), before)
            self.assertEqual((batch.exit_code, report.exit_code), (0, 0))
            shutil.move(root / "report", root / "moved")
            self.assertTrue((root / "moved" / rows[0]["cover_path"]).is_file())

    def test_partial_parse_and_bad_child_remain_rows_other_bundles_continue(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle(root, "A", cover=False)
            bundle(root, "B", text="(120){4}1h[4:1],invalid,E", cover=False)
            (root / "input" / "broken" / "nested").mkdir(parents=True)
            (root / "input" / "ignored.txt").write_text("not a child directory")
            batch = analyze_directory(root / "input")
            report = CsvExporter().export(batch.records, root / "report", feature_names=batch.feature_names)
            _, rows = read_rows(report.csv_path)
            self.assertEqual(len(rows), 3)
            self.assertEqual([row["status"] for row in rows], ["ok", "error", "error"])
            self.assertEqual([row["jack_raw"] for row in rows], ["2.6", "", ""])
            self.assertTrue(all(row["cover_path"] == "" for row in rows))
            self.assertEqual(
                json.loads(rows[1]["diagnostics"])["features"],
                {
                    "jack": False,
                    "sweep": False,
                    "note": False,
                    "peak": False,
                    "slide_tricky": False,
                    "slide_sequence": False,
                    "slide_cumulate": False,
                },
            )
            self.assertEqual(json.loads(rows[-1]["diagnostics"])["source"], "broken")
            self.assertEqual(report.failed_records, 2)
            self.assertEqual((batch.exit_code, report.exit_code), (1, 1))

    def test_identical_colliding_covers_are_reused_without_deduplicating_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = bundle(root)
            shutil.copytree(source, root / "input" / "duplicate")
            batch = analyze_directory(root / "input")
            report = CsvExporter().export(batch.records, root / "report", feature_names=batch.feature_names)
            _, rows = read_rows(report.csv_path)
            self.assertEqual(len(rows), 2)
            self.assertEqual(len(list((root / "report" / "covers").iterdir())), 1)
            self.assertEqual([row["status"] for row in rows], ["ok", "ok"])
            self.assertEqual(rows[1]["jack_raw"], "2.6")
            self.assertEqual(rows[1]["cover_path"], rows[0]["cover_path"])
            self.assertEqual(report.exit_code, 0)
            self.assertEqual(batch.exit_code, 0)  # Export errors do not mutate core results.

    def test_different_colliding_covers_use_chart_metadata_without_overwriting(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = bundle(root)
            shutil.copytree(source, root / "input" / "duplicate")
            batch = analyze_directory(root / "input")
            alternate = root / "alternate.png"
            alternate.write_bytes(b"different cover content")
            batch.records[1].cover_path = alternate
            report = CsvExporter().export(batch.records, root / "report", feature_names=batch.feature_names)
            _, rows = read_rows(report.csv_path)
            self.assertEqual([row["status"] for row in rows], ["ok", "ok"])
            self.assertEqual(rows[1]["cover_path"], "covers/曲名-5-dx.png")
            self.assertEqual((root / "report" / rows[0]["cover_path"]).read_bytes(), (root / "art.png").read_bytes())
            self.assertEqual((root / "report" / rows[1]["cover_path"]).read_bytes(), alternate.read_bytes())
            self.assertEqual(report.exit_code, 0)
            self.assertNotIn("export", json.loads(rows[1]["diagnostics"]))

    def test_failed_cover_copy_keeps_raw_value_and_continues_next_record(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle(root, "A")
            bundle(root, "B")
            batch = analyze_directory(root / "input")
            copyfile = shutil.copyfile

            def fail_first(source, target):
                if target.name == "A.png":
                    target.write_bytes(b"partial copy")
                    raise OSError("simulated copy failure")
                return copyfile(source, target)

            with patch("mairadar.exporters.csv.shutil.copyfile", side_effect=fail_first):
                report = CsvExporter().export(batch.records, root / "report", feature_names=batch.feature_names)
            _, rows = read_rows(report.csv_path)
            self.assertEqual([row["status"] for row in rows], ["ok", "ok"])
            self.assertEqual(rows[0]["jack_raw"], "2.6")
            self.assertEqual(rows[0]["cover_path"], "")
            self.assertEqual(len(list((root / "report" / "covers").iterdir())), 1)

    def test_existing_files_are_preserved_and_failed_publish_cleans_staging(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle(root)
            batch = analyze_directory(root / "input")
            target = root / "report"
            target.mkdir()
            marker = target / "personal.txt"
            marker.write_text("keep")
            with self.assertRaises(FileExistsError):
                CsvExporter().export(batch.records, target, feature_names=batch.feature_names)
            self.assertEqual(marker.read_text(), "keep")
            marker.unlink()
            with patch("mairadar.exporters.csv.os.rename", side_effect=OSError("publish failed")):
                with self.assertRaises(OSError):
                    CsvExporter().export(batch.records, target, feature_names=batch.feature_names)
            self.assertTrue(target.is_dir())
            self.assertEqual(list(target.iterdir()), [])
            self.assertEqual(list(root.glob(".report-*")), [])

    def test_cli_runs_without_install_and_returns_nonzero_on_partial_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle(root, cover=False)
            command = [sys.executable, str(ROOT / "scripts/mairadar.py"), "--mode", "analysis",
                       "--input", str(root / "input")]
            process = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            feature = json.loads(process.stdout)["analysis"]["features"]["jack"]
            self.assertTrue(feature["success"])
            self.assertIsInstance(feature["data"], (int, float))
            self.assertFalse((root / "report").exists())
            (root / "input" / "bad").mkdir()
            process = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(process.returncode, 1, process.stderr)
            self.assertEqual(len(process.stdout.splitlines()), 2)

    def test_cli_chooser_selection_cancellation_and_unavailable_gui(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle(root, cover=False)
            args = ["--mode", "analysis", "--choose"]
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with patch("mairadar.cli.choose_directory", return_value=root / "input"):
                    self.assertEqual(main(args), 0)
                with patch("mairadar.cli.choose_directory", return_value=None):
                    self.assertEqual(main(args), 1)
                with patch("mairadar.cli.choose_directory", side_effect=ImportError("no tkinter")):
                    self.assertEqual(main(args), 1)

    def test_empty_input_and_nested_output_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "input").mkdir()
            self.assertEqual(analyze_directory(root / "input").exit_code, 1)
            with contextlib.redirect_stderr(io.StringIO()):
                for output in (root / "report", root / "input" / "report"):
                    self.assertEqual(main(["--mode", "analysis", "-i", str(root / "input"), "-o", str(output)]), 1)
                    self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
