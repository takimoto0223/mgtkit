# -*- coding: utf-8 -*-
"""配布 ZIP の展開と依存インストール."""
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
        except OSError:
            log.exception('フォルダの入れ替えに失敗しました: %s', install_dir)
            if not os.path.isdir(install_dir) and os.path.isdir(backup):
                try:
                    os.rename(backup, install_dir)
                except OSError:
                    pass
            raise InstallError('アプリのフォルダが使用中のため、新しい版に'
                               '置き換えられませんでした。アプリの画面を'
                               '閉じて再試行し、直らない場合はパソコンを'
                               '再起動してからお試しください。')
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
        except (zipfile.BadZipFile, OSError):
            raise InstallError('取得した ZIP を展開できませんでした。'
                               '再度お試しください。')
        entries = [e for e in os.listdir(tmp) if e != '__MACOSX']
        if len(entries) == 1 and os.path.isdir(os.path.join(tmp, entries[0])):
            src = os.path.join(tmp, entries[0])
        else:
            src = tmp
        _replace_dir(src, install_dir, work_parent)
    finally:
        safeio.rmtree(tmp)    # ZIP の中身 (クローンが混ざっていることも)
    return install_dir


def install_requirements(install_dir, python=None):
    """requirements.txt があれば pip install する."""
    req = os.path.join(install_dir, 'requirements.txt')
    if not os.path.isfile(req):
        log.warning('requirements.txt が見つかりません: %s', install_dir)
        return False
    python = python or sys.executable
    proc = subprocess.run(
        [python, '-m', 'pip', 'install', '-r', req],
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    if proc.returncode != 0:
        log.error('pip install failed: %s', proc.stderr)
        raise InstallError('必要ライブラリのインストールに失敗しました。'
                           'ネットワーク接続を確認して再試行してください。')
    return True
