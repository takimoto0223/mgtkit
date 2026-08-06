# -*- coding: utf-8 -*-
"""mgtkit (Flask アプリ) の起動とブラウザ表示."""
import logging
import os
import socket
import subprocess
import sys
import webbrowser

from .paths import app_dir, upload_tmp_dir

log = logging.getLogger(__name__)


class LaunchError(Exception):
    """起動失敗。str() はユーザー向けの平易な日本語メッセージ."""


def port_in_use(port, host='127.0.0.1'):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def app_url(port):
    return 'http://127.0.0.1:%d/' % port


def launch_app(instance_dir, port, channel='stable', python=None):
    """インストール済み mgtkit を起動しブラウザを開く.

    instance_dir は stable/ や beta/<version>/ のインスタンスフォルダ
    (アプリ本体はその中の mgtkit/)。既に同じポートで起動済みの場合は
    新規起動せずブラウザだけ開く。
    戻り値: (subprocess.Popen | None, url)
    """
    app_py = os.path.join(app_dir(instance_dir), 'app.py')
    if not os.path.isfile(app_py):
        raise LaunchError('アプリがまだ取得されていません。'
                          '「更新」タブから最新版を取得してください。')
    url = app_url(port)
    if port_in_use(port):
        log.info('port %d は使用中。既存の画面を開きます', port)
        webbrowser.open(url)
        return None, url

    env = dict(os.environ)
    env['MGTKIT_PORT'] = str(port)
    env['MGTKIT_CHANNEL'] = channel
    env['MGTKIT_UPLOAD_DIR'] = upload_tmp_dir(instance_dir)
    # ブラウザは app.py 自身が 1.2 秒後に開く (MGTKIT_NO_BROWSER は設定しない)

    kw = {}
    if sys.platform == 'win32':
        kw['creationflags'] = 0x08000000  # CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(
            [python or sys.executable, app_py],
            cwd=app_dir(instance_dir), env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
    except OSError as e:
        log.error('launch failed: %s', e)
        raise LaunchError('アプリの起動に失敗しました。'
                          'Python の導入状態を確認してください。')
    return proc, url
