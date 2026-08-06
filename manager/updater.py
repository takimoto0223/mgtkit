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


def check_update(repo, instance_dir):
    """更新確認。戻り値: dict(local, latest, has_update, releases).

    local: version.json の dict または None
    latest: 最新正式版リリース dict または None
    has_update: 取得すべき新しい正式版があるか
    """
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
