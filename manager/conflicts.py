# -*- coding: utf-8 -*-
"""衝突解決フロー (spec 2.2 タブ4 の衝突時処理、UI 非依存).

基点より main が先行し、同一箇所が変更されていた場合:
  1. マネージャーが merge を試行し、衝突を検出
  2. 衝突箇所を Claude が「機能レベルの説明」に翻訳
  3. ユーザーが方針を選択 (両方残す[推奨] / 自分優先 / 最新版優先)
  4. 方針を指示として統合を実行 → commit → push (PR が更新される)

ラジオボタンは Git を直接操作するものではなく Claude への指示書 (spec)。
ただし「自分優先」「最新版優先」は機械的に確定できるため git で直接解決し、
「両方残す」のみ Claude によるコードレベル統合を行う。
"""
import logging
import os
import subprocess
import sys

from . import claude_helper, ghcli, paths
from .autofix import _PROTECTED_PREFIXES
from .gitcli import GitError, ensure_work_repo, run_git
from .submit import workrepo_dir

log = logging.getLogger(__name__)

POLICIES = {
    'both': '両方の機能を残す (提出者の変更と最新版の変更を矛盾なく共存させる)',
    'ours': '提出者の機能を優先する (最新版側の該当部分を破棄する)',
    'theirs': '最新版を優先する (提出者の該当部分を破棄する)',
}


class ConflictError(Exception):
    """衝突処理の中断。str() はユーザー向けの平易な日本語メッセージ."""


def _git_raw(args, cwd):
    kw = {}
    if sys.platform == 'win32':
        kw['creationflags'] = 0x08000000
    return subprocess.run(['git'] + list(args), cwd=cwd, capture_output=True,
                          text=True, encoding='utf-8', errors='replace',
                          **kw)


def _start_merge(workrepo, branch, base):
    """branch に origin/base の merge を試行する.

    戻り値: 衝突ファイルの相対パスリスト (空 = 衝突なしで取り込み完了、
    コミットは未実施)。
    """
    run_git(['fetch', 'origin', branch, base], cwd=workrepo, timeout=300)
    run_git(['checkout', '-B', branch, 'origin/%s' % branch], cwd=workrepo)
    proc = _git_raw(['merge', '--no-commit', '--no-ff',
                     'origin/%s' % base], cwd=workrepo)
    conflicted = [p for p in run_git(
        ['diff', '--name-only', '--diff-filter=U'],
        cwd=workrepo).splitlines() if p.strip()]
    if proc.returncode != 0 and not conflicted:
        _git_raw(['merge', '--abort'], cwd=workrepo)
        log.error('merge failed: %s', proc.stderr)
        raise ConflictError('最新版の取り込みに失敗しました。'
                            '時間をおいて再試行してください。')
    return conflicted


def _read_conflicts(workrepo, conflicted):
    files = {}
    for rel in conflicted:
        full = os.path.join(workrepo, rel.replace('/', os.sep))
        try:
            with open(full, encoding='utf-8', errors='replace') as f:
                files[rel] = f.read()
        except OSError:
            files[rel] = '(読み取り不可)'
    return files


def analyze(branch, config=None, on_progress=None):
    """衝突を検出し、機能レベルの説明を返す.

    戻り値: dict(conflicted=[paths], explanation, merged_clean=bool)
    merged_clean=True は衝突なしで取り込めた状態 (resolve('clean') で確定)。
    merge は中断状態のまま返す (resolve か abort を呼ぶこと)。
    """
    def progress(msg):
        log.info('%s', msg)
        if on_progress:
            on_progress(msg)

    base = (config or {}).get('base_branch', 'main')
    workrepo = ensure_work_repo(paths.repo_slug(config),
                                workrepo_dir(config))
    progress('最新版との違いを調べています...')
    conflicted = _start_merge(workrepo, branch, base)
    if not conflicted:
        return {'workrepo': workrepo, 'branch': branch, 'conflicted': [],
                'explanation': '最新版とぶつかる変更はありませんでした。'
                               'そのまま取り込めます。',
                'merged_clean': True}
    progress('ぶつかっている箇所を分析しています...')
    files = _read_conflicts(workrepo, conflicted)
    explanation = claude_helper.generate_conflict_explanation(files)
    return {'workrepo': workrepo, 'branch': branch, 'conflicted': conflicted,
            'explanation': explanation, 'merged_clean': False}


def abort(analysis):
    """merge を取り消して元の状態に戻す."""
    _git_raw(['merge', '--abort'], cwd=analysis['workrepo'])


def resolve(analysis, policy, config=None, on_progress=None):
    """選択された方針で統合を確定し push する.

    policy: 'both' | 'ours' | 'theirs' (merged_clean の場合は無視)
    戻り値: 統合内容の説明。
    """
    def progress(msg):
        log.info('%s', msg)
        if on_progress:
            on_progress(msg)

    workrepo = analysis['workrepo']
    branch = analysis['branch']
    conflicted = analysis['conflicted']
    base = (config or {}).get('base_branch', 'main')

    summary = '最新版を取り込みました。'
    try:
        if conflicted:
            if policy == 'ours':
                for rel in conflicted:
                    run_git(['checkout', '--ours', '--', rel], cwd=workrepo)
                    run_git(['add', '--', rel], cwd=workrepo)
                summary = ('あなたの変更を優先して最新版を取り込みました '
                           '(該当箇所の最新版側の変更は破棄)。')
            elif policy == 'theirs':
                for rel in conflicted:
                    run_git(['checkout', '--theirs', '--', rel],
                            cwd=workrepo)
                    run_git(['add', '--', rel], cwd=workrepo)
                summary = ('最新版を優先して取り込みました '
                           '(該当箇所のあなたの変更は破棄)。')
            elif policy == 'both':
                progress('両方の機能を残す形で統合しています...')
                files = _read_conflicts(workrepo, conflicted)
                merged = claude_helper.generate_merge(
                    files, POLICIES['both'])
                if not merged or not merged.get('files'):
                    raise ConflictError(
                        '自動統合を実行できませんでした。API キーの登録と'
                        'ネットワーク接続を確認するか、別の方針を選んで'
                        'ください。')
                allowed = set(conflicted)
                for item in merged['files']:
                    rel = str(item.get('path', '')).replace('\\', '/')
                    if rel not in allowed or any(
                            rel.startswith(p) for p in _PROTECTED_PREFIXES):
                        raise ConflictError('統合結果に想定外のファイルが'
                                            '含まれたため中断しました。')
                    if '<<<<<<<' in item.get('content', ''):
                        raise ConflictError('統合結果に未解決の箇所が残った'
                                            'ため中断しました。')
                    full = os.path.join(workrepo, rel.replace('/', os.sep))
                    with open(full, 'w', encoding='utf-8', newline='') as f:
                        f.write(item['content'])
                    run_git(['add', '--', rel], cwd=workrepo)
                summary = merged.get('summary') or \
                    '両方の機能を残して統合しました。'
            else:
                raise ConflictError('統合方針が選択されていません。')

        progress('統合結果を記録しています...')
        user = ghcli.run_gh(['api', 'user', '--jq', '.login']).strip()
        run_git(['-c', 'user.name=%s' % user,
                 '-c', 'user.email=%s@users.noreply.github.com' % user,
                 'commit', '--no-edit', '-m',
                 '最新版 (%s) を取り込み: %s' % (base,
                                                 summary.splitlines()[0])],
                cwd=workrepo)
        run_git(['push', 'origin', branch], cwd=workrepo, timeout=300)
        return summary
    except (GitError, ghcli.GhError):
        _git_raw(['merge', '--abort'], cwd=workrepo)
        raise
    except ConflictError:
        _git_raw(['merge', '--abort'], cwd=workrepo)
        raise
