# -*- coding: utf-8 -*-
"""git コマンドのサブプロセスラッパー (作業用クローンの管理込み).

方針は ghcli.py と同じ: 失敗時は stderr をログに残し、ユーザーには
平易な日本語メッセージを見せる。Git 用語は極力出さない。
"""
import logging
import os
import subprocess
import sys
import threading

from . import safeio

log = logging.getLogger(__name__)

# 作業クローンを壊す・作り直す処理を同時に走らせないための錠。
# 読むだけの git 操作 (差分表示など) はこの錠を取らない
_REPAIR_LOCK = threading.Lock()


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


def _clone_work_repo(repo_slug, workrepo_dir):
    os.makedirs(os.path.dirname(workrepo_dir) or '.', exist_ok=True)
    run_git(['clone', 'https://github.com/%s.git' % repo_slug,
             workrepo_dir], timeout=600)       # クローン直後は最新


def _repo_is_usable(workrepo_dir):
    """作業クローンの .git が使える状態かを読むだけで確かめる.

    取得中に強制終了された残骸などで HEAD が引けない場合は使えない。
    (作業ツリーの乱れはここでは見ない — それは書き込みを始める側が
    ``reset_work_tree`` で直す。)
    """
    try:
        run_git(['rev-parse', '--verify', 'HEAD^{commit}'],
                cwd=workrepo_dir)
        return True
    except GitError:
        return False


def ensure_work_repo(repo_slug, workrepo_dir, fetch=True):
    """提出処理用の作業クローンを用意し、最新化して返す.

    ユーザーの見えない場所 (<install_root>/workrepo) に clone を保持する。
    認証は gh auth setup-git 済みの資格情報、または既存の git 資格情報に従う。
    fetch=False は「クローンの用意だけして最新化しない」(呼び出し側が
    必要なものだけを fetch する高速経路。差分表示が使う)。

    .git が壊れている (HEAD が引けない) クローンは**残骸**とみなして
    作り直す。取得が途中で強制終了されると壊れたまま残り、そのままでは
    以後の提出・統合が「時間をおいて再試行してください」で**永久に**
    失敗するため (時間をおいても直らない)。
    """
    if not os.path.isdir(os.path.join(workrepo_dir, '.git')):
        with _REPAIR_LOCK:
            # 待っている間に別スレッドが作った場合は作り直さない
            if not os.path.isdir(os.path.join(workrepo_dir, '.git')):
                _clone_work_repo(repo_slug, workrepo_dir)
        return workrepo_dir
    if not _repo_is_usable(workrepo_dir):
        with _REPAIR_LOCK:
            if not _repo_is_usable(workrepo_dir):
                log.warning('作業用の置き場が壊れているため作り直します: %s',
                            workrepo_dir)
                if not safeio.rmtree(workrepo_dir):
                    raise GitError('作業用の置き場を作り直せませんでした '
                                   '(場所: %s)。マネージャーを閉じて再度'
                                   'お試しください。' % workrepo_dir)
                _clone_work_repo(repo_slug, workrepo_dir)
        return workrepo_dir                # クローン直後なので最新
    if fetch:
        run_git(['fetch', 'origin', '--prune'], cwd=workrepo_dir,
                timeout=300)
    return workrepo_dir


def reset_work_tree(workrepo_dir):
    """作業クローンを既知のまっさらな状態に戻す (書き込みの開始時に呼ぶ).

    取得や提出がチェックアウトの途中で強制終了されると、``.git`` は
    無事でも作業ツリーが半端なまま残る (``index.lock`` + 未追跡扱いの
    ファイル)。この状態では ``checkout -B`` が拒否され、提出の確定と
    統合が何度やり直しても同じ文言で失敗する。workrepo はマネージャー
    専用の作業場所で利用者の成果物は入らないため、使う前に遠慮なく
    戻してよい。

    呼ぶのは**作業ツリーに書き込む流れの入口だけ** (提出の確定・統合・
    自動修正)。読むだけの流れ (差分表示) が途中で呼ぶと、進行中の
    書き込みを消してしまうため呼ばない。
    """
    if not os.path.isdir(workrepo_dir):
        raise GitError('作業用のデータが見つかりませんでした。'
                       'もう一度やり直してください。')
    with _REPAIR_LOCK:
        lock = os.path.join(workrepo_dir, '.git', 'index.lock')
        try:
            if os.path.exists(lock):
                # マネージャーの git は run_git で直列に完走するため、
                # 残っている lock は前回強制終了の置き土産
                os.remove(lock)
                log.info('前回の強制終了で残った index.lock を外しました')
        except OSError:
            log.warning('index.lock を外せませんでした', exc_info=True)
        run_git(['reset', '--hard'], cwd=workrepo_dir)
        run_git(['clean', '-fdx'], cwd=workrepo_dir)
    return workrepo_dir
