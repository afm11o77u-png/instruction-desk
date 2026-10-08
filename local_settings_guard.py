"""Keep machine-specific settings outside Git and preserve them during updates."""
from contextlib import contextmanager
from pathlib import Path
import shutil
import uuid


def check(git, target=None):
    tracked = git('ls-files', '--', 'network.json', 'tls').stdout.strip()
    incoming = git('ls-tree', '-r', '--name-only', target, '--', 'network.json', 'tls').stdout.strip() if target else ''
    if tracked or incoming:
        raise RuntimeError('接続設定・証明書がGitの管理対象に含まれています。保護のため更新・送信を停止しました。')
    for name in ('network.json', 'tls/server.key'):
        if git('check-ignore', '-q', '--', name, check=False).returncode:
            raise RuntimeError('接続設定・証明書の除外設定が不足しています。保護のため停止しました。')


@contextmanager
def preserve(git):
    check(git)
    folder = Path(git('rev-parse', '--git-path', 'desk-local-settings').stdout.strip()) / uuid.uuid4().hex
    files = [Path('network.json')] if Path('network.json').is_file() else []
    if Path('tls').exists():
        files += [p for p in Path('tls').rglob('*') if p.is_file()]
    for p in files:
        if p.is_symlink() or any(parent.is_symlink() for parent in p.parents):
            raise RuntimeError('接続設定にリンクが含まれています。保護のため停止しました。')
        dest = folder / p
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest)
    try:
        yield
    finally:
        for p in files:
            backup = folder / p
            if not p.is_file() or p.read_bytes() != backup.read_bytes():
                if p.is_symlink() or any(parent.is_symlink() for parent in p.parents):
                    raise RuntimeError('設定の復元先がリンクに変わりました。退避先: ' + str(folder.resolve()))
                p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, p)
                print('PC固有の設定を更新前の状態に復元しました: ' + str(p))
