"""旧商品台帳取り込み：確認リストの品番・色番自動対応ルール

特定品番について、要確認リストで手動判断を待たずに
「色別に分けて登録（品番-色番）」または「まとめる（同一商品へ統合）」を自動適用する。
"""
from __future__ import annotations

import re
import stock

# ---------------------------------------------------------------------------
# 1. 色別に分けて登録（24品番）
# ---------------------------------------------------------------------------
CUSTOM_COLOR_SPLIT_SPECS = {
    '00330-AVP': {
        'defaultProductName': 'ドライポロシャツ(ポケット付)',
        'variants': [
            {'patterns': ['バーガンディ', 'ﾊﾞｰｶﾞﾝﾃﾞｨ', '112', 'ワイン', '赤'], 'code': '00330-AVP-112', 'color': 'バーガンディ(112)'},
            # ホワイトまたは無指定の基本行
            {'patterns': ['ホワイト', '001', '白', ''], 'code': '00330-AVP-001', 'color': 'ホワイト(001)'},
        ],
    },
    '72-1226': {
        'defaultProductName': 'ジャケット(男女兼用・半袖)',
        'variants': [
            {'patterns': ['ブルー', '青', '1224'], 'code': '72-1224', 'color': 'ブルー×ネイビー'},
            {'patterns': ['ワイン', '赤', '1226', ''], 'code': '72-1226', 'color': 'ワイン×ネイビー'},
        ],
    },
    '7751': {
        'defaultProductName': 'コックシャツ（五分袖）[兼用]',
        'variants': [
            {'patterns': ['C/10', 'C-10', 'C10', '黒', 'ブラック'], 'code': '7751-C10', 'color': 'C-10ブラック'},
            {'patterns': ['C/1', 'C-1', 'C1', '白', 'ホワイト', ''], 'code': '7751-C1', 'color': 'C-1ホワイト'},
        ],
    },
    'AS7804': {
        'defaultProductName': 'コックシャツ(七分袖)[兼用]',
        'variants': [
            {'patterns': ['C/71', 'C-71', 'C71', 'ブラウン', '茶'], 'code': 'AS7804-C71', 'color': 'ブラウン(C71)'},
            {'patterns': ['C/2', 'C-2', 'C2', 'ワイン', '赤'], 'code': 'AS7804-C2', 'color': 'ワイン(C2)'},
            {'patterns': ['C/5', 'C-5', 'C5', '-5', 'ネイビー', '紺', ''], 'code': 'AS7804-C5', 'color': 'ネイビー(C5)'},
        ],
    },
    'AS8047': {
        'defaultProductName': 'コックシャツ/半袖(男女兼用)',
        'variants': [
            {'patterns': ['C/5', 'C-5', 'C5', 'ネイビー', '紺'], 'code': 'AS8047-C5', 'color': 'ネイビー(C5)'},
            {'patterns': ['C/7', 'C-7', 'C7', 'ベージュ', 'ﾍﾞｰｼﾞｭ'], 'code': 'AS8047-C7', 'color': 'ベージュ(C7)'},
            {'patterns': ['C/1', 'C-1', 'C1', 'ホワイト', '白', ''], 'code': 'AS8047-C1', 'color': 'ホワイト(C1)'},
        ],
    },
    'AS8085': {
        'defaultProductName': 'ハンチング帽',
        'variants': [
            {'patterns': ['C/29', 'C-29', 'C29', 'エンジ', 'ｴﾝｼﾞ'], 'code': 'AS8085-C29', 'color': 'エンジ(C29)'},
            {'patterns': ['C/10', 'C-10', 'C10', 'ブラック', '黒', 'ﾌﾞﾗｯｸ', ''], 'code': 'AS8085-C10', 'color': 'ブラック(C10)'},
            {'patterns': ['C/1', 'C-1', 'C1', '-C1'], 'code': 'AS8085-C1', 'color': 'ホワイト(C1)'},
        ],
    },
    'AS8523': {
        'defaultProductName': '七分袖コックシャツ(男女兼用)',
        'variants': [
            {'patterns': ['C/10', 'C-10', 'C10', 'ブラック', '黒', 'ﾌﾞﾗｯｸ'], 'code': 'AS8523-C10', 'color': 'ブラック(C10)'},
            {'patterns': ['C/5', 'C-5', 'C5', 'ネイビー', '紺', 'ﾈｲﾋﾞｰ', ''], 'code': 'AS8523-C5', 'color': 'ネイビー(C5)'},
        ],
    },
    'BC-6920': {
        'defaultProductName': 'ボタンダウンシャツ/長袖(女性用)',
        'variants': [
            {'patterns': ['C-10', 'C/10', 'c-10', 'C10', 'ブラック', '黒'], 'code': 'BC-6920-C10', 'color': 'ブラック(C10)'},
            {'patterns': ['C-1', 'C/1', 'c-1', 'C1', 'ホワイト', '白', ''], 'code': 'BC-6920-C1', 'color': 'ホワイト(C1)'},
        ],
    },
    'BSS-100': {
        'defaultProductName': '胸当てエプロン X型',
        'variants': [
            {'patterns': ['C/10', 'C-10', 'C10', 'ブラック', '黒', 'ﾌﾞﾗｯｸ'], 'code': 'BSS-100-C10', 'color': 'ブラック(C10)'},
            {'patterns': ['C/5', 'C-5', 'C5', 'ネイビー', '紺', 'ﾈｲﾋﾞｰ', ''], 'code': 'BSS-100-C5', 'color': 'ネイビー(C5)'},
        ],
    },
    'RF-5122': {
        'defaultProductName': '女子スクラブ',
        'variants': [
            {'patterns': ['93', 'ネイビー×ピンク', 'ネイビー+ピンク', 'ネイビー'], 'code': 'RF-5122-93', 'color': 'ネイビー＋ピンク(93)'},
            {'patterns': ['96', 'ボルドー×ピンク', 'ボルドー+ピンク', 'ボルドー'], 'code': 'RF-5122-96', 'color': 'ボルドー＋ピンク(96)'},
            {'patterns': ['94', 'ロイヤルブルー', 'ブルー'], 'code': 'RF-5122-94', 'color': 'ネイビー＋ロイヤルブルー(94)'},
            {'patterns': ['95', 'ターコイズ'], 'code': 'RF-5122-95', 'color': 'ネイビー＋ターコイズ(95)'},
        ],
    },
    'RF-5127': {
        'defaultProductName': '男子スクラブ',
        'variants': [
            {'patterns': ['94', 'ロイヤルブルー', 'ブルー'], 'code': 'RF-5127-94', 'color': 'ネイビー＋ロイヤルブルー(94)'},
            {'patterns': ['95', 'ターコイズ'], 'code': 'RF-5127-95', 'color': 'ネイビー＋ターコイズ(95)'},
        ],
    },
    'SD5000AM-50': {
        'defaultProductName': '帽子',
        'variants': [
            {'patterns': ['グリーン', '緑'], 'code': 'SD5000AM-50-グリーン', 'color': 'グリーン'},
            # 50番規格はブルー（新帽子・青帽子含む）
            {'patterns': ['ブルー', '青', '新帽子', ''], 'code': 'SD5000AM-50-ブルー', 'color': 'ブルー'},
        ],
    },
    'SD5000AM-5U': {
        'defaultProductName': '帽子',
        'variants': [
            {'patterns': ['ブルー', '青'], 'code': 'SD5000AM-5U-ブルー', 'color': 'ブルー'},
            {'patterns': ['ピンク'], 'code': 'SD5000AM-5U-ピンク', 'color': 'ピンク'},
            {'patterns': ['白', 'ホワイト'], 'code': 'SD5000AM-5U-白', 'color': '白'},
            {'patterns': ['黄', '黄色', 'イエロー'], 'code': 'SD5000AM-5U-黄色', 'color': '黄色'},
            # 5U規格の基本色はグリーン
            {'patterns': ['グリーン', '緑', ''], 'code': 'SD5000AM-5U-グリーン', 'color': 'グリーン'},
        ],
    },
    'T134': {
        'defaultProductName': 'H型 胸当てエプロン(男女兼用)',
        'variants': [
            {'patterns': ['C/6', 'C-6', 'C6', 'グリーン', 'ｸﾞﾘｰﾝ', '緑'], 'code': 'T134-C6', 'color': 'グリーン(C6)'},
            {'patterns': ['C/4', 'C-4', 'C4', 'オレンジ', 'ｵﾚﾝｼﾞ', ''], 'code': 'T134-C4', 'color': 'オレンジ(C4)'},
        ],
    },
    'T152': {
        'defaultProductName': 'エプロン 男女兼用',
        'variants': [
            {'patterns': ['C/3', 'C-3', 'C3', 'ブルー', 'ﾌﾞﾙｰ', '青'], 'code': 'T152-C3', 'color': 'ブルー(C3)'},
            {'patterns': ['C/2', 'C-2', 'C2', 'ピンク', 'ﾋﾟﾝｸ', ''], 'code': 'T152-C2', 'color': 'ピンク(C2)'},
        ],
    },
    'T62': {
        'defaultProductName': '胸当てエプロン 首掛け(男女兼用)',
        'variants': [
            {'patterns': ['C/62', 'C-62', 'C62', 'T62-62', 'モスグリーン', 'ﾓｽｸﾞﾘｰﾝ'], 'code': 'T62-C62', 'color': 'モスグリーン(C62)'},
            {'patterns': ['C/29', 'C-29', 'C29', 'T62-29', 'ワイン', 'ﾜｲﾝ'], 'code': 'T62-C29', 'color': 'ワイン(C29)'},
            {'patterns': ['C/10', 'C-10', 'C10', 'T62-10', 'ブラック', '黒', 'ﾌﾞﾗｯｸ', ''], 'code': 'T62-C10', 'color': 'ブラック(C10)'},
        ],
    },
    'T6232': {
        'defaultProductName': 'ミドル丈撥水エプロン(男女兼用)',
        'variants': [
            {'patterns': ['C-73', 'C/73', 'C73', 'T6232-73', 'ベージュ', 'ﾍﾞｰｼﾞｭ'], 'code': 'T6232-C73', 'color': 'ベージュ(C73)'},
            {'patterns': ['C-7', 'C/7', 'C7', 'T6232-7', 'ブラウン', 'ﾌﾞﾗｳﾝ', '茶'], 'code': 'T6232-C7', 'color': 'ブラウン(C7)'},
            {'patterns': ['C-2', 'C/2', 'C2', 'T6232-2', 'ワイン', 'ﾜｲﾝ', ''], 'code': 'T6232-C2', 'color': 'ワイン(C2)'},
        ],
    },
    'T63': {
        'defaultProductName': '胸当てエプロン 背中X型(男女兼用)',
        'variants': [
            {'patterns': ['C/7', 'C-7', 'C7', 'ブラウン', 'ﾌﾞﾗｳﾝ', '茶'], 'code': 'T63-C7', 'color': 'ブラウン(C7)'},
            {'patterns': ['C/62', 'C-62', 'C62', 'モスグリーン', 'ﾓｽｸﾞﾘｰﾝ'], 'code': 'T63-C62', 'color': 'モスグリーン(C62)'},
            {'patterns': ['C/29', 'C-29', 'C29', 'ワイン', 'ﾜｲﾝ'], 'code': 'T63-C29', 'color': 'ワイン(C29)'},
            {'patterns': ['C/10', 'C-10', 'C10', 'ブラック', '黒', 'ﾌﾞﾗｯｸ', ''], 'code': 'T63-C10', 'color': 'ブラック(C10)'},
        ],
    },
    'T7501': {
        'defaultProductName': '胸当てエプロン H型(男女兼用)',
        'variants': [
            {'patterns': ['C/29', 'C-29', 'C29', 'ワイン', 'ﾜｲﾝ'], 'code': 'T7501-C29', 'color': 'ワイン(C29)'},
            {'patterns': ['C/5', 'C-5', 'C5', 'ネイビー', '紺', 'ﾈｲﾋﾞｰ', ''], 'code': 'T7501-C5', 'color': 'ネイビー(C5)'},
        ],
    },
    'T8088': {
        'defaultProductName': 'プレミアムショートエプロン(男女兼用)',
        'variants': [
            {'patterns': ['C/71', 'C-71', 'C71', 'ブラウン', 'ﾌﾞﾗｳﾝ', '茶'], 'code': 'T8088-C71', 'color': 'ブラウン(C71)'},
            {'patterns': ['C/29', 'C-29', 'C29', 'エンジ', 'ｴﾝｼﾞ', ''], 'code': 'T8088-C29', 'color': 'エンジ(C29)'},
        ],
    },
    'T8089': {
        'defaultProductName': 'プレミアムロングエプロン(男女兼用)',
        'variants': [
            {'patterns': ['C/62', 'C-62', 'C62', 'ダークグリーン', 'ﾀﾞｰｸｸﾞﾘｰﾝ'], 'code': 'T8089-C62', 'color': 'ダークグリーン(C62)'},
            {'patterns': ['C/71', 'C-71', 'C71', 'ブラウン', 'ﾌﾞﾗｳﾝ', '茶'], 'code': 'T8089-C71', 'color': 'ブラウン(C71)'},
            {'patterns': ['10', '-10', 'ブラック'], 'code': 'T8089-10', 'color': 'ブラック(10)'},
            {'patterns': ['C/7', 'C-7', 'C7', '-7', 'ベージュ', 'ﾍﾞｰｼﾞｭ', ''], 'code': 'T8089-C7', 'color': 'ベージュ(C7)'},
        ],
    },
    'T8091': {
        'defaultProductName': '胸当てエプロン(男女兼用)',
        'variants': [
            {'patterns': ['C/71', 'C-71', 'C71', 'ブラウン', 'ﾌﾞﾗｳﾝ', '茶'], 'code': 'T8091-C71', 'color': 'ブラウン(C71)'},
            {'patterns': ['C/29', 'C-29', 'C29', 'エンジ', 'ｴﾝｼﾞ', ''], 'code': 'T8091-C29', 'color': 'エンジ(C29)'},
        ],
    },
    'T8092': {
        'defaultProductName': '胸当てエプロン H型(男女兼用)',
        'variants': [
            {'patterns': ['C/5', 'C-5', 'C5', '5', 'ダークネイビー', 'ﾀﾞｰｸﾈｲﾋﾞｰ', 'ネイビー'], 'code': 'T8092-5', 'color': 'ダークネイビー(5)'},
            {'patterns': ['C/71', 'C-71', 'C71', '71', 'ブラウン', 'ﾌﾞﾗｳﾝ', '茶'], 'code': 'T8092-71', 'color': 'ブラウン(71)'},
            {'patterns': ['C/7', 'C-7', 'C7', '7', 'ベージュ', 'ﾍﾞｰｼﾞｭ'], 'code': 'T8092-7', 'color': 'ベージュ(7)'},
            {'patterns': ['C/62', 'C-62', 'C62', '62', 'ダークグリーン', 'ﾀﾞｰｸｸﾞﾘｰﾝ'], 'code': 'T8092-62', 'color': 'ダークグリーン(62)'},
            {'patterns': ['C/29', 'C-29', 'C29', '29', 'エンジ', 'ｴﾝｼﾞ', ''], 'code': 'T8092-29', 'color': 'エンジ(29)'},
        ],
    },
    'UN0031': {
        'defaultProductName': 'ポロシャツ 男女兼用',
        'variants': [
            {'patterns': ['C-10', 'C/10', 'C10', 'ブラック', '黒', 'ﾌﾞﾗｯｸ'], 'code': 'UN0031-C10', 'color': 'ブラック(C10)'},
            {'patterns': ['C-5', 'C/5', 'C5', 'ネイビー', '紺', 'ﾈｲﾋﾞｰ'], 'code': 'UN0031-C5', 'color': 'ネイビー(C5)'},
            {'patterns': ['C-1', 'C/1', 'C1', 'ホワイト', '白', 'ﾎﾜｲﾄ', ''], 'code': 'UN0031-C1', 'color': 'ホワイト(C1)'},
        ],
    },
}

# ---------------------------------------------------------------------------
# 2. まとめる（同一商品へ自動統合：4品番）
# ---------------------------------------------------------------------------
CUSTOM_MERGE_SPECS = {
    '832-11': {
        'officialName': '白石 訪問リハ紺パンツ',
        'color': 'ネイビー/ホワイト',
        'primarySupplier': 'ＫＡＺＥＮ ＷＬＤ㈱',
    },
    'AIK302-11': {
        'officialName': '半袖ニットシャツ（男女兼用）',
        'color': 'ベージュ',
        'primarySupplier': '㈱札幌白衣',
        'subSupplier': '㈱ＣＵＰ',
    },
    'AIK302-79': {
        'officialName': '半袖ニットシャツ（男女兼用）',
        'color': 'ワイン',
        'primarySupplier': '㈱札幌白衣',
        'subSupplier': '㈱ＣＵＰ',
    },
    'MC117': {
        'officialName': 'ロングエプロン',
        'color': 'ボトルグリーン',
        'primarySupplier': '住商モンブラン㈱',
    },
}


def is_custom_color_split_code(code: str) -> bool:
    t = stock.text(code or '')
    return t in CUSTOM_COLOR_SPLIT_SPECS


def is_custom_merge_code(code: str) -> bool:
    t = stock.text(code or '')
    return t in CUSTOM_MERGE_SPECS


def get_custom_merge_spec(code: str) -> dict | None:
    t = stock.text(code or '')
    return CUSTOM_MERGE_SPECS.get(t)


def match_custom_color_variant(maker_code: str, row: dict, info: dict) -> dict | None:
    spec = CUSTOM_COLOR_SPLIT_SPECS.get(maker_code)
    if not spec:
        return None
    csv_info = row.get('csv') or {}
    code_raw = stock.text(csv_info.get('code') or '')
    name_raw = stock.text(csv_info.get('productName') or '')
    color_val = stock.text(info.get('color') or '')
    candidates = [stock.text(c) for c in (info.get('colorCandidates') or [])]
    search_haystack = f"{code_raw} {name_raw} {color_val} {' '.join(candidates)}".upper()

    default_variant = None
    for var in spec.get('variants') or []:
        patterns = var.get('patterns') or []
        for pat in patterns:
            if not pat:
                default_variant = var
                continue
            if pat.upper() in search_haystack:
                return {
                    'code': var['code'],
                    'color': var['color'],
                    'productName': spec.get('defaultProductName') or name_raw,
                }
    if default_variant:
        return {
            'code': default_variant['code'],
            'color': default_variant['color'],
            'productName': spec.get('defaultProductName') or name_raw,
        }
    return None
