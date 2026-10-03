"""Synchronize this checkout without discarding local edits or rewriting history."""
import os
from pathlib import Path
import subprocess
import sys
import uuid


def git(*args, check=True):
    p = subprocess.run(['git', *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding='utf-8', errors='replace')
    if check and p.returncode:
        raise RuntimeError(p.stderr.strip() or p.stdout.strip() or 'Gitの処理に失敗しました。')
    return p


def validate():
    if git('branch', '--show-current').stdout.strip() != 'main':
        raise RuntimeError('main以外のブランチです。GitHub Desktopで作業内容を確認してください。')
    for name in ('MERGE_HEAD', 'rebase-merge', 'rebase-apply', 'CHERRY_PICK_HEAD', 'REVERT_HEAD'):
        if Path(git('rev-parse', '--git-path', name).stdout.strip()).exists():
            raise RuntimeError('別の統合作業が途中です。GitHub Desktopで確認してください。')
    if git('ls-files', '-u').stdout:
        raise RuntimeError('変更が重なったファイルがあります。内容の確認が必要です。')
    marker = Path(git('rev-parse', '--git-path', 'desk-sync-recovery').stdout.strip())
    if marker.exists():
        raise RuntimeError('前回の変更復元が未完了です。復旧情報: ' + str(marker.resolve()))
    return marker


def sync():
    marker = validate()
    print('GitHubの最新情報を確認しています...', flush=True)
    git('fetch', 'origin', 'refs/heads/main')
    target = git('rev-parse', 'FETCH_HEAD').stdout.strip()
    if git('merge-base', '--is-ancestor', target, 'HEAD', check=False).returncode == 0:
        return
    saved = None
    if git('status', '--porcelain').stdout:
        git('stash', 'push', '--include-untracked', '-m', 'desk-sync-' + uuid.uuid4().hex)
        saved = git('rev-parse', 'stash@{0}').stdout.strip()
        marker.write_text(saved, encoding='utf-8')
    merged = git('-c', 'merge.autoStash=false', 'merge', '--no-edit', '--no-gpg-sign', target, check=False)
    if merged.returncode:
        merge_head = Path(git('rev-parse', '--git-path', 'MERGE_HEAD').stdout.strip())
        if merge_head.exists():
            git('merge', '--abort')
    if saved:
        restored = git('stash', 'apply', '--index', saved, check=False)
        if restored.returncode:
            raise RuntimeError('手元の変更と更新が重なりました。自動処理を停止しました。'
                               '\n元の変更はGitのstashに保管されています。復旧情報: ' + str(marker.resolve()))
        if git('rev-parse', 'stash@{0}').stdout.strip() == saved:
            git('stash', 'drop', 'stash@{0}')
        marker.unlink()
    if merged.returncode:
        raise RuntimeError('自動統合できませんでした。統合を取り消し、元の変更を保持しています。'
                           '\nGitHub Desktopで確認してください。\n' + merged.stderr + merged.stdout)
    print('重ならない変更を統合しました。', flush=True)


def main():
    os.chdir(Path(__file__).resolve().parent)
    validate()
    if len(sys.argv) > 1 and sys.argv[1] == 'save':
        if git('status', '--porcelain').stdout:
            message = input('コミットメッセージを入力してください: ').strip()
            if not message:
                raise RuntimeError('説明が空のため、保存を中止しました。')
            git('add', '.')
            git('commit', '-m', message)
        else:
            print('新しい変更はありません。未送信分を確認します。')
        sync()
        git('push', '-u', 'origin', 'HEAD:refs/heads/main')
        print('GitHubへの保存が完了しました')
    else:
        sync()
        print('最新の取得が完了しました。')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, OSError) as exc:
        print('[エラー]', exc)
        print('通信・認証の失敗なら、確認後に再実行できます。変更は強制上書きしません。')
        sys.exit(1)
