"""旧CSV一括取り込み: 顧客・仕入先・別注に限定した色名補正。

対象（3条件すべて＋商品名パターン）:
  顧客: ㈱ＬＥＯＣ
  仕入先: (有)ユニスタイル
  品番: 別注
  商品名: 「エプロン」を含み、かつ「ブルー」と「サックス」の両方を含む

登録予定:
  名: エプロン / 色: ブルー・サックス / 補足: 胸当（ある場合）

ブルーとサックスは別色の配色ではなく、同一色の言い換えとして扱う。
表記ゆれ（全角・半角・空白）は unify_import_notation で吸収する。
他顧客・他仕入先・他の別注商品には適用しない。
"""
from __future__ import annotations

import product_ledger_paren as paren_classify
import stock
import legacy_csv_import as legacy

SCOPED_CUSTOMER = '㈱ＬＥＯＣ'
SCOPED_SUPPLIER = '(有)ユニスタイル'
SCOPED_COLOR = 'ブルー・サックス'
SCOPED_NAME = 'エプロン'
SCOPED_SUPPLEMENT = '胸当'
SCOPED_REASON = '指定した顧客・仕入先・別注商品の色名補正を適用'


def party_match_key(value: str) -> str:
    """顧客・仕入先の照合キー（全角半角・空白を unify で吸収）。"""
    return paren_classify.unify_import_notation(stock.text(value) or '')


def matches_scoped_parties(*, customer: str, supplier: str, code: str) -> bool:
    if not legacy.is_custom_order_code(stock.text(code) or ''):
        return False
    if party_match_key(customer) != party_match_key(SCOPED_CUSTOMER):
        return False
    if party_match_key(supplier) != party_match_key(SCOPED_SUPPLIER):
        return False
    return True


def is_blue_sax_apron_name(product_name: str) -> bool:
    """エプロン名にブルーとサックスの両方が含まれるか（表記ゆれ吸収後）。"""
    unified = paren_classify.unify_import_notation(stock.text(product_name) or '')
    if not unified:
        return False
    if 'エプロン' not in unified:
        return False
    if 'ブルー' not in unified:
        return False
    if 'サックス' not in unified:
        return False
    return True


def matches_scoped_color_correction(*, customer: str, supplier: str, code: str, product_name: str) -> bool:
    """3条件＋商品名パターンすべて一致したときだけ真。"""
    if not matches_scoped_parties(customer=customer, supplier=supplier, code=code):
        return False
    return is_blue_sax_apron_name(product_name)


def extract_munedo_supplement(product_name: str, unclassified=None) -> str:
    """胸当／胸当てを補足として正規化抽出。無ければ空。"""
    unified = paren_classify.unify_import_notation(stock.text(product_name) or '')
    for token in ('胸当て', '胸当'):
        if token in unified:
            return SCOPED_SUPPLEMENT
    for item in unclassified or []:
        iu = paren_classify.unify_import_notation(stock.text(item) or '')
        if not iu:
            continue
        if iu in ('胸当', '胸当て') or '胸当' in iu:
            return SCOPED_SUPPLEMENT
    return ''


def apply_scoped_color_correction(plan: dict, *, original_name: str) -> dict:
    """括弧分類結果へ LEOC 別注エプロンの色名補正を上書きする。

    呼び出し側で matches_scoped_color_correction を確認済みであること。
    ユーザー手編集の上書きは呼び出し側でスキップする。
    """
    out = dict(plan or {})
    unclassified = list(out.get('unclassified') or [])
    supplement = extract_munedo_supplement(original_name, unclassified)
    # 胸当は補足へ移し、未分類からは外す（名に戻さない）
    if supplement:
        unclassified = [
            u for u in unclassified
            if '胸当' not in paren_classify.unify_import_notation(stock.text(u) or '')
        ]
    out['plannedName'] = SCOPED_NAME
    out['color'] = SCOPED_COLOR
    out['colorKey'] = paren_classify.color_match_key(SCOPED_COLOR)
    out['supplement'] = supplement
    out['unclassified'] = unclassified
    out['needsConfirm'] = False
    out['confirmReasons'] = []
    out['autoClassified'] = True
    out['keptOriginal'] = False
    out['hasExplicitColor'] = True
    out['colorSource'] = 'scoped_leoc_unistyle'
    out['scopedColorCorrection'] = True
    out['changeSummary'] = SCOPED_REASON
    notices = [
        n for n in (out.get('infoNotices') or [])
        if '未分類' not in n and '配色として' not in n
    ]
    notices.append(SCOPED_REASON)
    if supplement:
        notices.append(f'補足『{supplement}』を保持します')
    out['infoNotices'] = notices
    # 複数色候補表示は補正後の1色に揃える
    out['colorCandidates'] = [SCOPED_COLOR]
    return out
