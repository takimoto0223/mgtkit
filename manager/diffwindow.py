# -*- coding: utf-8 -*-
"""差分ビューワ HTML をアプリ専用ウィンドウで表示する.

Flet の WebView は Windows デスクトップ未対応のため、pywebview
(Windows 標準の Edge WebView2 を使う) の別プロセスでウィンドウを開く。
HTML はブラウザで開いていたものと完全に同一 (見た目・操作とも)。

- 親側: open_diff_window(path, title) — 子プロセスを起動して Popen を返す
  (pywebview が無い環境では None。呼び出し側はブラウザへフォールバック)
- 子側: python -m manager.diffwindow <html_path> <title>
  (pywebview は自前のイベントループを持つため、Flet と同居させず
  別プロセスで動かす)
"""
import importlib.util
import logging
import os
import subprocess
import sys

log = logging.getLogger(__name__)


def available():
    """pywebview が導入済みか (導入されるまではブラウザで開く)."""
    return importlib.util.find_spec('webview') is not None


def open_diff_window(path, title):
    """差分ウィンドウの子プロセスを起動する。起動できなければ None.

    子プロセスの生死は呼び出し側が数秒後に poll で確かめ、失敗して
    いればブラウザへフォールバックする (WebView2 ランタイム不在など)。
    """
    if not available():
        return None
    try:
        kwargs = {'stdout': subprocess.DEVNULL,
                  'stderr': subprocess.DEVNULL}
        if os.name == 'nt':
            # コンソール窓を出さない
            kwargs['creationflags'] = 0x08000000  # CREATE_NO_WINDOW
        return subprocess.Popen(
            [sys.executable, '-m', 'manager.diffwindow', path, title],
            **kwargs)
    except OSError:
        log.exception('差分ウィンドウの起動に失敗')
        return None


def main(argv):
    path, title = argv[0], argv[1]
    import webview
    # data: URI の ZIP 保存 (確認用データのダウンロード) を許可する
    try:
        webview.settings['ALLOW_DOWNLOADS'] = True
    except Exception:
        pass
    webview.create_window(title, url='file:///' + path.replace(os.sep, '/'),
                          width=1280, height=900)
    webview.start()


if __name__ == '__main__':
    main(sys.argv[1:])
