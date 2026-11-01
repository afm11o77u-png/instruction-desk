"""未反映の変更が再照合後も消えない不具合（合成状態のみ）。"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_ledger_review_five_fixes import HARNESS, JS

DRIVER = HARNESS.split('const api')[0].replace('console,', 'console,esc:s=>String(s==null?"":s),') + r'''
const api = sandbox.window.ProductLedgerCsv.__test;
const st = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
api.snapshotAppliedSkuDecisions(st);
process.stdout.write(JSON.stringify({codes: api.pendingRematchCodes(st), banner: api.pendingRematchBannerHtml(st)}));
'''


def run(st):
    with tempfile.TemporaryDirectory() as d:
        h = Path(d) / 'h.js'; h.write_text(DRIVER, encoding='utf-8')
        s = Path(d) / 's.json'; s.write_text(json.dumps(st, ensure_ascii=False), encoding='utf-8')
        out = subprocess.run(['node', str(h), str(JS), str(s)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def state(groups, local):
    return {'preview': {'skuGroups': groups, 'rows': []}, 'skuDecisions': local,
            'appliedSkuDecisions': {}, 'rowExclusions': {}, 'rowClassifications': {}}


class PhantomPendingTest(unittest.TestCase):
    def test_server_decision_without_local_choice_is_not_pending(self):
        r = run(state([{'code': 'A1', 'decision': {'decision': 'hold'}}], {}))
        self.assertEqual(r['codes'], [])
        self.assertEqual(r['banner'], '')

    def test_stale_code_not_in_preview_is_ignored(self):
        st = state([{'code': 'A1'}], {})
        st['skuDecisions'] = {'OLD9': {'decision': 'merge'}}
        st['appliedSkuDecisions'] = {}
        # スナップショット後に古い品番だけ残っても数えない
        r = run(st)
        self.assertEqual(r['codes'], [])

    def test_banner_names_pending_code(self):
        st = state([{'code': 'A1'}], {})
        drv = DRIVER.replace('api.snapshotAppliedSkuDecisions(st);',
                             "api.snapshotAppliedSkuDecisions(st); st.skuDecisions.A1={decision:'merge'};")
        with tempfile.TemporaryDirectory() as d:
            h = Path(d) / 'h.js'; h.write_text(drv, encoding='utf-8')
            s = Path(d) / 's.json'; s.write_text(json.dumps(st), encoding='utf-8')
            r = json.loads(subprocess.run(['node', str(h), str(JS), str(s)], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(r['codes'], ['A1'])
        self.assertIn('品番 A1', r['banner'])


if __name__ == '__main__':
    unittest.main()
