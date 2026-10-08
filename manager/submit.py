# -*- coding: utf-8 -*-
"""提出タブの中核ロジック (UI 非依存).

ユーザー操作は「ZIP を選んでアップロード」だけ。裏側で
  1. ZIP 展開 → version.json から基点コミットを特定
  2. 基点との差分計算 (追加/変更/削除)
  3. 提出対象外ファイルの除外 (実行ファイル・PDF などは受け取っても
     差分に含めず、基点の内容を維持する)
  4. 古い書き方のタブの登録を新しい書き方に直す (manager/tabconv.py。
     直せないときはそのまま提出し、警告として知らせる)
  5. 安全チェック (サイズ/件数上限・秘密情報スキャン)
  6. feature ブランチ作成 → 上書き → commit → push → PR 作成
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

from . import (claude_helper, ghcli, installer, paths, safeio, tabconv,
               versions)
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
# release.yml で ZIP から外すものは必ずここにも入れる (入れ忘れると、ZIP に
# 無いだけのファイルが提出で「削除」に見える。test_manager_submit が照合する)。
# .claude/ は管理者向けの CLAUDE.md・スキル (配布 ZIP からも外す)
DIST_EXCLUDE_DIRS = ('.github/', 'tests/', 'docs/', 'scripts/', 'manager/',
                     '.claude/')
DIST_EXCLUDE_FILES = ('.gitignore', 'pytest.ini', 'requirements-dev.txt',
                      'CLAUDE.md', '.gitattributes')

# メンバー向けの規約 (本体直下の CLAUDE.md。配布 ZIP にも入れる)。
# 上の DIST_EXCLUDE_FILES で提出の差分からは外れるが、ZIP の中で書き換えられて
# いたら「外した」ことを画面とログで知らせる (提出自体は止めない。管理者の
# 方針 2026-10「変な使い方のときだけはじく・更新意欲をそがない」)
RULE_FILES = ('CLAUDE.md',)

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
            'version.json が書き換えられていないか確かめ、マネージャーで'
            '取得した版のフォルダにある version.json をそのまま入れて ZIP を'
            '作り直してください。')
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
            'この ZIP の版の情報 (version.json) が、配布された版のどれとも'
            '一致しません。version.json を書き換えずに、マネージャーで取得'
            'した版のフォルダにある version.json をそのまま入れて ZIP を'
            '作り直してください。')

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


def changed_rule_files(workrepo, base_commit, extract_dir):
    """ZIP の中で書き換えられていた規約のファイル (RULE_FILES) を返す.

    規約は提出の差分の対象外 (_is_dist_scope) なので送られないが、黙って
    外すと本人は通ったと思う。外したものを知らせるために使う。
    ZIP に入っていない (従来の配布 ZIP) ときは何も言わない。
    """
    out = []
    for rel in RULE_FILES:
        path = os.path.join(extract_dir, rel)
        if not os.path.isfile(path):
            continue
        with open(path, 'rb') as f:
            data = f.read()
        line = run_git(['ls-tree', base_commit, '--', rel],
                       cwd=workrepo).split()
        base_sha = line[2] if len(line) >= 3 else None
        if base_sha != _hash_bytes(data):
            out.append(rel)
    return out


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
        prep['rules_excluded'] = changed_rule_files(
            workrepo, prep['base_commit'], prep['extract_dir'])
        if prep['rules_excluded']:
            log.info('規約のファイルの変更を提出から外しました: %s',
                     ', '.join(prep['rules_excluded']))
        prep['tabconv'] = _convert_old_style(prep, config, progress)
        ch = prep['changes']
        if not (ch['added'] or ch['modified'] or ch['deleted']):
            if prep['rules_excluded']:
                raise SubmitError(
                    '変更が見つかったのはメンバー向けの規約 (%s) だけでした。'
                    '規約は提出では変えられません (変えたいときは管理者に'
                    '相談してください)。' % '、'.join(prep['rules_excluded']))
            if prep['skipped']:
                raise SubmitError(
                    '変更が見つかったのは提出対象外の種類のファイルだけ'
                    'でした (%s)。コードやデータの変更を含めて提出して'
                    'ください。' % '、'.join(prep['skipped'][:5]))
            raise SubmitError('基点の版から変更されたファイルがありません。')

        prep['split'] = _plan_split(prep, progress)

        progress('安全チェックを実行しています...')
        prep['safety'] = safety_check(ch, prep['extract_dir'], config)
        # 古い書き方を直せなかった・一部を外したときは、確認画面の警告と
        # PR 本文の「提出時の警告」に出す (黙って捨てない)
        prep['safety']['warnings'].extend(prep['tabconv']['warnings'])
        for rel in prep['rules_excluded']:
            prep['safety']['warnings'].append(
                '本体直下の %s (メンバー向けの規約) への変更は、提出に含めずに'
                '外しました (ほかの変更はそのまま提出できます)。規約を'
                '変えたいときは管理者に相談してください。' % rel)
        return prep
    except Exception:
        cleanup(prep)
        raise


def _plan_split(prep, progress):
    """タブごとに分けて出す案 (manager/splitplan.py)。分けられなければ None.

    案づくりの不具合で提出そのものを止めないよう、想定外の例外はログに
    残して「分けずに 1 本で出す」に倒す。
    """
    from . import splitplan
    progress('タブごとに分けられるかを調べています...')
    try:
        result = splitplan.plan(prep['workrepo'], prep['base_commit'],
                                prep['extract_dir'], prep['changes'])
    except Exception:
        log.exception('分け方の案を作れませんでした (1 本で提出します)')
        return None
    return result if result['splittable'] else None


def _convert_old_style(prep, config, progress):
    """古い書き方のタブの登録 (app.py / index.html / app.js への追記) を直す.

    直せたら展開先のファイルを書き換えて差分を計算し直す。直せない・
    直すものが無いときは展開先に触らない。変換の不具合で提出そのものを
    止めないよう、想定外の例外はログに残して「そのまま提出」に倒す。
    """
    if not any(h in prep['changes']['modified'] for h in tabconv.HUB_FILES):
        return {'status': 'none', 'warnings': [], 'report': [],
                'needs_merge': False}
    progress('タブの登録の書き方を確かめています...')
    target = 'origin/%s' % (config or {}).get('base_branch', 'main')
    try:
        result = tabconv.plan(prep['workrepo'], prep['base_commit'],
                              prep['extract_dir'], prep['changes'], target)
        if result['status'] == 'converted':
            tabconv.apply(result, prep['extract_dir'])
            prep['changes'] = compute_changes(
                prep['workrepo'], prep['base_commit'], prep['extract_dir'])
            prep['skipped'] = filter_unsupported(prep['changes'], config)
        return result
    except SubmitError:
        raise
    except Exception:
        log.exception('タブの登録を直す処理に失敗しました (そのまま提出します)')
        return {'status': 'kept', 'report': [], 'needs_merge': False,
                'warnings': ['タブの登録の書き方を確かめる処理でエラーが'
                             '起きたため、そのまま提出します (原因はログ '
                             'manager.log に残っています。管理者に知らせて'
                             'ください)。']}


def _merge_latest_for_converted_tabs(workrepo, user, config, progress):
    """直したタブが β版の画面に出るよう、最新版をこの提出に取り込む.

    基点 (提出者が取得した版) が見出し・自動登録より前の版だと、見出しに
    直したタブはその版のままでは画面に出ない (β版は提出の中身で作る)。
    最新版の取り込み (衝突解決の「最新版を取り込み」と同じ操作) を
    ぶつからない場合に限って行う。ぶつかるときは取り込まずに続け、
    従来どおり承認タブの衝突解決に任せる。
    戻り値: 取り込めたら True。
    """
    base = (config or {}).get('base_branch', 'main')
    progress('最新版を取り込んでいます...')
    try:
        run_git(['fetch', 'origin', base], cwd=workrepo, timeout=300)
        run_git(['-c', 'user.name=%s' % user,
                 '-c', 'user.email=%s@users.noreply.github.com' % user,
                 'merge', '--no-ff', '--no-edit', '-m',
                 '最新版 (%s) を取り込み: タブの見出しに直したタブを画面に出すため'
                 % base, 'origin/%s' % base], cwd=workrepo)
        return True
    except GitError:
        log.warning('最新版の取り込みはぶつかるため行いません '
                    '(提出はそのまま続けます)', exc_info=True)
        try:
            run_git(['merge', '--abort'], cwd=workrepo)
        except GitError:
            log.warning('取り込みの取り消しに失敗しました', exc_info=True)
        return False


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


def _base_notes(prep):
    """PR 本文の末尾に付ける注記 (古い書き方を直した箇所・提出時の警告)."""
    notes = ''
    conv = prep.get('tabconv') or {}
    if conv.get('report'):
        # 古い書き方を直した箇所 (承認する人が差分と見比べるため)
        notes = '\n'.join(conv['report'])
    if prep['safety']['warnings']:
        notes += ('\n\n' if notes else '') + (
            '# 提出時の警告 (承認時に確認)\n- '
            + '\n- '.join(prep['safety']['warnings']))
    return notes


def _compose(prep, summary, diff_text, notes, commit_message, limitations,
             use_ai, on_review, title, progress, part=None, marker=''):
    """タイトル・PR 本文・コミットメッセージを作る.

    part (分けた提出の何本目か) があれば、自動作成の確認画面に渡す。
    marker は基点の印のすぐ後ろに置く印 (分けた提出の束の印など)。
    戻り値: (タイトル, 本文, コミットメッセージ, 更新内容)
    """
    # タイトルも本文も送信の前に用意する。自動作成のときは提出者に
    # 見せて直させるので、ここで取り消されても push 済みのブランチが
    # 残らない
    update_text = (commit_message or '').strip()
    title_text = title_line(title) or title_line(update_text)
    body = None
    if use_ai:
        # API 呼び出しは提出 1 本につきこの 1 回だけ。タイトルも
        # 本文の 1 行目 (# 行) としてまとめて書かせて取り出す
        # 一番長く待たされる区間 (API 呼び出し)。提出者が選んだ
        # 「Claude で自動作成する」の実行中だと分かる言葉にする
        progress('Claude が更新内容を作成しています... '
                 '(数十秒かかることがあります)' if part is None else
                 'Claude が更新内容を作成しています (%d/%d 本目)... '
                 '(数十秒かかることがあります)' % (part['k'], part['n']))
        body = claude_helper.generate_pr_body(
            summary, diff_text, prep['base_version'], notes, strict=True)
        drafted, body = split_body_title(body)
        title_text = title_line(drafted) or title_text
        update_text, limits_text = user_sections(body)
        if on_review:
            if part is None:
                reviewed = on_review(title_text, update_text, limits_text)
            else:
                reviewed = on_review(title_text, update_text, limits_text,
                                     part)
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
    head = versions.base_marker(prep['base_version'], prep['base_commit'])
    if marker:
        head += '\n' + marker
    body = '%s\n%s' % (head, body)
    if notes:
        body += '\n\n' + notes
    # タイトルは PR の見出し (承認タブ) と正式版のリリースノートの
    # 1 行目になる。コミットメッセージは「タイトル + 空行 + 更新内容」
    title = title_text or ('%s を基点とした機能追加の提出'
                           % prep['base_version'])
    message = '%s\n\n%s' % (title, update_text) if update_text else title
    return title, body, message, update_text


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

        conv = prep.get('tabconv') or {}
        title, body, message, update_text = _compose(
            prep, summary, diff_text, _base_notes(prep), commit_message,
            limitations, use_ai, on_review, title, progress)

        progress('変更を記録しています...')
        run_git(['-c', 'user.name=%s' % user,
                 '-c', 'user.email=%s@users.noreply.github.com' % user,
                 'commit', '-m', message], cwd=workrepo)
        if (conv.get('status') == 'converted' and conv.get('needs_merge')
                # 修正版の提出で、前回もう取り込んであれば要らない
                and not tabconv.has_auto_registration(workrepo, 'HEAD')):
            _merge_latest_for_converted_tabs(workrepo, user, config, progress)

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


# ---------------------------------------------------------------------------
# 6. タブごとに分けて出す (manager/splitplan.py の案に沿って)
# ---------------------------------------------------------------------------

SPLIT_MARKER = '<!-- mgtkit-split %s %d/%d -->'
_SPLIT_MARKER_RE = re.compile(r'<!-- mgtkit-split (\S+) (\d+)/(\d+) -->')
_SPLIT_LIST = '<!-- mgtkit-split-list -->'


def split_from_body(body):
    """本文の束の印 → dict(bundle, k, n) / 無ければ None."""
    m = _SPLIT_MARKER_RE.search(body or '')
    if not m:
        return None
    return {'bundle': m.group(1), 'k': int(m.group(2)), 'n': int(m.group(3))}


def _split_notes(units, k, shared, numbers=None):
    """分けた提出の本文に付ける説明 (承認する人向け)."""
    n = len(units)
    lines = ['# 分けて出した提出',
             '- この更新版は %d 本に分けて出したうちの %d 本目です '
             '(タブごとに分けています)。どの順に承認しても構いません。'
             % (n, k),
             _SPLIT_LIST]
    for j, u in enumerate(units, 1):
        num = (numbers or {}).get(j)
        lines.append('  - %d/%d: %s%s%s' % (
            j, n, u['label'], (' (#%s)' % num) if num else '',
            ' ← この提出' if j == k else ''))
    lines.append(_SPLIT_LIST)
    if shared:
        lines.append('- この提出だけでも動くように、ほかの提出と同じ変更を'
                     '入れたファイル (同じ変更どうしなので、取り込みで'
                     'ぶつかりません): %s' % '、'.join(shared))
    return '\n'.join(lines)


def _with_split_list(body, units, k, numbers):
    """本文の分けた提出の一覧を、提出番号つきに差し替える."""
    parts = body.split(_SPLIT_LIST)
    if len(parts) != 3:
        return body
    lines = []
    n = len(units)
    for j, u in enumerate(units, 1):
        num = numbers.get(j)
        lines.append('  - %d/%d: %s%s%s' % (
            j, n, u['label'], (' (#%s)' % num) if num else '',
            ' ← この提出' if j == k else ''))
    return (parts[0] + _SPLIT_LIST + '\n' + '\n'.join(lines) + '\n'
            + _SPLIT_LIST + parts[2])


def finalize_split(prep, units, intentional_deletions, commit_message='',
                   config=None, on_progress=None, limitations='',
                   use_ai=False, on_review=None, title=''):
    """準備済みの提出を、units (splitplan.plan の案) ごとに分けて確定する.

    1 本ずつ基点から作業し、全部の本文がそろって (自動作成なら提出者が
    1 本ずつ確かめて) から送る。途中で取り消したり自動作成に失敗したり
    しても、まだ何も送られていない。
    手書きの更新内容は分けた全部に同じものが付く (確認画面でその旨を出す)。
    戻り値: dict(pr_url (1 本目), branch (1 本目), prs=[dict(url, branch,
    title, label)])
    """
    from . import splitplan

    def progress(msg):
        log.info('%s', msg)
        if on_progress:
            on_progress(msg)

    workrepo = prep['workrepo']
    changes = prep['changes']
    intentional = set(d for d in intentional_deletions
                      if d in changes['deleted'])
    units = [u for u in units
             if splitplan.unit_files(u)
             or intentional & set(u.get('deleted') or [])]
    if len(units) < 2:
        raise SubmitError('分けて出せる単位が 1 つしかありません。'
                          'まとめて 1 本で提出してください。')
    n = len(units)
    built = []
    try:
        progress('提出用の作業場所を準備しています...')
        reset_work_tree(workrepo)
        user = ghcli.run_gh(['api', 'user', '--jq', '.login']).strip()
        first = _next_branch_name(workrepo, user)
        prefix, seq = re.match(r'^(.*-)(\d+)$', first).groups()
        seq = int(seq)
        conv = prep.get('tabconv') or {}
        base_notes = _base_notes(prep)
        for k, unit in enumerate(units, 1):
            branch = '%s%d' % (prefix, seq + k - 1)
            progress('%d/%d 本目 (%s) を用意しています...'
                     % (k, n, unit['label']))
            run_git(['checkout', '-B', branch, prep['base_commit']],
                    cwd=workrepo)
            files = splitplan.unit_files(unit)
            for rel in files:
                src = os.path.join(prep['extract_dir'],
                                   rel.replace('/', os.sep))
                dst = os.path.join(workrepo, rel.replace('/', os.sep))
                os.makedirs(os.path.dirname(dst) or workrepo, exist_ok=True)
                shutil.copyfile(src, dst)
            dels = sorted(intentional & set(unit.get('deleted') or []))
            for rel in dels:
                target = os.path.join(workrepo, rel.replace('/', os.sep))
                if os.path.isfile(target):
                    os.remove(target)
            run_git(['add', '-A'], cwd=workrepo)
            staged = run_git(['diff', '--cached', '--name-only'],
                             cwd=workrepo)
            if not staged.strip():
                continue
            part_changes = {
                'added': [r for r in files if r in changes['added']],
                'modified': [r for r in files if r in changes['modified']]}
            summary = _diff_summary(part_changes, dels)
            diff_text = run_git(['diff', '--cached'], cwd=workrepo)
            shared = list(unit.get('shared') or [])
            notes = _split_notes(units, k, shared)
            if base_notes:
                notes += '\n\n' + base_notes
            ttl, body, message, _upd = _compose(
                prep, summary, diff_text, notes, commit_message,
                limitations, use_ai, on_review,
                title or unit['label'], progress,
                part={'k': k, 'n': n, 'label': unit['label']},
                marker=SPLIT_MARKER % (first, k, n))
            if not use_ai and not title_line(title):
                # 手書きのときは、どのタブの提出か分かるタイトルにする
                ttl = ('%s: %s' % (unit['label'],
                                   title_line(commit_message))
                       )[:TITLE_MAX] if title_line(commit_message) else \
                    unit['label'][:TITLE_MAX]
                message = ('%s\n\n%s' % (ttl, (commit_message or '').strip())
                           if (commit_message or '').strip() else ttl)
            run_git(['-c', 'user.name=%s' % user,
                     '-c', 'user.email=%s@users.noreply.github.com' % user,
                     'commit', '-m', message], cwd=workrepo)
            if (conv.get('status') == 'converted' and conv.get('needs_merge')
                    and not tabconv.has_auto_registration(workrepo, 'HEAD')):
                _merge_latest_for_converted_tabs(workrepo, user, config,
                                                 progress)
            built.append({'k': k, 'branch': branch, 'title': ttl,
                          'body': body, 'label': unit['label']})
        if not built:
            raise SubmitError('基点の版から変更されたファイルがありません。')

        prs = []
        for b in built:
            progress('GitHub へ送信しています (%d/%d 本目)...' % (b['k'], n))
            try:
                run_git(['push', '-u', 'origin', b['branch']], cwd=workrepo,
                        timeout=300)
                url = ghcli.run_gh([
                    'pr', 'create', '--repo', paths.repo_slug(config),
                    '--base', (config or {}).get('base_branch', 'main'),
                    '--head', b['branch'], '--title', b['title'],
                    '--body', b['body'],
                ]).strip().splitlines()[-1]
            except (GitError, ghcli.GhError) as e:
                log.exception('分けた提出の %d 本目の送信に失敗しました',
                              b['k'])
                done = ''.join('\n- %s' % p['url'] for p in prs)
                raise SubmitError(
                    '%d 本目 (%s) を送れませんでした: %s%s' % (
                        b['k'], b['label'], e,
                        ('\nここまでに提出できたもの:' + done) if done
                        else '\nまだ何も提出されていません。')) from e
            prs.append({'url': url, 'branch': b['branch'],
                        'title': b['title'], 'label': b['label'],
                        'k': b['k'], 'body': b['body']})

        # 本文の一覧に提出番号を書き足す (失敗しても提出自体は済んでいる)
        numbers = {}
        for p in prs:
            m = re.search(r'/pull/(\d+)', p['url'])
            if m:
                numbers[p['k']] = m.group(1)
        if numbers:
            progress('提出どうしの番号を書き添えています...')
            for p in prs:
                body = _with_split_list(p['body'], units, p['k'], numbers)
                if body == p['body']:
                    continue
                try:
                    ghcli.run_gh(['pr', 'edit', p['branch'],
                                  '--repo', paths.repo_slug(config),
                                  '--body', body])
                except ghcli.GhError:
                    log.warning('分けた提出の本文に番号を書き足せませんでした'
                                ' (提出自体は完了): %s', p['url'])
        return {'pr_url': prs[0]['url'], 'branch': prs[0]['branch'],
                'prs': [{k: p[k] for k in ('url', 'branch', 'title',
                                           'label')} for p in prs]}
    finally:
        cleanup(prep)
