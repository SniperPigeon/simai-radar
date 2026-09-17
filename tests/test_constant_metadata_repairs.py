"""One-time metadata corrections preserve chart text and cached features."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "metadata_repairs", Path(__file__).resolve().parents[1] / "scripts/repair_constant_metadata.py",
)
repairs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repairs)


class MetadataRepairTests(unittest.TestCase):
    def fixture(self):
        entry = {"source_ref": "song/maidata.txt", "metadata": {
            "title": {"before": "Grip", "after": "Grip & Break down !!"},
            "cabinet": {"before": None, "after": "sd"},
        }, "chart_types_before": {"5": "sd", "6": "dx"}}
        payload = {"schemaVersion": "mairadar-visualizer-1", "songs": [{
            "title": "Grip", "artist": "A", "chartKinds": ["ST", "DX"],
            "charts": [{"difficulty": d, "kind": k, "sourceRef": "song/maidata.txt",
                        "rawScores": {"note": 1.2}, "scores": {"note": 100}}
                       for d, k in [(5, "ST"), (6, "DX")]],
        }]}
        return entry, payload

    def test_exact_values_preserve_bom_notes_and_crlf_and_are_idempotent(self):
        entry, _ = self.fixture()
        original = "\ufeff&title=Grip\r\n&artist=A\r\n&inote_6=(120){4}1-5[4:1],E\r\n"
        fixed, edits = repairs.repair_metadata(original, entry["metadata"])
        self.assertEqual(fixed, original.replace("\ufeff", "\ufeff&cabinet=sd\r\n", 1).replace("&title=Grip\r", "&title=Grip & Break down !!\r"))
        self.assertEqual(len(edits), 2)
        self.assertEqual(repairs.repair_metadata(fixed, entry["metadata"]), (fixed, []))
        with self.assertRaisesRegex(ValueError, "Unexpected"):
            repairs.repair_metadata(original.replace("Grip", "Different"), entry["metadata"])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            repairs.repair_metadata(original + "&title=Grip\r\n", entry["metadata"])

    def test_cached_corrections_preserve_features_and_original_payload(self):
        entry, payload = self.fixture()
        original = deepcopy(payload)
        fixed, changes = repairs.repair_payload(payload, [entry])
        self.assertEqual(payload, original)
        song = fixed["songs"][0]
        self.assertEqual(song["title"], "Grip & Break down !!")
        self.assertEqual(song["chartKinds"], ["ST"])
        for before, after in zip(original["songs"][0]["charts"], song["charts"]):
            self.assertEqual(after["rawScores"], before["rawScores"])
            self.assertEqual(after["scores"], before["scores"])
        self.assertEqual(changes[0]["cached_charts"], 2)
        self.assertEqual(repairs.repair_payload(fixed, [entry])[0], fixed)

    def test_backup_apply_dry_run_and_second_apply(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            entry, payload = self.fixture()
            raw = root / "raw/song/maidata.txt"
            raw.parent.mkdir(parents=True)
            original = b"\xef\xbb\xbf&title=Grip\r\n&inote_5=(120)1,E\r\n"
            raw.write_bytes(original)
            site = root / "songs.json"
            cover = root / "assets/covers/Grip.png"
            cover.parent.mkdir(parents=True)
            cover.write_bytes(b"original cover")
            payload["songs"][0]["cover"] = "assets/covers/Grip.png"
            for chart in payload["songs"][0]["charts"]:
                chart["cover"] = "assets/covers/Grip.png"
            site.write_text(json.dumps(payload))
            plan = {"schema_version": "mairadar-metadata-repairs-1", "repairs": [entry]}
            args = (root / "raw", site, plan, root / "fixed", root / "backups")
            self.assertEqual(repairs.run(*args)["raw_files_changed"], 1)
            self.assertEqual(raw.read_bytes(), original)
            self.assertFalse((root / "fixed").exists())
            report = repairs.run(*args, apply=True)
            self.assertEqual(report["raw_files_changed"], 1)
            self.assertEqual((root / "backups/song/maidata.txt").read_bytes(), original)
            self.assertEqual(report["covers_copied"], 1)
            self.assertEqual((root / "fixed/assets/covers/Grip.png").read_bytes(), b"original cover")
            repeated = repairs.run(root / "raw", root / "fixed", plan, root / "again", root / "backups", apply=True)
            self.assertEqual(repeated["raw_files_changed"], 0)
            self.assertEqual((root / "again/assets/covers/Grip.png").read_bytes(), b"original cover")

    def test_preflight_failure_leaves_sources_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            entry, payload = self.fixture()
            raw = root / "raw/song/maidata.txt"
            raw.parent.mkdir(parents=True)
            original = b"&title=Grip\n&cabinet=dx\n&inote_5=(120)1,E"
            raw.write_bytes(original)
            site = root / "songs.json"
            site.write_text(json.dumps(payload))
            plan = {"schema_version": "mairadar-metadata-repairs-1", "repairs": [entry]}
            with self.assertRaisesRegex(ValueError, "Unexpected"):
                repairs.run(root / "raw", site, plan, root / "fixed", root / "backup", apply=True)
            self.assertEqual(raw.read_bytes(), original)
            self.assertFalse((root / "backup").exists())


if __name__ == "__main__":
    unittest.main()
