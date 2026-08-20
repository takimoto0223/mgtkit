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


def _replace_dir(src, install_dir):
    """src の内容で install_dir を置き換える (失敗しても元の内容を残す).

    実行中のアプリに使われているフォルダを直接消すと、Windows では途中まで
    消えた壊れた状態で止まる。そこで同じドライブ上に新しい内容を先に用意し、
    フォルダ名の付け替えだけで入れ替える。付け替えできない場合は元のまま
    InstallError にする。
    """
    parent = os.path.dirname(install_dir) or '.'
    os.makedirs(parent, exist_ok=True)
    work = tempfile.mkdtemp(prefix='mgtkit_swap_', dir=parent)
    staging = os.path.join(work, 'new')
    backup = os.path.join(work, 'old')
    try:
        shutil.copytree(src, staging)
        try:
            if os.path.isdir(install_dir):
                os.rename(install_dir, backup)
            os.rename(staging, install_dir)
        except OSError:
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


def extract_zip(zip_path, install_dir):
    """ZIP を install_dir へ展開する (既存の中身は置き換え).

    GitHub のソースアーカイブのように「単一のトップフォルダ」に包まれて
    いる場合は 1 階層むいて展開する。
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
        _replace_dir(src, install_dir)
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
