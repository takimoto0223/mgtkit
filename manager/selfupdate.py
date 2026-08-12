# -*- coding: utf-8 -*-
"""起動時の自動最新化 (mgtkit フォルダの直接変更の退避つき).

mgtkit フォルダ (リポジトリ一式) 内のファイルを直接編集してしまうと、
更新 (pull) が上書きを拒んで止まり、本人が気づかないまま全体が
古いままになる。これを防ぐため、起動時に直接変更を見つけたら

  1. 見える退避フォルダ <リポジトリ>/stash/日時/ に実ファイルと
     差分ビューワ HTML をコピーして退避
  2. 手元の変更を破棄して最新版へ更新
  3. UI が注意ダイアログで退避先と「詳細 (差分)」を案内

する。リポジトリ管理外のファイル (メンバーの mgt データ・計算結果
など) には一切触れない。ローカルコミットがある場合 (管理者の開発
クローン想定) は何もしない。
"""
import datetime
import html
import logging
import os
import shutil

from . import paths
from .gitcli import GitError, run_git

log = logging.getLogger(__name__)

STASH_DIR_NAME = 'stash'


def _changed_files(root):
    """リポジトリ管理下で直接変更されたファイルの相対パス一覧."""
    out = run_git(['status', '--porcelain'], cwd=root)
    files = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        status, p = line[:2], line[3:].strip()
        if status == '??':          # 管理外 = 退避も破棄もしない
            continue
        if ' -> ' in p:             # リネームは新パス側
            p = p.split(' -> ')[-1]
        files.append(p.strip('"'))
    return files


def _head_lines(root, rel):
    try:
        text = run_git(['show', 'HEAD:%s' % rel], cwd=root)
    except GitError:
        return None
    return text.splitlines()


def _working_lines(root, rel):
    path = os.path.join(root, rel.replace('/', os.sep))
    if not os.path.isfile(path):
        return []                   # 直接削除された → 右側は空
    try:
        with open(path, encoding='utf-8') as f:
            return f.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return None


def _build_diff_html(root, files, stamp):
    """退避した直接変更の差分ビューワ (差分ボタンと同じ見た目) を作る."""
    from . import diffview
    sections = []
    for rel in files:
        old = _head_lines(root, rel)
        new = _working_lines(root, rel)
        title = html.escape('%s/%s' % (diffview.DISPLAY_ROOT, rel))
        if old is None or new is None:
            sections.append(
                '<div class="card"><h2>%s</h2><p class="note">'
                'バイナリ形式などのため差分表示の対象外です '
                '(退避フォルダに実ファイルがあります)。</p></div>' % title)
            continue
        body = []
        rows = diffview.collapse_context(diffview.side_by_side(old, new))
        for kind, lno, lt, rno, rt in rows:
            if kind == 'gap':
                body.append('<tr class="gap"><td colspan="4">… 変更のない '
                            '%d 行 …</td></tr>' % lt)
            else:
                body.append(
                    '<tr class="%s"><td class="ln">%s</td>'
                    '<td class="code l">%s</td><td class="ln">%s</td>'
                    '<td class="code r">%s</td></tr>'
                    % (kind, lno or '', diffview._esc(lt), rno or '',
                       diffview._esc(rt)))
        sections.append(
            '<div class="card"><h2>%s</h2>'
            '<table class="diff"><colgroup>'
            '<col style="width:48px"><col>'
            '<col style="width:48px"><col></colgroup>%s</table></div>'
            % (title, ''.join(body)))
    return (
        '<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">'
        '<title>退避した直接変更 (%s)</title><style>%s</style></head><body>'
        '<header><div><h1>退避した直接変更 (%s)</h1>'
        '<div class="meta">左 = リポジトリの内容 (最新版に更新済み) / '
        '右 = 退避した直接変更。'
        '<span style="background:#fecaca">&nbsp;赤&nbsp;</span>= 変更前、'
        '<span style="background:#bbf7d0">&nbsp;緑&nbsp;</span>'
        '= 変更後・追加、'
        '<span style="background:#d3ddee; text-decoration:line-through">'
        '&nbsp;紺&nbsp;</span>= 削除された行</div></div></header>'
        '<div class="wrap">%s</div></body></html>'
        % (stamp, diffview._CSS, stamp, ''.join(sections)))


def auto_update(root=None, now=None):
    """直接変更を退避 → 破棄 → 最新化する。UI 表示用の情報を返す.

    戻り値: dict(stashed=[相対パス], stash_dir, diff_html, updated, note)
    直接変更がなければ何もしない (通常の最新化は起動 bat が実施済み)。
    """
    root = root or paths.REPO_ROOT
    result = {'stashed': [], 'stash_dir': None, 'diff_html': None,
              'updated': False, 'note': ''}
    try:
        # メンバーのクローンは常に main。開発用ブランチや CI の
        # チェックアウト (detached HEAD) では絶対に何もしない
        base = (paths.load_config().get('base_branch') or 'main')
        branch = run_git(['rev-parse', '--abbrev-ref', 'HEAD'],
                         cwd=root).strip()
        if branch != base:
            result['note'] = 'not_base_branch'
            return result

        # ローカルコミットがある場合は触らない (管理者の開発クローン想定)
        try:
            ahead = run_git(['rev-list', '--count', '@{upstream}..HEAD'],
                            cwd=root).strip()
        except GitError:
            ahead = '0'
        if ahead not in ('', '0'):
            result['note'] = 'local_commits'
            return result

        files = _changed_files(root)
        if not files:
            return result

        stamp = (now or datetime.datetime.now()).strftime('%Y%m%d-%H%M%S')
        stash_dir = os.path.join(root, STASH_DIR_NAME, stamp)
        os.makedirs(stash_dir, exist_ok=True)
        for rel in files:
            src = os.path.join(root, rel.replace('/', os.sep))
            if os.path.isfile(src):
                dst = os.path.join(stash_dir, rel.replace('/', os.sep))
                os.makedirs(os.path.dirname(dst) or stash_dir, exist_ok=True)
                shutil.copy2(src, dst)
        page = _build_diff_html(root, files, stamp)
        diff_html = os.path.join(stash_dir, '退避した変更の差分.html')
        with open(diff_html, 'w', encoding='utf-8') as f:
            f.write(page)
        result.update(stashed=files, stash_dir=stash_dir,
                      diff_html=diff_html)

        # 退避が済んだので手元の変更を破棄して最新化 (管理外ファイルは無傷)
        run_git(['reset', '--hard', 'HEAD'], cwd=root)
        run_git(['pull', '--ff-only'], cwd=root, timeout=300)
        result['updated'] = True
    except GitError as e:
        result['note'] = str(e)
        log.warning('自動最新化を完了できませんでした: %s', e)
    except Exception:
        result['note'] = 'error'
        log.exception('自動最新化で予期しないエラー')
    return result
