"""Per-user list display mode preferences (ゆったり / 標準 / コンパクト / グリッド)."""
import re

MODES = ('cozy', 'standard', 'compact', 'grid')
DEFAULT = 'standard'
# Unset → 標準. Instruction lists stay grid; stock screens moved off former compact default.
SCREEN_DEFAULTS = {
    'orders': 'grid',
    'floor': 'grid',
    'drafts': 'grid',
    'shipinfo': 'standard',
    'stock-customers': 'standard',
    'stock-suppliers': 'standard',
    'stock-products': 'standard',
    'stock-locations': 'standard',
    'stock-opening': 'standard',
    'stock-list': 'standard',
    'stock-move': 'standard',
    'stock-period': 'standard',
}
SCREENS = set(SCREEN_DEFAULTS)
SCREEN_RE = re.compile(r'^[a-z][a-z0-9-]{0,39}$')

# Density remap (once per saved row via density_v):
# old 標準 → new ゆったり (cozy); old コンパクト → new 標準;
# old ゆったり → new ゆったり; グリッド unchanged.
DENSITY_REMAP_VERSION = 1
LEGACY_MODE_MAP = {
    'standard': 'cozy',
    'compact': 'standard',
    'cozy': 'cozy',
    'grid': 'grid',
}

def initialize(c):
    c.execute(
        'CREATE TABLE IF NOT EXISTS user_display_modes ('
        'user_id INTEGER NOT NULL, '
        'screen_id TEXT NOT NULL, '
        'mode TEXT NOT NULL, '
        'PRIMARY KEY (user_id, screen_id)'
        ')'
    )
    cols = {row[1] for row in c.execute('PRAGMA table_info(user_display_modes)')}
    if 'density_v' not in cols:
        c.execute(
            'ALTER TABLE user_display_modes '
            'ADD COLUMN density_v INTEGER NOT NULL DEFAULT 0'
        )

def default_for(screen_id):
    if screen_id in SCREEN_DEFAULTS:
        return SCREEN_DEFAULTS[screen_id]
    if isinstance(screen_id, str) and screen_id.startswith('stock-'):
        return 'standard'
    return DEFAULT

def remap_legacy_mode(mode):
    """Map a pre-remap stored mode to the post-remap key (labels unchanged for grid)."""
    return LEGACY_MODE_MAP.get(mode, mode)

def normalize_mode(mode, screen_id=None):
    return mode if mode in MODES else default_for(screen_id)

def normalize_screen(screen_id):
    if not isinstance(screen_id, str):
        return None
    value = screen_id.strip()
    if value not in SCREENS or not SCREEN_RE.fullmatch(value):
        return None
    return value

def _row_density_v(row):
    try:
        return int(row['density_v'])
    except (KeyError, IndexError, TypeError, ValueError):
        return 0

def migrate_user_modes(c, user_id):
    """Rewrite pre-remap rows once. Rows with density_v >= version are left alone."""
    rows = c.execute(
        'SELECT screen_id, mode, density_v FROM user_display_modes WHERE user_id=?',
        (user_id,),
    ).fetchall()
    changed = False
    for row in rows:
        if _row_density_v(row) >= DENSITY_REMAP_VERSION:
            continue
        screen = normalize_screen(row['screen_id'])
        if not screen:
            continue
        next_mode = normalize_mode(remap_legacy_mode(row['mode']), screen)
        c.execute(
            'UPDATE user_display_modes SET mode=?, density_v=? '
            'WHERE user_id=? AND screen_id=?',
            (next_mode, DENSITY_REMAP_VERSION, user_id, screen),
        )
        changed = True
    return changed

def handle_get(handler, c):
    u = handler.session_user(c)
    if not u:
        return 401, {'error': 'ログインしてください。'}
    if u['must_change']:
        return 403, {'error': '最初に仮パスワードを変更してください。'}
    migrate_user_modes(c, u['id'])
    rows = c.execute(
        'SELECT screen_id, mode, density_v FROM user_display_modes WHERE user_id=?',
        (u['id'],),
    ).fetchall()
    modes = {}
    for row in rows:
        screen = normalize_screen(row['screen_id'])
        if not screen:
            continue
        modes[screen] = normalize_mode(row['mode'], screen)
    return 200, {
        'modes': modes,
        'default': DEFAULT,
        'defaults': dict(SCREEN_DEFAULTS),
        'densityRemapVersion': DENSITY_REMAP_VERSION,
    }

def handle_post(handler, c, data):
    u = handler.session_user(c)
    if not u:
        return 401, {'error': 'ログインしてください。'}
    if u['must_change']:
        return 403, {'error': '最初に仮パスワードを変更してください。'}
    # Migrate first so a concurrent old row is not left at density_v=0 beside a new save.
    migrate_user_modes(c, u['id'])
    screen = normalize_screen(data.get('screenId'))
    if not screen:
        return 400, {'error': '画面を確認してください。'}
    mode = normalize_mode(data.get('mode'), screen)
    c.execute(
        'INSERT INTO user_display_modes(user_id, screen_id, mode, density_v) VALUES(?,?,?,?) '
        'ON CONFLICT(user_id, screen_id) DO UPDATE SET '
        'mode=excluded.mode, density_v=excluded.density_v',
        (u['id'], screen, mode, DENSITY_REMAP_VERSION),
    )
    return 200, {'screenId': screen, 'mode': mode}
