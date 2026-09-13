"""Regression cases for the reviewed official-chart compatibility policy."""

import csv
import json
from pathlib import Path
import tempfile
import unittest

from mairadar.batch import analyze_source
from mairadar.batch import analyze_directory
from mairadar.exporters import CsvExporter
from mairadar.exporters.visualizer import VisualizerExporter
from mairadar.io import read_bundle, write_bundle
from mairadar.parser import parse_chart, parse_text
from mairadar.parse_export import parse_source_to_bundles
from mairadar.validation import validate_result


class CompatibilityTests(unittest.TestCase):
    def test_batch_skips_empty_charts_but_keeps_invalid_or_missing_charts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'maidata.txt'
            path.write_text('&title=T\n&inote_1=\n&inote_2=|| comment\n'
                            '&inote_3=(120){4},,E\n&inote_4=(120)1,E\n'
                            '&inote_5=(120)bad,E')
            batch = analyze_source(path, difficulties=[4, 5])
            self.assertEqual([r.chart.difficulty_index for r in batch.records], [4, 5])
            self.assertEqual([r.status for r in batch.records], ['ok', 'error'])
            missing = analyze_source(path, difficulties=[6])
            self.assertEqual(len(missing.records), 1)
            self.assertEqual(missing.records[0].status, 'error')
            exported = parse_source_to_bundles(path, root / 'bundles')
            self.assertEqual([r.chart.difficulty_index for r in exported.records], [4, 5])
            self.assertEqual(exported.exported_bundles, 2)
            empty = parse_text('&title=empty\n&inote_1=(120),,E')[0]
            write_bundle(empty, root / 'bundles')
            restored = analyze_directory(root / 'bundles', difficulties=[4, 5])
            self.assertEqual(len(restored.records), 2)

    def test_bad_difficulty_metadata_and_body_do_not_poison_siblings(self):
        text = ('&des_4=A\n&des_4=B\n&lv_3=6\n&lv_3=6\n'
                '&inote_2=(120)1,E\n&inote_3=(120)2,E\n'
                '&inote_4=(120)3,E\n&inote_5=(120)Z,E\n'
                '&inote_6=(120)4,E\n&inote_6=(120)5,E')
        bundles = parse_text(text)
        self.assertEqual([b.complete for b in bundles], [True, True, False, False, False])
        self.assertFalse(bundles[0].diagnostics)
        self.assertEqual(bundles[1].diagnostics[0].severity, 'info')
        self.assertEqual(bundles[2].diagnostics[0].raw_text, '&des_4=B\n')
        self.assertEqual([e.position for e in bundles[-1].events if e.kind=='tap'], ['5'])
        scoped_tail = parse_text('&des_4=A\njunk\n&inote_2=(120)1,E\n&inote_4=(120)2,E')
        self.assertEqual([b.complete for b in scoped_tail], [True, False])

    def test_invalid_meter_comments_do_not_change_time_or_phase(self):
        base = parse_chart('(120){4}1,2,E')
        for comment in ('||some pattern,8,', '||solips', '||s 3/no', '||s 0/4', '||s 3/4/5'):
            result = parse_chart(f'(120){{4}}1,{comment}\n2,E')
            self.assertTrue(result.complete)
            self.assertEqual([(e.position,e.start_time_s,e.start_beat) for e in result.events],
                             [(e.position,e.start_time_s,e.start_beat) for e in base.events])
        self.assertFalse(parse_chart('(120)||s 3/4\n1,E').complete)

    def test_end_marker_ignores_everything_after_it_without_confusing_touch_or_exponents(self):
        for trailing in (',2,', '\r\nE', '(240){1},8,', ',(invalid),||s 3/4\n<HS*2>'):
            text = '(120){4}1,E' + trailing
            result = parse_chart(text)
            self.assertTrue(result.complete, result.diagnostics)
            self.assertEqual(result.chart_end_time_s, .5)
            self.assertEqual([e.position for e in result.events if e.kind!='timing'], ['1'])
            self.assertEqual(result.diagnostics[-1].severity, 'info')
            for e in result.events:
                self.assertEqual(text[e.source_start:e.source_end],e.raw_token)
            validate_result(result)
        result = parse_chart('(1.2E2){4}E 1/E8f,1h[#1E-3],E')
        self.assertTrue(result.complete)
        self.assertEqual([e.position for e in result.events if e.kind!='timing'], ['E1','E8','1'])
        bundles = parse_text('&inote_2=(120)1,E,8\n&inote_3=(120)2,E')
        self.assertEqual([[e.position for e in b.events if e.kind=='tap'] for b in bundles], [['1'],['2']])
        self.assertFalse(parse_chart('(120)CfE5,E').complete)

    def test_slide_b_ignored_only_when_not_before_bracket_or_at_end(self):
        result = parse_chart('(120)1p3b>5-1b[4:1]*-5b-1[4:1],2b-6b-2[4:1]b,E')
        self.assertTrue(result.complete,result.diagnostics)
        slides = [e for e in result.events if e.kind=='slide']
        self.assertEqual([e.is_break for e in slides], [True,False,True])
        heads = [e for e in result.events if e.is_slide_head]
        self.assertEqual([e.is_break for e in heads], [False,True])
        self.assertEqual([len(e.slide_path_json) for e in slides], [3,2,2])

    def test_missing_metadata_and_cover_conflict_do_not_fail_csv_or_visualizer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for folder, data in [('a',b'first cover'),('b',b'different cover')]:
                target=root/folder;target.mkdir()
                (target/'maidata.txt').write_text('(120)1h[4:1],E')
                (target/'bg.png').write_bytes(data)
            batch = analyze_source(root)
            for record in batch.records:
                self.assertIsNone(record.chart.title)
                record.chart.chart_type=None
            report=CsvExporter().export(batch.records,root/'csv',feature_names=batch.feature_names)
            with report.csv_path.open(encoding='utf-8-sig',newline='') as stream:
                rows=list(csv.DictReader(stream))
            self.assertEqual([r['title'] for r in rows],['',''])
            self.assertEqual([r['status'] for r in rows],['ok','ok'])
            self.assertEqual(rows[1]['cover_path'],'')
            self.assertEqual(report.exit_code,0)
            report=VisualizerExporter().export(batch.records,root/'site',feature_names=batch.feature_names,include_scores=True)
            payload=json.loads(report.data_path.read_text())
            self.assertEqual(report.exit_code,0)
            self.assertEqual(payload['songs'][1]['title'],'')
            self.assertIsNone(payload['songs'][1]['charts'][0]['cover'])
            b=parse_text('(120)1,E')[0]
            exported=read_bundle(write_bundle(b,root/'bundles'))
            self.assertIsNone(exported.chart.title)
            self.assertIsNone(exported.chart.chart_type)
            self.assertIsNone(exported.chart.difficulty_index)
