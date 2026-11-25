"""Company and site contact details, plus customer codes, for 顧客・契約管理."""
from contextlib import closing
from pathlib import Path
import sys
import web_requests as w
import web_request_organizations as organizations

_STOCK = Path(__file__).resolve().parent / 'stock-desk'
if str(_STOCK) not in sys.path:
    sys.path.insert(0, str(_STOCK))


KEY_COMPANY_FIELDS = ('customerCode', 'name', 'postalCode', 'address')
KEY_SITE_FIELDS = ('name', 'postalCode', 'address')


def handle(c, path, d, user):
    if not organizations.admin(user):
        raise PermissionError('拠点の詳細は管理者が編集してください。')
    cid = w.number(d.get('customer_id'), '顧客', 1, 2147483647)
    data = w.state(c, path, user)
    if cid not in {x['id'] for x in data['customers']}:
        raise PermissionError('この会社の詳細を変更する権限がありません。')
    if d.get('action') == 'load_site_details':
        return load(c, path, cid, data)
    return save(c, path, d, user, cid, data)


def _customer_row(path, cid):
    import stock
    with closing(stock.connect(path)) as s, s:
        stock.ensure_customer_profile_columns(s)
        row = s.execute('SELECT * FROM customers WHERE id=?', (cid,)).fetchone()
        if not row:
            raise ValueError('顧客が見つかりません。')
        profile = stock.customer_row(s, cid)
        profile['updatedAt'] = row['updated_at']
        return profile


def load(c, path, cid, data=None):
    org = organizations.read(c)
    customer = _customer_row(path, cid)
    nodes = [n for n in org['nodes'] if n.get('customer_id') == cid and n['kind'] in ('company', 'site')]
    return dict(
        customer=customer,
        nodes=nodes,
        orgVersion=org['version'],
        customerUpdatedAt=customer['updatedAt'],
    )


def incomplete(kind, customer, node):
    if kind == 'company':
        return not all((customer.get('customerCode') or '').strip(), (customer.get('name') or '').strip(), (customer.get('postalCode') or '').strip(), (customer.get('address') or '').strip())
    postal = (node.get('postal_code') or '').strip()
    address = (node.get('address') or '').strip()
    name = (node.get('official_name') or node.get('name') or '').strip()
    return not (name and postal and address)


def save(c, path, d, user, cid, data):
    import stock
    org = organizations.read(c)
    nid = w.text(d.get('node_id', ''), '組織', 80, True)
    node = next((n for n in org['nodes'] if n['id'] == nid and n.get('customer_id') == cid), None)
    if not node or node['kind'] not in ('company', 'site'):
        raise ValueError('会社または拠点を選んでください。')
    if type(d.get('orgVersion')) is not int or d['orgVersion'] != org['version']:
        raise ValueError('組織構成が更新されています。再読込してください。')
    official = w.text(d.get('officialName', ''), '正式名称', 100, True)
    kana = w.text(d.get('kana', ''), 'フリガナ', 80)
    postal = w.text(d.get('postalCode', ''), '郵便番号', 20)
    address = w.text(d.get('address', ''), '住所', 200)
    phone = w.text(d.get('phone', ''), '電話', 40)
    fax = w.text(d.get('fax', ''), 'FAX', 40)
    contact = w.text(d.get('contact', ''), '先方担当者', 80)
    email = w.text(d.get('email', ''), 'メール', 120)
    memo = w.text(d.get('memo', ''), 'メモ', 2000)

    if node['kind'] == 'company':
        with closing(stock.connect(path)) as s, s:
            s.execute('BEGIN IMMEDIATE')
            stock.ensure_customer_profile_columns(s)
            current = s.execute('SELECT updated_at FROM customers WHERE id=?', (cid,)).fetchone()
            if not current:
                raise ValueError('顧客が見つかりません。')
            if d.get('customerUpdatedAt') != current['updated_at']:
                raise ValueError('別の画面で変更されています。再読込してください。')
            body = dict(
                id=cid,
                name=official,
                kana=kana,
                address=address,
                phone=phone,
                contact=contact,
                postalCode=postal,
                fax=fax,
                email=email,
                memo=memo,
                updatedAt=current['updated_at'],
            )
            if 'customerCode' in d:
                body['customerCode'] = d.get('customerCode')
            stock.save_customer(s, body)
        node['name'] = official
        node['official_name'] = official
        node['kana'] = kana
        node['postal_code'] = postal
        node['address'] = address
        node['phone'] = phone
        node['fax'] = fax
        node['contact'] = contact
        node['email'] = email
        node['memo'] = memo
        organizations.save(c, org, d['orgVersion'], {x['id'] for x in data['customers']}, {x['id'] for x in data.get('organizationStaff', data['staff'])}, user)
    else:
        node['name'] = official
        node['official_name'] = official
        node['kana'] = kana
        node['postal_code'] = postal
        node['address'] = address
        node['phone'] = phone
        node['fax'] = fax
        node['contact'] = contact
        node['email'] = email
        node['memo'] = memo
        organizations.save(c, org, d['orgVersion'], {x['id'] for x in data['customers']}, {x['id'] for x in data.get('organizationStaff', data['staff'])}, user)
    return load(c, path, cid)


# Keep helper names imported by tests if needed
__all__ = ['handle', 'load', 'save', 'incomplete']
