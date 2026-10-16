"""Source assertions: 一覧にない商品を入庫 uses 初期在庫-aligned 通常入庫 form."""
from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
PICKERS_JS = (ROOT / 'static' / 'opening_pickers.js').read_text(encoding='utf-8')
STOCK_PY = (ROOT / 'stock.py').read_text(encoding='utf-8')


def _fn(source: str, name: str) -> str:
    m = re.search(rf'function {name}\([^)]*\)\{{', source)
    if not m:
        raise AssertionError(f'missing function {name}')
    start = m.start()
    nxt = re.search(r'\nfunction ', source[m.end():])
    end = m.end() + nxt.start() if nxt else len(source)
    return source[start:end]


class NormalInboundUiTest(unittest.TestCase):
    def test_separate_entry_buttons_kept(self):
        opening = _fn(APP_JS, 'opening')
        stock_list = _fn(APP_JS, 'stockList')
        self.assertIn('初期在庫を登録', opening)
        self.assertIn('id="openOpeningRegister"', opening)
        self.assertIn('一覧にない商品を入庫', stock_list)
        self.assertIn('id="listInboundNew"', stock_list)
        self.assertIn('openNormalInboundRegister()', stock_list)
        self.assertNotIn('openInboundDialog(null)', stock_list)

    def test_titles_distinguish_normal_vs_opening(self):
        form = _fn(APP_JS, 'mountOpeningRegisterForm')
        self.assertIn("'通常入庫'", form)
        self.assertIn("'初期在庫'", form)
        self.assertIn('isInboundRegister()', form)
        self.assertIn('confirmNormalInbound()', form)
        self.assertIn('confirmOpening()', form)
        inbound_confirm = _fn(APP_JS, 'confirmNormalInbound')
        opening_confirm = _fn(APP_JS, 'confirmOpening')
        self.assertIn('通常入庫の確認', inbound_confirm)
        self.assertIn('初期在庫の確認', opening_confirm)
        self.assertIn("'/api/inbound'", inbound_confirm)
        self.assertIn("'/api/opening'", opening_confirm)
        self.assertIn('lines', inbound_confirm)

    def test_inbound_hides_opening_only_fields_requires_received_on(self):
        form = _fn(APP_JS, 'mountOpeningRegisterForm')
        self.assertIn('入荷日（必須）', form)
        self.assertIn('在庫基準日（必須）', form)
        self.assertIn('入荷日不明', form)
        self.assertIn('払出し順', form)
        # Mode branch: opening-only hints gated; inbound notice present
        self.assertIn('通常入庫として記録します', form)
        self.assertIn('inboundHints', form)
        self.assertIn('openingHints', form)
        confirm = _fn(APP_JS, 'confirmNormalInbound')
        self.assertIn('入荷日を入力してください', confirm)
        self.assertNotIn('basisDate', confirm)
        self.assertNotIn('receivedUnknown', confirm)
        self.assertNotIn('payoutOrder', confirm)

    def test_shared_handle_picker_and_multi_size_rows(self):
        form = _fn(APP_JS, 'mountOpeningRegisterForm')
        self.assertIn("searchPickerMarkup({label:'取扱'", form)
        self.assertIn('取扱を新しく登録', form)
        self.assertIn('サイズごとの数量と仕入単価', form)
        self.assertIn('id="sizerows"', form)
        self.assertIn('別の単価', form)
        self.assertIn('collectRegisterSizeLines', APP_JS)

    def test_backend_supports_lines_keeps_legacy_single(self):
        self.assertIn('def _inbound_cleaned_lines', STOCK_PY)
        self.assertIn("body.get('lines')", STOCK_PY)
        start = STOCK_PY.index('def register_inbound')
        end = STOCK_PY.index('def register_outbound')
        inbound = STOCK_PY[start:end]
        self.assertIn('_inbound_cleaned_lines', inbound)
        self.assertIn("'inbound'", inbound)
        # Must not write opening move kind from register_inbound
        self.assertNotIn(", 'opening',", inbound)
        self.assertNotIn(', "opening",', inbound)

    def test_inbound_state_helper_keeps_location_not_basis(self):
        self.assertIn('function inboundStateAfterSuccessfulSave', PICKERS_JS)
        keep = _fn(PICKERS_JS, 'inboundStateAfterSuccessfulSave')
        self.assertIn('receivedOn', keep)
        self.assertNotIn('basis', keep)
        confirm = _fn(APP_JS, 'confirmNormalInbound')
        self.assertIn('inboundStateAfterSuccessfulSave(state.opening)', confirm)
        self.assertIn('openNormalInboundRegister()', confirm)
        self.assertIn('loadMoveTargets()', confirm)

    def test_list_inbound_dialog_still_single_size(self):
        body = _fn(APP_JS, 'openInboundDialog')
        self.assertIn("h2>入庫する", body)
        self.assertIn("'/api/inbound'", body)
        self.assertIn('SM.newMoveToken()', body)
        self.assertIn('draft.qty', body)
        self.assertNotIn('lines:', body)

    def test_node_inbound_state_helper(self):
        script = ROOT / 'tests' / 'test_opening_pickers.js'
        proc = subprocess.run(['node', str(script)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == '__main__':
    unittest.main()
