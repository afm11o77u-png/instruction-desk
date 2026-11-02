"""括弧内の否定仕様（反射材無し 等）は商品名に残し、加工にしない。fixtures / temp DB のみ。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import product_ledger_import as pli  # noqa: E402
import product_ledger_paren as paren  # noqa: E402
import stock  # noqa: E402
from test_ledger_color_split_register import b64, ledger_csv, newdb  # noqa: E402

ROWS = [
    '顧客A,ミドリ安全北海道,G403,綿ブルゾン(反射材無し),L',
    '顧客A,ミドリ安全北海道,GS2373,綿シャツ(反射材なし),M',
    '顧客A,ミドリ安全北海道,GS2403,綿夏ジャンパー(反射材無),L',
    '顧客A,ミドリ安全北海道,G404,綿ブルゾン反射材付き,L',
    '顧客A,ミドリ安全北海道,G405,綿ブルゾン(未加工品),L',
]


class NegationParenTest(unittest.TestCase):
    def test_detect(self):
        for t in ('反射材無し', '反射材なし', 'ポケット無', '名入れ不要'):
            self.assertTrue(paren.is_negation_spec(t), t)
        for t in ('無', 'なし', '反射材付き', '無地'):
            self.assertFalse(paren.is_negation_spec(t), t)

    def test_goods_phase_keeps_name_no_review(self):
        folder, path, c = newdb()
        try:
            b = {'importMode': 'products', 'productPhase': 'goods', 'name': 'n.csv',
                 'file': b64(ledger_csv(ROWS)), '_canWrite': True, '_actor': {'name': 't'}}
            p = pli.build_preview(c, b)
            for r in p['rows']:
                self.assertEqual(r['status'], 'create', r.get('reasons'))
                self.assertFalse(r.get('processLinkMissing'))
            with mock.patch.object(stock, 'backup_sqlite', return_value=''):
                pli.commit_preview(c, {**b, 'commitToken': 'tok-ng-1',
                                       'previewFingerprint': p['previewFingerprint']}, db_path=path)
            names = {r['code']: r['name'] for r in c.execute('SELECT code,name FROM products')}
            self.assertEqual(names['G403'], '綿ブルゾン(反射材無し)')
            self.assertEqual(names['GS2373'], '綿シャツ(反射材なし)')
            self.assertEqual(names['GS2403'], '綿夏ジャンパー(反射材無)')
            self.assertEqual(names['G404'], '綿ブルゾン反射材付き')  # 別商品のまま
            self.assertEqual(names['G405'], '綿ブルゾン')  # #312: 未加工品は従来どおり落とす
            self.assertEqual(c.execute(
                'SELECT COUNT(*) FROM products WHERE category=?',
                (stock.CATEGORY_PROCESS_SERVICE,)).fetchone()[0], 0)
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
