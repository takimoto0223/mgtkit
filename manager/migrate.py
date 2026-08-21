# -*- coding: utf-8 -*-
"""これまでの配置から新しい配置への引っ越し (setup.bat から呼ばれる).

    python -m manager.migrate --home "C:\\mgtkit_appmanager"

新しいクローンは setup.bat が <home>/.manager/mgtkit に作り終えている。
このモジュールはその続き、**持ち物の引き継ぎと入口づくり**を行う。

方針 (manager/docs/decisions.md):

- **旧フォルダを消すのはいちばん最後**。引き継ぎ・確認・入口づくりが全部
  済んでから消すので、途中で失敗すれば旧フォルダは無傷のまま残る
- **何度実行しても同じ結果になる** (すでに運び終えたものは飛ばす)。途中で
  失敗しても、もう一度 setup.bat を実行すれば続きから進む
- **引き継げたことを確かめてから消す**。settings.json と usage.json は
  失うと痛い (API キーの入れ直し・利用額の再計算不能) ので、新しい側で
  実際に読めることを確認してからでないと消さない
- **消せなかったものは札 (cleanup_pending.json) に残す**。使用中などで
  消えないことがあるので、マネージャーの次回起動で消し直す

試験モード (管理者が配布前に自分の PC で確かめるため):

- ``--dry-run``    何をするかを表示するだけで 1 バイトも書かない
- ``--no-cleanup`` 引っ越しはするが以前のフォルダを消さない
                   (旧環境をそのまま残したまま新しい方を試せる)
"""
import argparse
import datetime
import json
import logging
import os
import shutil
import subprocess
import sys

from . import launcher, paths, safeio

log = logging.getLogger(__name__)

MARKER_NAME = 'cleanup_pending.json'
ENTRY_LNK = 'mgtkit アプリマネージャー.lnk'
ENTRY_BAT = 'mgtkit アプリマネージャー起動.bat'
GUIDE_PDF = 'アプリマネージャー使い方ガイド.pdf'
LAUNCH_BAT = os.path.join('manager', 'マネージャー起動.bat')
# 何度消せなかったら案内に切り替えるか (永久に試し続けないため)
MAX_CLEANUP_ATTEMPTS = 5


class MigrateError(Exception):
    """引っ越しを続けられない。str() は利用者向けの平易な日本語."""


# ---------------------------------------------------------------------------
# これまでの配置を見つける
# ---------------------------------------------------------------------------

def old_clone_dir():
    """これまでのクローンの場所 (%USERPROFILE%/mgtkit)."""
    return os.path.join(os.path.expanduser('~'), 'mgtkit')


def old_install_root():
    """これまでの置き場 (%LOCALAPPDATA%/mgtkit)."""
    if sys.platform == 'win32':
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser(
            r'~\AppData\Local')
        return os.path.join(base, 'mgtkit')
    return os.path.join(os.path.expanduser('~'), '.local', 'share', 'mgtkit')


def find_old(clone=None, root=None):
    """引き継ぐ元を探す。どちらも無ければ (None, None) = 新規導入."""
    clone = clone or old_clone_dir()
    root = root or old_install_root()
    return (clone if os.path.isdir(os.path.join(clone, '.git')) else None,
            root if os.path.isdir(root) else None)


# ---------------------------------------------------------------------------
# 引き継ぎ
# ---------------------------------------------------------------------------

def plan(home, old_clone, old_root):
    """運ぶものの一覧。要素は (説明, 元, 先)。

    捨ててよいもの (workrepo / diffcache / beta / reviews_cache.json) は
    一覧に入れない — どれもコード上「無ければ作り直す」設計になっている。
    """
    manager_dir = os.path.join(home, paths.MANAGER_DIR_NAME)
    steps = []
    if old_root:
        steps += [
            ('お名前と API キー',
             os.path.join(old_root, 'settings.json'),
             os.path.join(manager_dir, 'settings.json')),
            ('API の利用量',
             os.path.join(old_root, 'usage.json'),
             os.path.join(manager_dir, 'usage.json')),
            ('この PC の表示の記録',
             os.path.join(old_root, 'local_state.json'),
             os.path.join(manager_dir, 'local_state.json')),
            ('いま入っている正式版',
             os.path.join(old_root, 'stable', 'mgtkit'),
             os.path.join(home, 'stable', 'mgtkit')),
            ('読み込んだファイルの一時置き場',
             os.path.join(old_root, 'stable', 'uploads_tmp'),
             os.path.join(manager_dir, 'tmp', 'stable')),
        ]
    if old_clone:
        steps.append(('直接なおした内容の退避先',
                      os.path.join(old_clone, 'stash'),
                      os.path.join(manager_dir, 'mgtkit', 'stash')))
    return steps


def carry_over(steps, dry_run=False, say=None):
    """段取りどおりに運ぶ。運んだものの説明を返す.

    元が無い・先にもう在るものは飛ばす (何度実行しても同じ結果になる)。
    """
    say = say or (lambda *_: None)
    moved = []
    for label, src, dst in steps:
        if not os.path.exists(src):
            continue                       # もともと無い (β版を試したことが無い等)
        if os.path.exists(dst):
            say('  すでにあります: %s' % label)
            continue
        say('  運びます: %s' % label)
        if dry_run:
            moved.append(label)
            continue
        parent = os.path.dirname(dst)
        if parent:
            os.makedirs(parent, exist_ok=True)
        try:
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        except OSError as e:
            raise MigrateError(
                '%s を新しい場所へ移せませんでした (%s)。'
                'パソコンの空き容量を確認して、もう一度 setup.bat を'
                '実行してください。' % (label, e))
        moved.append(label)
    return moved


def verify(home):
    """引き継ぎが成立しているかを見る。駄目な理由の一覧を返す (空なら OK)."""
    manager_dir = os.path.join(home, paths.MANAGER_DIR_NAME)
    problems = []
    s_path = os.path.join(manager_dir, 'settings.json')
    if os.path.exists(s_path):
        try:
            with open(s_path, encoding='utf-8') as f:
                data = json.load(f)
            if not str((data or {}).get('name', '')).strip():
                problems.append('お名前が読み取れません')
            if not str((data or {}).get('anthropic_api_key', '')).strip():
                problems.append('API キーが読み取れません')
        except (OSError, ValueError):
            problems.append('お名前と API キーのファイルが壊れています')
    u_path = os.path.join(manager_dir, 'usage.json')
    if os.path.exists(u_path):
        try:
            with open(u_path, encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance((data or {}).get('days'), dict):
                problems.append('API 利用量のファイルが壊れています')
        except (OSError, ValueError):
            problems.append('API 利用量のファイルが壊れています')
    return problems


def stop_old_apps(config=None, dry_run=False, say=None):
    """引っ越しの前に、これまでのアプリを終了する.

    Windows は**プロセスが作業フォルダにしているフォルダを削除できない**。
    アプリは ``launcher.launch_app`` が ``cwd=<インスタンス>/mgtkit`` で
    起動するため、動いたままだと旧フォルダの中の ``mgtkit/`` が消せず、
    その親も芋づるで残る。しかもアプリは Flask のサーバーなので、
    **ブラウザを閉じてもプロセスは生き続ける** (実機で確認。旧
    AppData\\Local\\mgtkit に空のフォルダが 5 つ残った)。

    この時点で 8765 / 8766 を持っているのは**これまでのアプリだけ**で、
    新しい方はまだ一度も起動していないので取り違えようがない。
    ``stop_app`` は python 以外が使っているポートには触らない。
    終了できなくても引っ越し自体は進める (片付けが次回に回るだけ)。
    """
    say = say or (lambda *_: None)
    stopped = []
    for label, port in (('正式版', paths.stable_port(config)),
                        ('β版', paths.beta_port(config))):
        if not launcher.port_in_use(port):
            continue
        say('  これまでの%sのアプリを終了します (ポート %d)' % (label, port))
        if dry_run:
            stopped.append(label)
            continue
        try:
            if launcher.stop_app(port):
                stopped.append(label)
        except launcher.LaunchError:
            log.warning('ポート %d のアプリを終了できませんでした', port,
                        exc_info=True)
    return stopped


# ---------------------------------------------------------------------------
# 入口 (ショートカット) と案内
# ---------------------------------------------------------------------------

def hide_manager_dir(home, dry_run=False):
    """.manager をエクスプローラーから隠す (Windows のみ).

    ドット始まりでは Windows では隠れないため attrib +h が要る。
    **中身までは隠さない** (`/s /d` を付けない)。隠しファイルになると
    設定ファイルの書き換えが弾かれたり、フォルダごとコピーしたときに
    既定で除外されて設定が持って行かれなくなる。
    """
    target = os.path.join(home, paths.MANAGER_DIR_NAME)
    if sys.platform != 'win32' or dry_run or not os.path.isdir(target):
        return False
    try:
        subprocess.run(['attrib', '+h', target], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        log.warning('%s を隠せませんでした', target, exc_info=True)
        return False


def _ps_quote(text):
    return "'%s'" % str(text).replace("'", "''")


def make_shortcut(home, clone, dry_run=False):
    """入口を作る。作った形 ('lnk' / 'bat') を返す.

    ふだんはショートカット (.lnk)。絶対パスを持つのでデスクトップへ
    コピーしても効く。作れなかった環境 (PowerShell が止められている等)
    のために、絶対パスを直書きした中継バッチに落とす。どちらも作れ
    なければ入口が無くなるので MigrateError にする。
    """
    target = os.path.join(clone, LAUNCH_BAT)
    lnk = os.path.join(home, ENTRY_LNK)
    bat = os.path.join(home, ENTRY_BAT)
    if dry_run:
        return 'lnk'
    if os.path.isfile(lnk) or os.path.isfile(bat):
        return 'lnk' if os.path.isfile(lnk) else 'bat'
    if sys.platform == 'win32':
        script = (
            '$s = New-Object -ComObject WScript.Shell; '
            '$l = $s.CreateShortcut(%s); '
            '$l.TargetPath = %s; '
            '$l.WorkingDirectory = %s; '
            '$l.Description = %s; '
            '$l.Save()' % (_ps_quote(lnk), _ps_quote(target),
                           _ps_quote(os.path.dirname(target)),
                           _ps_quote('mgtkit アプリマネージャーを起動します')))
        try:
            subprocess.run(
                ['powershell', '-NoProfile', '-NonInteractive',
                 '-ExecutionPolicy', 'Bypass', '-Command', script],
                check=False, timeout=60,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            log.warning('ショートカットを作れませんでした', exc_info=True)
        if os.path.isfile(lnk):
            return 'lnk'
    # 中継バッチ (Shift_JIS + chcp 932。配布バッチと同じ約束)
    body = ('@echo off\r\n'
            'chcp 932 >nul\r\n'
            'rem mgtkit アプリマネージャーの入口\r\n'
            'rem (ショートカットを作れなかったときの代わり。'
            'コピーしても使えるよう場所を直接書いてある)\r\n'
            'call "%s"\r\n' % target)
    try:
        with open(bat, 'wb') as f:
            f.write(body.encode('cp932'))
    except (OSError, UnicodeEncodeError) as e:
        raise MigrateError(
            'アプリマネージャーの入口を作れませんでした (%s)。'
            '管理者に連絡してください。' % e)
    return 'bat'


def copy_guide(home, clone, dry_run=False):
    """使い方ガイドを見えるところにも置く (中身は readme/ のものと同じ)."""
    src = os.path.join(clone, 'manager', 'readme', GUIDE_PDF)
    dst = os.path.join(home, GUIDE_PDF)
    if not os.path.isfile(src) or dry_run:
        return False
    try:
        shutil.copy2(src, dst)      # 版が上がったら上書きしたいので毎回コピー
        return True
    except OSError:
        log.warning('使い方ガイドを置けませんでした', exc_info=True)
        return False


# ---------------------------------------------------------------------------
# 旧フォルダの片付け
#
# 引っ越しの最後にここで消す (setup.bat 一発で終わらせるため)。消せなかった
# ものだけを札 (cleanup_pending.json) に残し、マネージャーの次回起動で消し
# 直す。札が無ければ、ふだんの起動でやることは「ファイルが 1 つ無いことを
# 確かめる」だけ。
# ---------------------------------------------------------------------------

def marker_path(config=None, install_root=None):
    return os.path.join(install_root or paths.install_root(config),
                        MARKER_NAME)


def write_marker(home, targets):
    """消しきれなかった対象を書き留める (次回の起動で消し直す)."""
    path = os.path.join(home, paths.MANAGER_DIR_NAME, MARKER_NAME)
    safeio.write_json(path, {
        'targets': [t for t in targets if t],
        'attempts': 0,
        'created_at': datetime.datetime.now().isoformat(timespec='seconds'),
    }, indent=2)
    return path


def read_marker(config=None, install_root=None):
    """札があれば読む。無ければ None (ふだんの起動はここで終わる)."""
    path = marker_path(config, install_root)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get('targets'):
        return None
    return data


def drop_marker(config=None, install_root=None):
    try:
        os.remove(marker_path(config, install_root))
    except OSError:
        pass


def remove_old(targets):
    """旧フォルダを消す。(消せた場所, 残った場所) を返す.

    git は Windows でクローンの中身に読み取り専用属性を付けるため、
    素の削除では必ず途中で止まる (safeio.rmtree が属性を落として消す)。
    """
    removed, left = [], []
    for target in targets:
        if not target or not os.path.exists(target):
            removed.append(target)
            continue
        (removed if safeio.rmtree(target) else left).append(target)
    return removed, left


def run_cleanup(config=None, install_root=None):
    """札があれば消し直す (マネージャーの起動時に裏で呼ぶ).

    札に載るのは「引っ越しのときに消しきれなかったもの」だけ。
    戻り値: (片付けた場所, 消せずに残った場所, 諦めたか)。
    札が無ければ ([], [], False) — ふだんの起動はここで終わる。
    """
    data = read_marker(config, install_root)
    if not data:
        return [], [], False
    removed, left = remove_old(data['targets'])
    if not left:
        drop_marker(config, install_root)
        return removed, [], False
    attempts = int(data.get('attempts') or 0) + 1
    if attempts >= MAX_CLEANUP_ATTEMPTS:
        # これ以上は毎回試しても消えない。案内に切り替えて札を下ろす
        drop_marker(config, install_root)
        return removed, left, True
    data['attempts'] = attempts
    data['targets'] = left
    try:
        safeio.write_json(marker_path(config, install_root), data, indent=2)
    except OSError:
        log.warning('片付けの記録を更新できませんでした', exc_info=True)
    return removed, left, False


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def migrate(home, clone=None, dry_run=False, no_cleanup=False,
            old_clone=None, old_root=None, say=print):
    """引っ越しを行う。行ったことの要約 (dict) を返す."""
    clone = clone or os.path.join(home, paths.MANAGER_DIR_NAME, 'mgtkit')
    if not os.path.isdir(clone):
        if not dry_run:
            raise MigrateError('新しい場所の準備ができていません (%s)。'
                               'setup.bat を最初から実行してください。'
                               % clone)
        # 下見 (--dry-run) は「まだ何も作っていない状態」から実行できる
        # ようにする。いま動いているクローンを元に、何が起きるかだけ言う
        clone = paths.REPO_ROOT
    found_clone, found_root = find_old(old_clone, old_root)
    say('置き場所: %s' % home)
    result = {'moved': [], 'entry': None, 'hidden': False, 'guide': False,
              'marker': None, 'stopped': [], 'removed': [],
              'new_install': not (found_clone or found_root)}

    if result['new_install']:
        say('これまでのフォルダは見つかりませんでした (新規の導入として続けます)。')
    else:
        say('見つかったこれまでのフォルダ:')
        for found in (found_clone, found_root):
            if found:
                say('  %s' % found)
        say('引き継ぎます:')
        # 消す前ではなく**運ぶ前**に止める。動いたままだと旧フォルダの
        # 中の mgtkit/ が作業フォルダとして掴まれ、あとで消せない
        result['stopped'] = stop_old_apps(paths.load_config(),
                                          dry_run=dry_run, say=say)
        result['moved'] = carry_over(plan(home, found_clone, found_root),
                                     dry_run=dry_run, say=say)

    problems = [] if dry_run else verify(home)
    if problems:
        raise MigrateError('引き継いだ内容を確認できませんでした: %s。'
                           '旧フォルダはそのまま残してあります。'
                           % '、'.join(problems))

    result['hidden'] = hide_manager_dir(home, dry_run=dry_run)
    result['entry'] = make_shortcut(home, clone, dry_run=dry_run)
    result['guide'] = copy_guide(home, clone, dry_run=dry_run)

    # 片付けは**いちばん最後**。ここまで来ていれば引き継ぎも確認も入口も
    # 済んでいる。途中で失敗していれば旧フォルダは無傷で残る
    targets = [p for p in (found_clone, found_root) if p]
    if dry_run:
        if targets and not no_cleanup:
            say('以前のフォルダを片付けます: %s' % '、'.join(targets))
    elif targets and not no_cleanup:
        result['removed'], left = remove_old(targets)
        if result['removed']:
            say('以前のフォルダを片付けました。')
        if left:
            result['marker'] = write_marker(home, left)
            say('片付けきれなかったフォルダがあります。次にマネージャーを'
                '起動したときに消し直します: %s' % '、'.join(left))
    elif targets:
        say('※ 試験モードのため、以前のフォルダはそのまま残します。')
    return result


def main(argv=None):
    p = argparse.ArgumentParser(
        description='mgtkit アプリマネージャーの引っ越し')
    p.add_argument('--home', default=None,
                   help='新しい置き場所 (既定: %s)' % paths.default_home_root())
    p.add_argument('--dry-run', action='store_true',
                   help='何をするかを表示するだけで書き換えない')
    p.add_argument('--no-cleanup', action='store_true',
                   help='以前のフォルダの片付けを予約しない (試験用)')
    args = p.parse_args(argv)
    home = args.home or paths.default_home_root()
    if args.dry_run:
        print('--- 下見 (dry-run): 実際には何も書き換えません ---')
    try:
        migrate(home, dry_run=args.dry_run, no_cleanup=args.no_cleanup)
    except MigrateError as e:
        print('引っ越しを完了できませんでした: %s' % e)
        return 1
    if args.dry_run:
        print('--- 下見はここまで。何も書き換えていません ---')
    else:
        print('引っ越しの準備ができました: %s' % home)
    return 0


if __name__ == '__main__':
    sys.exit(main())
