"""小ネーム・苗字のみ・36pt（丸ゴシック36 / Pゴシック36）見本は登録 pt 厳守。"""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import names


def _connect(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, payload TEXT NOT NULL DEFAULT "{}")')
    return c


def _ink_ratios(path):
    from PIL import Image
    image = Image.open(path).convert('RGB')
    bg = names._corner_background(image)
    ink = names._ink_bbox(image, bg)
    assert ink is not None, path
    bw = ink[2] - ink[0] + 1
    bh = ink[3] - ink[1] + 1
    return bw / image.width, bh / image.height, image.size


class SmallSurname36ExactPtTest(unittest.TestCase):
    def test_fit_mode_detection(self):
        self.assertEqual(
            names._sample_fit_mode_for_format({
                'small': 1, 'layout': 'Surname', 'reading': '苗字のみ',
                'family': 'HG丸ゴシック', 'maximumFont': 36, 'fontPt': 36,
            }),
            'exact',
        )
        self.assertEqual(
            names._sample_fit_mode_for_format({
                'small': 1, 'layout': 'Surname', 'reading': '苗字のみ',
                'family': 'MSPゴシック', 'maximumFont': 36, 'fontPt': 36,
            }),
            'exact',
        )
        # 小汎用（26）は fill のまま
        self.assertEqual(
            names._sample_fit_mode_for_format({
                'small': 1, 'layout': 'Name', 'reading': 'フルネーム',
                'family': 'HG丸ゴシック', 'maximumFont': 26, 'fontPt': 26,
            }),
            'fill',
        )
        # 大ネーム苗字 80pt は対象外
        self.assertEqual(
            names._sample_fit_mode_for_format({
                'small': 0, 'layout': 'Surname', 'reading': '苗字のみ',
                'family': 'HG丸ゴシック', 'maximumFont': 80, 'fontPt': 80,
            }),
            'fill',
        )

    def test_exact_pt_matsumoto_vs_fill_and_generic_unchanged(self):
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            self.skipTest('Pillow未導入')

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            band36 = names._three_band('松本', height_mm=24, font_pt=36)
            band26 = names._three_band('山田 太郎', height_mm=24, font_pt=26)

            exact = tmp / 'exact-36.png'
            fill = tmp / 'fill-36.png'
            generic = tmp / 'fill-26.png'
            names._render_ashuka_banded_sample(
                exact, (band36,), width_mm=50, height_mm=24,
                family_hint='HG丸ゴシック', fit_mode='exact',
            )
            names._render_ashuka_banded_sample(
                fill, (band36,), width_mm=50, height_mm=24,
                family_hint='HG丸ゴシック', fit_mode='fill',
            )
            names._render_ashuka_banded_sample(
                generic, (band26,), width_mm=50, height_mm=24,
                family_hint='HG丸ゴシック', fit_mode='fill',
            )

            ew, eh, _ = _ink_ratios(exact)
            fw, fh, _ = _ink_ratios(fill)
            gw, gh, _ = _ink_ratios(generic)

            # Excel 目安: 2文字「松本」は幅おおよそ半分・高さ ~40%（厳密 50% 強制ではない）
            self.assertGreater(ew, 0.35)
            self.assertLess(ew, 0.62)
            self.assertGreater(eh, 0.30)
            self.assertLess(eh, 0.58)
            # fill は帯を埋めすぎる（修正前の不具合）
            self.assertGreater(fh, 0.75)
            # 小汎用 fill は自然帯（このテストでは従来どおり fill）
            self.assertLess(gh, 0.55)
            self.assertGreater(gh, 0.30)

            # MSP（Pゴシック相当）でも exact は同程度の比率
            p_exact = tmp / 'exact-36-p.png'
            names._render_ashuka_banded_sample(
                p_exact, (band36,), width_mm=50, height_mm=24,
                family_hint='MSPゴシック', fit_mode='exact',
            )
            pw, ph, _ = _ink_ratios(p_exact)
            self.assertLess(abs(pw - ew), 0.12)
            self.assertLess(abs(ph - eh), 0.12)

    def test_three_and_fk_regenerate_with_exact_fit(self):
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            self.skipTest('Pillow未導入')

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with _connect(data_dir / 'desk.sqlite3') as c:
                names.bind(data_dir)
                names.schema(c)

                # 旧 fill 相当の oversized 見本を先に置く想定: ensure で exact に直る
                three = names.ensure_three_customer_name_formats(c, data_dir)
                fk = names.ensure_fk_name_formats(c, data_dir)
                fix = names.ensure_small_surname_36_exact_samples(c, data_dir)
                self.assertIn(three['policy'], (names.THREE_NAMES_POLICY,))
                self.assertEqual(fk['policy'], names.FK_NAMES_POLICY)
                self.assertEqual(fix['policy'], names.SMALL_SURNAME_36_POLICY)

                targets = [
                    'airwater-agri:small-hat-surname',
                    'tokachi-meat:small-surname',
                    'fk:small-surname',
                ]
                # 対照: 小汎用は fill のまま（寸法メタは不変）
                control = c.execute(
                    "SELECT * FROM name_formats WHERE identity='bellcool:small-fullname' AND retired=0"
                ).fetchone()
                self.assertIsNotNone(control)
                self.assertEqual(float(control['maximum_font']), 26)
                control_spec = json.loads(control['spec_json'] or '{}')
                self.assertNotEqual(control_spec.get('sampleFit'), 'exact')

                for identity in targets:
                    fmt = c.execute(
                        'SELECT * FROM name_formats WHERE identity=? AND retired=0',
                        (identity,),
                    ).fetchone()
                    self.assertIsNotNone(fmt, identity)
                    self.assertEqual(float(fmt['maximum_font']), 36)
                    self.assertEqual(int(fmt['width_mm']), 50)
                    self.assertEqual(int(fmt['height_mm']), 24)
                    spec = json.loads(fmt['spec_json'] or '{}')
                    self.assertEqual(spec.get('sampleFit'), 'exact')
                    samples = c.execute(
                        'SELECT * FROM name_samples WHERE format_id=? ORDER BY id',
                        (fmt['id'],),
                    ).fetchall()
                    self.assertGreaterEqual(len(samples), 1)
                    for sample in samples:
                        path = data_dir / sample['relative_path']
                        self.assertTrue(path.is_file(), path)
                        w_ratio, h_ratio, size = _ink_ratios(path)
                        self.assertEqual(size, (500, 240))
                        self.assertLess(h_ratio, 0.60, identity)
                        self.assertGreater(h_ratio, 0.30, identity)
                        self.assertLess(w_ratio, 0.70, identity)

                # 再実行は no-op
                again = names.ensure_small_surname_36_exact_samples(c, data_dir)
                self.assertEqual(again.get('changed'), 0)
                self.assertEqual(again.get('reason'), 'already-applied')


if __name__ == '__main__':
    unittest.main()
