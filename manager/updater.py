# -*- coding: utf-8 -*-
"""更新チェックと取得・インストールの一連の流れ (UI 非依存)."""
import contextlib
import datetime
import logging
import os
import tempfile
import threading

from . import ghcli, installer, paths, safeio, versions

log = logging.getLogger(__name__)


def local_version_info(instance_dir):
    """インスタンスの version.json (無ければ None)."""
    return versions.read_version_json(paths.app_dir(instance_dir))


def prune_betas(keep_tags, config=None):
    """一覧に残っていないβ版のフォルダを片付ける.

    β版は試すたびに `<install_root>/beta/<版>/` が増える。正式版に
    なったか取り下げられた版は GitHub 側でも prerelease が削除される
    (.github/workflows/reject-cleanup.yml) ため、手元にだけ残っても
    「β版を試す」から二度と起動できないゴミになる。判断の基準を
    GitHub 側と同じ「いま一覧にあるβ版だけ残す」に揃える。

    keep_tags: 残すβ版の版名 (いま一覧にある prerelease)。
    戻り値: 片付けた版名の一覧 (使用中などで消せなかったものは含めない)。
    """
    root = paths.beta_root(config)
    keep = set(keep_tags or ())
    removed = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return removed          # まだ一度もβ版を試していない
    for name in names:
        path = os.path.join(root, name)
        if name in keep or not os.path.isdir(path):
            continue
        if not safeio.rmtree(path):
            # Windows では起動中のフォルダは消せない。次の機会に回す
            log.warning('β版 %s を片付けられませんでした', name)
            continue
        removed.append(name)
    return removed


def check_update(repo, instance_dir, releases=None):
    """更新確認。戻り値: dict(local, latest, has_update, releases).

    local: version.json の dict または None
    latest: 最新正式版リリース dict または None
    has_update: 取得すべき新しい正式版があるか
    releases: 取得済みのリリース一覧。渡されたら再取得しない
    (他の処理と同時に更新確認するときの二重取得を防ぐ)。
    """
    if releases is None:
        releases = ghcli.fetch_releases(repo)
    latest = ghcli.latest_stable(releases)
    local = local_version_info(instance_dir)
    has_update = False
    if latest is not None:
        if local is None or not versions.is_valid_version(
                local.get('version', '')):
            has_update = True
        elif versions.compare_versions(latest['tag'],
                                       local['version']) > 0:
            has_update = True
    return {'local': local, 'latest': latest,
            'has_update': has_update, 'releases': releases}


def swap_work_dir(config=None):
    """入れ替えの作業フォルダを作る場所 (マネージャーの持ち物の中).

    既定では install_dir の親に作られるが、新しい配置ではそこが
    利用者に見える <home>/stable なので、更新のたびに mgtkit_swap_XXXX が
    一瞬見えてしまう。隠している側に寄せる。
    """
    return os.path.join(paths.install_root(config), 'tmp')


def sweep_leftovers(config=None):
    """前回の入れ替えが途中で終わったときの残骸を掃く (起動時に一度).

    作業フォルダの置き場のほか、以前の版が使っていた「インスタンスの
    親」も見る (安定版と、試用中のβ版それぞれ)。
    """
    dirs = [swap_work_dir(config), paths.stable_dir(config)]
    beta_root = paths.beta_root(config)
    try:
        dirs += [os.path.join(beta_root, name)
                 for name in sorted(os.listdir(beta_root))]
    except OSError:
        pass                            # β版を試したことが無い
    return installer.sweep_swap_leftovers(*dirs)


# --- 取り込みの直列化 ------------------------------------------------------
#
# 取り込みは「フォルダの置き換え + 同じ Python 環境への pip install」で、
# **同時に 2 本走らせてはいけない**。負けた側は置き換えに失敗して
# 「フォルダが使用中」と誤った案内になり、pip も同じ環境で衝突する。
# 初回起動では、画面の案内どおり「起動」を押した人と起動時の自動更新が
# ちょうどぶつかる (実測で数 % 再現)。起動・自動更新・β版のすべての
# 取得はこの錠を通す。

_INSTALL_LOCK = threading.Lock()


def installing():
    """いま取り込みが走っているか (待つ前の案内表示用)."""
    return _INSTALL_LOCK.locked()


@contextlib.contextmanager
def install_lock():
    """取り込みの錠 (with で使う)。先客がいれば終わるまで待つ."""
    _INSTALL_LOCK.acquire()
    try:
        yield
    finally:
        _INSTALL_LOCK.release()


@contextlib.contextmanager
def _held_lock():
    try:
        yield
    finally:
        _INSTALL_LOCK.release()


def try_install_lock():
    """待たない版。先客がいれば None、取れたら with で使える錠.

    定期の自動更新が使う (先客がいるなら次の機会に回せばよく、
    待って重ねて取り込む意味がない)。
    """
    if not _INSTALL_LOCK.acquire(blocking=False):
        return None
    return _held_lock()


def install_if_needed(repo, release, instance_dir, python=None,
                      on_progress=None, config=None, prepare=None):
    """錠を取り、まだ入っていなければ取り込む (待つ側の共通形).

    先客を待っている間に同じ版が入っていたら取り込みを飛ばす
    (同じ ZIP をもう一度落として入れ直すだけの無駄になるため)。
    prepare: 実際に取り込むときだけ、取り込みの直前に呼ぶ
    (起動中アプリの終了など、飛ばすときにはしたくない処理)。
    戻り値: 取り込んだら True / すでに入っていて飛ばしたら False。

    注意: 錠を持つのは取り込みの間だけにする。リリース一覧の取得などの
    ネットワーク呼び出しは、この関数の**外**で済ませてから渡すこと。
    """
    with install_lock():
        local = local_version_info(instance_dir)
        if local is not None and local.get('version') == release['tag']:
            return False                # 待っている間に済んでいた
        if prepare is not None:
            prepare()
        install_release(repo, release, instance_dir, python=python,
                        on_progress=on_progress, config=config)
        return True


def _take_version_json(app_dir):
    """展開直後の version.json を取り上げて中身を返す (無ければ None).

    version.json は**取り込みが最後まで済んだ印**として扱われている
    (「まだ入っていない」の判定は ``local_version_info`` が返す None
    だけ)。展開した時点で置いたままにすると、そのあとの
    ``install_requirements`` が失敗しても「入っている」ことになり、
    次からは取り込みを丸ごと飛ばして**必要ライブラリの無いアプリ**を
    起動しようとする (画面は成功と出るのに何も起きない)。そこでいったん
    取り上げ、全部済んでから ``_put_version_json`` で戻す。

    中身はそのまま持ち回す (配布物に入っていた版の内容を書き換えない)。
    """
    if versions.read_version_json(app_dir) is None:
        return None                     # 無い / 壊れている = 印にならない
    path = os.path.join(app_dir, 'version.json')
    with open(path, 'rb') as f:
        raw = f.read()
    os.remove(path)
    return raw


def _put_version_json(app_dir, raw):
    """取り上げた version.json を戻す (取り込み完了の印)."""
    with open(os.path.join(app_dir, 'version.json'), 'wb') as f:
        f.write(raw)


def install_release(repo, release, instance_dir, python=None,
                    on_progress=None, config=None):
    """リリースを取得してインスタンスへ展開・依存インストールする.

    version.json が配布物に無い場合 (CI 整備前のソースアーカイブ) は
    リリース情報から補完して書き込む。どちらの場合も、書き込むのは
    **必要ライブラリの導入まで済んだあと**にする (_take_version_json)。
    """
    def progress(msg):
        log.info('%s', msg)
        if on_progress:
            on_progress(msg)

    tag = release['tag']
    tmp = tempfile.mkdtemp(prefix='mgtkit_dl_')
    try:
        progress('%s をダウンロード中...' % tag)
        zip_path, is_source = ghcli.download_release(
            repo, tag, tmp, has_assets=bool(release.get('assets')))

        progress('展開中...')
        app_d = paths.app_dir(instance_dir)
        installer.extract_zip(zip_path, app_d, swap_work_dir(config))

        raw = _take_version_json(app_d)
        if raw is None:
            commit = ''
            try:
                commit = ghcli.tag_commit_sha(repo, tag)
            except ghcli.GhError:
                log.warning('タグ %s のコミット SHA を取得できませんでした', tag)
            distributed_at = (release.get('published_at')
                              or datetime.date.today().isoformat())

        progress('必要ライブラリを確認中...')
        installer.install_requirements(app_d, python=python)

        # ここまで来て初めて「取り込み済み」になる
        if raw is None:
            versions.write_version_json(app_d, tag, commit, distributed_at)
        else:
            _put_version_json(app_d, raw)
        progress('完了')
        return app_d
    finally:
        safeio.rmtree(tmp)
