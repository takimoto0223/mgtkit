# -*- coding: utf-8 -*-
"""mgtkit (Flask アプリ) の起動・停止とブラウザ表示."""
import logging
import os
import socket
import subprocess
import sys
import time
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


def launch_app(instance_dir, port, channel='stable', python=None,
               config=None):
    """インストール済み mgtkit を起動しブラウザを開く.

    instance_dir は stable/ や beta/<version>/ のインスタンスフォルダ
    (アプリ本体はその中の mgtkit/)。既に同じポートで起動済みの場合は
    新規起動せずブラウザだけ開く。
    戻り値: (subprocess.Popen | None, url)
    """
    app_py = os.path.join(app_dir(instance_dir), 'app.py')
    if not os.path.isfile(app_py):
        raise LaunchError('アプリがまだ取得されていません。'
                          'もう一度「起動」を押すと自動で取得されます。')
    url = app_url(port)
    if port_in_use(port):
        log.info('port %d は使用中。既存の画面を開きます', port)
        webbrowser.open(url)
        return None, url

    env = dict(os.environ)
    env['MGTKIT_PORT'] = str(port)
    env['MGTKIT_CHANNEL'] = channel
    env['MGTKIT_UPLOAD_DIR'] = upload_tmp_dir(instance_dir, channel, config)
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


def _query_kwargs():
    kw = {'capture_output': True, 'text': True, 'encoding': 'utf-8',
          'errors': 'replace', 'timeout': 15}
    if sys.platform == 'win32':
        kw['creationflags'] = 0x08000000  # CREATE_NO_WINDOW
    return kw


def _pids_listening(port):
    """port を LISTEN しているプロセスの PID 一覧 (特定できなければ空)."""
    pids = set()
    try:
        if sys.platform == 'win32':
            out = subprocess.run(['netstat', '-ano', '-p', 'TCP'],
                                 **_query_kwargs()).stdout or ''
            suffix = ':%d' % port
            for line in out.splitlines():
                parts = line.split()
                if (len(parts) >= 5 and parts[0] == 'TCP'
                        and parts[1].endswith(suffix)
                        and parts[3] == 'LISTENING'):
                    pids.add(int(parts[4]))
        else:
            out = subprocess.run(['lsof', '-t', '-iTCP:%d' % port,
                                  '-sTCP:LISTEN'],
                                 **_query_kwargs()).stdout or ''
            pids.update(int(p) for p in out.split())
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        log.warning('port %d の使用プロセスを特定できませんでした: %s',
                    port, e)
    return sorted(pids)


def _process_name(pid):
    """PID のプログラム名 (小文字)。取得できなければ ''."""
    try:
        if sys.platform == 'win32':
            out = subprocess.run(
                ['tasklist', '/FI', 'PID eq %d' % pid, '/FO', 'CSV', '/NH'],
                **_query_kwargs()).stdout or ''
            for line in out.splitlines():
                if line.startswith('"'):
                    return line.split('","')[0].strip('"').lower()
        else:
            out = subprocess.run(['ps', '-p', str(pid), '-o', 'comm='],
                                 **_query_kwargs()).stdout or ''
            return out.strip().lower()
    except (OSError, subprocess.SubprocessError):
        pass
    return ''


def _terminate(pid):
    """プロセスを終了させる (Windows は子プロセスごと)."""
    if sys.platform == 'win32':
        subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                       **_query_kwargs())
    else:
        import signal
        os.kill(pid, signal.SIGTERM)


def stop_app(port, timeout=10.0):
    """port で動作中の mgtkit (python) を終了し、ポートが空くまで待つ.

    更新でアプリのフォルダを置き換える前に呼ぶ。アプリが起動したままだと
    Windows ではフォルダが「使用中」になり置き換えられないため。
    戻り値: 終了させたら True / もともと動いていなければ False。
    port を python 以外のプログラムが使っている場合は誤って終了させず、
    ポートが空かなければ LaunchError にする。
    """
    if not port_in_use(port):
        return False
    stopped = False
    for pid in _pids_listening(port):
        name = _process_name(pid)
        if name and 'python' not in name:
            log.warning('port %d は %s (PID %d) が使用中のため終了しません',
                        port, name, pid)
            continue
        log.info('起動中のアプリ (PID %d) を終了します', pid)
        try:
            _terminate(pid)
            stopped = True
        except OSError as e:
            log.warning('PID %d を終了できませんでした: %s', pid, e)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not port_in_use(port):
            return stopped
        time.sleep(0.2)
    raise LaunchError('起動中のアプリを終了できませんでした。'
                      'パソコンを再起動してからもう一度お試しください。')
