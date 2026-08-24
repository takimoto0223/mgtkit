# -*- coding: utf-8 -*-
"""提出タブの中核ロジック (UI 非依存).

ユーザー操作は「ZIP を選んでアップロード」だけ。裏側で
  1. ZIP 展開 → version.json から基点コミットを特定
  2. 基点との差分計算 (追加/変更/削除)
  3. 提出対象外ファイルの除外 (実行ファイル・PDF などは受け取っても
     差分に含めず、基点の内容を維持する)
  4. 安全チェック (サイズ/件数上限・秘密情報スキャン)
  5. feature ブランチ作成 → 上書き → commit → push → PR 作成
を行う (manager/docs/decisions.md)。

削除ファイルの確認や秘密情報警告など、ユーザー判断が要る箇所で
処理を分割している: prepare_submission() → (UI で確認) → finalize_submission()
"""
import datetime
import logging
import os
import re
import shutil
import tempfile

from . import claude_helper, ghcli, installer, paths, safeio, versions
from .gitcli import (GitError, ensure_work_repo,
                     reset_work_tree, run_git)

log = logging.getLogger(__name__)

# PR 本文のうちリリースノートへ転載する 2 節の見出し
# (claude_helper.generate_pr_body の様式と対。
#  制限事項は表記ゆれを許容するため LIMITS_KEY で部分一致させる)
UPDATE_KEY = '更新内容'
LIMITS_KEY = '制限事項'
LIMITS_KEY_FULL = 'ご利用にあたっての制限事項'


class SubmitError(Exception):
    """提出処理の中断。str() はユーザー向けの平易な日本語メッセージ."""


class SubmitCancelled(SubmitError):
    """提出者が確認画面で取り消した (失敗ではない)."""


# 実行ファイルは差分対象にしない (ZIP に入っていてもエラーにせず除外)
BLOCKED_EXTENSIONS = {
    '.exe', '.dll', '.so', '.dylib', '.bat', '.cmd', '.ps1', '.sh',
    '.msi', '.scr', '.com', '.vbs', '.jar', '.pyd',
}

# 提出対象となるファイル種類のホワイトリスト (config で上書き可)。
# これ以外 (.pdf など) は受け取っても差分に含めない
DEFAULT_ALLOWED_EXTENSIONS = [
    '.py', '.md', '.txt', '.json', '.html', '.css', '.js',
    '.yml', '.yaml', '.cfg', '.ini', '.csv', '.dxf', '.toml',
]

# 配布 ZIP に含めない開発用ファイル (.github/workflows/release.yml の除外と同期)。
# これらは提出の差分対象外とし、基点の内容を常に維持する。
DIST_EXCLUDE_DIRS = ('.github/', 'tests/', 'docs/', 'scripts/', 'manager/')
DIST_EXCLUDE_FILES = ('.gitignore', 'pytest.ini', 'requirements-dev.txt',
                      'CLAUDE.md', '.gitattributes')

# version.json は配布時に生成、settings.json はマネージャーの個人設定
# (名前・API キー)、usage.json は API 利用量の個人記録。
# いずれも提出対象から常に除外する
GENERATED_FILES = ('version.json', 'settings.json', 'usage.json')

# 作業フォルダに混ざりがちな生成物・環境フォルダ。ZIP に入っていても
# 差分対象にしない (「フォルダ丸ごと ZIP で OK」を成立させるため)。
# stash は起動時の直接変更の退避フォルダ (manager/selfupdate.py)
JUNK_DIRS = {'.git', '__pycache__', '.pytest_cache', 'mgtkit_out',
             '.venv', 'venv', '.idea', '.vscode', 'stash'}

# 明確な認証情報は即ブロック (誤提出による流出を防ぐ)
_SECRET_BLOCKER_PATTERNS = [
    ('Claude API キー', re.compile(r'sk-ant-[A-Za-z0-9_\-]{16,}')),
    ('AWS アクセスキー', re.compile(r'AKIA[0-9A-Z]{16}')),
    ('GitHub トークン',
     re.compile(r'(?:ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})')),
    ('秘密鍵', re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----')),
]

# 疑わしい記述は警告 (確認の上で続行可)
_SECRET_WARNING_PATTERNS = [
    ('パスワード様の記述',
     re.compile(r'(?i)(?:password|passwd|secret|api_key|apikey)'
                r'\s*[=:]\s*["\'][^"\'\s]{8,}["\']')),
]


def _is_dist_scope(relpath):
    """提出の差分対象となるパスか (開発用ファイル・生成物を除外)."""
    p = relpath.replace(os.sep, '/')
    if p in GENERATED_FILES or p in DIST_EXCLUDE_FILES:
        return False
    if any(seg in JUNK_DIRS for seg in p.split('/')[:-1]):
        return False
    return not any(p.startswith(d) for d in DIST_EXCLUDE_DIRS)


def _is_submittable(relpath, allowed):
    """提出対象となるファイル種類か (拡張子なしは対象)."""
    ext = os.path.splitext(relpath)[1].lower()
    if ext in BLOCKED_EXTENSIONS:
        return False
    return not ext or ext in allowed


def filter_unsupported(changes, config=None):
    """更新情報として扱わない種類のファイル (.bat・.pdf など) を差分から外す.

    ZIP に入っていても提出をエラーにせず、リポジトリ側は基点の内容を
    維持する (「フォルダ丸ごと ZIP で OK」を種類の面でも成立させる)。
    changes を書き換え、除外した相対パスのリストを返す。
    """
    mgr = (config or {}).get('manager') or {}
    allowed = set(mgr.get('allowed_extensions') or DEFAULT_ALLOWED_EXTENSIONS)
    skipped = []
    for key in ('added', 'modified', 'deleted'):
        kept = []
        for rel in changes[key]:
            (kept if _is_submittable(rel, allowed) else skipped).append(rel)
        changes[key] = kept
    return sorted(skipped)


def _walk_files(root):
    """root 以下の全ファイルの相対パス (/ 区切り) を返す."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in JUNK_DIRS]
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, '/')
            out.append(rel)
    return sorted(out)


def workrepo_dir(config=None):
    return os.path.join(paths.install_root(config), 'workrepo')


# ---------------------------------------------------------------------------
# 1. ZIP の検査
# ---------------------------------------------------------------------------

def inspect_zip(zip_path):
    """ZIP を展開し version.json から基点を特定する.

    戻り値: dict(tmp, extract_dir, base_version, base_commit)
    呼び出し側は使用後に cleanup() すること。
    """
    tmp = tempfile.mkdtemp(prefix='mgtkit_submit_')
    extract_dir = os.path.join(tmp, 'zip')
    try:
        installer.extract_zip(zip_path, extract_dir)
    except installer.InstallError as e:
        safeio.rmtree(tmp)
        # 提出は利用者が選んだ ZIP なので「取得した ZIP」とは言わない。
        # 原因 (空き容量・パスの長さ等) は installer が文言に入れている
        log.exception('提出 ZIP を開けませんでした: %s', zip_path)
        raise SubmitError(str(e)) from e

    info = versions.read_version_json(extract_dir)
    if info is None:
        safeio.rmtree(tmp)
        raise SubmitError(
            'この ZIP には版の情報 (version.json) が含まれていないか、'
            '壊れています。マネージャーで取得した版のフォルダを丸ごと ZIP に'
            'して提出してください。')
    commit = str(info.get('commit') or '').strip()
    if not commit:
        safeio.rmtree(tmp)
        raise SubmitError(
            '版の情報 (version.json) に基点の記録がありません。'
            'マネージャーで取得した版を基に作業してください。')
    return {'tmp': tmp, 'extract_dir': extract_dir,
            'base_version': info.get('version', '?'), 'base_commit': commit}


# ---------------------------------------------------------------------------
# 2. 差分計算
# ---------------------------------------------------------------------------

def compute_changes(workrepo, base_commit, extract_dir):
    """基点コミットと ZIP の中身の差分を求める.

    戻り値: dict(added, modified, deleted, unchanged) — いずれも相対パスのリスト
    """
    try:
        run_git(['cat-file', '-e', base_commit + '^{commit}'], cwd=workrepo)
    except GitError:
        raise SubmitError(
            'この ZIP の基点となる版がリポジトリの履歴に見つかりません。'
            'マネージャーで配布された版を基に作業したか確認してください。')

    base_files = [
        p for p in run_git(['ls-tree', '-r', '--name-only', base_commit],
                           cwd=workrepo).splitlines()
        if p.strip() and _is_dist_scope(p)]
    zip_files = [p for p in _walk_files(extract_dir) if _is_dist_scope(p)]

    base_set = set(base_files)
    added, modified, unchanged = [], [], []
    for rel in zip_files:
        with open(os.path.join(extract_dir, rel.replace('/', os.sep)),
                  'rb') as f:
            new_data = f.read()
        if rel not in base_set:
            added.append(rel)
            continue
        # 内容比較は git の blob ハッシュ同士で行う (改行変換の影響を受けない)
        if _blob_sha(workrepo, base_commit, rel) == _hash_bytes(new_data):
            unchanged.append(rel)
        else:
            modified.append(rel)
    deleted = sorted(base_set - set(zip_files))
    return {'added': added, 'modified': modified, 'deleted': deleted,
            'unchanged': unchanged}


def _blob_sha(workrepo, commit, rel):
    return run_git(['rev-parse', '%s:%s' % (commit, rel)],
                   cwd=workrepo).strip()


def _hash_bytes(data):
    import hashlib
    h = hashlib.sha1()
    h.update(b'blob %d\0' % len(data))
    h.update(data)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 3. 安全チェック
# ---------------------------------------------------------------------------

def safety_check(changes, extract_dir, config=None):
    """提出内容の安全チェック.

    戻り値: dict(blockers=[中断理由], warnings=[確認の上続行可の警告])
    """
    mgr = (config or {}).get('manager') or {}
    max_mb = float(mgr.get('max_upload_mb', 50))
    max_files = int(mgr.get('max_upload_files', 500))

    blockers, warnings = [], []
    target_files = changes['added'] + changes['modified']

    # 対象外の種類 (実行ファイル・PDF など) は filter_unsupported() で
    # 差分から除外済みのため、ここでの拡張子チェックは不要

    # サイズ・件数上限
    total = 0
    for rel in target_files:
        total += os.path.getsize(
            os.path.join(extract_dir, rel.replace('/', os.sep)))
    if total > max_mb * 1024 * 1024:
        blockers.append('変更ファイルの合計サイズが上限 (%.0f MB) を超えています'
                        % max_mb)
    if len(target_files) > max_files:
        blockers.append('変更ファイル数が上限 (%d 件) を超えています' % max_files)

    # 秘密情報スキャン (明確な認証情報は即ブロック、疑わしい記述は警告)
    for rel in target_files:
        path = os.path.join(extract_dir, rel.replace('/', os.sep))
        try:
            with open(path, encoding='utf-8', errors='ignore') as f:
                text = f.read()
        except OSError:
            continue
        for label, pattern in _SECRET_BLOCKER_PATTERNS:
            if pattern.search(text):
                blockers.append('%s が含まれているため提出できません: %s '
                                '(該当箇所を削除してください)' % (label, rel))
        for label, pattern in _SECRET_WARNING_PATTERNS:
            if pattern.search(text):
                warnings.append('%s らしき記述があります: %s' % (label, rel))

    # requirements.txt の変更は明示 (承認時の判断材料)
    if 'requirements.txt' in changes['modified'] + changes['added']:
        warnings.append('必要ライブラリ (requirements.txt) が変更されています。'
                        '新規パッケージの追加は承認時に確認されます。')
    return {'blockers': blockers, 'warnings': warnings}


# ---------------------------------------------------------------------------
# 4. 準備 (UI へ確認内容を返す)
# ---------------------------------------------------------------------------

def prepare_submission(zip_path, config=None, workrepo=None,
                       on_progress=None):
    """提出の準備: 展開・基点特定・差分・安全チェックまで.

    戻り値 prep dict。blockers が空なら UI で削除確認・警告確認の後
    finalize_submission(prep, ...) を呼ぶ。中断時も cleanup(prep) を呼ぶこと。
    """
    def progress(msg):
        log.info('%s', msg)
        if on_progress:
            on_progress(msg)

    progress('ZIP を確認しています...')
    prep = inspect_zip(zip_path)
    try:
        progress('最新のリポジトリ情報を取得しています...')
        if workrepo is None:
            workrepo = ensure_work_repo(paths.repo_slug(config),
                                        workrepo_dir(config))
        prep['workrepo'] = workrepo

        progress('基点 %s からの変更点を調べています...'
                 % prep['base_version'])
        prep['changes'] = compute_changes(workrepo, prep['base_commit'],
                                          prep['extract_dir'])
        prep['skipped'] = filter_unsupported(prep['changes'], config)
        ch = prep['changes']
        if not (ch['added'] or ch['modified'] or ch['deleted']):
            if prep['skipped']:
                raise SubmitError(
                    '変更が見つかったのは提出対象外の種類のファイルだけ'
                    'でした (%s)。コードやデータの変更を含めて提出して'
                    'ください。' % '、'.join(prep['skipped'][:5]))
            raise SubmitError('基点の版から変更されたファイルがありません。')

        progress('安全チェックを実行しています...')
        prep['safety'] = safety_check(ch, prep['extract_dir'], config)
        return prep
    except Exception:
        cleanup(prep)
        raise


def cleanup(prep):
    """展開に使った一時フォルダを片付ける (消えたら True).

    提出者の ZIP をそのまま展開した中身なので、クローン (.git) が
    混ざっていることがある。git は Windows でクローンの中身に読み取り
    専用属性を付けるため、素の rmtree では消しきれない。
    """
    return safeio.rmtree(prep.get('tmp', ''))


# ---------------------------------------------------------------------------
# 5. 確定 (ブランチ → commit → push → PR)
# ---------------------------------------------------------------------------

def _next_branch_name(workrepo, user):
    """feature/{ユーザー名}-{YYYYMMDD}-{連番}."""
    today = datetime.date.today().strftime('%Y%m%d')
    prefix = 'feature/%s-%s-' % (user, today)
    out = run_git(['ls-remote', '--heads', 'origin', prefix + '*'],
                  cwd=workrepo)
    seq = 0
    for line in out.splitlines():
        name = line.split('refs/heads/')[-1].strip()
        m = re.match(re.escape(prefix) + r'(\d+)$', name)
        if m:
            seq = max(seq, int(m.group(1)))
    return '%s%d' % (prefix, seq + 1)


def _diff_summary(changes, intentional_deletions):
    lines = []
    for rel in changes['added']:
        lines.append('追加: %s' % rel)
    for rel in changes['modified']:
        lines.append('変更: %s' % rel)
    for rel in intentional_deletions:
        lines.append('削除: %s' % rel)
    return '\n'.join(lines)


def _split_sections(body):
    """PR 本文を「# / ## 見出し」ごとに分ける。### 以下は本文側に含める.

    戻り値: [(見出しの # の数, 見出し, 本文)] を出現順に。
    見出しより前の文章は ('', '', 本文) として先頭に入る。
    """
    out = []
    level, title, lines = 0, '', []
    for line in (body or '').replace('\r\n', '\n').split('\n'):
        m = re.match(r'^(#{1,2})\s+(.+?)\s*$', line)
        if m:
            if title or lines:
                out.append((level, title, '\n'.join(lines).strip()))
            level, title, lines = len(m.group(1)), m.group(2), []
        else:
            lines.append(line)
    if title or lines:
        out.append((level, title, '\n'.join(lines).strip()))
    return out


def pr_body_sections(body):
    """PR 本文を {見出し: 本文} にする (同じ見出しは最初のものを採る)."""
    sections = {}
    for _level, title, content in _split_sections(body):
        if title and title not in sections:
            sections[title] = content
    return sections


# PR タイトルの最大長 (GitHub の一覧で切れずに読める範囲)。承認タブの
# 見出しと、正式版のリリースノートの 1 行目に出る
TITLE_MAX = 70

# タイトルに使う行の頭から落とす箇条書きの記号 ("-30%" のような書き出しを
# 壊さないよう、ハイフン・アスタリスクは後ろに空白がある場合だけ落とす)
_BULLET = re.compile(r'^\s*(?:[-*]\s+|[・･]\s*)')


def title_line(text):
    """複数行の文章から PR タイトル用の 1 行を作る.

    最初の中身のある行を採り、箇条書きの記号を落として TITLE_MAX で切る。
    空文字なら '' (呼び出し側が既定のタイトルへ落とす)。
    """
    for line in (text or '').splitlines():
        line = _BULLET.sub('', line).strip()
        if line:
            return line[:TITLE_MAX]
    return ''


def split_body_title(body):
    """本文の先頭にある「# タイトル」行を (タイトル, 残り) に分ける.

    generate_pr_body は 1 行目にタイトルを書く (API 呼び出しを提出
    1 回につき 1 回にするため、タイトル専用の生成はしない)。PR 本文には
    タイトル欄が別にあるので、本文からはこの行を抜いて使う。
    無ければ ('', 全文)。
    """
    lines = (body or '').lstrip().splitlines()
    if lines and lines[0].startswith('# '):
        return (lines[0][2:].strip(),
                '\n'.join(lines[1:]).lstrip('\n'))
    return '', (body or '')


def user_sections(body):
    """PR 本文から利用者向けの 2 項目 (更新内容, 制限事項) を取り出す.

    リリースノートに転載されるのはこの 2 項目だけ (reviews.release_notes_from_pr)。
    制限事項は見出しの表記ゆれを許容するため部分一致で拾う。
    """
    sections = pr_body_sections(body)
    limits = next((v for k, v in sections.items() if LIMITS_KEY in k), '')
    return sections.get(UPDATE_KEY, '').strip(), (limits or '').strip()


def body_with_user_sections(body, update_text, limitations):
    """PR 本文の利用者向け 2 項目だけを差し替える (他の節は順序ごと残す).

    自動作成した本文を提出者が手直ししたときに使う。
    """
    head = _user_sections_text(update_text, limitations)
    rest = []
    for level, title, content in _split_sections(body):
        if not title:
            continue
        if title == UPDATE_KEY or LIMITS_KEY in title:
            continue
        rest.append('%s %s\n\n%s' % ('#' * (level or 2), title, content))
    return '\n'.join(head + ['']) + '\n'.join(rest)


def _user_sections_text(update_text, limitations):
    """様式どおりの「## 更新内容」「## ご利用にあたっての制限事項」の行."""
    update = (update_text or '').strip()
    limits = (limitations or '').strip()
    if not update:
        return ['## %s' % LIMITS_KEY_FULL, '', limits, ''] if limits else []
    return ['## %s' % UPDATE_KEY, '', update, '',
            '## %s' % LIMITS_KEY_FULL, '', limits or '- なし', '']


def fallback_pr_body(update_text, limitations, base_version, summary):
    """手書きの内容 (または空欄) から API を使わず PR 本文を組み立てる.

    reviews.release_notes_from_pr がこの様式から正式版のリリースノートを
    機械抽出するため、手書きの提出でも節の構成を自動生成と揃える。
    更新内容が空欄なら「## 更新内容」の節を作らない (リリースノートも
    空欄になる。管理者の指示 2026-08)。
    基点の機械可読な記録は finalize_submission が本文の先頭へ付けるため、
    ここの「(基点: %s)」は人が読むための表示 (versions.base_marker 参照)。
    """
    parts = _user_sections_text(update_text, limitations)
    parts += ['## 変更ファイルの説明', '',
              'マネージャー経由の提出です (基点: %s)。' % base_version, '',
              '```', summary, '```']
    return '\n'.join(parts) + '\n'


def finalize_submission(prep, intentional_deletions, commit_message='',
                        config=None, on_progress=None, existing_branch=None,
                        limitations='', use_ai=False, on_review=None,
                        title=''):
    """準備済みの提出を確定する.

    intentional_deletions: 「意図的な削除」とユーザーが確認したファイル。
    それ以外の削除候補 (入れ忘れ) は基点の内容を維持する。
    existing_branch: 指定すると新規 PR を作らず、既存の提出 (同一 PR) に
    修正版として積む (差し戻し後の再提出フロー)。
    limitations: 提出者が手書きした「ご利用にあたっての制限事項」(任意)。
    use_ai: True なら提出のまとめ (コミットメッセージ・PR 本文) を Claude で
    自動生成する (API 使用料は提出者負担のため、UI で本人が選んだときのみ
    True にする)。False なら手書きの内容から API を使わず組み立てる。
    on_review: 自動生成した「タイトル」「更新内容」「制限事項」を提出者に
    見せて直させるための関数 (title, update, limits) -> 同じ 3 つ組 / None。
    この 3 項目はそのまま正式版のリリースノートになるため、本人が一度も
    読まないまま公開されないようにする (管理者の指示 2026-08)。None を
    返したら SubmitCancelled を送出する (まだ push していないので副作用は
    残らない)。
    title: 提出者が書いたタイトル (任意)。空なら更新内容の 1 行目を使い、
    それも無ければ「vX.Y を基点とした機能追加の提出」に落とす。
    use_ai のときは自動生成が失敗した時点で claude_helper.ClaudeError を
    そのまま上げる (黙って定型文で提出すると、更新内容が空のまま正式版まで
    進んでしまうため。管理者の指示 2026-08)。
    戻り値: dict(pr_url, branch, commit_message)
    """
    def progress(msg):
        log.info('%s', msg)
        if on_progress:
            on_progress(msg)

    workrepo = prep['workrepo']
    changes = prep['changes']
    intentional = [d for d in intentional_deletions
                   if d in changes['deleted']]
    try:
        progress('提出用の作業場所を準備しています...')
        # 前回の強制終了の残骸 (index.lock・半端な作業ツリー) が残って
        # いると checkout -B が拒否される。書き込みを始める前に戻す
        reset_work_tree(workrepo)
        user = ghcli.run_gh(['api', 'user', '--jq', '.login']).strip()
        if existing_branch:
            branch = existing_branch
            run_git(['fetch', 'origin', branch], cwd=workrepo, timeout=300)
            run_git(['checkout', '-B', branch, 'origin/%s' % branch],
                    cwd=workrepo)
        else:
            branch = _next_branch_name(workrepo, user)
            run_git(['checkout', '-B', branch, prep['base_commit']],
                    cwd=workrepo)

        progress('変更を取り込んでいます...')
        for rel in changes['added'] + changes['modified']:
            src = os.path.join(prep['extract_dir'], rel.replace('/', os.sep))
            dst = os.path.join(workrepo, rel.replace('/', os.sep))
            os.makedirs(os.path.dirname(dst) or workrepo, exist_ok=True)
            shutil.copyfile(src, dst)
        for rel in intentional:
            target = os.path.join(workrepo, rel.replace('/', os.sep))
            if os.path.isfile(target):
                os.remove(target)
        run_git(['add', '-A'], cwd=workrepo)

        staged = run_git(['diff', '--cached', '--name-only'], cwd=workrepo)
        if not staged.strip():
            raise SubmitError('基点の版から変更されたファイルがありません。')

        summary = _diff_summary(changes, intentional)
        diff_text = run_git(['diff', '--cached'], cwd=workrepo)

        notes = ''
        if prep['safety']['warnings']:
            notes = ('# 提出時の警告 (承認時に確認)\n- '
                     + '\n- '.join(prep['safety']['warnings']))
        # タイトルも本文も送信の前に用意する。自動作成のときは提出者に
        # 見せて直させるので、ここで取り消されても push 済みのブランチが
        # 残らない
        update_text = (commit_message or '').strip()
        title_text = title_line(title) or title_line(update_text)
        body = None
        if use_ai:
            # API 呼び出しは提出 1 回につきこの 1 回だけ。タイトルも
            # 本文の 1 行目 (# 行) としてまとめて書かせて取り出す
            # 一番長く待たされる区間 (API 呼び出し)。提出者が選んだ
            # 「Claude で自動作成する」の実行中だと分かる言葉にする
            progress('Claude が更新内容を作成しています... '
                     '(数十秒かかることがあります)')
            body = claude_helper.generate_pr_body(
                summary, diff_text, prep['base_version'], notes, strict=True)
            drafted, body = split_body_title(body)
            title_text = title_line(drafted) or title_text
            update_text, limits_text = user_sections(body)
            if on_review:
                reviewed = on_review(title_text, update_text, limits_text)
                if reviewed is None:
                    raise SubmitCancelled('提出を取り消しました。')
                title_text = title_line(reviewed[0]) or title_text
                update_text, limits_text = reviewed[1], reviewed[2]
                body = body_with_user_sections(body, update_text, limits_text)
        if not body:
            body = fallback_pr_body(update_text, limitations,
                                    prep['base_version'], summary)
        # 提出の基点 (提出者が取得した版) を機械可読で残す。過去の更新ログの
        # 図はこれを読む。自動生成した本文には版名が入る保証がないため、
        # 本文の作り方によらず必ず先頭に付ける
        body = '%s\n%s' % (versions.base_marker(prep['base_version'],
                                                prep['base_commit']), body)
        if notes:
            body += '\n\n' + notes
        # タイトルは PR の見出し (承認タブ) と正式版のリリースノートの
        # 1 行目になる。コミットメッセージは「タイトル + 空行 + 更新内容」
        title = title_text or ('%s を基点とした機能追加の提出'
                               % prep['base_version'])
        message = '%s\n\n%s' % (title, update_text) if update_text else title

        progress('変更を記録しています...')
        run_git(['-c', 'user.name=%s' % user,
                 '-c', 'user.email=%s@users.noreply.github.com' % user,
                 'commit', '-m', message], cwd=workrepo)

        progress('GitHub へ送信しています...')
        run_git(['push', '-u', 'origin', branch], cwd=workrepo, timeout=300)

        if existing_branch:
            # 既存 PR に修正版として積む (新規 PR は作らない)。
            # 本文 (ファイル別説明を含む) は最新の提出内容で更新する
            try:
                ghcli.run_gh(['pr', 'edit', branch,
                              '--repo', paths.repo_slug(config),
                              '--body', body])
            except ghcli.GhError:
                log.warning('PR 本文の更新に失敗しました (提出自体は完了)')
            out = ghcli.run_gh([
                'pr', 'list', '--repo', paths.repo_slug(config),
                '--head', branch, '--state', 'open', '--json', 'url',
                '--jq', '.[0].url'])
            pr_url = out.strip() or '(既存の提出)'
        else:
            pr_url = ghcli.run_gh([
                'pr', 'create', '--repo', paths.repo_slug(config),
                '--base', (config or {}).get('base_branch', 'main'),
                '--head', branch, '--title', title, '--body', body,
            ]).strip().splitlines()[-1]
        return {'pr_url': pr_url, 'branch': branch,
                'commit_message': message}
    finally:
        cleanup(prep)
