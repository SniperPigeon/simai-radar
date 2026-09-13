#!/usr/bin/env python3
"""Apply the reviewed test0913 transcription corrections, outside the parser.

Dry-run by default. Only the listed files/difficulties and diagnosed tokens are
eligible. Preserve BOM/newlines, back up original bytes before writing, and emit
JSON changes. A second --apply run makes no changes. Requires Python >= 3.11.
"""

import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mairadar.parser import parse_text

REPAIRS = [('AstroDX-raw/04. GreeN PLUS/ハロ／ハワユ/maidata.txt',
  5,
  (('8v4[8:1]', '8-4[8:1]'), ('2v6[8:1]', '2-6[8:1]'))),
 ('AstroDX-raw/04. GreeN PLUS/待チ人ハ来ズ。/maidata.txt',
  5,
  (('7v3[8:1]', '7-3[8:1]'), ('2v6[8:1]', '2-6[8:1]'))),
 ('AstroDX-raw/05. ORANGE/MIRROR of MAGIC/maidata.txt',
  5,
  (('3v7[8:1]', '3-7[8:1]'), ('6v2[8:1]', '6-2[8:1]'))),
 ('AstroDX-raw/06. ORANGE PLUS/ってゐ！ ～えいえんてゐVer～/maidata.txt',
  5,
  (('3?v7[10000:1267]', '3?-7[10000:1267]'),)),
 ('AstroDX-raw/22. BUDDiES/Love Kills U/maidata.txt', 4, (('3[4:1]', '3h[4:1]'),)),
 ('AstroDX-raw/22. BUDDiES/Mystic Parade/maidata.txt', 3, (('s\r\n{2}7', '\r\n{2}7'),)),
 ('AstroDX-raw/22. BUDDiES/Oboro [DX]/maidata.txt', 4, (('2>6/6[4:3]', '2>6[4:3]/6'),)),
 ('AstroDX-raw/22. BUDDiES/[協] 青春コンプレックス [1P] [EASY]/maidata.txt',
  7,
  (('7[4:3]', '7h[4:3]'), ('7[4:1]', '7h[4:1]'), ('1$/', '1$'))),
 ('AstroDX-raw/22. BUDDiES/[協] 青春コンプレックス [2P] [EASY]/maidata.txt', 7, (('6[4:1]', '6h[4:1]'),)),
 ('AstroDX-raw/22. BUDDiES/ジェヘナ/maidata.txt',
  5,
  (('4V75-3[24:7]', '4V25-3[24:7]'), ('3qq4>3V28b[24:31]', '3qq4>3V58b[24:31]'))),
 ('AstroDX-raw/23. BUDDiES PLUS/Lunatic Vibes/maidata.txt', 4, (('4x[4:1]', '4xh[4:1]'),)),
 ('AstroDX-raw/23. BUDDiES PLUS/[奏] ヒトガタ [1P]/maidata.txt', 7, (('C8f', 'C'),)),
 ('AstroDX-raw/23. BUDDiES PLUS/ハッピーシンセサイザ [DX]/maidata.txt', 5, (('7V38b[4:1]', '7V58b[4:1]'),)),
 ('AstroDX-raw/24. PRiSM/Divide et impera!/maidata.txt', 4, (('7x[16:3]', '7xh[16:3]'),)),
 ('AstroDX-raw/24. PRiSM/[奏] アンビバレンス [2P]/maidata.txt', 7, (('C8f', 'C'),)),
 ('AstroDX-raw/24. PRiSM/[奏] 洗脳 [1P]/maidata.txt', 7, (('2h[8:1:]', '2h[8:1]'),)),
 ('AstroDX-raw/24. PRiSM/人マニア/maidata.txt',
  5,
  (('7bV36b[16:3]', '7bV16b[16:3]'),
   ('2V63-5[8:3]', '2V83-5[8:3]'),
   ('5qq4V82v1[4:5]', '5qq4V62v1[4:5]'))),
 ('AstroDX-raw/25. PRiSM PLUS/[戻] WE’RE BACK!!/maidata.txt',
  7,
  (('8?V31[4:1]*V57[4:1]', '8?V61[4:1]*V27[4:1]'),
   ('1?V42[4:1]*?V68[4:1]', '1?V72[4:1]*?V38[4:1]'))),
 ('AstroDX-raw/25. PRiSM PLUS/crazy (about you)/maidata.txt',
  4,
  (('1V72V64[4:3]', '1V72V84[4:3]'),)),
 ('AstroDX-raw/25. PRiSM PLUS/忙シー日/maidata.txt', 4, (('5[2:1]', '5h[2:1]'), ('1[4:1]', '1h[4:1]'))),
 ('AstroDX-raw/26. CiRCLE/Unwelcome School/maidata.txt', 3, (('C8', 'C'),)),
 ('AstroDX-raw/26. CiRCLE/[充] エンジェル ドリーム [2P]/maidata.txt', 7, (('1v5[4:1]', '1-5[4:1]'),)),
 ('AstroDX-raw/26. CiRCLE/[邪] アマノジャクリバース feat. ｙｔｒ/maidata.txt',
  7,
  (('1x-5-2-8-6-3-7[16:18]-4[8:1]', '1x-5-2-8-6-3-7-4[4:5]'),
   ('8x-5-2-4-7-3-6[16:18]-1[8:1]>7[4:1]', '8x-5-2-4-7-3-6-1>7[2:3]'),
   ('4x-2-6-4-7-3[16:15]>5[16:5]-8[4:1]', '4x-2-6-4-7-3>5-8[2:3]'),
   ('5x<8>3>1<6>2[16:15]-6[16:5]', '5x<8>3>1<6>2-6[4:5]'),
   ('4xqq4qq4[8:6]-1[8:1]', '4xqq4qq4-1[8:7]'),
   ('8xqq8qq8[8:6]qq8[4:1]-5[8:1]', '8xqq8qq8qq8-5[8:9]'),
   ('3xpp3pp3pp3[8:9]pp3[4:1]', '3xpp3pp3pp3pp3[8:11]'),
   ('3x>1>2<1>2<1>2[16:6]<6[16:7]', '3x>1>2<1>2<1>2<6[16:13]'),
   ('3x>6[4:1]<7<6<7<6<7<6<7<6<7<6<7[16:11]<5[16:5]', '3x>6<7<6<7<6<7<6<7<6<7<6<7<5[4:5]'),
   ('5x<6>5<6>5<6>5<6>5[16:8]<8[8:1]', '5x<6>5<6>5<6>5<6>5<8[8:5]'),
   ('4x>2>3>2>3>2>3>2>3>2>3>2>3>2>3>2>3>2>3[16:18]>1[16:1]',
    '4x>2>3>2>3>2>3>2>3>2>3>2>3>2>3>2>3>2>3>1[16:19]'))),
 ('AstroDX-raw/26. CiRCLE/give it up to you/maidata.txt', 4, (('}', ''),)),
 ('AstroDX-raw/26. CiRCLE/有頂天ドリーマーズ/maidata.txt', 3, (('7s', '7'),)),
 ('AstroDX-raw/02. maimai PLUS/Link/maidata.txt', 5, (('1b5b', '1b/5b'),)),
 ('AstroDX-raw/20. FESTiVAL/アノーイング！さんさんウィーク！/maidata.txt', 3,
  (('1x8x', '1x/8x'),)),
 ('AstroDX-raw/24. PRiSM/Abstruse Dilemma/maidata.txt', 5, (('2x6x', '2x/6x'),)),
 ('AstroDX-raw/24. PRiSM/果ての空、僕らが見た光。/maidata.txt', 4, (('CfE5', 'Cf/E5'),)),
 ('AstroDX-raw/26. CiRCLE/7 Wonders/maidata.txt', 2, (('1b2b', '1b/2b'),)),
 ('AstroDX-raw/27. CiRCLE PLUS/[奏] クレイジークレイジーダンサーズ [1P]/maidata.txt', 7,
  (('1b3', '1b/3'),)),
 ('AstroDX-raw/27. CiRCLE PLUS/[奏] クレイジークレイジーダンサーズ [2P]/maidata.txt', 7,
  (('8b6', '8b/6'),)),
 ('AstroDX-raw/23. BUDDiES PLUS/INTERNET YAMERO/maidata.txt', 6, ()),
 ('AstroDX-raw/25. PRiSM PLUS/∀/maidata.txt', 4, ())]

# Explicit user choices for these two files only; no global duplicate policy.
# None selects the first declaration, a string selects that scalar value.
FIELD_SELECTIONS = {
    'AstroDX-raw/23. BUDDiES PLUS/INTERNET YAMERO/maidata.txt': ('inote_6', None),
    'AstroDX-raw/25. PRiSM PLUS/∀/maidata.txt': ('des_4', 'Luxizhel'),
}
FIELD = re.compile(r'^[ \t]*&([^=\s]+)=[ \t]*', re.MULTILINE)


def repair_text(text, difficulty, replacements, field_selection=None):
    """Plan exact source-span edits; never replace substrings inside valid notes."""
    bom = text.startswith('\ufeff')
    source = text.removeprefix('\ufeff')
    bundle = parse_text(source, difficulties=[difficulty])[0]
    edits = {}
    if field_selection is not None:
        key, preferred = field_selection
        fields = list(FIELD.finditer(source))
        candidates = [(match, fields[i + 1].start() if i + 1 < len(fields) else len(source))
                      for i, match in enumerate(fields) if match[1] == key]
        selected = next((match.start() for match, end in candidates
                         if preferred is None or source[match.end():end].strip() == preferred), None)
        if selected is None:
            raise ValueError(f'Cannot find selected &{key} value: {preferred!r}')
        for match, end in candidates:
            start = match.start()
            if start == selected:
                continue
            edits[start, end] = dict(
                start=start, end=end, line=source.count('\n', 0, start) + 1,
                column=start - source.rfind('\n', 0, start),
                before=source[start:end], after='',
            )
    for diagnostic in bundle.diagnostics:
        if diagnostic.severity != 'error':
            continue
        for old, new in replacements:
            start, end = diagnostic.source_start, diagnostic.source_end
            if old == '2>6/6[4:3]' and diagnostic.raw_text == '2>6':
                end = start + len(old)
            elif diagnostic.raw_text != old:
                continue
            if source[start:end] != old:
                raise ValueError(f'Original source mismatch at {start}:{end}')
            edits[start, end] = dict(
                start=start, end=end, line=diagnostic.source_line,
                column=diagnostic.source_column, before=old, after=new,
            )
    ordered = sorted(edits.values(), key=lambda e: e['start'])
    for left, right in zip(ordered, ordered[1:]):
        if left['end'] > right['start']:
            raise ValueError('Overlapping corrections')
    for edit in reversed(ordered):
        source = source[:edit['start']] + edit['after'] + source[edit['end']:]
    return ('\ufeff' if bom else '') + source, ordered


def run(root, backup_root, *, apply=False):
    root, backup_root = Path(root).resolve(), Path(backup_root).resolve()
    if backup_root.is_relative_to(root):
        raise ValueError('Backups must be outside the input tree')
    planned = []
    failures = []
    for relative, difficulty, replacements in REPAIRS:
        path = root / relative
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError('Refusing a source symlink outside the input tree')
            original = path.read_bytes()
            selection = FIELD_SELECTIONS.get(relative)
            updated, edits = repair_text(original.decode('utf-8'), difficulty, replacements, selection)
            if not edits:
                continue
            # Verify idempotence before touching the source file.
            repeated, second_edits = repair_text(updated, difficulty, replacements, selection)
            if repeated != updated or second_edits:
                raise ValueError('Correction did not converge in one pass')
            planned.append((path, original, updated.encode('utf-8'), relative, difficulty, edits))
        except (OSError, ValueError, UnicodeError) as exc:
            failures.append(dict(source=relative, error=str(exc)))
    # Preflight every known file before making any changes.
    if failures:
        return dict(applied=False, changed_files=0, changes=[], failures=failures)
    if apply:
        # Create all backups before the first source write. Never overwrite one.
        for path, original, updated, relative, difficulty, edits in planned:
            backup = backup_root / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            if backup.exists():
                if backup.is_symlink() or backup.read_bytes() != original:
                    raise ValueError(f'Existing backup differs: {backup}')
            else:
                with backup.open('xb') as stream:
                    stream.write(original)
        for path, original, updated, relative, difficulty, edits in planned:
            if path.read_bytes() != original:
                raise ValueError(f'Source changed during repair: {path}')
            path.write_bytes(updated)
    changes = [dict(source=relative, difficulty_index=difficulty, edits=edits)
               for path, original, updated, relative, difficulty, edits in planned]
    return dict(applied=apply, changed_files=len(changes),
                changed_tokens=sum(len(x['edits']) for x in changes), changes=changes, failures=[])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1] / 'data/raw')
    parser.add_argument('--backup-root', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'outputs/test0913-repairs/originals')
    parser.add_argument('--apply', action='store_true', help='Write corrections (default: preview only)')
    args = parser.parse_args(argv)
    try:
        result = run(args.root, args.backup_root, apply=args.apply)
    except (OSError, ValueError) as exc:
        print(json.dumps(dict(error=str(exc)), ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(bool(result['failures']))


if __name__ == '__main__':
    raise SystemExit(main())
