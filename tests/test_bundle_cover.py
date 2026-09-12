"""Optional artwork stays in the export layer, with transactional replacement."""

import base64
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mairadar.io import find_cover, read_bundle, read_cover_path, write_bundle
from mairadar.parser import parse_text

ROOT = Path(__file__).resolve().parents[1]
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a23sAAAAASUVORK5CYII="
)


def example():
    return parse_text("&title=Cover Test\n&cabinet=DX\n&inote_5=(120)1,E")[0]


class CoverTests(unittest.TestCase):
    def test_cover_copied_and_manifest_records_relative_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cover = root / "source.png"
            cover.write_bytes(PNG)
            bundle = example()
            target = write_bundle(bundle, root / "out", cover_path=cover)
            manifest = json.loads((target / "manifest.json").read_text())
            self.assertEqual(manifest["assets"], {"cover": "cover.png"})
            self.assertIn("cover.png", manifest["file_sha256"])
            self.assertEqual(read_cover_path(target), target / "cover.png")
            self.assertEqual((target / "cover.png").read_bytes(), PNG)
            self.assertEqual(cover.read_bytes(), PNG)
            self.assertEqual(read_bundle(target), bundle)
            self.assertEqual(len(list(target.iterdir())), 5)

    def test_old_bundle_without_assets_field_still_loads(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = example()
            target = write_bundle(bundle, temp)
            manifest = json.loads((target / "manifest.json").read_text())
            manifest.pop("assets")
            (target / "manifest.json").write_text(json.dumps(manifest))
            self.assertIsNone(read_cover_path(target))
            self.assertEqual(read_bundle(target), bundle)

    def test_cover_corruption_is_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cover = root / "source.png"
            cover.write_bytes(PNG)
            target = write_bundle(example(), root / "out", cover_path=cover)
            (target / "cover.png").write_bytes(b"changed")
            for reader in (read_bundle, read_cover_path):
                with self.assertRaisesRegex(ValueError, "Checksum mismatch: cover.png"):
                    reader(target)

    def test_asset_path_cannot_escape_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            target = write_bundle(example(), temp)
            manifest = json.loads((target / "manifest.json").read_text())
            for unsafe in ("../outside.png", "/tmp/outside.png", "events.csv"):
                manifest["assets"] = {"cover": unsafe}
                (target / "manifest.json").write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, "asset path"):
                    read_cover_path(target)

    def test_overwrite_replaces_or_omits_only_managed_cover(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cover = root / "source.png"
            cover.write_bytes(PNG)
            bundle = example()
            target = write_bundle(bundle, root / "out", cover_path=cover)
            # The existing packaged cover can itself be a source: copy precedes rename.
            write_bundle(bundle, root / "out", overwrite=True, cover_path=target / "cover.png")
            self.assertEqual(read_cover_path(target).read_bytes(), PNG)
            (target / "user-picture.png").write_bytes(PNG)
            with self.assertRaisesRegex(ValueError, "extra files"):
                write_bundle(bundle, root / "out", overwrite=True)
            (target / "user-picture.png").unlink()
            write_bundle(bundle, root / "out", overwrite=True)
            self.assertIsNone(read_cover_path(target))
            self.assertEqual(len(list(target.iterdir())), 4)

    def test_cover_copy_failure_leaves_previous_bundle_intact(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cover = root / "source.png"
            cover.write_bytes(PNG)
            bundle = example()
            target = write_bundle(bundle, root / "out", cover_path=cover)
            old_manifest = (target / "manifest.json").read_bytes()
            with patch("mairadar.io.shutil.copyfile", side_effect=OSError("copy failed")):
                with self.assertRaises(OSError):
                    write_bundle(bundle, root / "out", cover_path=cover, overwrite=True)
            self.assertEqual((target / "manifest.json").read_bytes(), old_manifest)
            self.assertEqual(read_cover_path(target).read_bytes(), PNG)
            self.assertEqual(list((root / "out").iterdir()), [target])

    def test_cover_discovery_priority_and_absence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "track.mp3").write_bytes(b"audio is not an asset candidate")
            self.assertIsNone(find_cover(root))
            (root / "bg.jpg").write_bytes(b"jpeg source")
            (root / "bg.png").write_bytes(PNG)
            self.assertEqual(find_cover(root), root / "bg.png")

    def test_invalid_explicit_cover_does_not_replace_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle = example()
            target = write_bundle(bundle, root / "out")
            for name in ("missing.png", "not-a-cover.txt"):
                with self.assertRaises((FileNotFoundError, ValueError)):
                    write_bundle(bundle, root / "out", overwrite=True, cover_path=root / name)
            self.assertEqual(read_bundle(target), bundle)

    def test_unified_cli_discovers_cover_for_full_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "maidata.txt"
            source.write_text("&title=CLI\n&cabinet=SD\n&inote_5=(120)1,E")
            (root / "bg.png").write_bytes(PNG)
            args = [sys.executable, str(ROOT / "scripts/mairadar.py"), "--mode", "full",
                    "-i", str(source), "-o", str(root / "out")]
            result = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            with (root / "out/charts.csv").open(encoding="utf-8-sig") as stream:
                [row] = list(csv.DictReader(stream))
            self.assertEqual((root / "out" / row["cover_path"]).read_bytes(), PNG)

    def test_cli_batch_uses_each_songs_own_cover(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for title in ("A", "B"):
                folder = root / "input" / title
                folder.mkdir(parents=True)
                (folder / "maidata.txt").write_text(f"&title={title}\n&cabinet=SD\n&inote_5=(120)1,E")
                (folder / "bg.png").write_bytes(PNG + title.encode())
            args = [sys.executable, str(ROOT / "scripts/mairadar.py"), "--mode", "full",
                    "-i", str(root / "input"), "-o", str(root / "out")]
            result = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            with (root / "out/charts.csv").open(encoding="utf-8-sig") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([row["title"] for row in rows], ["A", "B"])
            for row in rows:
                self.assertEqual((root / "out" / row["cover_path"]).read_bytes(), PNG + row["title"].encode())


if __name__ == "__main__":
    unittest.main()
