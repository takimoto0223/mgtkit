# -*- coding: utf-8 -*-
"""承認タブの中核ロジック (UI 非依存).

- 承認待ち一覧 (open PR) と 承認 n/必要数 の集計
- 承認 / 却下 (コメント必須) / 提出者本人の自己承認禁止
- 提出者の変更と [auto-fix] 修正の色分け用コミット分類
- 必要数の承認が揃ったらリリース (squash merge → 正式版タグ → Releases)

状態はすべて GitHub (PR / Reviews / Releases) を真実とし、
マネージャーは表示と操作の窓口に徹する。
"""
import json
import logging
import re

from . import claude_helper, feedback, ghcli, paths
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
               .get('required_approvals', 2))


def rejected_cleanup_days(config=None):
    """却下確定からβ版・提出を自動で片付けるまでの日数."""
    return int(((config or {}).get('manager') or {})
               .get('rejected_cleanup_days', 3))


def rejected_since(summary, n_req):
    """却下が必要数に達した時刻 (n 人目の却下の時刻)。未達なら None."""
    if len(summary['rejected']) < n_req:
        return None
    times = sorted(r.get('at') or '' for r in summary['rejected'])
    return times[n_req - 1] or None


def admins(config=None):
    """リリース操作を許可するユーザーの一覧。空 = 制限なし (全員可)."""
    lst = ((config or {}).get('manager') or {}).get('admins') or []
    return [str(a) for a in lst]


def can_operate_release(me, config=None):
    """リリース操作の権限判定。admins が空なら必要数の承認だけで全員可."""
    a = admins(config)
    return not a or me in a


def collaborators(config=None):
    """push 権限を持つ collaborator の GitHub ユーザー名一覧 (set).

    public リポジトリでは誰でも PR にレビューを付けられるため、
    承認・却下のカウントはこの一覧のメンバーに限定する。
    取得できない場合は None を返し、呼び出し側はフィルタなし
    (従来どおり全レビューを数える) にフォールバックする。
    """
    try:
        out = ghcli.run_gh([
            'api', 'repos/%s/collaborators?per_page=100'
            % paths.repo_slug(config),
            '--jq', '[.[] | select(.permissions.push) | .login]'])
        names = json.loads(out)
    except (ghcli.GhError, ValueError):
        return None
    return set(names) if names else None


def approval_summary(reviews, members=None):
    """レビュー一覧から各メンバーの最新状態を集計する.

    members: 承認・却下をカウントする GitHub ユーザー名の集合。
    None ならフィルタしない (全レビューを数える)。
    戻り値: dict(approved=[名前], rejected=[{name, comment}])
    """
    latest = {}
    for r in reviews or []:
        state = (r.get('state') or '').upper()
        if state not in ('APPROVED', 'CHANGES_REQUESTED', 'DISMISSED'):
            continue  # COMMENTED 等は承認状態に影響しない
        name = ((r.get('author') or {}).get('login')) or '?'
        if members is not None and name not in members:
            continue  # メンバー外 (public リポジトリの第三者等) は数えない
        latest[name] = {'state': state, 'comment': r.get('body') or '',
                        'at': r.get('submittedAt')
                        or r.get('submitted_at') or ''}
    approved = sorted(n for n, v in latest.items()
                      if v['state'] == 'APPROVED')
    rejected = [{'name': n, 'comment': v['comment'], 'at': v['at']}
                for n, v in sorted(latest.items())
                if v['state'] == 'CHANGES_REQUESTED']
    return {'approved': approved, 'rejected': rejected}


def parse_feedback(comments):
    """PR コメントからβ版フィードバックを抽出する.

    feedback.post_feedback が投稿する
    「β版 <tag> のフィードバック (<名前>):\\n\\n<本文>」形式のみ拾う。
    戻り値: [{'tag', 'name', 'date', 'text', 'author', 'comment_id'}]
    (投稿順)。author は GitHub ログイン名、comment_id は編集・削除用の
    コメント ID (URL から取れない場合は None)。
    """
    result = []
    for c in comments or []:
        body = c.get('body') or ''
        m = re.match(r'β版 (\S+) のフィードバック \((.+?)\):\s*', body)
        if not m:
            continue
        cid = re.search(r'#issuecomment-(\d+)', c.get('url') or '')
        result.append({'tag': m.group(1), 'name': m.group(2),
                       'date': (c.get('createdAt') or '')[:10],
                       'text': body[m.end():].strip(),
                       'author': (c.get('author') or {}).get('login', ''),
                       'comment_id': cid.group(1) if cid else None})
    return result


# マネージャー経由の提出 (submit.py) が使うブランチの接頭辞。
# これ以外 (管理者によるマネージャー自体の更新など) は mgtkit の更新では
# ないため、承認タブの対象にしない
SUBMISSION_BRANCH_PREFIX = 'feature/'


def _is_submission(pr):
    return (pr.get('headRefName') or '').startswith(SUBMISSION_BRANCH_PREFIX)


def list_pending(config=None):
    """承認待ちの提出一覧 (open PR + 承認状況 + 検証状況 + 競合有無).

    マネージャー経由の提出 (feature/ ブランチ) のみを対象とする。
    """
    out = ghcli.run_gh([
        'pr', 'list', '--repo', paths.repo_slug(config), '--state', 'open',
        '--json', 'number,title,url,author,headRefName'])
    try:
        prs = json.loads(out)
    except ValueError:
        raise ReviewError('承認待ち一覧を取得できませんでした。')
    members = collaborators(config)
    n_req = required_approvals(config)
    result = []
    for pr in prs:
        if not _is_submission(pr):
            continue
        detail = _pr_detail(pr['number'], config)
        summary = approval_summary(detail.get('reviews'), members)
        since = rejected_since(summary, n_req)
        result.append({
            'number': pr['number'],
            'title': pr['title'],
            'url': pr['url'],
            'branch': pr['headRefName'],
            'author': (pr.get('author') or {}).get('login', '?'),
            'approved': summary['approved'],
            'rejected': summary['rejected'],
            'rejected_final': len(summary['rejected']) >= n_req,
            'rejected_since': since,
            'feedback': parse_feedback(detail.get('comments')),
            'checks': _summarize_checks(detail.get('statusCheckRollup')
                                        or []),
            'conflicting': (detail.get('mergeable') or '').upper()
                           == 'CONFLICTING',
        })
    return result


def count_pending(config=None):
    """承認待ちの提出件数のみを軽量に取得する (タブバッジ用).

    list_pending と同じく feature/ ブランチの提出のみ数える。
    """
    out = ghcli.run_gh([
        'pr', 'list', '--repo', paths.repo_slug(config), '--state', 'open',
        '--json', 'headRefName'])
    try:
        prs = json.loads(out)
    except ValueError:
        return 0
    return sum(1 for pr in prs if _is_submission(pr))


def _pr_detail(pr_number, config=None):
    out = ghcli.run_gh([
        'pr', 'view', str(pr_number), '--repo', paths.repo_slug(config),
        '--json', 'reviews,statusCheckRollup,mergeable,headRefName,'
                  'title,body,author,comments'])
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


def cancel_my_review(pr_number, config=None):
    """自分の承認・却下を取り消してニュートラルに戻す.

    GitHub のレビュー却下 (dismiss) を自分の最新レビューに対して行う。
    push 権限を持つ collaborator であれば実行できる。
    """
    slug = paths.repo_slug(config)
    me = current_user()
    out = ghcli.run_gh(['api', 'repos/%s/pulls/%d/reviews?per_page=100'
                        % (slug, int(pr_number))])
    try:
        revs = json.loads(out)
    except ValueError:
        raise ReviewError('レビュー情報を取得できませんでした。')
    target = None
    for r in revs:
        if ((r.get('user') or {}).get('login')) != me:
            continue
        if (r.get('state') or '').upper() in ('APPROVED',
                                              'CHANGES_REQUESTED'):
            target = r  # 自分の最新の承認/却下
    if target is None:
        raise ReviewError('取り消せる承認・却下がありません。')
    ghcli.run_gh(['api', '-X', 'PUT',
                  'repos/%s/pulls/%d/reviews/%s/dismissals'
                  % (slug, int(pr_number), target['id']),
                  '-f', 'message=本人が取り消しました'])


def delete_betas_for(pr_number, config=None):
    """提出 pr_number に対応するβ版 (prerelease) を削除する.

    リリース・取り下げ・却下確定の後始末に共通で使う。
    失敗しても本処理は成立しているため警告ログのみ。
    """
    slug = paths.repo_slug(config)
    try:
        betas = ghcli.prereleases(ghcli.fetch_releases(slug))
    except ghcli.GhError:
        return
    for r in betas:
        if feedback.pr_number_from_release(r) != int(pr_number):
            continue
        try:
            ghcli.run_gh(['release', 'delete', r['tag'], '--repo', slug,
                          '--yes', '--cleanup-tag'])
        except ghcli.GhError:
            log.warning('β版 %s の削除に失敗 (残っても動作に影響なし)',
                        r['tag'])


def reset_all_reviews(pr_number, config=None):
    """全員の承認・却下を取り消す (統合で内容が変わったときの仕切り直し).

    各メンバーの最新レビューを dismiss する。個別の失敗はスキップ。
    戻り値: 取り消した件数。
    """
    slug = paths.repo_slug(config)
    out = ghcli.run_gh(['api', 'repos/%s/pulls/%d/reviews?per_page=100'
                        % (slug, int(pr_number))])
    try:
        revs = json.loads(out)
    except ValueError:
        return 0
    latest = {}
    for r in revs:
        user = (r.get('user') or {}).get('login')
        state = (r.get('state') or '').upper()
        if user and state in ('APPROVED', 'CHANGES_REQUESTED'):
            latest[user] = r
    count = 0
    for r in latest.values():
        try:
            ghcli.run_gh([
                'api', '-X', 'PUT',
                'repos/%s/pulls/%d/reviews/%s/dismissals'
                % (slug, int(pr_number), r['id']),
                '-f', 'message=最新版との統合により内容が変わったため、'
                      '承認・却下をリセットしました'])
            count += 1
        except ghcli.GhError:
            log.warning('レビュー %s の取り消しに失敗 (続行)', r.get('id'))
    return count


def withdraw(pr_number, reason='', config=None):
    """提出者本人が自分の提出を取り下げる.

    PR をクローズして提出ブランチを削除し、対応するβ版 (prerelease) も
    片付ける。GitHub の仕様で自分の提出には却下レビューを付けられない
    ため、本人向けは「取り下げ」として提供する。
    """
    detail = _pr_detail(pr_number, config)
    if ((detail.get('author') or {}).get('login')) != current_user():
        raise ReviewError('取り下げは提出者本人のみ行えます。'
                          '他の提出には「却下」を使ってください。')
    slug = paths.repo_slug(config)
    reason = (reason or '').strip()
    if reason:
        ghcli.run_gh(['pr', 'comment', str(pr_number), '--repo', slug,
                      '--body', '提出者が取り下げました: %s' % reason])
    ghcli.run_gh(['pr', 'close', str(pr_number), '--repo', slug,
                  '--delete-branch'])
    # 対応するβ版が残骸として残らないよう削除する
    delete_betas_for(pr_number, config)


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
    summary = approval_summary(detail.get('reviews'),
                               collaborators(config))
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
    progress('役目を終えたβ版を片付けています...')
    delete_betas_for(pr_number, config)
    return {'version': version,
            'message': ('%s として取り込みました。数分でリリースが公開され、'
                        '各メンバーの更新タブに表示されます。' % version)}
