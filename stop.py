import ssl
import json
"""Stop only the verified instruction-desk instance on its configured port."""
import subprocess, sys, urllib.request, time, re
from pathlib import Path

root = Path(__file__).resolve().parent
config = json.loads((root / 'network.json').read_text(encoding='utf-8-sig')) if (root / 'network.json').exists() else {}
port = int(config.get('port', 8765))
scheme = 'https' if config.get('tls') else 'http'
url = f'{scheme}://127.0.0.1:{port}'
context = ssl.create_default_context(cafile=str(root / 'tls/trial-root.cer')) if config.get('tls') else None


def main():
    if sys.platform != 'win32':
        print('この停止プログラムはWindows用です。')
        return 1
    result = subprocess.run(['netstat', '-ano'], capture_output=True, check=True)
    output = result.stdout.decode('cp932', errors='replace')
    listeners = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 5 and parts[0] == 'TCP' and parts[1] in (f'127.0.0.1:{port}', f'0.0.0.0:{port}') and parts[3] == 'LISTENING' and re.fullmatch(r'\d+', parts[4]):
            listeners.append(parts[4])
    if not listeners:
        print('起動していません。')
        return 0
    if len(set(listeners)) != 1:
        print('起動中のアプリを特定できません。停止を中止しました。')
        return 1
    for route, name in [('/', 'index.html'), ('/app.js', 'app.js')]:
        try:
            with urllib.request.urlopen(url + route, timeout=5, context=context) as response:
                same = response.read() == (root / name).read_bytes()
        except Exception:
            same = False
        if not same:
            print('別フォルダのアプリが起動している可能性があるため、停止しませんでした。')
            return 1
    stopped = subprocess.run(['taskkill', '/PID', listeners[0], '/F'], capture_output=True)
    if stopped.returncode:
        print('Windowsに停止を拒否されました。')
        return 1
    for _ in range(30):
        try:
            urllib.request.urlopen(url + '/api/me', timeout=.3, context=context).close()
        except Exception:
            print('指示書デスクを停止しました。')
            return 0
        time.sleep(.1)
    print('アプリの停止を確認できません。少し待ってから実行してください。')
    return 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        print('停止できませんでした：', str(e))
        sys.exit(1)
