"""manager/installer.py・ghcli.py・updater.py のテスト (外部プロセスはモック)。"""
import io
import json
import os
import zipfile

import pytest

from manager import ghcli, installer, updater, versions


def _make_zip(path, entries, top=None):
    with zipfile.ZipFile(path, 'w') as zf:
        for name, data in entries.items():
            arc = '%s/%s' % (top, name) if top else name
            zf.writestr(arc, data)


class TestExtractZip:
    def test_flat_zip(self, tmp_path):
        z = tmp_path / 'a.zip'
        _make_zip(str(z), {'app.py': 'x', 'util.py': 'y'})
        dest = tmp_path / 'inst' / 'mgtkit'
        installer.extract_zip(str(z), str(dest))
        assert (dest / 'app.py').read_text() == 'x'

    def test_wrapped_zip_strips_top_folder(self, tmp_path):
        # GitHub ソースアーカイブ形式 (単一トップフォルダ) は 1 階層むく
        z = tmp_path / 'src.zip'
        _make_zip(str(z), {'app.py': 'x'}, top='mgtkit-1a2b3c')
        dest = tmp_path / 'inst' / 'mgtkit'
        installer.extract_zip(str(z), str(dest))
        assert (dest / 'app.py').read_text() == 'x'

    def test_replaces_existing_install(self, tmp_path):
        dest = tmp_path / 'inst' / 'mgtkit'
        dest.mkdir(parents=True)
        (dest / 'old.py').write_text('old')
        z = tmp_path / 'a.zip'
        _make_zip(str(z), {'app.py': 'x'})
        installer.extract_zip(str(z), str(dest))
        assert not (dest / 'old.py').exists()
        assert (dest / 'app.py').exists()

    def test_bad_zip_raises_friendly_error(self, tmp_path):
        bad = tmp_path / 'bad.zip'
        bad.write_bytes(b'not a zip')
        with pytest.raises(installer.InstallError):
            installer.extract_zip(str(bad), str(tmp_path / 'x'))

    def test_locked_install_dir_keeps_old_install(self, tmp_path,
                                                  monkeypatch):
        # Windows でアプリ起動中 (フォルダ使用中) の置き換え失敗を再現:
        # 平易なエラーになり、インストール済みの内容は壊れず残ること
        dest = tmp_path / 'inst' / 'mgtkit'
        dest.mkdir(parents=True)
        (dest / 'old.py').write_text('old')
        z = tmp_path / 'a.zip'
        _make_zip(str(z), {'app.py': 'x'})

        real_rename = os.rename

        def locked_rename(a, b):
            if os.path.abspath(a) == str(dest):
                raise PermissionError(13, '使用中', a)
            return real_rename(a, b)

        monkeypatch.setattr(installer.os, 'rename', locked_rename)
        with pytest.raises(installer.InstallError, match='使用中'):
            installer.extract_zip(str(z), str(dest))
        assert (dest / 'old.py').read_text() == 'old'
        # 作業用の一時フォルダが残らないこと
        assert os.listdir(str(tmp_path / 'inst')) == ['mgtkit']


RELEASES_JSON = [
    {'tag_name': 'v1.3-beta.2', 'name': 'beta 2', 'prerelease': True,
     'body': 'beta notes', 'published_at': '2026-08-01T00:00:00Z',
     'assets': []},
    {'tag_name': 'v1.2', 'name': 'v1.2', 'prerelease': False,
     'body': 'stable notes', 'published_at': '2026-07-01T00:00:00Z',
     'assets': [{'name': 'mgtkit-v1.2.zip', 'url': 'http://x/a'}]},
    {'tag_name': 'draft', 'name': 'draft', 'prerelease': False,
     'draft': True, 'assets': []},
]


class TestGhcli:
    def test_fetch_releases_parses_and_skips_draft(self, monkeypatch):
        monkeypatch.setattr(ghcli, 'run_gh',
                            lambda args, timeout=60:
                            json.dumps(RELEASES_JSON))
        rel = ghcli.fetch_releases('o/r')
        assert [r['tag'] for r in rel] == ['v1.3-beta.2', 'v1.2']
        assert rel[1]['assets'][0]['name'] == 'mgtkit-v1.2.zip'
        assert rel[0]['published_at'] == '2026-08-01'

    def test_latest_stable_and_prereleases(self, monkeypatch):
        monkeypatch.setattr(ghcli, 'run_gh',
                            lambda args, timeout=60:
                            json.dumps(RELEASES_JSON))
        rel = ghcli.fetch_releases('o/r')
        assert ghcli.latest_stable(rel)['tag'] == 'v1.2'
        assert [r['tag'] for r in ghcli.prereleases(rel)] == ['v1.3-beta.2']

    def test_missing_gh_binary(self, monkeypatch):
        def raise_fnf(*a, **kw):
            raise FileNotFoundError()
        monkeypatch.setattr(ghcli.subprocess, 'run', raise_fnf)
        with pytest.raises(ghcli.GhError, match='gh コマンドが見つかりません'):
            ghcli.run_gh(['api', 'x'])

    @pytest.mark.parametrize('stderr,keyword', [
        ('HTTP 401 authentication required; run gh auth login', 'ログイン'),
        ('could not resolve host github.com', 'ネットワーク'),
        ('HTTP 404 Not Found', '見つかりません'),
        ('API rate limit exceeded', '利用制限'),
        ('something weird', '通信でエラー'),
    ])
    def test_friendly_messages(self, stderr, keyword):
        assert keyword in ghcli._friendly_message(stderr)


class TestCheckUpdate:
    def _patch_releases(self, monkeypatch, releases):
        monkeypatch.setattr(ghcli, 'fetch_releases',
                            lambda repo, limit=30: releases)

    def test_not_installed_and_release_exists(self, monkeypatch, tmp_path):
        self._patch_releases(monkeypatch, [
            {'tag': 'v1.2', 'name': 'v1.2', 'prerelease': False,
             'notes': '', 'published_at': '2026-07-01', 'assets': []}])
        r = updater.check_update('o/r', str(tmp_path))
        assert r['local'] is None
        assert r['has_update'] is True

    def test_up_to_date(self, monkeypatch, tmp_path):
        app_d = tmp_path / 'mgtkit'
        app_d.mkdir()
        versions.write_version_json(str(app_d), 'v1.2', 'sha', '2026-07-01')
        self._patch_releases(monkeypatch, [
            {'tag': 'v1.2', 'name': 'v1.2', 'prerelease': False,
             'notes': '', 'published_at': '2026-07-01', 'assets': []}])
        r = updater.check_update('o/r', str(tmp_path))
        assert r['has_update'] is False

    def test_newer_release_available(self, monkeypatch, tmp_path):
        app_d = tmp_path / 'mgtkit'
        app_d.mkdir()
        versions.write_version_json(str(app_d), 'v1.2', 'sha', '2026-07-01')
        self._patch_releases(monkeypatch, [
            {'tag': 'v1.3', 'name': 'v1.3', 'prerelease': False,
             'notes': 'x', 'published_at': '2026-08-01', 'assets': []},
            {'tag': 'v1.2', 'name': 'v1.2', 'prerelease': False,
             'notes': '', 'published_at': '2026-07-01', 'assets': []}])
        r = updater.check_update('o/r', str(tmp_path))
        assert r['has_update'] is True
        assert r['latest']['tag'] == 'v1.3'

    def test_no_releases_yet(self, monkeypatch, tmp_path):
        self._patch_releases(monkeypatch, [])
        r = updater.check_update('o/r', str(tmp_path))
        assert r['latest'] is None
        assert r['has_update'] is False

    def test_preloaded_releases_skip_fetch(self, monkeypatch, tmp_path):
        """取得済みの一覧を渡したら GitHub へ取りに行かない."""
        def boom(repo, limit=30):
            raise AssertionError('fetch_releases が呼ばれた')
        monkeypatch.setattr(ghcli, 'fetch_releases', boom)
        r = updater.check_update('o/r', str(tmp_path), releases=[
            {'tag': 'v1.2', 'name': 'v1.2', 'prerelease': False,
             'notes': '', 'published_at': '2026-07-01', 'assets': []}])
        assert r['has_update'] is True
        assert r['latest']['tag'] == 'v1.2'


def test_manager_main_compiles():
    # flet は CI に入れないため import はせず、構文チェックのみ行う
    src_path = os.path.join(os.path.dirname(updater.__file__), 'main.py')
    with io.open(src_path, encoding='utf-8') as f:
        compile(f.read(), src_path, 'exec')


class TestJoinRequest:
    def test_has_push_access(self, monkeypatch):
        monkeypatch.setattr(ghcli, 'run_gh',
                            lambda args, timeout=60: 'true\n')
        assert ghcli.has_push_access('o/r') is True
        monkeypatch.setattr(ghcli, 'run_gh',
                            lambda args, timeout=60: 'false\n')
        assert ghcli.has_push_access('o/r') is False

    def test_find_my_join_request(self, monkeypatch):
        monkeypatch.setattr(
            ghcli, 'run_gh',
            lambda args, timeout=60: json.dumps([
                {'number': 3, 'title': '別の質問', 'state': 'OPEN'},
                {'number': 5, 'title': '参加申請: 山田太郎 (@yamada)',
                 'state': 'OPEN'},
            ]))
        found = ghcli.find_my_join_request('o/r')
        assert found['number'] == 5

    def test_find_my_join_request_none(self, monkeypatch):
        monkeypatch.setattr(ghcli, 'run_gh', lambda args, timeout=60: '[]')
        assert ghcli.find_my_join_request('o/r') is None

    def test_create_join_request(self, monkeypatch):
        calls = []

        def fake_run_gh(args, timeout=60):
            calls.append(args)
            if args[:2] == ['api', 'user']:
                return 'yamada\n'
            return ''

        monkeypatch.setattr(ghcli, 'run_gh', fake_run_gh)
        ghcli.create_join_request('o/r', '山田太郎')
        create = calls[-1]
        assert create[:4] == ['issue', 'create', '--repo', 'o/r']
        title = create[create.index('--title') + 1]
        assert title == '参加申請: 山田太郎 (@yamada)'
        body = create[create.index('--body') + 1]
        assert '@yamada' in body and '承認' in body
        # オーナーへの @メンション (Watch 設定によらず通知を届ける)
        assert '@o さんへ' in body


class TestCollaboratorInvitation:
    def test_accept_matching_invitation(self, monkeypatch):
        calls = []

        def fake_run_gh(args, timeout=60):
            calls.append(args)
            if args == ['api', '/user/repository_invitations']:
                return json.dumps([
                    {'id': 5, 'repository': {'full_name': 'other/repo'}},
                    {'id': 7, 'repository': {'full_name': 'O/R'}},
                ])
            return ''

        monkeypatch.setattr(ghcli, 'run_gh', fake_run_gh)
        assert ghcli.accept_repo_invitation('o/r') is True
        assert calls[-1] == ['api', '-X', 'PATCH',
                             '/user/repository_invitations/7']

    def test_no_invitation_is_noop(self, monkeypatch):
        monkeypatch.setattr(ghcli, 'run_gh',
                            lambda args, timeout=60: '[]')
        assert ghcli.accept_repo_invitation('o/r') is False
