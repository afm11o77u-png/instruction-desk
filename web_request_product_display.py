"""WEB依頼の商品ごとの「WEB表示名」と「写真」。商品単位で1件、全顧客で共通。
在庫DBは読み取り専用のため、指示書DBに保存する。社内画面は元の商品名のまま。"""
import base64
import io
from datetime import datetime

MAX_PHOTO_BYTES = 3 * 1024 * 1024
MAX_NAME = 120
PHOTO_TYPES = {
    'image/jpeg': (b'\xff\xd8\xff',),
    'image/png': (b'\x89PNG\r\n\x1a\n',),
    'image/webp': (b'RIFF',),
}


def schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS web_request_product_display (
      product_id INTEGER PRIMARY KEY, display_name TEXT NOT NULL DEFAULT '',
      photo BLOB, photo_type TEXT NOT NULL DEFAULT '', photo_version INTEGER NOT NULL DEFAULT 0,
      updated_at TEXT, updated_by INTEGER)''')


def resolve_name(contract_display_name, product_display_name, stock_name):
    """顧客向けの商品名：契約の表示名 → 商品のWEB表示名 → 在庫の商品名。"""
    for value in (contract_display_name, product_display_name, stock_name):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def clean_name(value):
    if value is None:
        return ''
    if not isinstance(value, str) or len(value.strip()) > MAX_NAME:
        raise ValueError(f'WEB表示名は{MAX_NAME}文字以内で入力してください。')
    return ' '.join(value.split())


def decode_photo(value):
    """data URL（JPEG・PNG・WebP、3MB以下）を検査して (bytes, mime) を返す。"""
    if not isinstance(value, str) or ',' not in value:
        raise ValueError('写真はJPEG・PNG・WebPの画像を選んでください。')
    head, encoded = value.split(',', 1)
    mime = head[5:-7] if head.startswith('data:') and head.endswith(';base64') else ''
    if mime not in PHOTO_TYPES:
        raise ValueError('写真はJPEG・PNG・WebPの画像を選んでください。')
    if len(encoded) > (MAX_PHOTO_BYTES * 4) // 3 + 8:
        raise ValueError('写真は3MB以下の画像を選んでください。')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as e:
        raise ValueError('写真を読み取れませんでした。') from e
    if len(raw) > MAX_PHOTO_BYTES:
        raise ValueError('写真は3MB以下の画像を選んでください。')
    if not raw.startswith(PHOTO_TYPES[mime]) or (mime == 'image/webp' and raw[8:12] != b'WEBP'):
        raise ValueError('写真の形式と中身が一致しません。JPEG・PNG・WebPの画像を選んでください。')
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return raw, mime
    try:
        with Image.open(io.BytesIO(raw)) as img:
            if img.width * img.height > 40000000 or getattr(img, 'n_frames', 1) != 1:
                raise ValueError()
            img = ImageOps.exif_transpose(img)
            img.thumbnail((1200, 1200))
            canvas = Image.new('RGB', img.size, 'white')
            if 'A' in img.getbands():
                canvas.paste(img, mask=img.getchannel('A'))
            else:
                canvas.paste(img.convert('RGB'))
            out = io.BytesIO()
            canvas.save(out, format='JPEG', quality=85)
        return out.getvalue(), 'image/jpeg'
    except Exception as e:
        raise ValueError('写真はJPEG・PNG・WebPの静止画像を選んでください。') from e


def read_map(c, product_ids=None):
    rows = c.execute('SELECT product_id,display_name,photo_version,photo IS NOT NULL AS has_photo FROM web_request_product_display').fetchall()
    keep = set(product_ids) if product_ids is not None else None
    out = {}
    for r in rows:
        if keep is not None and r[0] not in keep:
            continue
        out[str(r[0])] = dict(display_name=r[1] or '', photo_version=r[2] if r[3] else 0)
    return out


def save(c, user, d, product_ids):
    """1商品の表示名・写真を保存。主管理者・管理者のみ。"""
    if not (user and (user['role'] == 'owner' or user['manage'])):
        raise PermissionError('WEB表示名・写真の設定は主管理者・管理者が行ってください。')
    pid = d.get('product_id')
    if type(pid) is not int or pid not in product_ids:
        raise ValueError('商品を選んでください。')
    now = datetime.now().isoformat(timespec='seconds')
    c.execute('INSERT OR IGNORE INTO web_request_product_display(product_id) VALUES(?)', (pid,))
    if 'display_name' in d:
        c.execute('UPDATE web_request_product_display SET display_name=?,updated_at=?,updated_by=? WHERE product_id=?', (clean_name(d['display_name']), now, user['id'], pid))
    if d.get('remove_photo') is True:
        c.execute("UPDATE web_request_product_display SET photo=NULL,photo_type='',photo_version=photo_version+1,updated_at=?,updated_by=? WHERE product_id=?", (now, user['id'], pid))
    elif d.get('photo'):
        raw, mime = decode_photo(d['photo'])
        c.execute('UPDATE web_request_product_display SET photo=?,photo_type=?,photo_version=photo_version+1,updated_at=?,updated_by=? WHERE product_id=?', (raw, mime, now, user['id'], pid))
    c.commit()
    return read_map(c, [pid]).get(str(pid), dict(display_name='', photo_version=0))


def photo(c, pid):
    row = c.execute('SELECT photo,photo_type FROM web_request_product_display WHERE product_id=? AND photo IS NOT NULL', (pid,)).fetchone()
    if not row or row[1] not in PHOTO_TYPES:
        return None
    return bytes(row[0]), row[1]
