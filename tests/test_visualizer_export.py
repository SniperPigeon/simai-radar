"""Visualizer export stays an optional presentation layer over scored records."""

import contextlib
import io
import json
from pathlib import Path
import re
import tempfile
import unittest

from mairadar.analysis import AnalysisResult, FeatureResult
from mairadar.cli import main
from mairadar.exporters import (
    DimensionPresentation,
    VisualizerExporter,
)
from mairadar.model import Chart
from mairadar.pipeline import run_pipeline
from mairadar.reporting import AnalysisRecord
from mairadar.scoring import FeatureScore, ScoreResult


class TestMapper:
    def transform(self, result):
        return ScoreResult({
            name: FeatureScore(feature.data * 10)
            for name, feature in result.features.items()
        }, "visualizer-test-v1")


def scored_record(
    source: str,
    *,
    chart_type: str,
    difficulty: int,
    title: str = "同名曲",
    features: tuple[str, ...] = ("alpha", "beta", "gamma"),
) -> AnalysisRecord:
    analysis = AnalysisResult({
        name: FeatureResult(index + 1.0)
        for index, name in enumerate(features)
    })
    scores = ScoreResult({
        name: FeatureScore((index + 1) * 25.0)
        for index, name in enumerate(features)
    }, "three-dimension-v1")
    return AnalysisRecord(
        source,
        chart=Chart(
            source_name=source,
            chart_type=chart_type,
            difficulty_index=difficulty,
            difficulty_label=f"Difficulty {difficulty}",
            level_text="13+",
            title=title,
            artist="曲师",
            designer="谱师",
            chart_end_time_s=90,
            metadata_json={"wholebpm": "120-180", "genre": "测试", "version": "v1"},
        ),
        analysis=analysis,
        scores=scores,
    )


class VisualizerExporterTests(unittest.TestCase):
    def test_pipeline_exports_self_contained_site_with_dynamic_difficulties(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            song = root / "raw" / "song"
            song.mkdir(parents=True)
            source = song / "maidata.txt"
            source.write_text(
                "&title=测试曲\n&artist=曲师\n&des=谱师\n&cabinet=DX\n"
                "&wholebpm=120-180\n&genre=测试分类\n&version=测试版本\n"
                "&lv_2=7\n&lv_7=宴\n"
                "&inote_2=(120){4}1h[4:1],E\n&inote_7=(180){4}1h[4:1],,E\n"
            )
            (song / "bg.png").write_bytes(b"synthetic cover")
            batch, report = run_pipeline(
                "full",
                root / "raw",
                output=root / "site",
                transformer=TestMapper(),
                exporter=VisualizerExporter(),
            )

            self.assertEqual((batch.exit_code, report.exit_code), (0, 0))
            self.assertEqual(report.site_path, root / "site")
            self.assertTrue(all((root / "site" / name).is_file() for name in (
                "index.html", "app.js", "styles.css", "data/songs.json",
            )))
            payload = json.loads(report.data_path.read_text())
            self.assertEqual(payload["schemaVersion"], "mairadar-visualizer-1")
            self.assertEqual(payload["mappingVersions"], ["visualizer-test-v1"])
            self.assertEqual(payload["dimensions"], [
                {
                    "key": "hold",
                    "label": "Hold频率",
                    "shortLabel": "Hold",
                    "color": "#ef476f",
                },
                {
                    "key": "note",
                    "label": "总体物量",
                    "shortLabel": "Note",
                    "color": "#ff9f1c",
                },
            ])
            self.assertEqual(payload["stats"]["songCount"], 1)
            self.assertEqual(payload["stats"]["chartCount"], 2)
            [exported_song] = payload["songs"]
            self.assertEqual(exported_song["id"], "song-1")
            self.assertEqual(exported_song["difficulties"], [2, 7])
            self.assertEqual([chart["id"] for chart in exported_song["charts"]], [
                "chart-1", "chart-2",
            ])
            self.assertEqual([chart["difficultyLabel"] for chart in exported_song["charts"]], [
                "Basic", "Utage",
            ])
            self.assertTrue(all(chart["kind"] == "DX" for chart in exported_song["charts"]))
            self.assertTrue(all(chart["cover"].startswith("assets/covers/")
                                for chart in exported_song["charts"]))
            self.assertTrue(all((root / "site" / chart["cover"]).is_file()
                                for chart in exported_song["charts"]))

    def test_grouping_preserves_same_title_dx_sd_and_uses_sequential_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            records = [
                scored_record(
                    "official/dx/maidata.txt", chart_type="dx", difficulty=5,
                    features=("alpha", "beta", "gamma_raw"),
                ),
                scored_record(
                    "official/dx/maidata.txt", chart_type="dx", difficulty=6,
                    features=("alpha", "beta", "gamma_raw"),
                ),
                scored_record(
                    "official/sd/maidata.txt", chart_type="sd", difficulty=5,
                    features=("alpha", "beta", "gamma_raw"),
                ),
            ]
            presentation = {
                "alpha": DimensionPresentation("甲", "甲", "#111111"),
                "beta": DimensionPresentation("乙"),
            }
            report = VisualizerExporter(presentation).export(
                records,
                root / "site",
                feature_names=("alpha", "beta", "gamma_raw"),
                include_scores=True,
            )
            payload = json.loads(report.data_path.read_text())

            self.assertEqual(payload["stats"]["songCount"], 2)
            self.assertEqual(payload["stats"]["chartCount"], 3)
            self.assertEqual([song["id"] for song in payload["songs"]], ["song-1", "song-2"])
            self.assertEqual([len(song["charts"]) for song in payload["songs"]], [2, 1])
            self.assertEqual([song["chartKinds"] for song in payload["songs"]], [["DX"], ["ST"]])
            ids = [chart["id"] for song in payload["songs"] for chart in song["charts"]]
            self.assertEqual(ids, ["chart-1", "chart-2", "chart-3"])
            self.assertTrue(all(re.fullmatch(r"(?:song|chart)-\d+", value)
                                for value in ["song-1", "song-2", *ids]))
            self.assertEqual([item["key"] for item in payload["dimensions"]], [
                "alpha", "beta", "gamma_raw",
            ])
            self.assertEqual(payload["dimensions"][0], {
                "key": "alpha", "label": "甲", "shortLabel": "甲", "color": "#111111",
            })
            self.assertEqual(payload["dimensions"][2]["label"], "gamma_raw")

    def test_missing_scores_remain_null_and_failed_record_is_visible_in_stats(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record = scored_record("partial/maidata.txt", chart_type="dx", difficulty=5)
            record.scores = ScoreResult({
                "alpha": FeatureScore(10),
                "beta": FeatureScore(None, "unavailable"),
                "gamma": FeatureScore(30),
            }, "partial-v1")
            report = VisualizerExporter().export(
                [record], root / "site",
                feature_names=("alpha", "beta", "gamma"),
                include_scores=True,
            )
            payload = json.loads(report.data_path.read_text())
            chart = payload["songs"][0]["charts"][0]
            self.assertEqual(chart["scores"], {"alpha": 10, "beta": None, "gamma": 30})
            self.assertEqual(chart["dominantDimension"], "gamma")
            self.assertEqual(chart["status"], "partial")
            self.assertEqual(payload["stats"]["failedChartCount"], 1)
            self.assertEqual(report.exit_code, 1)

    def test_cli_selects_visualizer_without_a_template_argument(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "chart.simai"
            source.write_text("(120){4}1h[4:1],E")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main([
                    "--mode", "full", "--input", str(source),
                    "--output", str(root / "site"), "--format", "visualizer",
                ], transformer=TestMapper())
            self.assertEqual(code, 0)
            self.assertIn(f"visualizer={root / 'site'}", stdout.getvalue())
            self.assertTrue((root / "site" / "data" / "songs.json").is_file())

    def test_existing_output_and_unscored_export_are_rejected_without_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "site"
            target.mkdir()
            marker = target / "keep.txt"
            marker.write_text("keep")
            record = scored_record("source/maidata.txt", chart_type="dx", difficulty=5)
            exporter = VisualizerExporter()
            with self.assertRaises(FileExistsError):
                exporter.export(
                    [record], target,
                    feature_names=("alpha", "beta", "gamma"),
                    include_scores=True,
                )
            self.assertEqual(marker.read_text(), "keep")
            with self.assertRaisesRegex(ValueError, "requires mapped scores"):
                exporter.export(
                    [record], root / "raw-only",
                    feature_names=("alpha", "beta", "gamma"),
                    include_scores=False,
                )
            self.assertFalse((root / "raw-only").exists())


if __name__ == "__main__":
    unittest.main()
