"""Reviewed source corrections stay scoped, byte-preserving and idempotent."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mairadar.parser import parse_chart

spec=importlib.util.spec_from_file_location('repairs',Path(__file__).resolve().parents[1]/'scripts/repair_test0913_charts.py')
repairs=importlib.util.module_from_spec(spec)
spec.loader.exec_module(repairs)


class RepairTests(unittest.TestCase):
    def test_confirmed_separators_preserve_both_notes_and_flags(self):
        cases = [('1b5b', '1b/5b', [('1', True, False), ('5', True, False)]),
                 ('1x8x', '1x/8x', [('1', False, True), ('8', False, True)]),
                 ('2x6x', '2x/6x', [('2', False, True), ('6', False, True)]),
                 ('1b2b', '1b/2b', [('1', True, False), ('2', True, False)]),
                 ('1b3', '1b/3', [('1', True, False), ('3', False, False)]),
                 ('8b6', '8b/6', [('8', True, False), ('6', False, False)])]
        for old, new, expected in cases:
            text = f'&inote_5=(120){old},E'
            fixed, edits = repairs.repair_text(text, 5, ((old, new),))
            parsed = parse_chart(fixed.split('=', 1)[1])
            self.assertTrue(parsed.complete)
            self.assertEqual([(e.position, e.is_break, e.is_ex) for e in parsed.events
                              if e.kind == 'tap'], expected)
            self.assertEqual({e.start_time_s for e in parsed.events}, {0})
            self.assertEqual(repairs.repair_text(fixed, 5, ((old, new),)), (fixed, []))
        fixed, _ = repairs.repair_text('&inote_4=(120)CfE5,E', 4, (('CfE5', 'Cf/E5'),))
        parsed = parse_chart(fixed.split('=', 1)[1])
        self.assertEqual([(e.position, e.flags_json.get('hanabi', False)) for e in parsed.events
                          if e.kind == 'touch'], [('C', True), ('E5', False)])

    def test_selected_duplicate_field_preserves_retained_bytes_and_other_difficulties(self):
        text = ('\ufeff&title=T\r\n&inote_6=\r\n(120)1,E\r\n'
                '&inote_5=(120)5,E\r\n&inote_6=(240)2,E\r\n'
                '&inote_7=(120)7,E\r\n')
        fixed, edits = repairs.repair_text(text, 6, (), ('inote_6', None))
        self.assertEqual(fixed, text.replace('&inote_6=(240)2,E\r\n', ''))
        self.assertEqual(len(edits), 1)
        self.assertEqual(repairs.repair_text(fixed, 6, (), ('inote_6', None)), (fixed, []))
        text = '&des_4=91.10\n&des_4=Luxizhel\r\n&des_5=Other\n&inote_4=(120)4,E'
        fixed, edits = repairs.repair_text(text, 4, (), ('des_4', 'Luxizhel'))
        self.assertEqual(fixed, text.removeprefix('&des_4=91.10\n'))
        self.assertEqual(repairs.repair_text(fixed, 4, (), ('des_4', 'Luxizhel')), (fixed, []))
        with self.assertRaisesRegex(ValueError, 'Cannot find selected'):
            repairs.repair_text(text, 4, (), ('des_4', 'unknown'))

    def test_exact_diagnostic_spans_do_not_change_valid_slide_endings_or_other_difficulties(self):
        text='\ufeff&inote_4=(120)3[4:1]/1-3[4:1],E\r\n&inote_5=(120)3[4:1],E\r\n'
        fixed,edits=repairs.repair_text(text,4,(('3[4:1]','3h[4:1]'),))
        self.assertEqual(len(edits),1)
        self.assertEqual(fixed,text.replace('3[4:1]/','3h[4:1]/',1))
        self.assertEqual(repairs.repair_text(fixed,4,(('3[4:1]','3h[4:1]'),)),(fixed,[]))
        text='&inote_4=(120){8}},1,{4}2,E'
        fixed,edits=repairs.repair_text(text,4,(('}',''),))
        self.assertEqual(fixed,'&inote_4=(120){8},1,{4}2,E')
        fixed, edits = repairs.repair_text('&inote_7=(120)1$/,1/1,,E',7,(('1$/','1$'),))
        self.assertEqual(fixed, '&inote_7=(120)1$,1/1,,E')
        self.assertEqual(len(edits), 1)

    def test_oboro_move_preserves_slide_time_and_simultaneous_tap(self):
        fixed,edits=repairs.repair_text('&inote_4=(120)2>6/6[4:3],E',4,(('2>6/6[4:3]','2>6[4:3]/6'),))
        p=parse_chart(fixed.split('=',1)[1]);self.assertTrue(p.complete)
        slide=next(e for e in p.events if e.kind=='slide')
        self.assertEqual((slide.start_time_s,slide.end_time_s),(.5,2))
        self.assertEqual([(e.position,e.is_slide_head) for e in p.events if e.kind=='tap'],[('2',True),('6',False)])

    def test_reviewed_geometry_is_valid_and_combined_duration_keeps_total(self):
        for path,difficulty,pairs in repairs.REPAIRS:
            for old,new in pairs:
                if 'V' in old or 'v' in old or ('[16:18]' in old):
                    result=parse_chart(f'(120){new},E')
                    self.assertTrue(result.complete,(old,new,result.diagnostics))
        result=parse_chart('(120)1x-5-2-8-6-3-7-4[4:5],E')
        slide=next(e for e in result.events if e.kind=='slide')
        self.assertEqual(slide.end_time_s-slide.start_time_s,2.5)

    def test_apply_backup_and_second_run_preserve_original_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'raw';root.mkdir()
            source=root/'maidata.txt';original=b'\xef\xbb\xbf&inote_4=(120)3[4:1],E\r\n'
            source.write_bytes(original)
            rules=[('maidata.txt',4,(('3[4:1]','3h[4:1]'),))]
            with patch.object(repairs,'REPAIRS',rules):
                backup=Path(directory)/'backup'
                self.assertEqual(repairs.run(root,backup)['changed_files'],1)
                self.assertEqual(source.read_bytes(),original)
                self.assertEqual(repairs.run(root,backup,apply=True)['changed_files'],1)
                self.assertEqual(source.read_bytes(),original.replace(b'3[',b'3h['))
                self.assertEqual(repairs.run(root,backup,apply=True)['changed_files'],0)
                self.assertEqual((backup/'maidata.txt').read_bytes(),original)
