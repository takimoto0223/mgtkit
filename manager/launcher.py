# -*- coding: utf-8 -*-
"""mgtkit (Flask アプリ) の起動・停止とブラウザ表示."""
import json
import logging
import os
import socket
import subprocess
import sys
import time
import webbrowser

from . import paths, safeio
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


# アプリが画面を出せるようになるまで待つ上限 (秒)。ここを過ぎても
# 生きていれば、遅い PC で読み込みに時間がかかっているものとして扱う
# (落ちるときは数秒以内に落ちるため、待つのはその見極めのため)
STARTUP_TIMEOUT = 20.0


def app_log_path(channel='stable', config=None):
    """アプリ自身の出力を書き出す先 (起動できなかった理由が残る).

    親から見えないところで落ちると原因が分からないため、子の出力を
    捨てずにファイルへ回す。**起動のたびに書き直す** (直前の 1 回分が
    あれば足り、放っておくと際限なく育つため)。
    """
    return os.path.join(paths.install_root(config),
                        'app-%s.log' % (channel or 'stable'))


def _open_app_log(channel, config):
    """子の出力先を開く。開けなければ None (起動そのものは止めない)."""
    path = app_log_path(channel, config)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return open(path, 'w', encoding='utf-8', errors='replace'), path
    except OSError:
        log.warning('アプリの出力を記録できません: %s', path, exc_info=True)
        return None, path


def _wait_until_serving(proc, port, timeout):
    """画面を出せる状態になるまで待つ。なったら True.

    途中でアプリが終了したら、待たずに False を返す。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_in_use(port):
            return True
        if proc.poll() is not None:
            return False                # 起動直後に落ちた
        time.sleep(0.2)
    return False


def _startup_failure(log_path, returncode):
    """起動直後に落ちたときの案内 (理由が読めた分だけ名指しする)."""
    tail = ''
    try:
        with open(log_path, encoding='utf-8', errors='replace') as f:
            tail = f.read()[-4000:]
    except OSError:
        pass
    log.error('アプリが起動直後に終了しました (終了コード %s)\n%s',
              returncode, tail)
    if 'ModuleNotFoundError' in tail or 'ImportError' in tail:
        # 取り込みが途中で終わった PC。版の印は付いていないので、
        # もう一度押せば取り込み直される
        return ('アプリの動作に必要なものが足りないため起動できません'
                'でした。もう一度「起動」を押すと取り込み直します。')
    return ('アプリを起動できませんでした。繰り返す場合は、ログ '
            '(%s) を管理者にお知らせください。' % os.path.basename(log_path))


def launch_app(instance_dir, port, channel='stable', python=None,
               config=None, startup_timeout=None):
    """インストール済み mgtkit を起動しブラウザを開く.

    instance_dir は stable/ や beta/<version>/ のインスタンスフォルダ
    (アプリ本体はその中の mgtkit/)。既に同じポートで起動済みの場合は
    新規起動せずブラウザだけ開く。

    **起動したことを見届けてから返す**。プロセスを作っただけで「開き
    ます」と言うと、アプリが起動直後に落ちても画面には成功と出て、
    ブラウザは開かないまま利用者が待ち続けることになる。

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
    out, log_path = _open_app_log(channel, config)
    try:
        proc = subprocess.Popen(
            [python or sys.executable, app_py],
            cwd=app_dir(instance_dir), env=env,
            stdin=subprocess.DEVNULL,
            stdout=out or subprocess.DEVNULL,
            stderr=subprocess.STDOUT if out else subprocess.DEVNULL, **kw)
    except OSError as e:
        log.error('launch failed: %s', e)
        raise LaunchError('アプリの起動に失敗しました。'
                          'Python の導入状態を確認してください。')
    finally:
        if out is not None:
            out.close()     # 子が自分の複製を持つので親は閉じてよい

    timeout = STARTUP_TIMEOUT if startup_timeout is None else startup_timeout
    if not _wait_until_serving(proc, port, timeout):
        if proc.poll() is not None:
            raise LaunchError(_startup_failure(log_path, proc.returncode))
        # 生きてはいる (遅い PC での読み込み中)。ここで失敗と言うと
        # 実際には開く画面を「開かない」と案内することになるため、
        # 記録だけ残して先へ進む
        log.warning('%s 秒たってもアプリが応答しません (起動は継続)',
                    timeout)
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


# --- いま動かしているβ版 -------------------------------------------------
#
# β版は版ごとに別フォルダだが、**ポートは 1 つ**しかない。画面にも版名は
# 出ないため、どの版を動かしているかはマネージャーしか知らない。控えて
# おかないと ``stop_other_beta`` が「今回の版かどうか」を判断できない。

RUNNING_BETA_NAME = 'running.json'


def _running_beta_path(config=None):
    return os.path.join(paths.beta_root(config), RUNNING_BETA_NAME)


def running_beta(config=None):
    """いまβ版ポートで動かしている版名。分からなければ None.

    分からないときは None を返し、呼び出し側は「別の版かもしれない」
    ものとして扱う (止めてから起動し直す方が安全なため)。
    """
    try:
        with open(_running_beta_path(config), encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    tag = data.get('tag') if isinstance(data, dict) else None
    return str(tag) if tag else None


def remember_beta(tag, config=None):
    """動かしている版を控える (tag=None で忘れる).

    控えられなくても起動そのものは成功しているので、例外にはしない
    (次に試すときへの影響は「別の版とみなして止め直す」だけ)。
    """
    path = _running_beta_path(config)
    try:
        if tag is None:
            if os.path.exists(path):
                os.remove(path)
        else:
            safeio.write_json(path, {'tag': str(tag)}, indent=2)
    except OSError:
        log.info('動かしているβ版を控えられませんでした: %s', path,
                 exc_info=True)


def stop_other_beta(tag, port, config=None):
    """β版ポートで動いているのが tag 以外なら終了する (止めたら True).

    取得済みの版は入れ替えが要らないので、以前はここを素通りしていた。
    しかし別のβ版が動いたままだとポートが塞がっており、``launch_app``
    は新しく起動せず**前の版の画面**を開く。画面に版名が出ないため、
    利用者は違う版を見たまま気づかず、フィードバックも見ていない版の
    提出に付いてしまう。

    どの版が動いているか分からないときも止める (取り違えるより安全)。
    止められないときは ``stop_app`` が LaunchError を投げる。
    """
    if not port_in_use(port):
        return False
    if running_beta(config) == str(tag):
        return False            # 同じ版が動いている (開き直すだけでよい)
    stopped = stop_app(port)
    remember_beta(None, config)
    return stopped
