"""旧QR対応ダイアログの表示。照合ルール・APIは触らない。"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

JS = Path(__file__).resolve().parents[1] / 'static' / 'qr_link.js'

HARNESS = r'''
const fs = require('fs');
const vm = require('vm');
const sb = {console, window: {}};
vm.createContext(sb);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sb);
const t = sb.window.QrLink.__test;
const spec = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const out = {};
for (const [name, args] of Object.entries(spec)) {
  out[name] = args.kind === 'dialog'
    ? t.renderDialog(args.vm)
    : t.previewHtml(args.preview, args.opts || {});
}
process.stdout.write(JSON.stringify(out));
'''


def legacy(**kw):
    base = {
        'stockId': 'S1', 'customerId': 'C1', 'itemNum': 'P-1', 'itemName': '品',
        'customerName': '客', 'itemSize': 'L', 'condition': 'new',
    }
    base.update(kw)
    return base


def row(**kw):
    base = {
        'rowNo': 1,
        'status': 'review',
        'statusLabel': '要確認',
        'reasons': [],
        'legacy': legacy(),
        'candidates': [{'key': 'k1', 'label': '候補A'}],
        'existing': None,
        'includeDefault': False,
        'canCommit': True,
    }
    base.update(kw)
    return base


def sample_preview():
    rows = [
        row(rowNo=1, status='exact', statusLabel='完全一致', includeDefault=True,
            candidates=[{'key': 'auto', 'label': '完全一致の候補'}]),
        row(rowNo=2, status='review', statusLabel='要確認'),
        row(rowNo=3, status='ambiguous', statusLabel='候補が複数',
            candidates=[{'key': 'a', 'label': 'A'}, {'key': 'b', 'label': 'B'}]),
        row(rowNo=4, status='unmatched', statusLabel='未一致', canCommit=False, candidates=[]),
        row(rowNo=5, status='invalid', statusLabel='不正', canCommit=False, candidates=[],
            reasons=['旧在庫行IDが空です。']),
        row(rowNo=6, status='duplicate', statusLabel='同じ旧ID', canCommit=False, candidates=[]),
        row(rowNo=7, status='unchanged', statusLabel='登録済み', canCommit=False, candidates=[]),
        row(rowNo=8, status='conflict', statusLabel='別対応が登録済み',
            existing={'productId': 9, 'sizeLabel': 'M', 'condition': 'used'}),
        row(rowNo=9, status='other', statusLabel='別ステータス', canCommit=False, candidates=[]),
    ]
    counts = {
        '完全一致': 1, '要確認': 1, '候補が複数': 1, '未一致': 1, '不正': 1,
        '同じ旧ID': 1, '登録済み': 1, '別対応が登録済み': 1,
    }
    return {
        'fileName': 'qr-link-from-legacy-1.xlsx',
        'counts': counts,
        'notices': ['この段階ではデータベースを変更していません。'],
        'rows': rows,
    }


def section(html, sid):
    import re
    m = re.search(
        rf'<(section|details)\b[^>]*data-qr-section="{sid}"[^>]*>[\s\S]*?</\1>',
        html,
    )
    return m.group(0) if m else ''


def opening_tag(html):
    return html.split('>', 1)[0]


class QrLinkUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = {
            'mixed': {'kind': 'preview', 'preview': sample_preview(), 'opts': {'writable': True, 'busy': False}},
            'exactOnly': {
                'kind': 'preview',
                'preview': {
                    'fileName': 'only.xlsx',
                    'counts': {'完全一致': 1, '要確認': 0},
                    'notices': [],
                    'rows': [row(rowNo=11, status='exact', statusLabel='完全一致', includeDefault=True,
                                 candidates=[{'key': 'auto', 'label': '自動'}])],
                },
                'opts': {'writable': True, 'busy': False},
            },
            'matching': {
                'kind': 'dialog',
                'vm': {
                    'busy': True,
                    'progressLabel': '照合中…（登録はまだしていません）',
                    'writable': True,
                    'error': '',
                    'preview': None,
                    'result': None,
                },
            },
            'saving': {
                'kind': 'dialog',
                'vm': {
                    'busy': True,
                    'progressLabel': '登録中…',
                    'writable': True,
                    'error': '',
                    'result': None,
                    'preview': {
                        'fileName': 'qr.xlsx',
                        'counts': {'要確認': 1, '完全一致': 1},
                        'notices': [],
                        'rows': [
                            row(rowNo=1, status='exact', statusLabel='完全一致', includeDefault=True),
                            row(rowNo=2, status='review', statusLabel='要確認'),
                        ],
                    },
                },
            },
            'failed': {
                'kind': 'dialog',
                'vm': {
                    'busy': False,
                    'progressLabel': '',
                    'writable': True,
                    'error': '照合に失敗しました。',
                    'preview': None,
                    'result': None,
                },
            },
            'idle': {
                'kind': 'dialog',
                'vm': {
                    'busy': False,
                    'progressLabel': '',
                    'writable': True,
                    'error': '',
                    'preview': None,
                    'result': None,
                },
            },
            'diffs': {
                'kind': 'preview',
                'preview': {
                    'fileName': 'diff.xlsx',
                    'counts': {'要確認': 1, '候補が複数': 1, '完全一致': 1},
                    'notices': [],
                    'rows': [
                        row(rowNo=1, status='exact', statusLabel='完全一致', includeDefault=True,
                            candidates=[{'key': 'auto', 'label': '完全一致の候補', 'matchNote': '完全一致'}]),
                        row(
                            rowNo=2, status='review', statusLabel='要確認',
                            reasons=['メーカーが違います（旧：(有)ユニスタイル / 今：熊谷縫製）'],
                            legacy=legacy(itemNum='別注', itemName='ブルーエプロン (胸当・サックス)'),
                            candidates=[{
                                'key': 'k1', 'productName': 'ブルーエプロン',
                                'label': '別注 ブルーエプロン（メーカーが違います（旧：(有)ユニスタイル / 今：熊谷縫製）、在庫ロット 1件）',
                                'matchNote': 'メーカーが違います（旧：(有)ユニスタイル / 今：熊谷縫製）',
                            }],
                        ),
                        row(
                            rowNo=3, status='ambiguous', statusLabel='候補が複数',
                            reasons=[
                                '候補が複数あります。一つ選ぶまで登録しません。',
                                'ネイビーエプロン：品名が違います（旧：作業着 / 今：ネイビーエプロン）',
                                'ブルーエプロン：品名が違います（旧：作業着 / 今：ブルーエプロン）',
                            ],
                            candidates=[
                                {'key': 'a', 'productName': 'ネイビーエプロン', 'label': 'ネイビー',
                                 'matchNote': '品名が違います（旧：作業着 / 今：ネイビーエプロン）'},
                                {'key': 'b', 'productName': 'ブルーエプロン', 'label': 'ブルー',
                                 'matchNote': '品名が違います（旧：作業着 / 今：ブルーエプロン）'},
                            ],
                        ),
                    ],
                },
                'opts': {'writable': True, 'busy': False},
            },
        }
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as harness:
            harness.write(HARNESS)
            harness_path = harness.name
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8') as blob:
            json.dump(spec, blob, ensure_ascii=False)
            blob_path = blob.name
        proc = subprocess.run(
            ['node', harness_path, str(JS), blob_path],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            raise AssertionError(proc.stderr or proc.stdout)
        cls.html = json.loads(proc.stdout)

    def test_sections_are_split(self):
        html = self.html['mixed']
        main = section(html, 'main')
        exact = section(html, 'exact')
        unmatched = section(html, 'unmatched')
        invalid = section(html, 'invalid')
        ambiguous_home = main
        self.assertTrue(main.startswith('<section'))
        self.assertIn('>要確認<', main)
        self.assertIn('>候補が複数<', ambiguous_home)
        self.assertIn('候補が複数 1 件もこの一覧です。', main)
        for label in ('完全一致', '未一致', '不正', '同じ旧ID', '登録済み', '別対応が登録済み', '別ステータス'):
            self.assertNotIn(f'>{label}<', main)
        self.assertNotIn('data-qr-include="1"', main)
        self.assertIn('data-qr-include="2"', main)
        self.assertIn('data-qr-include="3"', main)

        self.assertTrue(opening_tag(exact).startswith('<details'))
        self.assertNotIn(' open', opening_tag(exact))
        self.assertIn('>完全一致<', exact)
        self.assertIn('data-qr-include="1" checked', exact)
        self.assertIn('初期選択のまま登録します。', exact)
        self.assertNotIn('>要確認<', exact)
        self.assertNotIn('>未一致<', exact)

        self.assertIn(' open', opening_tag(unmatched))
        self.assertIn('>未一致<', unmatched)
        self.assertNotIn('data-qr-include=', unmatched)

        self.assertIn('qr-link-section-alert', invalid)
        self.assertIn('>不正<', invalid)
        self.assertNotIn('>要確認<', invalid)

        conflict = section(html, 'conflict')
        duplicate = section(html, 'duplicate')
        unchanged = section(html, 'unchanged')
        other = section(html, 'other')
        self.assertIn('>別対応が登録済み<', conflict)
        self.assertIn('data-qr-replace="8"', conflict)
        self.assertIn('qr-link-section-alert', duplicate)
        self.assertIn('>同じ旧ID<', duplicate)
        self.assertTrue(opening_tag(unchanged).startswith('<details'))
        self.assertNotIn(' open', opening_tag(unchanged))
        self.assertIn('>登録済み<', unchanged)
        self.assertIn('>別ステータス<', other)

        self.assertIn(
            '完全一致 1、要確認 1、候補が複数 1、未一致 1、不正 1、同じ旧ID 1、登録済み 1、別対応が登録済み 1',
            html,
        )
        self.assertLess(html.find('data-qr-section="invalid"'), html.find('data-qr-section="main"'))
        self.assertLess(html.find('data-qr-section="main"'), html.find('data-qr-section="exact"'))
        self.assertLess(html.find('data-qr-section="exact"'), html.find('data-qr-section="unmatched"'))

    def test_exact_only_keeps_main_empty_and_checked(self):
        html = self.html['exactOnly']
        main = section(html, 'main')
        exact = section(html, 'exact')
        self.assertIn('要確認の行はありません。', main)
        self.assertNotIn('<table', main)
        self.assertNotIn(' open', opening_tag(exact))
        self.assertIn('data-qr-include="11" checked', exact)
        # 件数0はサマリから省く（従来どおり）
        head = html.split('data-qr-section="main"', 1)[0]
        self.assertNotIn('要確認 0', head)
        self.assertIn('完全一致 1', head)

    def test_busy_labels_and_disabled_controls(self):
        matching = self.html['matching']
        self.assertIn('role="status"', matching)
        self.assertIn('照合中…（登録はまだしていません）', matching)
        self.assertIn('id="qrPreview" disabled>照合中…', matching)
        self.assertIn('id="qrTemplate" disabled>', matching)
        self.assertIn('id="qrFile"', matching)
        self.assertIn('disabled>', matching[matching.find('id="qrFile"'):])
        self.assertNotIn('id="qrProgress"', matching.replace('id="qrProgress"', '', 1))
        self.assertNotIn('照合に失敗しました。', matching)

        saving = self.html['saving']
        self.assertIn('id="qrProgress"', saving)
        self.assertIn('登録中…', saving)
        self.assertIn('id="qrCommit" disabled>登録中…', saving)
        self.assertIn('id="qrPreview" disabled>照合する', saving)
        self.assertIn('data-qr-include="1" checked disabled', saving)
        self.assertIn('data-qr-include="2" disabled', saving)
        self.assertIn('data-qr-key="2" disabled', saving)
        exact = section(saving, 'exact')
        main = section(saving, 'main')
        self.assertIn('data-qr-include="1"', exact)
        self.assertNotIn('data-qr-include="1"', main)

        failed = self.html['failed']
        self.assertIn('role="alert"', failed)
        self.assertIn('照合に失敗しました。', failed)
        self.assertNotIn('id="qrProgress"', failed)
        self.assertNotIn('照合中…', failed)
        self.assertIn('id="qrPreview">照合する', failed)

        idle = self.html['idle']
        self.assertNotIn('id="qrProgress"', idle)
        self.assertIn('id="qrPreview">照合する', idle)
        self.assertNotIn('disabled', idle)

    def test_progress_is_shown_before_the_request_finishes(self):
        js = JS.read_text(encoding='utf-8')
        preview = js.split('async function runPreview')[1].split('async function runCommit')[0]
        commit = js.split('async function runCommit')[1].split('function open')[0]
        self.assertIn('if(st.busy)return', preview)
        self.assertIn('if(st.busy)return', commit)
        self.assertIn("const LABEL_READING='読み込み中…'", js)
        self.assertIn("const LABEL_MATCHING='照合中…（登録はまだしていません）'", js)
        self.assertIn("const LABEL_SAVING='登録中…'", js)
        self.assertLess(preview.find('selectedFile'), preview.find('session.busy=true'))
        self.assertLess(preview.find('LABEL_READING'), preview.find("'/api/qr-link/preview'"))
        self.assertLess(preview.find('LABEL_MATCHING'), preview.find("'/api/qr-link/preview'"))
        self.assertLess(preview.find("'/api/qr-link/preview'"), preview.rfind("session.progressLabel=''"))
        self.assertLess(commit.find('selections('), commit.find('LABEL_SAVING'))
        self.assertLess(commit.find('LABEL_SAVING'), commit.find("'/api/qr-link/commit'"))
        self.assertLess(commit.find("'/api/qr-link/commit'"), commit.rfind("session.progressLabel=''"))
        # 完全一致を登録対象から外す処理は足していない
        self.assertNotIn('includeDefault=false', js)
        self.assertIn("row.includeDefault?' checked'", js)

    def test_review_reasons_are_visible_without_opening_candidates(self):
        import re
        html = self.html['diffs']
        main = section(html, 'main')
        exact = section(html, 'exact')
        self.assertIn('qr-link-diff', main)
        self.assertIn('メーカーが違います（旧：(有)ユニスタイル / 今：熊谷縫製）', main)
        self.assertIn('ネイビーエプロン：品名が違います（旧：作業着 / 今：ネイビーエプロン）', main)
        self.assertIn('ブルーエプロン：品名が違います（旧：作業着 / 今：ブルーエプロン）', main)
        body = re.sub(r'<option\b[\s\S]*?</option>', '', main)
        self.assertIn('メーカーが違います（旧：(有)ユニスタイル / 今：熊谷縫製）', body)
        self.assertNotIn('メーカーが違います', exact)


if __name__ == '__main__':
    unittest.main()
