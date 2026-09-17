"""Restoring existing report artwork never reruns analysis or loses metadata."""

from copy import deepcopy
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

spec = importlib.util.spec_from_file_location("pages_covers", Path(__file__).resolve().parents[1] / "scripts/build_pages.py")
pages = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pages)


class PagesCoverTests(unittest.TestCase):
    def fixture(self, root):
        payload = {
            "schemaVersion": "mairadar-visualizer-1", "dimensions": [{"key": "note"}],
            "stats": {"songCount": 2, "chartCount": 2, "coverCount": 1,
                      "failedChartCount": 0, "skippedRecordCount": 0},
            "songs": [],
        }
        for title, kind, folder in [("TRUST", "ST", "sd"), ("Trust", "DX", "dx")]:
            source = root / "raw" / folder
            source.mkdir(parents=True)
            (source / "maidata.txt").write_text("not parsed during artwork restoration")
            (source / "bg.png").write_bytes(folder.encode())
            payload["songs"].append({"title": title, "artist": folder, "cover": None, "charts": [{
                "id": folder, "difficulty": 5, "kind": kind, "sourceRef": f"{folder}/maidata.txt",
                "cover": "assets/covers/TRUST.png" if kind == "ST" else None,
                "status": "ok", "exportIssues": [] if kind == "ST" else ["Conflicting cover filename: Trust.png"],
                "rawScores": {"note": 1.23}, "scores": {"note": 123}, "fittedConstant": 13.45,
            }]})
        site = root / "metadata"
        (site / "data").mkdir(parents=True)
        (site / "data/songs.json").write_text(json.dumps(payload))
        return site, payload

    def test_cli_restores_covers_including_case_conflict_and_zips_them(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            site, original = self.fixture(root)
            target = root / "site"
            with contextlib.redirect_stdout(io.StringIO()):
                code = pages.main(["--site", str(site), "--cover-root", str(root / "raw"), "--output", str(target)])
            self.assertEqual(code, 0)
            payload = json.loads((target / "data/songs.json").read_text())
            self.assertEqual(payload["stats"]["coverCount"], 2)
            self.assertEqual(payload["songs"][1]["charts"][0]["cover"], "assets/covers/Trust-5-dx.png")
            with ZipFile(root / "site.zip") as archive:
                for old, song in zip(original["songs"], payload["songs"]):
                    chart = song["charts"][0]
                    self.assertEqual(chart["exportIssues"], [])
                    self.assertEqual((target / chart["cover"]).read_bytes(), song["artist"].encode())
                    self.assertEqual(archive.read(chart["cover"]), song["artist"].encode())
                    for field in ("rawScores", "scores", "fittedConstant"):
                        self.assertEqual(chart[field], old["charts"][0][field])
            self.assertEqual(json.loads((site / "data/songs.json").read_text()), original)

    def test_originally_absent_cover_keeps_placeholder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, payload = self.fixture(root)
            (root / "raw/sd/bg.png").unlink()
            pages.restore_covers(payload, root / "target", root / "raw")
            self.assertIsNone(payload["songs"][0]["cover"])
            self.assertEqual(payload["stats"]["coverCount"], 1)

    def test_invalid_or_missing_source_ref_does_not_create_final_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            site, payload = self.fixture(root)
            for ref in ("../escape.txt", "missing/maidata.txt", None):
                modified = deepcopy(payload)
                modified["songs"][0]["charts"][0]["sourceRef"] = ref
                (site / "data/songs.json").write_text(json.dumps(modified))
                with contextlib.redirect_stderr(io.StringIO()):
                    code = pages.main(["--site", str(site), "--cover-root", str(root / "raw"),
                                       "--output", str(root / "output")])
                self.assertEqual(code, 1)
                self.assertFalse((root / "output").exists())
                self.assertFalse((root / "output.zip").exists())


if __name__ == "__main__":
    unittest.main()
