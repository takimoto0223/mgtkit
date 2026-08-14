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
import threading
import time

from . import claude_helper, feedback, ghcli, paths
from .autofix import AUTOFIX_PREFIX, _summarize_checks
from .gitcli import ensure_work_repo, run_git
from .submit import workrepo_dir

log = logging.getLogger(__name__)


class ReviewError(Exception):
    """承認処理の中断。str() はユーザー向けの平易な日本語メッセージ."""


# gh 呼び出し (プロセス起動 + GitHub への往復) は 1 回 0.3〜1 秒かかる。
# セッション中ほぼ変わらない情報はキャッシュし、ボタン操作のたびに
# 取り直さない (PR やレビューの状態は毎回 GitHub から取る)。
_cache = {}
_COLLABORATORS_TTL = 600  # 秒。メンバー構成が変わるのはまれ


def clear_cache():
    """セッションキャッシュを破棄する (テストや再ログイン時に使う)."""
    _cache.clear()


def current_user():
    """自分の GitHub ログイン名。起動中に変わらないため初回のみ取得する."""
    if 'user' not in _cache:
        _cache['user'] = ghcli.run_gh(
            ['api', 'user', '--jq', '.login']).strip()
    return _cache['user']


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
    結果は _COLLABORATORS_TTL 秒キャッシュする (取得失敗はキャッシュしない)。
    """
    cached = _cache.get('collaborators')
    if cached is not None and (time.monotonic() - cached[1]
                               < _COLLABORATORS_TTL):
        return cached[0]
    try:
        out = ghcli.run_gh([
            'api', 'repos/%s/collaborators?per_page=100'
            % paths.repo_slug(config),
            '--jq', '[.[] | select(.permissions.push) | .login]'])
        names = json.loads(out)
    except (ghcli.GhError, ValueError):
        return None
    result = set(names) if names else None
    _cache['collaborators'] = (result, time.monotonic())
    return result


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
    レビュー・検証・コメントも含めて 1 回の gh pr list で取得する
    (PR ごとの pr view を繰り返すと件数分の往復になり体感が重い)。
    """
    out = ghcli.run_gh([
        'pr', 'list', '--repo', paths.repo_slug(config), '--state', 'open',
        '--json', 'number,title,url,author,headRefName,createdAt,'
                  'reviews,statusCheckRollup,mergeable,comments'])
    try:
        prs = json.loads(out)
    except ValueError:
        raise ReviewError('承認待ち一覧を取得できませんでした。')
    return _build_pending(prs, config)


def _build_pending(prs, config):
    """gh pr list --json 形式の PR dict 一覧から承認待ち一覧を組み立てる."""
    members = collaborators(config)
    n_req = required_approvals(config)
    result = []
    for pr in prs:
        if not _is_submission(pr):
            continue
        summary = approval_summary(pr.get('reviews'), members)
        since = rejected_since(summary, n_req)
        result.append({
            'number': pr['number'],
            'title': pr['title'],
            'url': pr['url'],
            'branch': pr['headRefName'],
            'author': (pr.get('author') or {}).get('login', '?'),
            'created_at': (pr.get('createdAt') or '')[:10],
            'approved': summary['approved'],
            'rejected': summary['rejected'],
            'rejected_final': len(summary['rejected']) >= n_req,
            'rejected_since': since,
            'feedback': parse_feedback(pr.get('comments')),
            'checks': _summarize_checks(pr.get('statusCheckRollup')
                                        or []),
            'conflicting': (pr.get('mergeable') or '').upper()
                           == 'CONFLICTING',
        })
    return result


# 承認タブの表示に必要な情報 (自分のログイン名・open PR とそのレビュー・
# 検証・コメント・リリース一覧) を 1 回の往復でまとめて取る GraphQL。
# collaborator 一覧は権限によって読めないことがあるため含めない
# (従来どおり collaborators() のキャッシュ付き取得を使う)
_SNAPSHOT_QUERY = '''\
query($owner: String!, $name: String!) {
  viewer { login }
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, first: 50) {
      nodes {
        number title url headRefName mergeable createdAt
        author { login }
        reviews(first: 100) {
          nodes { state body submittedAt author { login } }
        }
        comments(first: 100) {
          nodes { body createdAt url author { login } }
        }
        commits(last: 1) {
          nodes { commit { statusCheckRollup { contexts(first: 100) {
            nodes {
              __typename
              ... on CheckRun { status conclusion }
              ... on StatusContext { state }
            }
          } } } }
        }
      }
    }
    merged: pullRequests(states: MERGED, first: 40,
                         orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes {
        number title headRefName createdAt mergedAt
        author { login }
      }
    }
    releases(first: 30, orderBy: {field: CREATED_AT, direction: DESC}) {
      nodes {
        tagName name isPrerelease isDraft description publishedAt
        releaseAssets(first: 10) { nodes { name downloadUrl } }
      }
    }
  }
}
'''


def fetch_snapshot(config=None, on_progress=None):
    """承認タブ用の一括取得: 承認待ち一覧 + リリース一覧 + ログイン名.

    まず GraphQL 1 回 (プロセス起動 1 回・往復 1 回) で取得し、
    通信失敗や想定外の応答のときは従来の取得 (pr list + リリース一覧の
    並列) へ自動でフォールバックする。
    on_progress: 進行状況をユーザーへ伝えるコールバック (平易な日本語)。
    戻り値: dict(pending, releases, me)。
    """
    def progress(msg):
        if on_progress:
            on_progress(msg)

    progress('最新の提出状況とβ版・リリースの一覧を確認しています...')
    try:
        return _snapshot_via_graphql(config)
    except Exception:
        log.info('一括取得に失敗したため従来の方法で取得します',
                 exc_info=True)
    progress('うまく取得できなかったため、方法を変えて確認し直して'
             'います...')
    return _snapshot_via_rest(config)


def _snapshot_via_graphql(config):
    slug = paths.repo_slug(config)
    owner, name = slug.split('/', 1)
    out = ghcli.run_gh(['api', 'graphql',
                        '-f', 'query=%s' % _SNAPSHOT_QUERY,
                        '-f', 'owner=%s' % owner,
                        '-f', 'name=%s' % name])
    data = json.loads(out)['data']
    me = data['viewer']['login']
    _cache['user'] = me  # ログイン名もこの 1 回から得られる
    repo = data['repository']
    prs = [_pr_from_graphql(n) for n in repo['pullRequests']['nodes']]
    releases = [_release_from_graphql(n) for n in repo['releases']['nodes']
                if not n.get('isDraft')]
    merged = _merged_from_prs((repo.get('merged') or {}).get('nodes') or [])
    return {'pending': _build_pending(prs, config),
            'releases': releases, 'me': me, 'merged': merged}


def _pr_from_graphql(node):
    """GraphQL の PR ノードを gh pr list --json と同じ形に変換する."""
    commits = (node.get('commits') or {}).get('nodes') or []
    rollup = (((commits[0].get('commit') or {}).get('statusCheckRollup'))
              if commits else None) or {}
    contexts = (rollup.get('contexts') or {}).get('nodes') or []
    return {
        'number': node['number'],
        'title': node.get('title') or '',
        'url': node.get('url') or '',
        'headRefName': node.get('headRefName') or '',
        'mergeable': node.get('mergeable') or '',
        'createdAt': node.get('createdAt') or '',
        'author': node.get('author') or {},
        'reviews': (node.get('reviews') or {}).get('nodes') or [],
        'comments': (node.get('comments') or {}).get('nodes') or [],
        'statusCheckRollup': contexts,
    }


def _release_from_graphql(node):
    """GraphQL の Release ノードを ghcli.fetch_releases と同じ形に変換."""
    assets = (node.get('releaseAssets') or {}).get('nodes') or []
    return {
        'tag': node.get('tagName') or '',
        'name': node.get('name') or node.get('tagName') or '',
        'prerelease': bool(node.get('isPrerelease')),
        'notes': node.get('description') or '',
        'published_at': (node.get('publishedAt') or '')[:10],
        'assets': [{'name': a.get('name'), 'url': a.get('downloadUrl')}
                   for a in assets],
    }


def _merged_from_prs(prs):
    """PR dict (gh --json / GraphQL 変換済み) から済み提出の要約を作る.

    過去の更新ログの図 (誰がいつ提出し、いつ正式版になったか) 用。
    マネージャー経由の提出 (feature/ ブランチ) のみを対象とする。
    """
    out = []
    for pr in prs:
        if not _is_submission(pr):
            continue
        out.append({
            'number': pr['number'],
            'title': pr.get('title') or '',
            'author': (pr.get('author') or {}).get('login', '?'),
            'created_at': (pr.get('createdAt') or '')[:10],
            'merged_at': (pr.get('mergedAt') or '')[:10],
        })
    return out


def list_merged(config=None):
    """取り込み済みの提出一覧 (過去の更新ログの図用・直近 40 件)."""
    out = ghcli.run_gh([
        'pr', 'list', '--repo', paths.repo_slug(config),
        '--state', 'merged', '--limit', '40',
        '--json', 'number,title,author,headRefName,createdAt,mergedAt'])
    try:
        prs = json.loads(out)
    except ValueError:
        raise ReviewError('取り込み済みの提出一覧を取得できませんでした。')
    return _merged_from_prs(prs)


def _snapshot_via_rest(config):
    """従来経路のフォールバック: pr list とリリース一覧を並列に取得."""
    results, errors = {}, []

    def fetch(name, fn):
        def run():
            try:
                results[name] = fn()
            except (ReviewError, ghcli.GhError) as e:
                errors.append(e)
        return threading.Thread(target=run, daemon=True)

    def merged_safe():
        # 図のための補助情報。取れなくても一覧表示は成立するので
        # 失敗はエラーにしない (図は「準備中」表示になる)
        try:
            return list_merged(config)
        except (ReviewError, ghcli.GhError):
            return []

    threads = [
        fetch('pending', lambda: (list_pending(config), current_user())),
        fetch('releases',
              lambda: ghcli.fetch_releases(paths.repo_slug(config))),
        fetch('merged', merged_safe),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        raise errors[0]
    pending, me = results['pending']
    return {'pending': pending, 'releases': results['releases'], 'me': me,
            'merged': results.get('merged') or []}


def _pr_detail(pr_number, config=None):
    out = ghcli.run_gh([
        'pr', 'view', str(pr_number), '--repo', paths.repo_slug(config),
        '--json', 'reviews,statusCheckRollup,mergeable,headRefName,'
                  'title,body,author,comments'])
    try:
        return json.loads(out)
    except ValueError:
        raise ReviewError('提出内容の情報を取得できませんでした。')


def approve(pr_number, config=None, author=None):
    """承認する。提出者本人の自己承認は禁止.

    author: 提出者の GitHub ログイン名。一覧取得済みの呼び出し側が
    渡せば PR 情報の再取得を省ける (提出者は後から変わらない)。
    None なら従来どおり PR 情報を取得して確認する。
    """
    if author is None:
        detail = _pr_detail(pr_number, config)
        author = (detail.get('author') or {}).get('login')
    if author == current_user():
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


def ensure_branch_current(pr_number, config=None, on_progress=None):
    """提出ブランチが最新の正式版より古ければ取り込み直し、検証を待つ.

    ブランチ保護が「最新の状態でのみマージ可」(strict_status_checks)
    のため、古いままでは正式版として取り込めない。取り込み直すと
    検証 (CI) が走り直すので、完了までここで待つ (最大約 8 分)。
    """
    def progress(msg):
        log.info('%s', msg)
        if on_progress:
            on_progress(msg)

    repo = paths.repo_slug(config)
    out = ghcli.run_gh(['pr', 'view', str(pr_number), '--repo', repo,
                        '--json', 'mergeStateStatus'])
    try:
        state = (json.loads(out).get('mergeStateStatus') or '').upper()
    except ValueError:
        state = ''
    if state != 'BEHIND':
        return
    progress('提出内容に最新の正式版を取り込み直しています...')
    ghcli.run_gh(['api', '-X', 'PUT',
                  'repos/%s/pulls/%d/update-branch' % (repo, pr_number)],
                 timeout=120)
    progress('取り込み直し後の検証を待っています (数分かかります)...')
    deadline = time.time() + 8 * 60
    while time.time() < deadline:
        time.sleep(10)
        detail = _pr_detail(pr_number, config)
        checks = _summarize_checks(detail.get('statusCheckRollup') or [])
        if checks == 'failure':
            raise ReviewError('取り込み直し後の検証で問題が見つかりました。'
                              '提出者に修正を依頼してください。')
        if checks == 'success':
            progress('検証を通過しました。')
            return
    raise ReviewError('検証の完了を待ちきれませんでした。しばらくして'
                      'から、もう一度承認タブを開いてください。')


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

    # ブランチが古いと保護設定 (strict) でマージが拒否されるため先に最新化
    ensure_branch_current(pr_number, config, on_progress=on_progress)
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
                        '各メンバーへ自動で配布されます。' % version)}
