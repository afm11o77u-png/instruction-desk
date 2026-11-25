"""Rules for 納品希望日 on the customer WEB依頼 form.

Earliest date is the request date plus two calendar days, plus the largest
裾上げ（他） processing_days among selected uniforms. 自社集配 with collection
weekdays limits choices to those weekdays on or after the earliest date.
Courier methods and unset delivery allow any day on or after the earliest date.
Site settings walk up to category then company when the site itself has none.
"""
from datetime import date, timedelta

LEAD_DAYS = 2
WEEKDAYS = ('月', '火', '水', '木', '金', '土')
WEEKDAY_NAMES = '月火水木金土日'


def add_days(iso, days):
    y, m, d = (int(part) for part in iso.split('-'))
    return (date(y, m, d) + timedelta(days=days)).isoformat()


def weekday_jp(iso):
    y, m, d = (int(part) for part in iso.split('-'))
    return WEEKDAY_NAMES[date(y, m, d).weekday()]


def format_jp(iso):
    y, m, d = (int(part) for part in iso.split('-'))
    return f'{m}月{d}日（{weekday_jp(iso)}）'


def processing_days(items):
    days = [int(item.get('processing_days') or 0) for item in items or [] if item.get('hem_processing')]
    return max(days) if days else 0


def earliest_date(today, processing=0, lead=LEAD_DAYS):
    if not today:
        raise ValueError('依頼日を確認してください。')
    processing = max(0, int(processing or 0))
    return add_days(today, lead + processing)


def resolve_delivery(nodes, node_id):
    by_id = {n['id']: n for n in nodes or [] if n.get('id')}
    current = by_id.get(node_id)
    seen = set()
    while current and current['id'] not in seen:
        seen.add(current['id'])
        method = (current.get('delivery_method') or '').strip()
        if method:
            raw = current.get('collection_days') or []
            days = [d for d in WEEKDAYS if d in raw] if method == '自社集配' else []
            return method, days
        current = by_id.get(current.get('parent_id'))
    return '', []


def is_allowed(iso, earliest, method='', days=None):
    if not iso or iso < earliest:
        return False
    days = days or []
    if method == '自社集配' and days:
        return weekday_jp(iso) in days
    return True


def next_allowed(earliest, method='', days=None):
    iso = earliest
    for _ in range(400):
        if is_allowed(iso, earliest, method, days):
            return iso
        iso = add_days(iso, 1)
    return earliest


def hint_text(earliest, nxt, processing=0, method='', days=None):
    days = days or []
    reason = '依頼日から2日後' + (f'＋裾上げ{processing}日' if processing else '')
    text = f'最短は{format_jp(earliest)}です（{reason}）。'
    if method == '自社集配' and days:
        text += f'次回の集配日は{format_jp(nxt)}です。この拠点の集配曜日（{"・".join(days)}）だけ選べます。'
    else:
        text += 'この日以降を選んでください。'
    return text


def validate_desired_date(iso, today, items, nodes, node_id):
    processing = processing_days(items)
    earliest = earliest_date(today, processing)
    method, days = resolve_delivery(nodes, node_id)
    if not is_allowed(iso, earliest, method, days):
        nxt = next_allowed(earliest, method, days)
        if method == '自社集配' and days:
            raise ValueError(f'納品希望日は{format_jp(earliest)}以降の集配曜日だけ選べます。次回の集配日は{format_jp(nxt)}です。')
        raise ValueError(f'納品希望日は{format_jp(earliest)}以降を選んでください。')
    return True
