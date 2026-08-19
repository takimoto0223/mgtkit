# -*- coding: utf-8 -*-
"""更新チェックと取得・インストールの一連の流れ (UI 非依存)."""
import datetime
import logging
import os
import shutil
import tempfile

from . import ghcli, installer, paths, versions

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
    root = os.path.join(paths.install_root(config), 'beta')
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
        shutil.rmtree(path, ignore_errors=True)
        if os.path.exists(path):
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


def install_release(repo, release, instance_dir, python=None,
                    on_progress=None):
    """リリースを取得してインスタンスへ展開・依存インストールする.

    version.json が配布物に無い場合 (CI 整備前のソースアーカイブ) は
    リリース情報から補完して書き込む。
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
        installer.extract_zip(zip_path, app_d)

        if versions.read_version_json(app_d) is None:
            commit = ''
            try:
                commit = ghcli.tag_commit_sha(repo, tag)
            except ghcli.GhError:
                log.warning('タグ %s のコミット SHA を取得できませんでした', tag)
            versions.write_version_json(
                app_d, tag, commit,
                release.get('published_at')
                or datetime.date.today().isoformat())

        progress('必要ライブラリを確認中...')
        installer.install_requirements(app_d, python=python)
        progress('完了')
        return app_d
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
