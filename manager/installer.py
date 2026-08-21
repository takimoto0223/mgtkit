# -*- coding: utf-8 -*-
"""配布 ZIP の展開と依存インストール."""
import errno
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

from . import safeio

log = logging.getLogger(__name__)


class InstallError(Exception):
    """展開・インストール失敗。str() はユーザー向けの平易な日本語メッセージ."""


SWAP_PREFIX = 'mgtkit_swap_'


def _same_drive(a, b):
    return (os.path.splitdrive(os.path.abspath(a))[0].lower()
            == os.path.splitdrive(os.path.abspath(b))[0].lower())


def sweep_swap_leftovers(*dirs):
    """前回の入れ替えが途中で終わったときの残骸を片付ける (消した数を返す).

    ``_replace_dir`` は finally で作業フォルダを消すが、強制終了や電源断で
    そこまで届かないことがある。また入れ替え前のフォルダが使用中だと
    ``old`` が残る。放っておくと更新のたびに溜まるので、起動時に掃く。
    """
    removed = 0
    for target in dirs:
        try:
            names = sorted(os.listdir(target))
        except OSError:
            continue                    # まだ無い置き場は素通り
        for name in names:
            if not name.startswith(SWAP_PREFIX):
                continue
            path = os.path.join(target, name)
            if os.path.isdir(path) and safeio.rmtree(path):
                removed += 1
                log.info('前回の入れ替えの残骸を片付けました: %s', path)
    return removed


# 入れ替えが失敗する原因は「使用中」だけではない。原因を名指しする文言は
# errno で分かる分だけにする (分からないものに「再起動してください」と
# 書くと、直らない操作を延々させることになる)
_ENOSPC = (errno.ENOSPC, errno.EDQUOT) if hasattr(errno, 'EDQUOT') else (
    errno.ENOSPC,)
_EBUSY = (errno.EACCES, errno.EPERM, errno.EBUSY, errno.ENOTEMPTY,
          errno.EEXIST)


def _replace_message(exc):
    """入れ替え失敗の原因ごとの案内 (分からないときは決め打ちしない)."""
    code = getattr(exc, 'winerror', None) or exc.errno
    if exc.errno in _ENOSPC:
        return ('パソコンの空き容量が足りないため、新しい版に'
                '置き換えられませんでした。空き容量を増やしてから'
                'お試しください。')
    if exc.errno == errno.ENOENT:
        return ('アプリの置き場所が見つからないため、新しい版に'
                '置き換えられませんでした。マネージャーを開き直して'
                'お試しください。')
    if exc.errno in _EBUSY:
        return ('アプリのフォルダが使用中のため、新しい版に'
                '置き換えられませんでした。アプリの画面を'
                '閉じて再試行し、直らない場合はパソコンを'
                '再起動してからお試しください。')
    return ('新しい版に置き換えられませんでした (%s)。'
            'もう一度お試しください。直らない場合は、'
            'ログ (manager.log) を管理者にお知らせください。' % code)


def _replace_dir(src, install_dir, work_parent=None):
    """src の内容で install_dir を置き換える (失敗しても元の内容を残す).

    実行中のアプリに使われているフォルダを直接消すと、Windows では途中まで
    消えた壊れた状態で止まる。そこで同じドライブ上に新しい内容を先に用意し、
    フォルダ名の付け替えだけで入れ替える。付け替えできない場合は元のまま
    InstallError にする。

    work_parent: 作業フォルダを作る場所。既定は install_dir の親だが、
    そこが**利用者に見えるフォルダ**のときは更新のたびに mgtkit_swap_XXXX
    が一瞬見え、失敗すると残骸が残る。呼び出し側が隠し場所を渡せるように
    してある。**付け替え (os.rename) は同じドライブ内でしかできない**ので、
    別ドライブを渡されたときは既定に戻す。
    """
    parent = os.path.dirname(install_dir) or '.'
    # 置き場所そのものを先に作る (初回のβ版などまだ無いことがある)。
    # 作業フォルダを別の場所に作るときも、ここは必ず要る (付け替え先の
    # 親が無いと os.rename が失敗する)
    os.makedirs(parent, exist_ok=True)
    if work_parent and _same_drive(work_parent, install_dir):
        parent = work_parent
        os.makedirs(parent, exist_ok=True)
    work = tempfile.mkdtemp(prefix=SWAP_PREFIX, dir=parent)
    staging = os.path.join(work, 'new')
    backup = os.path.join(work, 'old')
    try:
        shutil.copytree(src, staging)
        try:
            if os.path.isdir(install_dir):
                os.rename(install_dir, backup)
            os.rename(staging, install_dir)
        except OSError as e:
            log.exception('フォルダの入れ替えに失敗しました: %s', install_dir)
            if not os.path.isdir(install_dir) and os.path.isdir(backup):
                try:
                    os.rename(backup, install_dir)
                except OSError:
                    pass
            raise InstallError(_replace_message(e)) from e
    finally:
        safeio.rmtree(work)   # 中身は入れ替え前のフォルダ (読み取り専用あり)


def extract_zip(zip_path, install_dir, work_parent=None):
    """ZIP を install_dir へ展開する (既存の中身は置き換え).

    GitHub のソースアーカイブのように「単一のトップフォルダ」に包まれて
    いる場合は 1 階層むいて展開する。work_parent は _replace_dir へ渡す。
    """
    tmp = tempfile.mkdtemp(prefix='mgtkit_extract_')
    try:
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(tmp)
        except zipfile.BadZipFile as e:
            log.exception('ZIP が壊れています: %s', zip_path)
            raise InstallError('ZIP ファイルが壊れているため開けません'
                               'でした。作り直してからお試しください。') from e
        except OSError as e:
            # 空き容量・パスの長さ・ドライブが外れた等。やり直しても
            # 直らないものが多いので「再度お試しください」とは言わない
            log.exception('ZIP を展開できませんでした: %s', zip_path)
            raise InstallError(
                'ZIP を展開できませんでした (%s)。パソコンの空き容量と、'
                'ファイルの置き場所 (フォルダ名が長すぎないか) を'
                '確認してください。'
                % (getattr(e, 'winerror', None) or e.errno)) from e
        entries = [e for e in os.listdir(tmp) if e != '__MACOSX']
        if len(entries) == 1 and os.path.isdir(os.path.join(tmp, entries[0])):
            src = os.path.join(tmp, entries[0])
        else:
            src = tmp
        _replace_dir(src, install_dir, work_parent)
    finally:
        safeio.rmtree(tmp)    # ZIP の中身 (クローンが混ざっていることも)
    return install_dir


# pip の失敗は原因がいろいろある (通信・空き容量・一覧が読めない…)。
# 「ネットワーク接続を確認して」一択の案内は、別の原因のときに利用者へ
# 直らない操作をさせるので、出力から分かる分だけ原因を名指しする
# (installer._replace_message や ghcli._friendly_message と同じ方針)
_PIP_NETWORK_MARKS = (
    'connectionerror', 'newconnectionerror', 'proxyerror', 'sslerror',
    'connection refused', 'connection reset', 'could not resolve',
    'name resolution', 'network is unreachable', 'timed out',
)


def _pip_message(output):
    """pip の出力から原因ごとの案内 (分からないときは決め打ちしない)."""
    low = (output or '').lower()
    if 'unicodedecodeerror' in low:
        # requirements.txt が想定の文字コード (UTF-8) で読めなかった。
        # 利用者側では直せないので、再試行はさせず管理者へつなぐ
        return ('必要ライブラリの一覧 (requirements.txt) を読み取れません'
                'でした。ログ (manager.log) を管理者にお知らせください。')
    if 'no space left' in low or 'disk quota' in low:
        return ('パソコンの空き容量が足りないため、必要ライブラリを'
                'インストールできませんでした。空き容量を増やしてから'
                'お試しください。')
    if any(mark in low for mark in _PIP_NETWORK_MARKS):
        return ('必要ライブラリのインストールに失敗しました。'
                'ネットワーク接続を確認して再試行してください。'
                '直らない場合は、ログ (manager.log) を管理者に'
                'お知らせください。')
    return ('必要ライブラリのインストールに失敗しました。'
            'もう一度お試しください。直らない場合は、ログ (manager.log) '
            'を管理者にお知らせください。')


def install_requirements(install_dir, python=None):
    """requirements.txt があれば pip install する."""
    req = os.path.join(install_dir, 'requirements.txt')
    if not os.path.isfile(req):
        log.warning('requirements.txt が見つかりません: %s', install_dir)
        return False
    python = python or sys.executable
    # pip は BOM の無い requirements.txt を OS の既定の文字コード (日本語
    # Windows では cp932) で読むため、UTF-8 の日本語コメントが入っていると
    # UnicodeDecodeError で pip ごと失敗する (25.1 以降の pip は UTF-8 既定
    # だが、メンバーの PC の pip の版には頼れない)。PYTHONUTF8=1 で pip 側
    # の既定を UTF-8 に固定する (配布物の requirements.txt は UTF-8 前提)
    env = dict(os.environ, PYTHONUTF8='1')
    proc = subprocess.run(
        [python, '-m', 'pip', 'install', '-r', req],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        env=env)
    if proc.returncode != 0:
        log.error('pip install failed: %s\n%s', proc.stderr, proc.stdout)
        raise InstallError(_pip_message('%s\n%s' % (proc.stderr or '',
                                                    proc.stdout or '')))
    return True
