"""manager/selfupdate.py (起動時の自動最新化・直接変更の退避) のテスト。"""
import os
import subprocess

import pytest

from manager import selfupdate
from manager.gitcli import run_git


def _git(args, cwd):
    subprocess.run(['git'] + args, cwd=cwd, check=True,
                   capture_output=True, text=True)


@pytest.fixture()
def member_repo(tmp_path):
    """origin (先行コミットあり) + メンバーのクローン (1 つ遅れ) を作る."""
    origin = tmp_path / 'origin.git'
    _git(['init', '--bare', '-b', 'main', str(origin)], cwd=tmp_path)

    seed = tmp_path / 'seed'
    seed.mkdir()
    _git(['init', '-b', 'main', '.'], cwd=seed)
    (seed / 'app.py').write_text('print("v1")\n', encoding='utf-8')
    (seed / 'util.py').write_text('def f():\n    return 1\n',
                                  encoding='utf-8')
    _git(['add', '-A'], cwd=seed)
    _git(['-c', 'user.name=t', '-c', 'user.email=t@example.com',
          'commit', '-m', 'v1'], cwd=seed)
    _git(['remote', 'add', 'origin', str(origin)], cwd=seed)
    _git(['push', '-q', 'origin', 'main'], cwd=seed)

    clone = tmp_path / 'mgtkit'
    _git(['clone', '-q', str(origin), str(clone)], cwd=tmp_path)
    _git(['config', 'user.email', 'm@example.com'], cwd=clone)
    _git(['config', 'user.name', 'member'], cwd=clone)

    # origin はその後 v2 に進んでいる (メンバーは 1 コミット遅れ)
    (seed / 'app.py').write_text('print("v2")\n', encoding='utf-8')
    _git(['add', '-A'], cwd=seed)
    _git(['-c', 'user.name=t', '-c', 'user.email=t@example.com',
          'commit', '-m', 'v2'], cwd=seed)
    _git(['push', '-q', 'origin', 'main'], cwd=seed)
    return str(clone)


class TestAutoUpdate:
    def test_clean_repo_does_nothing(self, member_repo):
        result = selfupdate.auto_update(member_repo)
        assert result['stashed'] == []
        assert result['stash_dir'] is None
        # 変更なし時は pull しない (通常の最新化は起動 bat が担当)
        assert 'print("v1")' in (
            open(os.path.join(member_repo, 'app.py')).read())

    def test_dirty_file_is_stashed_and_updated(self, member_repo):
        # 直接変更 + 管理外ファイル + 直接削除を混在させる
        with open(os.path.join(member_repo, 'app.py'), 'w',
                  encoding='utf-8') as f:
            f.write('print("直接編集")\n')
        os.remove(os.path.join(member_repo, 'util.py'))
        with open(os.path.join(member_repo, 'mydata.txt'), 'w',
                  encoding='utf-8') as f:
            f.write('メンバー自身のファイル\n')

        result = selfupdate.auto_update(member_repo)

        assert sorted(result['stashed']) == ['app.py', 'util.py']
        assert result['updated'] is True
        # 退避フォルダ: 実ファイルのコピー + 差分ビューワ HTML
        stash = result['stash_dir']
        assert stash.startswith(
            os.path.join(member_repo, selfupdate.STASH_DIR_NAME))
        assert open(os.path.join(stash, 'app.py'),
                    encoding='utf-8').read() == 'print("直接編集")\n'
        page = open(result['diff_html'], encoding='utf-8').read()
        assert '退避した直接変更' in page
        assert '直接編集' in page
        # 作業ツリーは最新版 (v2) に更新され、削除ファイルも復元される
        assert 'print("v2")' in (
            open(os.path.join(member_repo, 'app.py')).read())
        assert os.path.isfile(os.path.join(member_repo, 'util.py'))
        # 管理外ファイルは無傷
        assert open(os.path.join(member_repo, 'mydata.txt'),
                    encoding='utf-8').read() == 'メンバー自身のファイル\n'

    def test_non_main_branch_is_left_alone(self, member_repo):
        # 開発用ブランチ (や CI の detached HEAD) では絶対に何もしない
        _git(['checkout', '-b', 'claude/dev-branch'], cwd=member_repo)
        with open(os.path.join(member_repo, 'app.py'), 'w',
                  encoding='utf-8') as f:
            f.write('print("開発中")\n')

        result = selfupdate.auto_update(member_repo)
        assert result['note'] == 'not_base_branch'
        assert result['stashed'] == []
        assert '開発中' in (
            open(os.path.join(member_repo, 'app.py')).read())

    def test_local_commits_are_left_alone(self, member_repo):
        with open(os.path.join(member_repo, 'app.py'), 'w',
                  encoding='utf-8') as f:
            f.write('print("committed")\n')
        _git(['commit', '-am', 'local work'], cwd=member_repo)

        result = selfupdate.auto_update(member_repo)
        assert result['note'] == 'local_commits'
        assert result['stashed'] == []
        # ローカルコミットは消えない
        assert 'committed' in (
            open(os.path.join(member_repo, 'app.py')).read())

    def test_second_stash_gets_new_folder(self, member_repo):
        import datetime
        with open(os.path.join(member_repo, 'app.py'), 'w',
                  encoding='utf-8') as f:
            f.write('edit1\n')
        r1 = selfupdate.auto_update(
            member_repo, now=datetime.datetime(2026, 8, 12, 10, 0, 0))
        with open(os.path.join(member_repo, 'util.py'), 'w',
                  encoding='utf-8') as f:
            f.write('edit2\n')
        r2 = selfupdate.auto_update(
            member_repo, now=datetime.datetime(2026, 8, 12, 11, 0, 0))
        assert r1['stash_dir'] != r2['stash_dir']
        assert os.path.isdir(r1['stash_dir'])
        assert os.path.isdir(r2['stash_dir'])
