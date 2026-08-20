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

    def test_first_install_creates_missing_folders(self, tmp_path):
        # β版の初回導入: 置き場も作業フォルダの置き場もまだ無い状態
        z = tmp_path / 'a.zip'
        _make_zip(str(z), {'app.py': 'x'})
        dest = tmp_path / '.manager' / 'beta' / 'v1.5-beta.1' / 'mgtkit'
        installer.extract_zip(str(z), str(dest),
                              str(tmp_path / '.manager' / 'tmp'))
        assert (dest / 'app.py').read_text() == 'x'

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


class TestPruneBetas:
    """試すたびに増えるβ版の置き場を、一覧に合わせて片付ける."""

    def _install_root(self, monkeypatch, tmp_path, *versions_):
        monkeypatch.setattr(updater.paths, 'install_root',
                            lambda config=None: str(tmp_path))
        for v in versions_:
            (tmp_path / 'beta' / v / 'mgtkit').mkdir(parents=True)
        return tmp_path / 'beta'

    def test_keeps_only_the_versions_still_listed(self, monkeypatch,
                                                  tmp_path):
        beta = self._install_root(monkeypatch, tmp_path,
                                  'v1.2-beta.1', 'v1.2-beta.2', 'v1.1-beta.9')
        removed = updater.prune_betas(['v1.2-beta.2'])
        assert removed == ['v1.1-beta.9', 'v1.2-beta.1']
        assert [p.name for p in sorted(beta.iterdir())] == ['v1.2-beta.2']

    def test_removes_everything_when_nothing_is_listed(self, monkeypatch,
                                                       tmp_path):
        """正式版になって一覧から消えたら、手元の置き場も残さない."""
        beta = self._install_root(monkeypatch, tmp_path, 'v1.2-beta.1')
        assert updater.prune_betas([]) == ['v1.2-beta.1']
        assert list(beta.iterdir()) == []

    def test_no_beta_folder_yet(self, monkeypatch, tmp_path):
        """一度もβ版を試していない PC でも落ちないこと."""
        monkeypatch.setattr(updater.paths, 'install_root',
                            lambda config=None: str(tmp_path))
        assert updater.prune_betas(['v1.2-beta.1']) == []

    def test_leaves_files_alone(self, monkeypatch, tmp_path):
        """フォルダ以外 (置かれた覚えのないファイル) には触らない."""
        beta = self._install_root(monkeypatch, tmp_path, 'v1.2-beta.1')
        (beta / 'メモ.txt').write_text('x', encoding='utf-8')
        assert updater.prune_betas([]) == ['v1.2-beta.1']
        assert (beta / 'メモ.txt').exists()


class TestInstallRelease:
    """取り込みは「全部済んでから」済んだ印 (version.json) を置く.

    印を展開直後に置くと、そのあとの pip が失敗しても「入っている」に
    なり、次からは取り込みごと飛ばして**必要ライブラリの無いアプリ**を
    起動しようとする (画面は成功と出るのに何も起きず、自力では直せない)。
    """

    def _prepare(self, monkeypatch, tmp_path, entries):
        """配布 ZIP の取得と作業フォルダをモックし、インスタンスを返す."""
        z = tmp_path / 'rel.zip'
        _make_zip(str(z), entries)
        monkeypatch.setattr(updater.ghcli, 'download_release',
                            lambda *a, **k: (str(z), False))
        monkeypatch.setattr(updater.ghcli, 'tag_commit_sha',
                            lambda *a, **k: 'abc123')
        monkeypatch.setattr(updater.paths, 'install_root',
                            lambda config=None: str(tmp_path / 'root'))
        return tmp_path / 'inst'

    def _release(self):
        return {'tag': 'v1.4', 'published_at': '2026-08-18', 'assets': []}

    def test_marker_is_not_left_behind_when_pip_fails(self, monkeypatch,
                                                      tmp_path):
        inst = self._prepare(monkeypatch, tmp_path, {
            'app.py': 'x',
            'version.json': '{"version": "v1.4", "commit": "abc"}'})

        def boom(*a, **k):
            raise installer.InstallError('必要ライブラリの…')
        monkeypatch.setattr(updater.installer, 'install_requirements', boom)

        with pytest.raises(installer.InstallError):
            updater.install_release('o/r', self._release(), str(inst))
        # 「入っている」と誤認されないこと (次に押せば取り込み直される)
        assert updater.local_version_info(str(inst)) is None
        assert (inst / 'mgtkit' / 'app.py').exists()

    def test_marker_keeps_the_distributed_contents(self, monkeypatch,
                                                   tmp_path):
        """配布物の version.json は中身をそのまま戻すこと."""
        body = ('{"version": "v1.4", "commit": "abc123", '
                '"distributed_at": "2026-08-18", "built_by": "CI"}')
        inst = self._prepare(monkeypatch, tmp_path,
                             {'app.py': 'x', 'version.json': body})
        monkeypatch.setattr(updater.installer, 'install_requirements',
                            lambda *a, **k: True)

        updater.install_release('o/r', self._release(), str(inst))
        info = updater.local_version_info(str(inst))
        assert info['version'] == 'v1.4'
        assert info['built_by'] == 'CI'      # 余分な項目も失わない

    def test_marker_is_written_after_pip_when_missing(self, monkeypatch,
                                                      tmp_path):
        """配布物に version.json が無いときも、印は最後に置くこと."""
        inst = self._prepare(monkeypatch, tmp_path, {'app.py': 'x'})
        seen = {}

        def install_requirements(app_dir, python=None):
            # pip の時点ではまだ印が無い (途中で落ちれば残らない)
            seen['during'] = versions.read_version_json(app_dir)
            return True
        monkeypatch.setattr(updater.installer, 'install_requirements',
                            install_requirements)

        updater.install_release('o/r', self._release(), str(inst))
        assert seen['during'] is None
        info = updater.local_version_info(str(inst))
        assert info == {'version': 'v1.4', 'commit': 'abc123',
                        'distributed_at': '2026-08-18'}


class TestInstallLock:
    """取り込みは同時に 2 本走らせない (置き換えと pip がぶつかる).

    初回起動では、画面の案内どおり「起動」を押した人と起動時の自動更新が
    ちょうどぶつかる。負けた側は置き換えに失敗し、まだ一度も起動して
    いないのに「フォルダが使用中です…再起動を」と案内されていた。
    """

    def test_two_installs_never_overlap(self):
        import threading
        import time
        inside, overlapped = [], []

        def work():
            with updater.install_lock():
                inside.append(1)
                if len(inside) > 1:
                    overlapped.append(1)
                time.sleep(0.01)
                inside.pop()

        threads = [threading.Thread(target=work) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert overlapped == []

    def test_try_lock_gives_up_when_busy(self):
        with updater.install_lock():
            assert updater.try_install_lock() is None
            assert updater.installing() is True
        assert updater.installing() is False
        held = updater.try_install_lock()
        assert held is not None
        with held:
            assert updater.installing() is True

    def test_waiting_side_skips_an_install_that_already_happened(
            self, monkeypatch, tmp_path):
        """待っている間に同じ版が入ったら、取り込みも停止もしないこと."""
        installed, prepared = [], []
        monkeypatch.setattr(updater, 'install_release',
                            lambda *a, **k: installed.append(1))
        monkeypatch.setattr(updater, 'local_version_info',
                            lambda d: {'version': 'v1.4'})
        took = updater.install_if_needed(
            'o/r', {'tag': 'v1.4'}, str(tmp_path),
            prepare=lambda: prepared.append(1))
        assert took is False
        assert installed == [] and prepared == []

    def test_a_different_version_is_installed(self, monkeypatch, tmp_path):
        installed, prepared = [], []
        monkeypatch.setattr(updater, 'install_release',
                            lambda *a, **k: installed.append(1))
        monkeypatch.setattr(updater, 'local_version_info',
                            lambda d: {'version': 'v1.3'})
        took = updater.install_if_needed(
            'o/r', {'tag': 'v1.4'}, str(tmp_path),
            prepare=lambda: prepared.append(1))
        assert took is True
        assert installed == [1] and prepared == [1]

    def test_the_lock_is_released_even_when_install_fails(self, monkeypatch,
                                                          tmp_path):
        def boom(*a, **k):
            raise installer.InstallError('失敗')
        monkeypatch.setattr(updater, 'install_release', boom)
        monkeypatch.setattr(updater, 'local_version_info', lambda d: None)
        with pytest.raises(installer.InstallError):
            updater.install_if_needed('o/r', {'tag': 'v1.4'}, str(tmp_path))
        assert updater.installing() is False   # 次の取り込みが詰まらない


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


class TestSwapLeftovers:
    """更新の入れ替えは「隣に用意して名前を付け替える」方式なので、
    強制終了や電源断で作業フォルダが残る。放置すると更新のたびに溜まる。
    新しい配置では stable が利用者に見えるので、なおさら残せない。
    """

    def test_sweeps_only_its_own_leftovers(self, tmp_path):
        (tmp_path / (installer.SWAP_PREFIX + 'aaa')).mkdir()
        (tmp_path / (installer.SWAP_PREFIX + 'bbb') / 'old').mkdir(parents=True)
        (tmp_path / 'mgtkit').mkdir()          # 本物。消してはいけない
        (tmp_path / 'version.json').write_text('{}', encoding='utf-8')
        assert installer.sweep_swap_leftovers(str(tmp_path)) == 2
        assert (tmp_path / 'mgtkit').is_dir()
        assert (tmp_path / 'version.json').is_file()
        assert not list(tmp_path.glob(installer.SWAP_PREFIX + '*'))

    def test_missing_folder_is_harmless(self, tmp_path):
        assert installer.sweep_swap_leftovers(str(tmp_path / 'nope')) == 0

    def test_work_parent_is_used_when_given(self, tmp_path, monkeypatch):
        # 作業フォルダは、指定された隠し場所に作られること
        src = tmp_path / 'src'
        src.mkdir()
        (src / 'app.py').write_text('# app', encoding='utf-8')
        install_dir = tmp_path / 'visible' / 'mgtkit'
        install_dir.mkdir(parents=True)
        work_parent = tmp_path / 'hidden' / 'tmp'
        installer._replace_dir(str(src), str(install_dir), str(work_parent))
        assert (install_dir / 'app.py').is_file()
        # 見える側に残骸が出ていないこと
        assert not list((tmp_path / 'visible').glob(
            installer.SWAP_PREFIX + '*'))

    def test_creates_the_install_parent_when_missing(self, tmp_path):
        # 初めてβ版を試すときは beta/<版>/ がまだ無い。作業フォルダを
        # 隠し側に作る指定でも、付け替え先の親は作られること
        # (作られないと os.rename が失敗し「使用中」と誤って案内される)
        src = tmp_path / 'src'
        src.mkdir()
        (src / 'app.py').write_text('# app', encoding='utf-8')
        install_dir = tmp_path / '.manager' / 'beta' / 'v1.5-beta.1' / 'mgtkit'
        work_parent = tmp_path / '.manager' / 'tmp'
        installer._replace_dir(str(src), str(install_dir), str(work_parent))
        assert (install_dir / 'app.py').is_file()

    def test_defaults_to_the_parent(self, tmp_path):
        src = tmp_path / 'src'
        src.mkdir()
        (src / 'app.py').write_text('# app', encoding='utf-8')
        install_dir = tmp_path / 'inst' / 'mgtkit'
        install_dir.mkdir(parents=True)
        installer._replace_dir(str(src), str(install_dir))
        assert (install_dir / 'app.py').is_file()
