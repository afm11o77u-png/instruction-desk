"""指示書一覧・現場の作業一覧の「一覧／かんばん／カレンダー」切り替え。"""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / 'index.html').read_text(encoding='utf-8')
APP = (ROOT / 'app.js').read_text(encoding='utf-8')
SERVER = (ROOT / 'server.py').read_text(encoding='utf-8')


class DeskViewsTest(unittest.TestCase):
    def test_helpers(self):
        r = subprocess.run(['node', str(ROOT / 'tests' / 'test_desk_views.js')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_wiring(self):
        self.assertLess(INDEX.index('src="/desk-views.js'), INDEX.index('src="/app.js'))
        self.assertIn("'/desk-views.js':'desk-views.js'", SERVER)
        self.assertIn("'/desk-views.css':'desk-views.css'", SERVER)
        # ドラッグは既存のAPI経由のみ、失敗時は再描画で元に戻す
        self.assertIn('DeskViews.moveStatus(api,order,target,currentUser,states)', APP)
        self.assertIn("catch(e){render();toast(e.message)}", APP)
        self.assertIn('paintDeskLayout(list)', APP)
        self.assertIn("count.insertAdjacentElement('afterend',sw)", APP)


if __name__ == '__main__':
    unittest.main()
