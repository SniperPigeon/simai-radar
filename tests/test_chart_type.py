"""Post-parser type inference and artwork export with absent cabinet metadata."""

from copy import deepcopy
import csv
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mairadar.chart_type import detect_chart_type
from mairadar.io import parse_file
from mairadar.parser import parse_chart, parse_text
from mairadar.pipeline import run_pipeline
from mairadar.scoring import FeatureScoreTransformer

ROOT = Path(__file__).resolve().parents[1]


class ChartTypeTests(unittest.TestCase):
    def test_requested_dx_markers(self):
        for token in (
            "A1", "Ch[4:1]", "1-3-5[4:1]", "1x", "1xh[4:1]",
            "1bx", "1bh[4:1]", "1b-5[4:1]", "1-5b[4:1]", "1b$",
        ):
            with self.subTest(token=token):
                parsed = parse_chart(f"(120){token},E")
                self.assertTrue(parsed.complete, parsed.diagnostics)
                before = deepcopy(parsed)
                self.assertEqual(detect_chart_type(parsed), "dx")
                self.assertEqual(parsed, before)

    def test_ordinary_notes_and_break_tap_do_not_trigger_dx(self):
        for token in ("1", "1b", "1h[4:1]", "1$", "1-5[4:1]", "1b@-5[4:1]"):
            with self.subTest(token=token):
                parsed = parse_chart(f"(120){token},E")
                self.assertTrue(parsed.complete, parsed.diagnostics)
                self.assertEqual(detect_chart_type(parsed), "sd")

    def test_incomplete_chart_is_not_classified_even_with_dx_evidence(self):
        parsed = parse_chart("(120)A1,invalid,E")
        self.assertFalse(parsed.complete)
        self.assertIsNone(detect_chart_type(parsed))

    def test_text_parser_keeps_metadata_missing_until_postprocessing(self):
        self.assertIsNone(parse_text("&inote_5=(120)A1,E")[0].chart.chart_type)
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "maidata.txt"
            source.write_text("&title=Example\n&inote_5=(120)A1,E")
            self.assertIsNone(parse_file(source)[0].chart.chart_type)

    def test_full_cli_missing_types_export_covers_for_dx_and_sd(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for title, token in (("DX sample", "A1"), ("SD sample", "1b")):
                folder = root / "input" / title
                folder.mkdir(parents=True)
                (folder / "maidata.txt").write_text(f"&title={title}\n&inote_5=(120){token},E")
                (folder / "bg.png").write_bytes(b"synthetic attachment")
            process = subprocess.run([
                sys.executable, str(ROOT / "scripts/mairadar.py"), "--mode", "full",
                "-i", str(root / "input"), "-o", str(root / "out"),
            ], capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            with (root / "out/charts.csv").open(encoding="utf-8-sig") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([row["chart_type"] for row in rows], ["dx", "sd"])
            self.assertTrue(all(row["status"] == "ok" for row in rows))
            for row in rows:
                self.assertEqual(row["cover_path"], f"covers/{row['title']}.png")
                self.assertTrue((root / "out" / row["cover_path"]).is_file())

    def test_explicit_metadata_and_caller_override_take_priority(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "maidata.txt"
            for index, (cabinet, token, override, expected) in enumerate((
                ("SD", "A1", None, "sd"),
                ("DX", "1b", None, "dx"),
                ("SD", "1b", "dx", "dx"),
                ("", "A1", "sd", "sd"),
            )):
                source.write_text(f"&title=T\n&cabinet={cabinet}\n&inote_5=(120){token},E")
                batch, _ = run_pipeline(
                    "full", source, output=root / f"out{index}", chart_type=override,
                    transformer=FeatureScoreTransformer(),
                )
                self.assertEqual(batch.records[0].chart.chart_type, expected)
                self.assertEqual(batch.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
