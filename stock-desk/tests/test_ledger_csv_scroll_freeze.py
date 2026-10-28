"""旧CSV一括取り込み: 勝手なスクロール停止（UI配線・フィクスチャのみ。実DB/在庫なし）。"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / 'static' / 'product_ledger_csv.js'


class CsvScrollFreezeSourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = JS.read_text(encoding='utf-8')

    def test_shared_preserve_policy_present(self):
        js = self.js
        self.assertIn('function scheduleLedgerScrollRestore', js)
        self.assertIn('function captureFocusKey', js)
        self.assertIn('preventScroll', js)
        self.assertIn('_ledgerScrollRestoreToken', js)
        self.assertIn('restore-superseded', js)
        # クランプ復元
        self.assertIn('scrollHeight', js)
        self.assertIn('clientHeight', js)

    def test_no_anchor_code_chase_on_actions(self):
        js = self.js
        self.assertNotIn('paintDialog({anchorCode:', js)
        # 方針・詳細・確認済み変更は paintDialog() のみ
        for marker in (
            "document.querySelectorAll('[data-pl-sku-dec]')",
            "document.querySelectorAll('[data-pl-sku-expand]')",
            "document.querySelectorAll('[data-pl-confirmed-expand]')",
            "document.querySelectorAll('[data-pl-unified-expand]')",
        ):
            self.assertIn(marker, js)
        # restore は opts.anchorCode を使わない（visibleAnchor + section のみ）
        restore = js.split('function restoreLedgerScroll(')[1].split('function paintDialog(')[0]
        self.assertNotIn('opts.anchorCode', restore)
        self.assertIn('visibleAnchor', restore)
        self.assertIn('section:viewSec', restore)

    def test_section_local_row_restore_no_confirmed_chase(self):
        js = self.js
        ensure = js.split('function ensureSkuRowInView(')[1].split('function restoreNearbySkuInView(')[0]
        self.assertIn('opts.section', ensure)
        self.assertIn('data-sku-section', ensure)
        self.assertIn('chase', ensure.lower())

    def test_maker_scroll_only_on_view_results_click(self):
        js = self.js
        self.assertIn('id="plBulkMakerViewResults"', js)
        self.assertIn('id="plMakerViewResults"', js)
        self.assertIn('結果を見る', js)
        self.assertIn('function bindMakerViewResultsButtons', js)
        # 自動呼び出しはボタン配線だけ（start/poll/lookup 本体には無い）
        calls = [m.start() for m in re.finditer(r'scrollMakerLookupIntoView\(\)', js)]
        self.assertEqual(len(calls), 3)  # def 内の実体は別。呼び出しは onclick 2 + … wait
        # 関数定義は `function scrollMakerLookupIntoView` — 呼び出しは () 付き
        call_sites = []
        for m in re.finditer(r'(?m)^(.*scrollMakerLookupIntoView\(\).*)$', js):
            line = m.group(1).strip()
            if line.startswith('function '):
                continue
            call_sites.append(line)
        self.assertEqual(len(call_sites), 2)
        self.assertTrue(all('onclick' in c for c in call_sites))

        start = js.split('async function startBulkMakerLookup(')[1].split(
            'function startBulkMakerPoll('
        )[0]
        self.assertNotIn('scrollMakerLookupIntoView()', start)
        poll = js.split('async function pollBulkMakerLookup(')[1].split(
            'async function cancelBulkMakerLookup('
        )[0]
        self.assertNotIn('scrollMakerLookupIntoView()', poll)
        self.assertIn('_completionToasted', poll)
        self.assertIn('結果を見る', poll)

    def test_maker_correct_handlers_do_not_auto_scroll(self):
        js = self.js
        for marker in (
            "document.querySelectorAll('[data-pl-maker-correct]')",
            "document.querySelectorAll('[data-pl-sku-maker-correct]')",
            "const makerUrlFetch=$('#plMakerUrlFetch')",
        ):
            chunk = js.split(marker)[1].split('document.querySelectorAll(')[0]
            if marker.startswith('const'):
                chunk = js.split(marker)[1].split("document.querySelectorAll('[data-pl-maker-adopt]')")[0]
            self.assertNotIn('scrollMakerLookupIntoView()', chunk)

    def test_checkbox_still_light_sync(self):
        js = self.js
        bind = js.split('function bindBulkMakerPanelHandlers(')[1]
        pick = bind.split("document.querySelectorAll('[data-pl-bulk-maker-pick]')")[1].split(
            "document.querySelectorAll('.pl-bulk-maker-panel [data-pl-maker-url-input]')"
        )[0]
        self.assertIn('syncBulkMakerAdoptSelectionUi(st)', pick)
        self.assertNotIn('paintDialog', pick)


if __name__ == '__main__':
    unittest.main()
