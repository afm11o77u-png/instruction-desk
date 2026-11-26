"""保存中(busy)に描き直しても、契約カードと部署の⠿はドラッグ可のまま（ドラッグ開始時に busy を確認する）。"""
import unittest
from pathlib import Path

JS = (Path(__file__).resolve().parents[1] / 'request-web.js').read_text(encoding='utf-8')


class ContractDragAfterSaveTest(unittest.TestCase):
    def test_draggable_does_not_depend_on_busy(self):
        self.assertIn('dragLocked=inherited()||!draft;', JS)
        self.assertIn('data-dept-contract="${esc(k.id)}" draggable="${!dragLocked}"', JS)
        self.assertIn('class="department-drag-handle" draggable="${!dragLocked}"', JS)
        self.assertIn("if(!k||inherited()||busy)return e.preventDefault()", JS)  # 保存中のドラッグは開始時に止める


if __name__ == '__main__':
    unittest.main()
