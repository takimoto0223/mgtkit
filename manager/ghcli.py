# -*- coding: utf-8 -*-
"""gh CLI のサブプロセスラッパー.

方針 (manager/docs/decisions.md):
- GitHub 操作は認証済み gh CLI に委ね、トークンをマネージャー内に保存しない
- 失敗時は stderr をログに残し、ユーザーには平易な日本語メッセージを見せる
"""
import json
import logging
import subprocess

log = logging.getLogger(__name__)

_CREATE_NO_WINDOW = 0x08000000  # Windows でコンソール窓を出さない


class GhError(Exception):
    """gh 実行失敗。str() はユーザー向けの平易な日本語メッセージ."""


def _popen_kwargs():
    kw = {}
    import sys
    if sys.platform == 'win32':
        kw['creationflags'] = _CREATE_NO_WINDOW
    return kw


def run_gh(args, timeout=60):
    """gh を実行し stdout を返す。失敗は GhError (詳細はログへ)."""
    cmd = ['gh'] + list(args)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=timeout, **_popen_kwargs())
    except FileNotFoundError:
        raise GhError('gh コマンドが見つかりません。セットアップ (setup.bat) を'
                      '実行するか、https://cli.github.com/ から導入してください。')
    except subprocess.TimeoutExpired:
        raise GhError('GitHub への接続がタイムアウトしました。'
                      'ネットワーク接続を確認してください。')
    if proc.returncode != 0:
        log.error('gh %s failed (%d): %s', args, proc.returncode, proc.stderr)
        raise GhError(_friendly_message(proc.stderr))
    return proc.stdout


def _friendly_message(stderr):
    s = (stderr or '').lower()
    if 'auth login' in s or 'authentication' in s or 'not logged' in s:
        return ('GitHub へのログインが必要です。コマンドプロンプトで '
                '「gh auth login」を実行してください。')
    if 'could not resolve' in s or 'connect' in s or 'network' in s:
        return 'ネットワークに接続できません。接続環境を確認してください。'
    if '403' in s or 'forbidden' in s:
        return ('権限がありません。この操作はリポジトリのオーナー (管理者) '
                'のみ実行できます。')
    if 'not found' in s or '404' in s:
        return '対象が見つかりませんでした。ユーザー名やリポジトリの設定を確認してください。'
    if 'rate limit' in s:
        return 'GitHub の利用制限に達しました。しばらく待って再試行してください。'
    return ('GitHub との通信でエラーが発生しました。'
            '時間をおいて再試行してください。')


def fetch_releases(repo, limit=30):
    """リリース一覧 (新しい順)。各要素: dict(tag, name, prerelease, notes,
    published_at, assets=[{name, url}])."""
    out = run_gh(['api', 'repos/%s/releases?per_page=%d' % (repo, limit)])
    try:
        raw = json.loads(out)
    except ValueError:
        raise GhError('GitHub からの応答を解釈できませんでした。')
    releases = []
    for r in raw:
        if r.get('draft'):
            continue
        releases.append({
            'tag': r.get('tag_name') or '',
            'name': r.get('name') or r.get('tag_name') or '',
            'prerelease': bool(r.get('prerelease')),
            'notes': r.get('body') or '',
            'published_at': (r.get('published_at') or '')[:10],
            # 同日に複数の出来事があるときの前後関係の判定用 (履歴図)
            'published_at_full': r.get('published_at') or '',
            'assets': [{'name': a.get('name'), 'url': a.get('url')}
                       for a in (r.get('assets') or [])],
        })
    return releases


def latest_stable(releases):
    """リリース一覧から最新の正式版を返す (無ければ None)."""
    for r in releases:
        if not r['prerelease']:
            return r
    return None


def prereleases(releases):
    return [r for r in releases if r['prerelease']]


JOIN_REQUEST_TITLE = '参加申請'


def has_push_access(repo):
    """自分がこのリポジトリへの push 権限 (collaborator) を持つか."""
    out = run_gh(['api', 'repos/%s' % repo, '--jq', '.permissions.push'])
    return out.strip() == 'true'


def find_my_join_request(repo):
    """自分が出した参加申請 Issue (open/closed 問わず) を返す。無ければ None.

    close 済みでも再申請しない (却下された申請の連投を防ぐ)。
    """
    out = run_gh(['issue', 'list', '--repo', repo, '--author', '@me',
                  '--state', 'all', '--search', JOIN_REQUEST_TITLE,
                  '--json', 'number,title,state'])
    try:
        issues = json.loads(out)
    except ValueError:
        return None
    for issue in issues:
        if (issue.get('title') or '').startswith(JOIN_REQUEST_TITLE):
            return issue
    return None


def create_join_request(repo, display_name):
    """参加申請 Issue を作成する (collaborator でない新メンバーの初回起動時).

    オーナーには GitHub から通知メールが届き、「承認」と返信 (コメント) すると
    .github/workflows/join-request.yml が自動で collaborator 招待を送る。
    """
    login = run_gh(['api', 'user', '--jq', '.login']).strip()
    owner = repo.split('/')[0]
    title = '%s: %s (@%s)' % (JOIN_REQUEST_TITLE, display_name or login,
                              login)
    # オーナーを @メンション する (Watch 設定によらず通知メールを確実に届ける)
    body = ('mgtkit アプリマネージャーからの参加申請です。\n\n'
            '- GitHub: @%s\n'
            '- 名前: %s\n\n'
            '@%s さんへ: この Issue に「承認」とコメントすると (通知メールへ'
            'の返信でも可)、自動で collaborator 招待が送られます。'
            '承認しない場合はコメントせずにクローズしてください。'
            % (login, display_name or '(未登録)', owner))
    run_gh(['issue', 'create', '--repo', repo,
            '--title', title, '--body', body])


def accept_repo_invitation(repo):
    """自分宛の招待のうち repo のものがあれば承諾する.

    メンバーがマネージャーを起動するだけで参加が完了するようにするための
    仕組み。招待が無ければ何もしない。戻り値: 承諾したら True。
    """
    out = run_gh(['api', '/user/repository_invitations'])
    try:
        invitations = json.loads(out)
    except ValueError:
        return False
    for inv in invitations:
        full_name = ((inv.get('repository') or {}).get('full_name') or '')
        if full_name.lower() == repo.lower():
            run_gh(['api', '-X', 'PATCH',
                    '/user/repository_invitations/%s' % inv['id']])
            return True
    return False


def tag_commit_sha(repo, tag):
    """タグの指すコミット SHA (version.json 補完用)."""
    out = run_gh(['api', 'repos/%s/commits/%s' % (repo, tag),
                  '--jq', '.sha'])
    return out.strip()


def download_release(repo, tag, dest_dir, has_assets):
    """リリースの ZIP を dest_dir へダウンロードする.

    CI 添付の配布 ZIP があればそれを、無ければソースアーカイブを取得する
    (Phase 4 の CI 整備前でも動作させるためのフォールバック)。
    戻り値: (zip のパス, is_source_archive)
    """
    import glob
    import os
    if has_assets:
        run_gh(['release', 'download', tag, '--repo', repo,
                '--pattern', '*.zip', '--dir', dest_dir, '--clobber'],
               timeout=300)
        is_source = False
    else:
        run_gh(['release', 'download', tag, '--repo', repo,
                '--archive=zip', '--dir', dest_dir, '--clobber'],
               timeout=300)
        is_source = True
    zips = sorted(glob.glob(os.path.join(dest_dir, '*.zip')),
                  key=os.path.getmtime)
    if not zips:
        raise GhError('リリースの ZIP を取得できませんでした。')
    return zips[-1], is_source
