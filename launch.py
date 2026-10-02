import ssl
"""Start the local app independently of the terminal and open its login page."""
import json
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

root = Path(__file__).resolve().parent
config=json.loads((root/'network.json').read_text(encoding='utf-8-sig')) if (root/'network.json').exists() else {}
port=int(config.get('port',8765))
scheme='https' if config.get('tls') else 'http'
url=f'{scheme}://127.0.0.1:{port}'
context=ssl.create_default_context(cafile=str(root/'tls/trial-root.cer')) if config.get('tls') else None

def ready():
    try:
        with urllib.request.urlopen(url + '/api/me', timeout=1, context=context) as response:
            value = json.load(response)
            return 'setup' in value and 'user' in value
    except Exception:
        return False

if not ready():
    folder = root / 'data'
    folder.mkdir(exist_ok=True)
    with open(folder / 'startup.log', 'ab') as log:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        subprocess.Popen([sys.executable, '-X', 'utf8', str(root / 'server.py')], cwd=root,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         creationflags=flags, close_fds=True)
    for _ in range(30):
        if ready(): break
        time.sleep(.2)
    else:
        print('起動できませんでした。data/startup.log を確認してください。')
        sys.exit(1)
if '--no-browser' not in sys.argv:
    webbrowser.open(url)
print('指示書デスクが起動しています。'+url)
