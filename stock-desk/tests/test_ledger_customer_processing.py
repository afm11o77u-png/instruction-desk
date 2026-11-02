import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import stock  # noqa: E402
import product_ledger_import as pli  # noqa: E402
from test_ledger_color_split_register import b64, ledger_csv, newdb, opening_csv  # noqa: E402


def _rows():
    rows = []
    for cust in ('顧客A', '顧客B', '顧客C'):
        for size in ('M', 'L'):
            rows.append(f'{cust},仕入S,CD630,白衣,{size}')
    rows.append('顧客D,仕入S,CD630,＊長袖ジャンパー　白,M')
    for size in ('M', 'L'):
        rows.append(f'釧路丸水㈱,仕入S,CD630,白衣(内ポケット加工),{size}')
        rows.append(f'釧路丸水㈱,仕入S,CD630,＊白衣(内ポケット加工),{size}')
    return rows


class CustomerProcessingTest(unittest.TestCase):
    def _import(self, aux=None):
        folder, path, c = newdb()
        self.addCleanup(folder.cleanup)
        base = {'importMode': 'products', 'name': 'p.csv', 'file': b64(ledger_csv(_rows())),
                '_canWrite': True, '_actor': {'name': 't'}}
        if aux:
            base.update({'auxName': 'o.csv', 'auxFile': b64(opening_csv(aux))})
        for i, phase in enumerate(('parts', 'goods')):
            body = {**base, 'productPhase': phase}
            p = pli.build_preview(c, body)
            und = [g['code'] for g in p.get('skuGroups') or [] if g.get('status') == 'needs_decision']
            if und:
                d = {k: {'decision': 'merge'} for k in und}
                body.update(skuDecisions=d, appliedSkuDecisions=d)
                p = pli.build_preview(c, body)
            with mock.patch.object(stock, 'backup_sqlite', return_value=''):
                pli.commit_preview(c, {**body, 'commitToken': f'cust-proc-{i}',
                                       'previewFingerprint': p['previewFingerprint']}, db_path=path)
        return c, path

    def _handles(self, c):
        return {r[0]: (r[1], r[2], r[3]) for r in c.execute(
            """SELECT cu.name,p.name,cp.embroidery,(SELECT group_concat(s.name) FROM customer_product_processes x
               JOIN products s ON s.id=x.process_product_id WHERE x.customer_product_id=cp.id)
               FROM customer_products cp JOIN customers cu ON cu.id=cp.customer_id
               JOIN products p ON p.id=cp.product_id WHERE p.code='CD630'""")}

    def test_customer_paren_processing_goes_to_spec_and_name_is_majority(self):
        c, _ = self._import()
        h = self._handles(c)
        self.assertEqual(h['釧路丸水㈱'], ('白衣', '内ポケット加工', '内ポケット加工'))
        self.assertEqual(h['顧客A'][1], stock.PENDING_EMBROIDERY)
        self.assertIsNone(h['顧客A'][2])

    def test_opening_stock_lands_on_processing_handle(self):
        aux = ['新品,仕入S,顧客A,白衣,CD630,M,1000,0,0,4,本社,棚1,0',
               '新品,仕入S,釧路丸水㈱,白衣(内ポケット加工),CD630,M,1000,0,0,5,本社,棚1,0']
        c, path = self._import(aux)
        st = {'importMode': 'opening_stock', 'auxName': 'o.csv', 'auxFile': b64(opening_csv(aux)),
              'basisDate': '2026-01-15', 'defaultSiteName': '未確認', '_canWrite': True, '_actor': {'name': 't'}}
        p = pli.build_preview(c, st)
        with mock.patch.object(stock, 'backup_sqlite', return_value=''):
            pli.commit_preview(c, {**st, 'commitToken': 'cust-proc-open',
                                   'previewFingerprint': p['previewFingerprint']}, db_path=path)
        lots = {r[0]: (r[1], r[2]) for r in c.execute(
            """SELECT cu.name,cp.embroidery,l.qty_received FROM lots l
               JOIN customer_products cp ON cp.id=l.customer_product_id
               JOIN customers cu ON cu.id=cp.customer_id""")}
        self.assertEqual(lots['釧路丸水㈱'], ('内ポケット加工', 5))
        self.assertEqual(lots['顧客A'], (stock.PENDING_EMBROIDERY, 4))


if __name__ == '__main__':
    unittest.main()
