# -*- coding: utf-8 -*-
"""git コマンドのサブプロセスラッパー (作業用クローンの管理込み).

方針は ghcli.py と同じ: 失敗時は stderr をログに残し、ユーザーには
平易な日本語メッセージを見せる。Git 用語は極力出さない。
"""
import logging
import os
import subprocess
import sys

log = logging.getLogger(__name__)


class GitError(Exception):
    """git 実行失敗。str() はユーザー向けの平易な日本語メッセージ."""


def _popen_kwargs():
    kw = {}
    if sys.platform == 'win32':
        kw['creationflags'] = 0x08000000  # CREATE_NO_WINDOW
    return kw


def run_git(args, cwd=None, timeout=120):
    """git を実行し stdout を返す。失敗は GitError (詳細はログへ).

    core.quotepath=off で日本語ファイル名をそのまま出力させる
    (既定ではエスケープ表記になり、パスの照合や再利用が壊れる)。
    """
    cmd = ['git', '-c', 'core.quotepath=off'] + list(args)
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=timeout, **_popen_kwargs())
    except FileNotFoundError:
        raise GitError('git コマンドが見つかりません。セットアップ (setup.bat) を'
                       '実行するか、https://git-scm.com/ から導入してください。')
    except subprocess.TimeoutExpired:
        raise GitError('処理がタイムアウトしました。'
                       'ネットワーク接続を確認してください。')
    if proc.returncode != 0:
        log.error('git %s failed (%d): %s', args, proc.returncode,
                  proc.stderr)
        raise GitError(_friendly_message(proc.stderr))
    return proc.stdout


def _friendly_message(stderr):
    s = (stderr or '').lower()
    if 'authentication' in s or 'could not read username' in s or \
            'permission denied' in s:
        return ('GitHub へのアクセス権がありません。'
                '「gh auth login」でログインしてください。')
    if 'could not resolve' in s or 'unable to access' in s:
        return 'ネットワークに接続できません。接続環境を確認してください。'
    return '処理中にエラーが発生しました。時間をおいて再試行してください。'


def ensure_work_repo(repo_slug, workrepo_dir):
    """提出処理用の作業クローンを用意し、最新化して返す.

    ユーザーの見えない場所 (<install_root>/workrepo) に clone を保持する。
    認証は gh auth setup-git 済みの資格情報、または既存の git 資格情報に従う。
    """
    if not os.path.isdir(os.path.join(workrepo_dir, '.git')):
        os.makedirs(os.path.dirname(workrepo_dir) or '.', exist_ok=True)
        run_git(['clone', 'https://github.com/%s.git' % repo_slug,
                 workrepo_dir], timeout=600)
    run_git(['fetch', 'origin', '--prune'], cwd=workrepo_dir, timeout=300)
    return workrepo_dir
