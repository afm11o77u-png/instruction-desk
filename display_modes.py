"""Per-user list display mode preferences (standard / compact / cozy / grid)."""
import re

MODES = ('standard', 'compact', 'cozy', 'grid')
DEFAULT = 'standard'
SCREEN_DEFAULTS = {
    'orders': 'grid',
    'floor': 'grid',
    'drafts': 'grid',
    'shipinfo': 'standard',
    'stock-customers': 'compact',
    'stock-suppliers': 'compact',
    'stock-products': 'compact',
    'stock-locations': 'compact',
    'stock-list': 'compact',
}
SCREENS = set(SCREEN_DEFAULTS)
SCREEN_RE = re.compile(r'^[a-z][a-z0-9-]{0,39}$')

def initialize(c):
    c.execute(
        'CREATE TABLE IF NOT EXISTS user_display_modes ('
        'user_id INTEGER NOT NULL, '
        'screen_id TEXT NOT NULL, '
        'mode TEXT NOT NULL, '
        'PRIMARY KEY (user_id, screen_id)'
        ')'
    )

def default_for(screen_id):
    if screen_id in SCREEN_DEFAULTS:
        return SCREEN_DEFAULTS[screen_id]
    if isinstance(screen_id, str) and screen_id.startswith('stock-'):
        return 'compact'
    return DEFAULT

def normalize_mode(mode, screen_id=None):
    return mode if mode in MODES else default_for(screen_id)

def normalize_screen(screen_id):
    if not isinstance(screen_id, str):
        return None
    value = screen_id.strip()
    if value not in SCREENS or not SCREEN_RE.fullmatch(value):
        return None
    return value

def handle_get(handler, c):
    u = handler.session_user(c)
    if not u:
        return 401, {'error': 'ログインしてください。'}
    if u['must_change']:
        return 403, {'error': '最初に仮パスワードを変更してください。'}
    rows = c.execute(
        'SELECT screen_id, mode FROM user_display_modes WHERE user_id=?',
        (u['id'],),
    ).fetchall()
    modes = {}
    for row in rows:
        screen = normalize_screen(row['screen_id'])
        if not screen:
            continue
        modes[screen] = normalize_mode(row['mode'], screen)
    return 200, {'modes': modes, 'default': DEFAULT, 'defaults': dict(SCREEN_DEFAULTS)}

def handle_post(handler, c, data):
    u = handler.session_user(c)
    if not u:
        return 401, {'error': 'ログインしてください。'}
    if u['must_change']:
        return 403, {'error': '最初に仮パスワードを変更してください。'}
    screen = normalize_screen(data.get('screenId'))
    if not screen:
        return 400, {'error': '画面を確認してください。'}
    mode = normalize_mode(data.get('mode'), screen)
    c.execute(
        'INSERT INTO user_display_modes(user_id, screen_id, mode) VALUES(?,?,?) '
        'ON CONFLICT(user_id, screen_id) DO UPDATE SET mode=excluded.mode',
        (u['id'], screen, mode),
    )
    return 200, {'screenId': screen, 'mode': mode}
