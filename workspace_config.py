"""業務選択（TOP）画面の表示切替。

本格始動で TOP を再び出すときは、SHOW_TOP_MENU を True に変えて再起動する。
ページそのものは残してあるので、この定数以外を戻す必要はない。
"""
from urllib.parse import parse_qs, urlparse

# False のあいだ、アプリを開くと指示書デスクに入る。True で業務選択画面を再開する。
SHOW_TOP_MENU = False
DESK_PATH = '/?module=desk'
WORKSPACE_PATHS = (
    ('desk', DESK_PATH),
    ('info', '/?module=info'),
    ('stock', '/?module=stock'),
    ('contracts', '/customer-contracts'),
    ('web', '/request-web'),
    ('rental', '/rental'),
    ('mark', '/?module=mark'),
)


def workspace_allowed(user):
    if not user:
        return {}
    owner = user['role'] == 'owner'
    manage = bool(user.get('manage'))
    return {
        'desk': True,
        'info': True,
        'contracts': owner or user['role'] == 'sales' or manage,
        'web': owner or user['role'] == 'sales' or manage,
        'rental': owner or user['role'] in ('sales', 'warehouse') or manage,
        'stock': owner or user['role'] == 'warehouse' or manage or bool(user.get('overview')),
        'mark': owner or user['role'] == 'floor3' or manage or bool(user.get('overview')) or bool(user.get('mark_kintai')),
    }


def entry_path():
    """未ログイン時など、権限がまだ分からないときの入口。"""
    return '/' if SHOW_TOP_MENU else DESK_PATH


def entry_path_for(user):
    if SHOW_TOP_MENU:
        return '/'
    allowed = workspace_allowed(user)
    for key, path in WORKSPACE_PATHS:
        if allowed.get(key):
            return path
    return '/'


def is_top_menu_request(full_path):
    parsed = urlparse(full_path)
    if parsed.path != '/':
        return False
    query = parse_qs(parsed.query, keep_blank_values=True)
    module = (query.get('module') or [None])[0]
    if module == 'home':
        return True
    return not query


def should_redirect_top(full_path):
    return (not SHOW_TOP_MENU) and is_top_menu_request(full_path)
