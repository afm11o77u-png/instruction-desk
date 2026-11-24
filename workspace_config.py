"""業務選択（TOP）画面の表示切替。

本格始動で TOP を再び出すときは、SHOW_TOP_MENU を True に変えて再起動する。
ページそのものは残してあるので、この定数以外を戻す必要はない。

True のときは / が業務選択画面。False のときは / 自身が指示書デスクを出す
（302 で URL を書き換えない。Chrome が空応答になるのを避けるため）。
"""

# False のあいだ、アプリを開くと指示書デスクに入る。True で業務選択画面を再開する。
SHOW_TOP_MENU = False


def browser_script():
    value = 'true' if SHOW_TOP_MENU else 'false'
    return f'window.SHOW_TOP_MENU={value};\n'
