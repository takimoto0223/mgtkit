# -*- coding: utf-8 -*-
"""検証 (CI) 失敗時の自動修正ループ (マネージャー側で実行).

管理者キーを GitHub に置かず「提出者本人の API キー」で修正を行うため、
自動修正は GitHub Actions ではなくマネージャー上で実行する:

    検証失敗 → 失敗ログ取得 → Claude が原因分析・修正 → [auto-fix] commit
    → push → 再検証 ... (最大 config の auto_fix_max_attempts 回)
    → それでも失敗なら平易な3行要約を PR コメントに投稿

ガード:
  1. tests/ 配下の変更・削除は禁止 (プロンプト + 適用時の二重チェック。
     さらに CI 側でも [auto-fix] commit の tests/ diff を検査)
  2. 修正 commit は [auto-fix] プレフィックス必須
  3. 修正 commit メッセージに「なぜ失敗していたか・何を変えたか」を含める
  4. 禁止パターン (bare except / テスト skip / アサーション緩和 / 仕様変更)
     はプロンプトで明示
"""
import json
import logging
import os
import re
import time

from . import claude_helper, ghcli, paths
from .gitcli import GitError, ensure_work_repo, run_git
from .submit import BLOCKED_EXTENSIONS, workrepo_dir

log = logging.getLogger(__name__)

AUTOFIX_PREFIX = '[auto-fix]'

# 自動修正が触ってよいのは提出対象と同じ範囲 + tests/ 禁止
_PROTECTED_PREFIXES = ('tests/', '.github/', 'scripts/', 'docs/', 'manager/')


class AutofixError(Exception):
    """自動修正の中断。str() はユーザー向けの平易な日本語メッセージ."""


def list_my_submissions(config=None):
    """自分が提出した open な PR の一覧 (検証状況付き)."""
    out = ghcli.run_gh([
        'pr', 'list', '--repo', paths.repo_slug(config),
        '--author', '@me', '--state', 'open',
        '--json', 'number,title,url,headRefName,statusCheckRollup'])
    try:
        prs = json.loads(out)
    except ValueError:
        raise AutofixError('提出一覧を取得できませんでした。')
    result = []
    for pr in prs:
        checks = pr.get('statusCheckRollup') or []
        status = _summarize_checks(checks)
        result.append({'number': pr['number'], 'title': pr['title'],
                       'url': pr['url'], 'branch': pr['headRefName'],
                       'status': status})
    return result


def _summarize_checks(checks):
    """statusCheckRollup を pending/success/failure に要約する."""
    if not checks:
        return 'pending'
    states = set()
    for c in checks:
        s = (c.get('conclusion') or c.get('status') or '').upper()
        states.add(s)
    if any(s in ('FAILURE', 'TIMED_OUT', 'CANCELLED') for s in states):
        return 'failure'
    if all(s in ('SUCCESS', 'NEUTRAL', 'SKIPPED') for s in states):
        return 'success'
    return 'pending'


def _pr_info(pr_number, config):
    out = ghcli.run_gh([
        'pr', 'view', str(pr_number), '--repo', paths.repo_slug(config),
        '--json', 'headRefName,statusCheckRollup,url,state'])
    try:
        return json.loads(out)
    except ValueError:
        raise AutofixError('提出内容の情報を取得できませんでした。')


def _failed_run_id(checks):
    for c in checks:
        if (c.get('conclusion') or '').upper() in ('FAILURE', 'TIMED_OUT'):
            m = re.search(r'/runs/(\d+)', c.get('detailsUrl') or '')
            if m:
                return m.group(1)
    return None


def _failure_log(run_id, config):
    try:
        return ghcli.run_gh(['run', 'view', run_id, '--repo',
                             paths.repo_slug(config), '--log-failed'],
                            timeout=120)
    except ghcli.GhError:
        return '(失敗ログを取得できませんでした)'


def _validate_fix_paths(fix):
    """ガード: 修正対象パスの検査 (tests/ 等の保護領域・実行ファイル禁止)."""
    for item in fix.get('files') or []:
        p = str(item.get('path', '')).replace('\\', '/').lstrip('/')
        if not p or '..' in p.split('/'):
            raise AutofixError('自動修正が不正なパスを返したため中断しました。')
        if any(p.startswith(pre) for pre in _PROTECTED_PREFIXES):
            raise AutofixError(
                '自動修正がテスト等の保護されたファイル (%s) を変更しようと'
                'したため中断しました。' % p)
        ext = os.path.splitext(p)[1].lower()
        if ext in BLOCKED_EXTENSIONS:
            raise AutofixError('自動修正が実行ファイルを生成しようとしたため'
                               '中断しました。')
        item['path'] = p


def attempt_fix(workrepo, branch, failure_log, config=None):
    """1回分の修正: Claude 生成 → ガード → [auto-fix] commit → push.

    戻り値: 修正の要約文字列。修正できない場合は AutofixError。
    """
    run_git(['fetch', 'origin', branch], cwd=workrepo, timeout=300)
    run_git(['checkout', '-B', branch, 'origin/%s' % branch], cwd=workrepo)

    base = (config or {}).get('base_branch', 'main')
    run_git(['fetch', 'origin', base], cwd=workrepo, timeout=300)
    changed = [p for p in run_git(
        ['diff', '--name-only', 'origin/%s...HEAD' % base],
        cwd=workrepo).splitlines() if p.strip()]
    files = {}
    for rel in changed:
        full = os.path.join(workrepo, rel.replace('/', os.sep))
        if os.path.isfile(full):
            try:
                with open(full, encoding='utf-8', errors='replace') as f:
                    files[rel] = f.read()
            except OSError:
                pass
    if not files:
        raise AutofixError('修正対象のファイルを特定できませんでした。')

    fix = claude_helper.generate_fix(failure_log, files)
    if not fix:
        raise AutofixError(
            '自動修正を実行できませんでした。API キーが登録済みか、'
            'ネットワーク接続を確認してください。')
    if not fix.get('files'):
        raise AutofixError('自動修正では原因を解決できませんでした: %s'
                           % fix.get('cause', '原因不明'))
    _validate_fix_paths(fix)

    for item in fix['files']:
        dst = os.path.join(workrepo, item['path'].replace('/', os.sep))
        os.makedirs(os.path.dirname(dst) or workrepo, exist_ok=True)
        with open(dst, 'w', encoding='utf-8', newline='') as f:
            f.write(item['content'])
    run_git(['add', '-A'], cwd=workrepo)
    if not run_git(['diff', '--cached', '--name-only'],
                   cwd=workrepo).strip():
        raise AutofixError('自動修正で変更点が生成されませんでした。')

    # ガード: 修正 commit に保護領域が混ざっていないか最終確認
    staged = run_git(['diff', '--cached', '--name-only'],
                     cwd=workrepo).splitlines()
    for p in staged:
        if any(p.startswith(pre) for pre in _PROTECTED_PREFIXES):
            run_git(['reset', '--hard', 'HEAD'], cwd=workrepo)
            raise AutofixError('自動修正が保護されたファイルを変更したため'
                               '取り消しました。')

    message = '%s %s\n\n原因: %s\n対応: %s' % (
        AUTOFIX_PREFIX, fix['summary'].splitlines()[0][:60],
        fix['cause'], fix['summary'])
    user = ghcli.run_gh(['api', 'user', '--jq', '.login']).strip()
    run_git(['-c', 'user.name=%s' % user,
             '-c', 'user.email=%s@users.noreply.github.com' % user,
             'commit', '-m', message], cwd=workrepo)
    run_git(['push', 'origin', branch], cwd=workrepo, timeout=300)
    return fix['summary']


def wait_for_checks(pr_number, config=None, timeout_s=1200, poll_s=30,
                    on_progress=None):
    """PR の検証完了を待つ。戻り値: 'success' | 'failure' | 'pending'."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        info = _pr_info(pr_number, config)
        status = _summarize_checks(info.get('statusCheckRollup') or [])
        if status != 'pending':
            return status
        if on_progress:
            on_progress('検証の完了を待っています...')
        time.sleep(poll_s)
    return 'pending'


def autofix_loop(pr_number, config=None, on_progress=None):
    """検証失敗 → 修正 → 再検証 を上限回数まで繰り返す.

    戻り値: dict(ok, attempts, message)
    """
    def progress(msg):
        log.info('%s', msg)
        if on_progress:
            on_progress(msg)

    max_attempts = int(((config or {}).get('manager') or {})
                       .get('auto_fix_max_attempts', 3))
    info = _pr_info(pr_number, config)
    branch = info['headRefName']
    workrepo = ensure_work_repo(paths.repo_slug(config),
                                workrepo_dir(config))

    last_log = ''
    for attempt in range(1, max_attempts + 1):
        info = _pr_info(pr_number, config)
        status = _summarize_checks(info.get('statusCheckRollup') or [])
        if status == 'success':
            return {'ok': True, 'attempts': attempt - 1,
                    'message': '検証を通過しました。'}
        if status == 'pending':
            status = wait_for_checks(pr_number, config,
                                     on_progress=on_progress)
            if status == 'success':
                return {'ok': True, 'attempts': attempt - 1,
                        'message': '検証を通過しました。'}
            if status == 'pending':
                return {'ok': False, 'attempts': attempt - 1,
                        'message': '検証が終わりません。時間をおいて再度'
                                   '確認してください。'}

        progress('検証失敗の内容を調べています... (%d/%d 回目)'
                 % (attempt, max_attempts))
        run_id = _failed_run_id(info.get('statusCheckRollup') or [])
        last_log = _failure_log(run_id, config) if run_id else '(ログなし)'

        progress('自動修正を生成しています... (%d/%d 回目)'
                 % (attempt, max_attempts))
        try:
            summary = attempt_fix(workrepo, branch, last_log, config)
        except (AutofixError, GitError, ghcli.GhError) as e:
            return {'ok': False, 'attempts': attempt,
                    'message': str(e)}
        progress('修正を送信しました: %s' % summary.splitlines()[0])

        status = wait_for_checks(pr_number, config, on_progress=on_progress)
        if status == 'success':
            return {'ok': True, 'attempts': attempt,
                    'message': '自動修正により検証を通過しました。'}

    # 上限到達: 平易な要約を PR コメントに投稿
    progress('自動修正の上限に達しました。要約を作成しています...')
    summary = claude_helper.generate_failure_summary(last_log)
    body = ('検証を通過できませんでした (自動修正 %d 回実施)。\n\n%s\n\n'
            '---\n_Generated by [Claude Code](https://claude.ai/code)_'
            % (max_attempts, summary))
    try:
        ghcli.run_gh(['pr', 'comment', str(pr_number), '--repo',
                      paths.repo_slug(config), '--body', body])
    except ghcli.GhError:
        log.warning('PR コメントの投稿に失敗しました')
    return {'ok': False, 'attempts': max_attempts,
            'message': '検証を通過できませんでした。\n%s' % summary}
