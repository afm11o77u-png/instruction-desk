"""拠点名の照合キー（拠点の一括取り込み・集配曜日の取り込み・組織の保存で共通）。

- NFKC・大文字小文字・空白を無視
- 先頭の会社名（例「北雄ラッキー」）と末尾の「店」を外す（北雄ラッキー篠路 ＝ 篠路店）
- 補助キー：先頭の店舗ブランド名（ラッキーマート）を外したもの（ラッキーマート西野 ＝ 西野店）
あいまいな推測はしない。完全一致だけを同じ拠点とみなす（「似た拠点」は案内のみで、自動では一致させない）。
"""
import re
import unicodedata

STORE_BRAND_PREFIXES = ('ラッキーマート',)
_CORPORATE = r'(?:株式会社|有限会社|\(株\)|\(有\))'


def _plain(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or ''))).casefold()


def company_key(value):
    """会社名から法人格・末尾の記号（㈱・㉈など）を外したキー。"""
    name = unicodedata.normalize('NFKC', str(value or '')).strip()
    name = re.sub(r'^' + _CORPORATE + r'\s*|\s*' + _CORPORATE + r'$', '', name)
    while name and not (unicodedata.category(name[-1]).startswith('L') or unicodedata.category(name[-1]) == 'Nd'):
        name = name[:-1]
    name = re.sub(r'^' + _CORPORATE + r'\s*|\s*' + _CORPORATE + r'$', '', name)
    return _plain(name)


def site_keys(name, company=''):
    """[主キー, 補助キー...]。主キーが空なら []。"""
    key = _plain(name)
    ck = company_key(company)
    if ck and key.startswith(ck) and len(key) > len(ck):
        key = key[len(ck):]
    if key.endswith('店') and len(key) > 1:
        key = key[:-1]
    if not key:
        return []
    keys = [key]
    for brand in STORE_BRAND_PREFIXES:
        b = _plain(brand)
        if key.startswith(b) and len(key) > len(b):
            keys.append(key[len(b):])
    return keys


def _common_prefix(a, b):
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return n


def find(name, company, sites, company_of):
    """sites から同じ拠点を探す。(node, how) / (None, reason, similar_names)。
    company_of(node) はその拠点の会社名。主キーの一致 → 補助キーの一致の順で、1件に決まる時だけ一致。"""
    def keys_for(node):
        return site_keys(node['name'], company_of(node))
    mine = {}
    for node in sites:
        co = company_of(node)
        if co not in mine:
            mine[co] = site_keys(name, co)
    primary = [n for n in sites if mine[company_of(n)] and keys_for(n)[:1] == mine[company_of(n)][:1]]
    if len(primary) == 1:
        return primary[0], '拠点名一致', []
    if len(primary) > 1:
        return None, '同じ名前の拠点が複数あります', [n['name'] for n in primary]
    alt = [n for n in sites if mine[company_of(n)] and set(keys_for(n)) & set(mine[company_of(n)])]
    if len(alt) == 1:
        return alt[0], '拠点名一致（ブランド名を省略）', []
    if len(alt) > 1:
        return None, '同じ名前の拠点が複数あります', [n['name'] for n in alt]
    similar = []
    for n in sites:
        a, b = mine[company_of(n)], keys_for(n)
        if a and b and (a[0] in b[0] or b[0] in a[0] or _common_prefix(a[0], b[0]) >= 2):
            similar.append(n['name'])
    return None, '一致する拠点がありません', similar
