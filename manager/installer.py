# -*- coding: utf-8 -*-
"""配布 ZIP の展開と依存インストール."""
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

log = logging.getLogger(__name__)


class InstallError(Exception):
    """展開・インストール失敗。str() はユーザー向けの平易な日本語メッセージ."""


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
        if os.path.isdir(install_dir):
            shutil.rmtree(install_dir)
        os.makedirs(os.path.dirname(install_dir) or '.', exist_ok=True)
        shutil.copytree(src, install_dir)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
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
