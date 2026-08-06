# -*- coding: utf-8 -*-
"""承認タブの中核ロジック (UI 非依存).

- 承認待ち一覧 (open PR) と 承認 n/3 の集計
- 承認 / 却下 (コメント必須) / 提出者本人の自己承認禁止
- 提出者の変更と [auto-fix] 修正の色分け用コミット分類
- 必要数の承認が揃ったらリリース (squash merge → 正式版タグ → Releases)

状態はすべて GitHub (PR / Reviews / Releases) を真実とし、
マネージャーは表示と操作の窓口に徹する。
"""
import json
import logging
import re

from . import claude_helper, ghcli, paths
from .autofix import AUTOFIX_PREFIX, _summarize_checks
from .gitcli import ensure_work_repo, run_git
from .submit import workrepo_dir

log = logging.getLogger(__name__)


class ReviewError(Exception):
    """承認処理の中断。str() はユーザー向けの平易な日本語メッセージ."""


def current_user():
    return ghcli.run_gh(['api', 'user', '--jq', '.login']).strip()


def required_approvals(config=None):
    return int(((config or {}).get('branch_protection') or {})
               .get('required_approvals', 3))


def admins(config=None):
    """リリース操作を許可するユーザーの一覧。空 = 制限なし (全員可)."""
    lst = ((config or {}).get('manager') or {}).get('admins') or []
    return [str(a) for a in lst]


def can_operate_release(me, config=None):
    """リリース操作の権限判定。admins が空なら必要数の承認だけで全員可."""
    a = admins(config)
    return not a or me in a


def approval_summary(reviews):
    """レビュー一覧から各メンバーの最新状態を集計する.

    戻り値: dict(approved=[名前], rejected=[{name, comment}])
    """
    latest = {}
    for r in reviews or []:
        state = (r.get('state') or '').upper()
        if state not in ('APPROVED', 'CHANGES_REQUESTED', 'DISMISSED'):
            continue  # COMMENTED 等は承認状態に影響しない
        name = ((r.get('author') or {}).get('login')) or '?'
        latest[name] = {'state': state, 'comment': r.get('body') or ''}
    approved = sorted(n for n, v in latest.items()
                      if v['state'] == 'APPROVED')
    rejected = [{'name': n, 'comment': v['comment']}
                for n, v in sorted(latest.items())
                if v['state'] == 'CHANGES_REQUESTED']
    return {'approved': approved, 'rejected': rejected}


def list_pending(config=None):
    """承認待ちの提出一覧 (open PR + 承認状況 + 検証状況 + 競合有無)."""
    out = ghcli.run_gh([
        'pr', 'list', '--repo', paths.repo_slug(config), '--state', 'open',
        '--json', 'number,title,url,author,headRefName'])
    try:
        prs = json.loads(out)
    except ValueError:
        raise ReviewError('承認待ち一覧を取得できませんでした。')
    result = []
    for pr in prs:
        detail = _pr_detail(pr['number'], config)
        summary = approval_summary(detail.get('reviews'))
        result.append({
            'number': pr['number'],
            'title': pr['title'],
            'url': pr['url'],
            'branch': pr['headRefName'],
            'author': (pr.get('author') or {}).get('login', '?'),
            'approved': summary['approved'],
            'rejected': summary['rejected'],
            'checks': _summarize_checks(detail.get('statusCheckRollup')
                                        or []),
            'conflicting': (detail.get('mergeable') or '').upper()
                           == 'CONFLICTING',
        })
    return result


def count_pending(config=None):
    """承認待ち (open PR) の件数のみを軽量に取得する (タブバッジ用)."""
    out = ghcli.run_gh([
        'pr', 'list', '--repo', paths.repo_slug(config), '--state', 'open',
        '--json', 'number', '--jq', 'length'])
    try:
        return int(out.strip() or 0)
    except ValueError:
        return 0


def _pr_detail(pr_number, config=None):
    out = ghcli.run_gh([
        'pr', 'view', str(pr_number), '--repo', paths.repo_slug(config),
        '--json', 'reviews,statusCheckRollup,mergeable,headRefName,'
                  'title,body,author'])
    try:
        return json.loads(out)
    except ValueError:
        raise ReviewError('提出内容の情報を取得できませんでした。')


def approve(pr_number, config=None):
    """承認する。提出者本人の自己承認は禁止."""
    detail = _pr_detail(pr_number, config)
    if ((detail.get('author') or {}).get('login')) == current_user():
        raise ReviewError('自分の提出は自分では承認できません。'
                          '他のメンバーの承認を待ってください。')
    ghcli.run_gh(['pr', 'review', str(pr_number), '--repo',
                  paths.repo_slug(config), '--approve'])


def request_changes(pr_number, comment, config=None):
    """却下する。理由コメントは必須."""
    comment = (comment or '').strip()
    if not comment:
        raise ReviewError('却下には理由の入力が必要です。')
    ghcli.run_gh(['pr', 'review', str(pr_number), '--repo',
                  paths.repo_slug(config), '--request-changes',
                  '--body', comment])


def classified_diff(pr_number, config=None):
    """差分を「提出者の変更」と「自動修正 ([auto-fix])」に分類する.

    戻り値: dict(user_files, autofix_files, diff_text)
    """
    detail = _pr_detail(pr_number, config)
    branch = detail['headRefName']
    base = (config or {}).get('base_branch', 'main')
    workrepo = ensure_work_repo(paths.repo_slug(config),
                                workrepo_dir(config))
    run_git(['fetch', 'origin', branch, base], cwd=workrepo, timeout=300)

    commits = run_git(
        ['log', '--format=%H\t%s',
         'origin/%s..origin/%s' % (base, branch)],
        cwd=workrepo).splitlines()
    user_files, autofix_files = set(), set()
    for line in commits:
        if '\t' not in line:
            continue
        sha, subject = line.split('\t', 1)
        files = run_git(['diff-tree', '--no-commit-id', '--name-only',
                         '-r', sha], cwd=workrepo).split()
        if subject.startswith(AUTOFIX_PREFIX):
            autofix_files.update(files)
        else:
            user_files.update(files)
    diff_text = run_git(
        ['diff', 'origin/%s...origin/%s' % (base, branch)],
        cwd=workrepo, timeout=300)
    return {'user_files': sorted(user_files),
            'autofix_files': sorted(autofix_files),
            'diff_text': diff_text}


def can_release(pr, config=None, me=None):
    """リリースボタンの有効条件: 必要数承認・却下なし・検証OK・管理者."""
    if me is None:
        me = current_user()
    return (len(pr['approved']) >= required_approvals(config)
            and not pr['rejected']
            and pr['checks'] == 'success'
            and not pr['conflicting']
            and can_operate_release(me, config))


def next_stable_version(config=None):
    """次の正式版バージョン (最新安定版 vX.Y の次のマイナー)."""
    releases = ghcli.fetch_releases(paths.repo_slug(config))
    stable = []
    for r in releases:
        m = re.match(r'^v(\d+)\.(\d+)$', r['tag'])
        if m and not r['prerelease']:
            stable.append((int(m.group(1)), int(m.group(2))))
    if not stable:
        return 'v1.0'
    major, minor = max(stable)
    return 'v%d.%d' % (major, minor + 1)


def release(pr_number, config=None, on_progress=None):
    """squash merge → 正式版タグ + Releases 登録 (release ワークフローを起動).

    戻り値: dict(version, message)
    """
    def progress(msg):
        log.info('%s', msg)
        if on_progress:
            on_progress(msg)

    detail = _pr_detail(pr_number, config)
    summary = approval_summary(detail.get('reviews'))
    if len(summary['approved']) < required_approvals(config):
        raise ReviewError('承認数が足りません (%d/%d)。'
                          % (len(summary['approved']),
                             required_approvals(config)))
    if summary['rejected']:
        raise ReviewError('却下したメンバーがいるためリリースできません。'
                          '修正版の再提出を待ってください。')
    if not can_operate_release(current_user(), config):
        raise ReviewError('リリース操作は管理者のみ実行できます '
                          '(config.json の manager.admins で設定)。')

    progress('正式版として取り込んでいます...')
    ghcli.run_gh(['pr', 'merge', str(pr_number), '--repo',
                  paths.repo_slug(config), '--squash'], timeout=120)

    version = next_stable_version(config)
    progress('リリースノートを作成しています...')
    notes = claude_helper.generate_release_notes(
        detail.get('title', ''), detail.get('body', ''), version)
    if not notes:
        notes = '%s リリース。\n\n%s' % (version, detail.get('title', ''))

    progress('%s のリリースを開始しています...' % version)
    ghcli.run_gh(['workflow', 'run', 'release.yml', '--repo',
                  paths.repo_slug(config),
                  '-f', 'version=%s' % version,
                  '-f', 'notes=%s' % notes], timeout=120)
    return {'version': version,
            'message': ('%s として取り込みました。数分でリリースが公開され、'
                        '各メンバーの更新タブに表示されます。' % version)}
