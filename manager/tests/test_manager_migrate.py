# -*- coding: utf-8 -*-
"""引っ越し (manager/migrate.py) のテスト。

実機 (Windows) でしか確かめられない部分 (隠し属性・ショートカット・
フォルダのロック) を除いた、段取りそのものを固定する。

いちばん大事なのは次の 3 つ:

- **旧フォルダをこの場で消さない** (消してよいのは新しい方が動いたあと)
- **何度実行しても同じ結果になる** (途中で失敗しても、もう一度実行できる)
- **引き継ぎを確認できないときは片付けを予約しない**
"""
import json
import os

import pytest

from manager import migrate, paths, safeio


def _old_layout(tmp_path, with_clone=True, usage_days=None):
    """これまでの配置をひととおり作る。(旧クローン, 旧置き場) を返す."""
    root = tmp_path / 'AppData' / 'Local' / 'mgtkit'
    (root / 'stable' / 'mgtkit').mkdir(parents=True)
    (root / 'stable' / 'mgtkit' / 'app.py').write_text('# app', encoding='utf-8')
    (root / 'stable' / 'uploads_tmp').mkdir(parents=True)
    (root / 'stable' / 'uploads_tmp' / 'out.pdf').write_text('x', encoding='utf-8')
    (root / 'settings.json').write_text(json.dumps(
        {'name': '山田太郎', 'anthropic_api_key': 'sk-ant-xxx'},
        ensure_ascii=False), encoding='utf-8')
    (root / 'usage.json').write_text(json.dumps(
        {'days': usage_days if usage_days is not None
         else {'2026-08-01': {'in': 1, 'out': 1, 'calls': 1, 'usd': 0.1}}}),
        encoding='utf-8')
    (root / 'local_state.json').write_text('{"hidden_prs": [3]}',
                                           encoding='utf-8')
    # 捨ててよいもの (運ばれないことを確かめるため置いておく)
    (root / 'workrepo').mkdir()
    (root / 'diffcache').mkdir()
    (root / 'beta' / 'v1.2-beta.1').mkdir(parents=True)
    (root / 'reviews_cache.json').write_text('{}', encoding='utf-8')

    clone = None
    if with_clone:
        clone = tmp_path / 'mgtkit'
        (clone / '.git').mkdir(parents=True)
        (clone / 'stash' / '2026-08-18_1030').mkdir(parents=True)
        (clone / 'stash' / '2026-08-18_1030' / 'app.py').write_text(
            '# 直接なおした', encoding='utf-8')
    return clone, root


def _new_home(tmp_path, name='mgtkit_appmanager'):
    """setup.bat が clone まで終えた直後の状態を作る."""
    home = tmp_path / name
    clone = home / paths.MANAGER_DIR_NAME / 'mgtkit'
    (clone / 'manager' / 'readme').mkdir(parents=True)
    (clone / 'manager' / 'マネージャー起動.bat').write_text(
        '@echo off', encoding='cp932')
    (clone / 'manager' / 'readme' / migrate.GUIDE_PDF).write_text(
        '%PDF-1.4', encoding='utf-8')
    return home, clone


def _run(home, old_clone, old_root, **kw):
    return migrate.migrate(str(home),
                           old_clone=str(old_clone) if old_clone else '',
                           old_root=str(old_root), say=lambda *_: None, **kw)


class TestCarryOver:
    def test_moves_what_matters(self, tmp_path):
        old_clone, old_root = _old_layout(tmp_path)
        home, _ = _new_home(tmp_path)
        _run(home, old_clone, old_root)

        mgr = home / paths.MANAGER_DIR_NAME
        assert json.loads((mgr / 'settings.json').read_text(
            encoding='utf-8'))['name'] == '山田太郎'
        assert (mgr / 'usage.json').is_file()
        assert (mgr / 'local_state.json').is_file()
        assert (home / 'stable' / 'mgtkit' / 'app.py').is_file()
        assert (mgr / 'tmp' / 'stable' / 'out.pdf').is_file()
        assert (mgr / 'mgtkit' / 'stash' / '2026-08-18_1030' / 'app.py').is_file()

    def test_leaves_the_rebuildable_ones_behind(self, tmp_path):
        # workrepo / diffcache / beta / reviews_cache.json は作り直せる。
        # 運ぶと数十 MB の無駄なコピーになるので運ばない
        old_clone, old_root = _old_layout(tmp_path)
        home, _ = _new_home(tmp_path)
        _run(home, old_clone, old_root)
        mgr = home / paths.MANAGER_DIR_NAME
        for name in ('workrepo', 'diffcache', 'beta', 'reviews_cache.json'):
            assert not (mgr / name).exists(), name

    def test_old_folders_are_untouched(self, tmp_path):
        # 引っ越しは読むだけ。消してよいのは新しい方が動いたあと
        old_clone, old_root = _old_layout(tmp_path)
        home, _ = _new_home(tmp_path)
        _run(home, old_clone, old_root)
        assert (old_root / 'settings.json').is_file()
        assert (old_clone / '.git').is_dir()

    def test_running_twice_changes_nothing(self, tmp_path):
        old_clone, old_root = _old_layout(tmp_path)
        home, _ = _new_home(tmp_path)
        _run(home, old_clone, old_root)
        # 新しい側で書き換えたものが 2 回目で上書きされないこと
        keep = home / paths.MANAGER_DIR_NAME / 'settings.json'
        keep.write_text(json.dumps(
            {'name': '新しい名前', 'anthropic_api_key': 'sk-ant-new'},
            ensure_ascii=False), encoding='utf-8')
        second = _run(home, old_clone, old_root)
        assert second['moved'] == []
        assert json.loads(keep.read_text(encoding='utf-8'))['name'] == '新しい名前'

    def test_new_install_has_nothing_to_carry(self, tmp_path):
        home, _ = _new_home(tmp_path)
        result = _run(home, None, tmp_path / 'nothing')
        assert result['new_install'] is True
        assert result['moved'] == []
        assert result['marker'] is None


class TestEntry:
    def test_an_entry_is_always_created(self, tmp_path):
        # ショートカットを作れない環境では中継バッチに落とす。
        # 「入口が 1 つも無い」で終わらせない
        old_clone, old_root = _old_layout(tmp_path)
        home, _ = _new_home(tmp_path)
        result = _run(home, old_clone, old_root)
        assert result['entry'] in ('lnk', 'bat')
        entries = [p for p in (home / migrate.ENTRY_LNK,
                               home / migrate.ENTRY_BAT) if p.is_file()]
        assert entries, '入口が作られていない'

    def test_relay_bat_points_at_an_absolute_path(self, tmp_path):
        # デスクトップへコピーしても動くよう、場所を直接書く
        old_clone, old_root = _old_layout(tmp_path)
        home, clone = _new_home(tmp_path)
        migrate.make_shortcut(str(home), str(clone))
        bat = home / migrate.ENTRY_BAT
        if not bat.is_file():
            pytest.skip('ショートカットを作れた環境')
        text = bat.read_bytes().decode('cp932')
        assert str(clone) in text
        assert '%~dp0' not in text, 'コピーすると壊れる書き方をしない'

    def test_guide_is_placed_where_it_can_be_seen(self, tmp_path):
        old_clone, old_root = _old_layout(tmp_path)
        home, _ = _new_home(tmp_path)
        _run(home, old_clone, old_root)
        assert (home / migrate.GUIDE_PDF).is_file()


class TestVerifyBeforeCleanup:
    def test_broken_settings_stops_the_migration(self, tmp_path):
        old_clone, old_root = _old_layout(tmp_path)
        (old_root / 'settings.json').write_text('{壊れている',
                                                encoding='utf-8')
        home, _ = _new_home(tmp_path)
        with pytest.raises(migrate.MigrateError):
            _run(home, old_clone, old_root)
        # 片付けを予約していないこと (旧フォルダが消える予定にならない)
        assert not (home / paths.MANAGER_DIR_NAME / migrate.MARKER_NAME).exists()
        assert (old_root / 'settings.json').is_file()

    def test_empty_usage_stops_the_migration(self, tmp_path):
        old_clone, old_root = _old_layout(tmp_path)
        (old_root / 'usage.json').write_text('', encoding='utf-8')
        home, _ = _new_home(tmp_path)
        with pytest.raises(migrate.MigrateError):
            _run(home, old_clone, old_root)


class TestDryRun:
    def test_writes_nothing(self, tmp_path):
        old_clone, old_root = _old_layout(tmp_path)
        home, clone = _new_home(tmp_path)
        before = sorted(p.relative_to(home) for p in home.rglob('*'))
        _run(home, old_clone, old_root, dry_run=True)
        after = sorted(p.relative_to(home) for p in home.rglob('*'))
        assert before == after
        assert not (home / paths.MANAGER_DIR_NAME / migrate.MARKER_NAME).exists()

    def test_works_before_anything_is_created(self, tmp_path):
        # 下見は「まだ何も作っていない状態」からできる必要がある
        # (setup.bat が clone する前に、何が起きるかだけ見たい)
        old_clone, old_root = _old_layout(tmp_path)
        home = tmp_path / 'not_created_yet'
        result = migrate.migrate(str(home), old_clone=str(old_clone),
                                 old_root=str(old_root), dry_run=True,
                                 say=lambda *_: None)
        assert result['moved']
        assert not home.exists(), '下見でフォルダを作らない'

    def test_still_reports_what_it_would_move(self, tmp_path):
        old_clone, old_root = _old_layout(tmp_path)
        home, _ = _new_home(tmp_path)
        result = _run(home, old_clone, old_root, dry_run=True)
        assert 'お名前と API キー' in result['moved']


class TestTestMode:
    def test_no_cleanup_leaves_no_marker(self, tmp_path):
        # 試験モード: 引っ越しはするが、旧フォルダを消す予約はしない
        old_clone, old_root = _old_layout(tmp_path)
        home, _ = _new_home(tmp_path, name='mgtkit_appmanager_test')
        result = _run(home, old_clone, old_root, no_cleanup=True)
        assert result['moved']
        assert result['marker'] is None
        assert not (home / paths.MANAGER_DIR_NAME / migrate.MARKER_NAME).exists()


class TestCleanupLater:
    def _migrated(self, tmp_path):
        old_clone, old_root = _old_layout(tmp_path)
        home, _ = _new_home(tmp_path)
        _run(home, old_clone, old_root)
        root = str(home / paths.MANAGER_DIR_NAME)
        return home, old_clone, old_root, root

    def test_marker_lists_both_old_folders(self, tmp_path):
        home, old_clone, old_root, root = self._migrated(tmp_path)
        data = migrate.read_marker(install_root=root)
        assert set(data['targets']) == {str(old_clone), str(old_root)}

    def test_cleanup_removes_them_and_drops_the_marker(self, tmp_path):
        home, old_clone, old_root, root = self._migrated(tmp_path)
        removed, left, gave_up = migrate.run_cleanup(install_root=root)
        assert set(removed) == {str(old_clone), str(old_root)}
        assert left == [] and gave_up is False
        assert not old_clone.exists() and not old_root.exists()
        assert migrate.read_marker(install_root=root) is None

    def test_no_marker_means_no_work(self, tmp_path):
        # ふだんの起動。札が無ければ何もしない
        home, _ = _new_home(tmp_path)
        root = str(home / paths.MANAGER_DIR_NAME)
        assert migrate.read_marker(install_root=root) is None
        assert migrate.run_cleanup(install_root=root) == ([], [], False)

    def test_locked_folder_is_left_for_next_time(self, tmp_path, monkeypatch):
        home, old_clone, old_root, root = self._migrated(tmp_path)
        monkeypatch.setattr(safeio, 'rmtree', lambda path: False)
        removed, left, gave_up = migrate.run_cleanup(install_root=root)
        assert removed == [] and gave_up is False
        assert len(left) == 2
        data = migrate.read_marker(install_root=root)
        assert data['attempts'] == 1, '次の起動で試し直せるよう札を残す'

    def test_gives_up_after_enough_tries(self, tmp_path, monkeypatch):
        # 永久に試し続けない。案内に切り替えて札を下ろす
        home, old_clone, old_root, root = self._migrated(tmp_path)
        monkeypatch.setattr(safeio, 'rmtree', lambda path: False)
        for _ in range(migrate.MAX_CLEANUP_ATTEMPTS - 1):
            _, _, gave_up = migrate.run_cleanup(install_root=root)
            assert gave_up is False
        _, left, gave_up = migrate.run_cleanup(install_root=root)
        assert gave_up is True
        assert left
        assert migrate.read_marker(install_root=root) is None

    def test_already_gone_counts_as_done(self, tmp_path):
        home, old_clone, old_root, root = self._migrated(tmp_path)
        safeio.rmtree(str(old_clone))
        safeio.rmtree(str(old_root))
        removed, left, gave_up = migrate.run_cleanup(install_root=root)
        assert left == [] and gave_up is False
        assert migrate.read_marker(install_root=root) is None
