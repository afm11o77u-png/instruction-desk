"""品番の追加文字を属性で分ける。合成データのみ。実ファイルは使わない。"""
from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import code_suffix_attributes as suffix  # noqa: E402
import product_ledger_import as pli  # noqa: E402
import product_ledger_processing_service as proc  # noqa: E402
import stock  # noqa: E402


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    conn = stock.connect(path)
    stock.initialize(conn)
    pli.ensure_schema(conn)
    return folder, path, conn


def commit_lines(conn, path, lines):
    text = '顧客,仕入先,商品コード,商品名,サイズ\n' + '\n'.join(lines)
    body = {
        'importMode': 'products', 'name': 'attr.csv',
        'file': base64.b64encode(text.encode('utf-8-sig')).decode('ascii'),
        '_canWrite': True, '_actor': {'name': '確認'},
    }
    preview = pli.build_preview(conn, body)
    result = pli.commit_preview(conn, {
        **body,
        'previewFingerprint': preview['previewFingerprint'],
        'previewAt': preview['previewAt'],
        'token': 'suffix-attr-commit-1',
    }, db_path=str(path))
    conn.commit()
    return preview, result


class ClassifyTest(unittest.TestCase):
    def test_examples(self):
        embroidery = suffix.classify_suffix(
            '6-615 刺繍', product_name='コックコート(LEOC刺繍)', supplier='住商モンブラン', size='L',
        )
        self.assertTrue(embroidery['apply'])
        self.assertEqual(embroidery['baseCode'], '6-615')
        self.assertEqual(embroidery['processingName'], '刺繍')
        leoc = suffix.classify_suffix(
            '6-951(LEOC刺繍)', product_name='コックコート', supplier='住商モンブラン', size='L',
        )
        self.assertTrue(leoc['apply'])
        self.assertFalse(leoc['needsConfirm'])
        self.assertEqual(leoc['baseCode'], '6-951')
        self.assertEqual(leoc['kind'], 'processing')
        self.assertEqual(leoc['processingName'], 'LEOC刺繍')
        wide = suffix.classify_suffix('6-951（LEOC刺繍）', product_name='コックコート')
        self.assertEqual(wide['baseCode'], '6-951')
        self.assertEqual(wide['processingName'], 'LEOC刺繍')
        self.assertFalse(suffix.classify_suffix('6-951(刺繍なし)')['apply'])
        parts = suffix.classify_suffix('BW554-73用Dカン', product_name='Dカン', supplier='住商モンブラン', size='-')
        self.assertTrue(parts['registerAsParts'])
        self.assertFalse(parts['apply'])
        parts_dbp = suffix.classify_suffix('DBP用生地', product_name='生地', supplier='シーズン', size='-')
        self.assertTrue(parts_dbp['registerAsParts'])
        self.assertFalse(parts_dbp['needsConfirm'])
        ec_res = suffix.classify_suffix('EC4609 C/#1 白', product_name='7-521生地')
        self.assertFalse(ec_res['needsConfirm'])
        self.assertEqual(ec_res['baseCode'], 'EC4609 C/#1')
        self.assertEqual(ec_res['color'], 'ホワイト')
        bettyu_res = suffix.classify_suffix('新型別注(48-880A)', product_name='ブルゾン')
        self.assertFalse(bettyu_res['needsConfirm'])
        self.assertEqual(bettyu_res['baseCode'], '48-880A')
        self.assertEqual(bettyu_res['note'], '新型別注')
        self.assertEqual(suffix.classify_suffix('AZ-8618-003')['baseCode'], 'AZ-8618-003')
        self.assertFalse(suffix.classify_suffix('AZ-8618-003')['apply'])
        midori = suffix.classify_suffix('NT7003上', supplier='ミドリ安全北海道', product_name='ジャンパー')
        self.assertEqual(midori['kind'], 'midori_position')
        self.assertFalse(midori['apply'])
        lower = suffix.classify_suffix('7-522下', supplier='仕入A', product_name='ズボン')
        self.assertEqual(lower['baseCode'], '7-522')
        self.assertEqual(lower['position'], '下')
        conflict = suffix.classify_suffix('AZ-5000（LL）', size='XL', product_name='帽')
        self.assertTrue(conflict['needsConfirm'])
        kk_res = suffix.classify_suffix('KK7122-16（LL）', size='XL', product_name='女性用カーディガン')
        self.assertFalse(kk_res['needsConfirm'])
        self.assertEqual(kk_res['note'], 'LL')
        self.assertEqual(kk_res['baseCode'], 'KK7122-16')
        change = suffix.classify_suffix('MH60B下無くなり次第F15BO78へ', product_name='帽')
        self.assertTrue(change['needsConfirm'])
        # 括弧内に移行・廃番メモがある場合は品番本体を採用して備考へ
        discon_memo = suffix.classify_suffix('7-731(廃番→7-733)')
        self.assertTrue(discon_memo['apply'])
        self.assertEqual(discon_memo['baseCode'], '7-731')
        self.assertEqual(discon_memo['note'], '廃番→7-733')
        self.assertFalse(discon_memo['needsConfirm'])
        unused = suffix.classify_suffix(
            'APK205-10 (不使用)', product_name='シャツコート(不使用)', supplier='KAZEN WLD(株)',
        )
        self.assertTrue(unused['apply'])
        self.assertEqual(unused['kind'], 'note')
        self.assertEqual(unused['baseCode'], 'APK205-10')
        self.assertEqual(unused['note'], '不使用')
        self.assertFalse(unused['registerAsParts'])
        wide_unused = suffix.classify_suffix(
            'APK205-10（不使用）', product_name='シャツコート（不使用）',
        )
        self.assertEqual(wide_unused['baseCode'], 'APK205-10')
        self.assertFalse(wide_unused['registerAsParts'])
        bare_you = suffix.classify_suffix('APK205-10用', product_name='シャツコート')
        self.assertFalse(bare_you['registerAsParts'])
        self.assertNotIn('部品のように見えます', bare_you.get('reason') or '')
        star = suffix.classify_suffix('*小前', product_name='*小前', supplier='(株)サーヴォ')
        self.assertFalse(star['registerAsParts'])
        self.assertNotEqual(star['kind'], 'parts')
        fabric = suffix.classify_suffix(
            'NT7003難燃作業衣 生地', product_name='NT7003難燃作業衣 生地',
            supplier='ミドリ安全北海道 釧路営業所',
        )
        self.assertTrue(fabric['registerAsParts'])
        self.assertFalse(fabric['needsConfirm'])
        self.assertEqual(fabric['baseCode'], 'NT7003難燃作業衣 生地')
        self.assertFalse(fabric['apply'])
        inclusive = suffix.classify_suffix(
            'AS7300-1 (刺繍代込み)', product_name='シャツ', supplier='仕入A',
        )
        self.assertEqual(inclusive['kind'], 'price_condition')
        self.assertFalse(inclusive['needsConfirm'])
        self.assertFalse(inclusive['apply'])
        self.assertEqual(inclusive['baseCode'], 'AS7300-1 (刺繍代込み)')
        self.assertFalse(inclusive['processingName'])
        self.assertFalse(proc.is_embroidery_class_name('刺繍代込み'))
        self.assertTrue(proc.is_embroidery_class_name('LEOC刺繍'))
        self.assertTrue(proc.is_embroidery_class_name('刺繍'))

        # *TC-410 used prefix
        tc = suffix.classify_suffix('*TC-410', product_name='コックコート', supplier='㈱サーヴォ')
        self.assertTrue(tc['apply'])
        self.assertFalse(tc['needsConfirm'])
        self.assertEqual(tc['baseCode'], 'TC-410')
        self.assertIn('中古', tc['note'])

        # C/1, C-10 color code in SKU
        as7300 = suffix.classify_suffix('AS7300 C/1', product_name='シャツ', supplier='チトセ')
        self.assertTrue(as7300['apply'])
        self.assertFalse(as7300['needsConfirm'])
        self.assertEqual(as7300['baseCode'], 'AS7300')
        self.assertEqual(as7300['color'], 'C/1')

        as5403 = suffix.classify_suffix('AS5403 C-10', product_name='シャツ', supplier='チトセ')
        self.assertTrue(as5403['apply'])
        self.assertFalse(as5403['needsConfirm'])
        self.assertEqual(as5403['baseCode'], 'AS5403')
        self.assertEqual(as5403['color'], 'C-10')

        # Slash in SKU (APK600-C/5, AS5402C/10)
        apk = suffix.classify_suffix('APK600-C/5', product_name='エプロン', supplier='KAZEN')
        self.assertFalse(apk['needsConfirm'])
        self.assertEqual(apk['baseCode'], 'APK600-C/5')

        # 別注 prefix
        betchu = suffix.classify_suffix('別注SD5000AM-50', product_name='作業服', supplier='仕入A')
        self.assertTrue(betchu['apply'])
        self.assertFalse(betchu['needsConfirm'])
        self.assertEqual(betchu['baseCode'], 'SD5000AM-50')
        self.assertIn('別注', betchu['note'])

        # #1702, No.1702 prefix
        hash_code = suffix.classify_suffix('#1702', product_name='つなぎ', supplier='フジ総合安全㈱')
        self.assertFalse(hash_code['needsConfirm'])
        self.assertEqual(hash_code['baseCode'], '#1702')

        no_code = suffix.classify_suffix('No.1702', product_name='つなぎ', supplier='フジ総合安全㈱')
        self.assertFalse(no_code['needsConfirm'])
        self.assertEqual(no_code['baseCode'], 'No.1702')

        # ■廃番■ prefix
        discon1 = suffix.classify_suffix('■廃番■9-970', product_name='コックコート', supplier='仕入A')
        self.assertTrue(discon1['apply'])
        self.assertFalse(discon1['needsConfirm'])
        self.assertEqual(discon1['baseCode'], '9-970')
        self.assertIn('廃番', discon1['note'])

        discon2 = suffix.classify_suffix('■廃番■MC20', product_name='作業着', supplier='仕入B')
        self.assertTrue(discon2['apply'])
        self.assertFalse(discon2['needsConfirm'])
        self.assertEqual(discon2['baseCode'], 'MC20')
        self.assertIn('廃番', discon2['note'])

        # 030 15mm (button with dimension)
        btn = suffix.classify_suffix('030 15mm', product_name='日鉄作業衣用 ボタン', supplier='ミドリ安全北海道㈱ 室蘭営業所', size='-')
        self.assertFalse(btn['needsConfirm'])
        self.assertTrue(btn['registerAsParts'])
        self.assertEqual(btn['baseCode'], '030 15mm')

        # 0803(エメラルドグリーン)
        emerald = suffix.classify_suffix('0803(エメラルドグリーン)', product_name='ポロシャツ', supplier='仕入C')
        self.assertFalse(emerald['needsConfirm'])
        self.assertEqual(emerald['baseCode'], '0803')
        self.assertEqual(emerald['color'], 'エメラルドグリーン')

        # 100-M-S-ｍ (fullwidth 'ｍ' in SKU)
        zenkaku = suffix.classify_suffix('100-M-S-ｍ', product_name='主任・男性長袖シャツ(マイスター)', supplier='㈱マジェックストレード')
        self.assertFalse(zenkaku['needsConfirm'])
        self.assertTrue(zenkaku['apply'])
        self.assertEqual(zenkaku['baseCode'], '100-M-S-m')

        # 116-1133 #053, #063: keep as-is without error
        w1 = suffix.classify_suffix('116-1133 #053', product_name='ライン', supplier='㈱脇本商事')
        self.assertFalse(w1['needsConfirm'])
        self.assertEqual(w1['baseCode'], '116-1133 #053')

        w2 = suffix.classify_suffix('116-1133 #063', product_name='ライン', supplier='㈱脇本商事')
        self.assertFalse(w2['needsConfirm'])
        self.assertEqual(w2['baseCode'], '116-1133 #063')

        # 特殊ハイフン正規化 (6‐605, 6‐612)
        h1 = suffix.classify_suffix('6‐605', product_name='コックコート', supplier='住商モンブラン㈱')
        self.assertFalse(h1['needsConfirm'])
        h2 = suffix.classify_suffix('6‐612', product_name='コックコート(半袖)', supplier='住商モンブラン㈱')
        self.assertFalse(h2['needsConfirm'])

        # バーガンディ / ダークグレーー（連続長音符）
        c1 = suffix.classify_suffix('330-AVP バーガンディ', product_name='ポロシャツ', supplier='トムス㈱')
        self.assertTrue(c1['apply'])
        self.assertEqual(c1['baseCode'], '330-AVP')
        self.assertEqual(c1['color'], 'バーガンディ')

        c2 = suffix.classify_suffix('330-AVP-187 ダークグレーー', product_name='ポロシャツ', supplier='トムス㈱')
        self.assertTrue(c2['apply'])
        self.assertEqual(c2['baseCode'], '330-AVP-187')
        self.assertEqual(c2['color'], 'ダークグレー')

        # 廃番メモ付き品番 (5-871(2023廃番→T-8537))
        m1 = suffix.classify_suffix('5-871(2023廃番→T-8537)', product_name='エプロン', supplier='住商モンブラン㈱')
        self.assertTrue(m1['apply'])
        self.assertEqual(m1['baseCode'], '5-871')
        self.assertEqual(m1['note'], '2023廃番→T-8537')
        self.assertFalse(m1['needsConfirm'])

        # FOLK ブランド名除去 + 色分離 (6013SC16（バーガンディ）FOLK, 7101SC11(ココア)FOLK)
        f1 = suffix.classify_suffix('6013SC16（バーガンディ）FOLK', product_name='レディスクラブ', supplier='㈱廣瀬商会')
        self.assertTrue(f1['apply'])
        self.assertEqual(f1['baseCode'], '6013SC16')
        self.assertEqual(f1['color'], 'バーガンディ')
        self.assertFalse(f1['needsConfirm'])

        f2 = suffix.classify_suffix('7101SC11(ココア)FOLK', product_name='レディスクラブ(ココア)', supplier='㈱廣瀬商会')
        self.assertTrue(f2['apply'])
        self.assertEqual(f2['baseCode'], '7101SC11')
        self.assertEqual(f2['color'], 'ココア')
        self.assertFalse(f2['needsConfirm'])

        # 「〜へ変更」メモ付き品番 (6991-099(2601へ変更))
        c_memo = suffix.classify_suffix('6991-099(2601へ変更)', product_name='ブラウス', supplier='アイトス㈱')
        self.assertTrue(c_memo['apply'])
        self.assertEqual(c_memo['baseCode'], '6991-099')
        self.assertEqual(c_memo['note'], '2601へ変更')
        self.assertFalse(c_memo['needsConfirm'])

        # 9-970廃番
        d_tail = suffix.classify_suffix('9-970廃番', product_name='ベレー帽', supplier='住商モンブラン㈱')
        self.assertTrue(d_tail['apply'])
        self.assertEqual(d_tail['baseCode'], '9-970')
        self.assertEqual(d_tail['note'], '廃番')
        self.assertFalse(d_tail['needsConfirm'])

        # 9-629(販売), 9-642(販売)
        hanbai = suffix.classify_suffix('9-629(販売)', product_name='八角帽子(工房)', supplier='住商モンブラン㈱')
        self.assertTrue(hanbai['apply'])
        self.assertEqual(hanbai['baseCode'], '9-629')
        self.assertEqual(hanbai['note'], '販売')
        self.assertFalse(hanbai['needsConfirm'])

        # A-7160-1(CO-COS)
        cocos = suffix.classify_suffix('A-7160-1(CO-COS)', product_name='ストレッチブルゾン', supplier='㈱廣瀬商会')
        self.assertTrue(cocos['apply'])
        self.assertEqual(cocos['baseCode'], 'A-7160-1')
        self.assertEqual(cocos['note'], 'CO-COS')
        self.assertFalse(cocos['needsConfirm'])

        # 9910(3M)
        m3 = suffix.classify_suffix('9910(3M)', product_name='反射材', supplier='ミドリ安全北海道㈱ 室蘭営業所')
        self.assertTrue(m3['apply'])
        self.assertEqual(m3['baseCode'], '9910')
        self.assertEqual(m3['note'], '3M')
        self.assertFalse(m3['needsConfirm'])

        # BA1220-1(セブン)
        seven = suffix.classify_suffix('BA1220-1(セブン)', product_name='コックコート(長袖)', supplier='㈱札幌白衣')
        self.assertTrue(seven['apply'])
        self.assertEqual(seven['baseCode'], 'BA1220-1')
        self.assertEqual(seven['note'], 'セブン')
        self.assertFalse(seven['needsConfirm'])

    def test_recent_user_requested_cases(self):
        # 1. APK489無地-C8 -> 無地をメモへ、品番はAPK489-C8
        apk = suffix.classify_suffix('APK489無地-C8')
        self.assertTrue(apk['apply'])
        self.assertEqual(apk['baseCode'], 'APK489-C8')
        self.assertEqual(apk['kind'], 'note')
        self.assertEqual(apk['note'], '無地')

        # 2. AS-8061 ｃ-10ブラック / AS-8062 ｃ-10ブラック -> c-10は品番、ブラックは色
        as8061 = suffix.classify_suffix('AS-8061 ｃ-10ブラック')
        self.assertTrue(as8061['apply'])
        self.assertEqual(as8061['baseCode'], 'AS-8061 c-10')
        self.assertEqual(as8061['kind'], 'color')
        self.assertEqual(as8061['color'], 'ブラック')

        as8062 = suffix.classify_suffix('AS-8062 ｃ-10ブラック')
        self.assertTrue(as8062['apply'])
        self.assertEqual(as8062['baseCode'], 'AS-8062 c-10')
        self.assertEqual(as8062['kind'], 'color')
        self.assertEqual(as8062['color'], 'ブラック')

        # 3. AS110(310-8) -> 括弧内コードはメモへ
        as110 = suffix.classify_suffix('AS110(310-8)')
        self.assertTrue(as110['apply'])
        self.assertEqual(as110['baseCode'], 'AS110')
        self.assertEqual(as110['kind'], 'note')
        self.assertEqual(as110['note'], '310-8')

        # 4. AS110→なくなり次第6-605へ -> 矢印以降をメモへ
        as_arrow = suffix.classify_suffix('AS110→なくなり次第6-605へ')
        self.assertTrue(as_arrow['apply'])
        self.assertEqual(as_arrow['baseCode'], 'AS110')
        self.assertEqual(as_arrow['kind'], 'note')
        self.assertEqual(as_arrow['note'], '→なくなり次第6-605へ')

        # 5. AZ-1730-006 スカイブルー -> スカイブルーは色
        az = suffix.classify_suffix('AZ-1730-006 スカイブルー')
        self.assertTrue(az['apply'])
        self.assertEqual(az['baseCode'], 'AZ-1730-006')
        self.assertEqual(az['kind'], 'color')
        self.assertEqual(az['color'], 'スカイブルー')

        # 6. EP-6840 C10(ブラック) / EP-6840 C5(ネイビー) / EP-6849 c-1ホワイト
        ep_c10 = suffix.classify_suffix('EP-6840 C10(ブラック)')
        self.assertTrue(ep_c10['apply'])
        self.assertEqual(ep_c10['baseCode'], 'EP-6840 C10')
        self.assertEqual(ep_c10['kind'], 'color')
        self.assertEqual(ep_c10['color'], 'ブラック')

        ep_c5 = suffix.classify_suffix('EP-6840 C5(ネイビー)')
        self.assertTrue(ep_c5['apply'])
        self.assertEqual(ep_c5['baseCode'], 'EP-6840 C5')
        self.assertEqual(ep_c5['kind'], 'color')
        self.assertEqual(ep_c5['color'], 'ネイビー')

        ep_c1 = suffix.classify_suffix('EP-6849 c-1ホワイト')
        self.assertTrue(ep_c1['apply'])
        self.assertEqual(ep_c1['baseCode'], 'EP-6849 c-1')
        self.assertEqual(ep_c1['kind'], 'color')
        self.assertEqual(ep_c1['color'], 'ホワイト')

        # 7. FB4026L-15→FB4534Uへ変更 -> 矢印以降をメモへ
        fb_arrow = suffix.classify_suffix('FB4026L-15→FB4534Uへ変更')
        self.assertTrue(fb_arrow['apply'])
        self.assertEqual(fb_arrow['baseCode'], 'FB4026L-15')
        self.assertEqual(fb_arrow['kind'], 'note')
        self.assertEqual(fb_arrow['note'], '→FB4534Uへ変更')

        # 8. FH207C-01(東洋リントフリー製) / FK7177(ボンマックス)
        lint = suffix.classify_suffix('FH207C-01(東洋リントフリー製)')
        self.assertTrue(lint['apply'])
        self.assertEqual(lint['baseCode'], 'FH207C-01')
        self.assertEqual(lint['kind'], 'note')
        self.assertEqual(lint['note'], '東洋リントフリー製')

        bon = suffix.classify_suffix('FK7177(ボンマックス)')
        self.assertTrue(bon['apply'])
        self.assertEqual(bon['baseCode'], 'FK7177')
        self.assertEqual(bon['kind'], 'note')
        self.assertEqual(bon['note'], 'ボンマックス')

        # 9. スラッシュ併記品番（両側が正規品番の場合はそのまま登録、日本語混ざりは除外）
        slash = suffix.classify_suffix('SDAW01UA / ANDAKW01-UAC')
        self.assertEqual(slash['baseCode'], 'SDAW01UA / ANDAKW01-UAC')
        self.assertEqual(slash['kind'], 'plain')
        self.assertFalse(slash['needsConfirm'])

        # スラッシュ併記＋末尾カラー
        slash_color = suffix.classify_suffix('SDAW01UA / ANDAKW01-UAC ブラック')
        self.assertEqual(slash_color['baseCode'], 'SDAW01UA / ANDAKW01-UAC')
        self.assertEqual(slash_color['kind'], 'color')
        self.assertEqual(slash_color['color'], 'ブラック')

        # スラッシュの後に日本語がある場合は品番併記とみなさず要確認
        slash_proc = suffix.classify_suffix('SDAW01UA / ペン差し加工')
        self.assertTrue(slash_proc['needsConfirm'])
        self.assertFalse(slash_proc['apply'])


class ImportTest(unittest.TestCase):
    def test_processing_color_parts_and_notes_register_without_shape_block(self):
        folder, path, conn = newdb()
        try:
            lines = [
                '顧客A,住商モンブラン,6-615,コックコート半袖加工,L',
                '顧客B,住商モンブラン,6-615 刺繍,コックコート(LEOC刺繍),６L',
                '顧客A,住商モンブラン,6-615 青,コックコート,M',
                '顧客A,住商モンブラン,BW554-73用Dカン,Dカン,-',
                '顧客A,住商モンブラン,6-827 (デリカ),帽子,F',
                '顧客A,仕入A,AZ-8618-003,白衣,M',
                '顧客A,仕入A,7-522下,作業ズボン,LL',
                '顧客A,仕入A,7-522,作業ズボン,2L',
                '顧客C,住商モンブラン,6-615 プリント,コックコート,S',
            ]
            preview, result = commit_lines(conn, path, lines)
            self.assertTrue(result['stockUnchanged'])
            ids = {g['id'] for g in preview['codeShapeGroups']}
            self.assertNotIn('extend:6-615', ids)
            self.assertNotIn('extend:7-522', ids)
            codes = {
                row['code']: row for row in conn.execute(
                    'SELECT id, code, name, color, category FROM products WHERE merged_into_id IS NULL'
                )
            }
            self.assertIn('6-615', codes)
            self.assertNotIn('6-615 刺繍', codes)
            self.assertNotIn('6-615 青', codes)
            self.assertEqual(codes['BW554-73用Dカン']['category'], '部品・資材')
            self.assertIn('AZ-8618-003', codes)
            self.assertIn('6-827', codes)
            self.assertIn('7-522', codes)
            self.assertNotIn('7-522下', codes)
            pid = codes['6-615']['id']
            sizes = {r['label'] for r in conn.execute('SELECT label FROM product_sizes WHERE product_id=?', (pid,))}
            self.assertIn('L', sizes)
            self.assertIn('6L', sizes)
            self.assertNotIn('６L', sizes)
            handles = list(conn.execute(
                'SELECT embroidery FROM customer_products WHERE product_id=? ORDER BY id', (pid,)
            ))
            self.assertIn('LEOC刺繍', [r['embroidery'] for r in handles])
            services = list(conn.execute(
                "SELECT name, supplier_id FROM products WHERE category='加工サービス'"
            ))
            names = {r['name'] for r in services}
            self.assertIn('プリント', names)
            k2 = conn.execute("SELECT id FROM suppliers WHERE name='K2スタッフ工房'").fetchone()
            self.assertIsNotNone(k2)
            printed = next(r for r in services if r['name'] == 'プリント')
            self.assertEqual(printed['supplier_id'], k2['id'])
            garment = conn.execute("SELECT id FROM suppliers WHERE name='住商モンブラン'").fetchone()
            self.assertNotEqual(printed['supplier_id'], garment['id'])
            pants = codes['7-522']['id']
            pants_sizes = {r['label'] for r in conn.execute('SELECT label FROM product_sizes WHERE product_id=?', (pants,))}
            self.assertEqual(pants_sizes, {'LL', '2L'})
            # 再取込で商品が増えない
            preview2, result2 = commit_lines(conn, path, lines)
            self.assertTrue(result2.get('already') or result2['counts'].get('productsCreated', 0) == 0)
            again = conn.execute(
                "SELECT COUNT(*) n FROM products WHERE code='6-615' AND merged_into_id IS NULL"
            ).fetchone()['n']
            self.assertEqual(again, 1)
            self.assertIn('6-615', {g.get('code') for g in preview2['skuGroups']})
        finally:
            conn.close()
            folder.cleanup()

    def test_dimension_conflict_stays_uncommitted(self):
        folder, path, conn = newdb()
        try:
            text = '顧客,仕入先,商品コード,商品名,サイズ\n顧客A,仕入A,AZ-5000（LL）,帽子,XL\n'
            body = {
                'importMode': 'products', 'name': 'dim.csv',
                'file': base64.b64encode(text.encode('utf-8-sig')).decode('ascii'),
                '_canWrite': True, '_actor': {'name': '確認'},
            }
            preview = pli.build_preview(conn, body)
            row = preview['rows'][0]
            self.assertEqual(row['status'], 'needs_review')
            self.assertFalse(row['canCommit'])
            with self.assertRaises(ValueError):
                pli.commit_preview(conn, {
                    **body,
                    'previewFingerprint': preview['previewFingerprint'],
                    'previewAt': preview['previewAt'],
                    'token': 'suffix-dim-1',
                }, db_path=str(path))
            self.assertEqual(conn.execute('SELECT COUNT(*) n FROM products').fetchone()['n'], 0)
        finally:
            conn.close()
            folder.cleanup()

    def test_embroidery_price_included_registers_as_its_own_code(self):
        folder, path, conn = newdb()
        try:
            text = (
                '顧客,仕入先,商品コード,商品名,サイズ,仕入単価\n'
                '顧客A,仕入A,AS7300-1,シャツ,L,1200\n'
                '顧客A,仕入A,AS7300-1 (刺繍代込み),シャツ,L,1800\n'
            )
            body = {
                'importMode': 'products', 'name': 'price.csv',
                'file': base64.b64encode(text.encode('utf-8-sig')).decode('ascii'),
                '_canWrite': True, '_actor': {'name': '確認'},
            }
            preview = pli.build_preview(conn, body)
            by_code = {r['csv']['code']: r for r in preview['rows']}
            plain = by_code['AS7300-1']
            held = by_code['AS7300-1 (刺繍代込み)']
            self.assertTrue(plain['canCommit'])
            self.assertTrue(held['canCommit'])
            self.assertEqual(held['csv']['code'], 'AS7300-1 (刺繍代込み)')
            self.assertNotEqual(held['csv']['code'], plain['csv']['code'])
            names = [c.get('serviceName') for c in preview.get('processingCandidates') or []]
            self.assertNotIn('刺繍', names)
            self.assertNotIn('刺繍代込み', names)
            pli.commit_preview(conn, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'suffix-price-own-code-1',
            }, db_path=str(path))
            conn.commit()
            codes = {
                r['code'] for r in conn.execute(
                    "SELECT code FROM products WHERE merged_into_id IS NULL"
                )
            }
            self.assertIn('AS7300-1', codes)
            self.assertIn('AS7300-1 (刺繍代込み)', codes)
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) n FROM products WHERE category='加工サービス'"
                ).fetchone()['n'],
                0,
            )
        finally:
            conn.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
