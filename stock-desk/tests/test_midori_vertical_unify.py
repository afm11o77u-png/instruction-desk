"""ミドリ安全の制服上下自動統合テスト。"""
from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import product_ledger_import as pli  # noqa: E402
import stock  # noqa: E402


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def _b64(text: str) -> str:
    return base64.b64encode(text.encode('utf-8-sig')).decode('ascii')


def _body(rows):
    csv_text = '\n'.join(['顧客,仕入先,商品コード,商品名,サイズ'] + rows)
    return {
        'name': 'ledger.csv',
        'file': _b64(csv_text),
        'importMode': 'products',
        'productPhase': 'products',
        '_canWrite': True,
        '_actor': {'name': 'テスト管理者'},
    }


class MidoriVerticalUnifyTest(unittest.TestCase):
    def test_midori_unifies_to_suffix_when_counterpart_exists(self):
        folder, path, c = newdb()
        try:
            rows = [
                # VEHS29N: プレーン(パンツ) + 下(白下) -> VEHS29N下 に統合
                '顧客A,ミドリ安全北海道㈱,VEHS29N,兼用パンツ 紺,S',
                '顧客A,ミドリ安全北海道㈱,VEHS29N 下,白下 ネイビー,M',
                # VEHS3212B: プレーン(上衣) + 上(上衣) -> VEHS3212B上 に統合
                '顧客A,ミドリ安全北海道㈱,VEHS3212B,兼用上衣 ブルー,SS',
                '顧客A,ミドリ安全北海道㈱,VEHS3212B上,上衣、青衣,S',
                # VEHS3212W上: 単独上衣 -> 確認不要でそのままVEHS3212W上
                '顧客A,ミドリ安全北海道㈱,VEHS3212W上,上衣、白衣,M',
                # 他仕入先: パンツだが相方なし -> 変更なし(ABC-100)
                '顧客A,別仕入先㈱,ABC-100,作業服 パンツ,L',
                # ミドリ安全だが相方なし -> 変更なし(XYZ-99)
                '顧客A,ミドリ安全北海道㈱,XYZ-99,作業ズボン,LL',
            ]
            body = _body(rows)
            preview = pli.build_preview(c, body)

            # プレビューの行コードを確認
            codes_by_row = {r['rowNo']: (r.get('product') or {}).get('code') for r in preview['rows']}
            self.assertEqual(codes_by_row[2], 'VEHS29N下')
            self.assertEqual(codes_by_row[3], 'VEHS29N下')
            self.assertEqual(codes_by_row[4], 'VEHS3212B上')
            self.assertEqual(codes_by_row[5], 'VEHS3212B上')
            self.assertEqual(codes_by_row[6], 'VEHS3212W上')
            self.assertEqual(codes_by_row[7], 'ABC-100')
            self.assertEqual(codes_by_row[8], 'XYZ-99')

            # codeShapeGroups の確認: VEHS29N, VEHS3212B, VEHS3212W上 は1メンバーまたは統合済みのため確認グループにならない
            shape_group_ids = [g['id'] for g in preview.get('codeShapeGroups') or []]
            self.assertNotIn('midori:VEHS29N', shape_group_ids)
            self.assertNotIn('midori:VEHS3212B', shape_group_ids)
            self.assertNotIn('midori:VEHS3212W', shape_group_ids)

            # コミットしてDB登録を確認（未決定エラーが出ずに正常登録できること）
            res = pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'midori-unify-token-1',
            }, db_path=str(path))
            c.commit()

            registered_codes = sorted(r['code'] for r in c.execute('SELECT code FROM products'))
            self.assertIn('VEHS29N下', registered_codes)
            self.assertIn('VEHS3212B上', registered_codes)
            self.assertIn('VEHS3212W上', registered_codes)
            self.assertIn('ABC-100', registered_codes)
            self.assertIn('XYZ-99', registered_codes)
            self.assertNotIn('VEHS29N', registered_codes)
            self.assertNotIn('VEHS3212B', registered_codes)

            # VEHS29N下 のサイズがSとM両方登録されていること
            p_veh = c.execute("SELECT id FROM products WHERE code='VEHS29N下'").fetchone()
            sizes = sorted(r['label'] for r in c.execute('SELECT label FROM product_sizes WHERE product_id=?', (p_veh['id'],)))
            self.assertEqual(sizes, ['M', 'S'])

            # VEHS3212B上 のサイズがSとSS両方登録されていること
            p_veh_b = c.execute("SELECT id FROM products WHERE code='VEHS3212B上'").fetchone()
            sizes_b = sorted(r['label'] for r in c.execute('SELECT label FROM product_sizes WHERE product_id=?', (p_veh_b['id'],)))
            self.assertEqual(sizes_b, ['S', 'SS'])

        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
