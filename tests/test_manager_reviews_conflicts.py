"""manager/reviews.py・conflicts.py のテスト (gh はモック、git は実操作)。"""
import subprocess

import pytest

from manager import conflicts, reviews
from manager.gitcli import run_git


class TestApprovalSummary:
    def test_latest_state_per_reviewer(self):
        reviews_data = [
            {'author': {'login': 'a'}, 'state': 'CHANGES_REQUESTED',
             'body': 'だめ'},
            {'author': {'login': 'a'}, 'state': 'APPROVED', 'body': ''},
            {'author': {'login': 'b'}, 'state': 'APPROVED', 'body': ''},
            {'author': {'login': 'c'}, 'state': 'COMMENTED',
             'body': 'メモ'},
        ]
        s = reviews.approval_summary(reviews_data)
        assert s['approved'] == ['a', 'b']
        assert s['rejected'] == []

    def test_rejection_blocks(self):
        s = reviews.approval_summary([
            {'author': {'login': 'a'}, 'state': 'APPROVED', 'body': ''},
            {'author': {'login': 'b'}, 'state': 'CHANGES_REQUESTED',
             'body': '検定比の丸めが違います'},
        ])
        assert s['approved'] == ['a']
        assert s['rejected'] == [
            {'name': 'b', 'comment': '検定比の丸めが違います'}]

    def test_empty(self):
        s = reviews.approval_summary([])
        assert s == {'approved': [], 'rejected': []}


class TestReleaseRules:
    def _pr(self, **kw):
        pr = {'approved': ['a', 'b', 'c'], 'rejected': [],
              'checks': 'success', 'conflicting': False}
        pr.update(kw)
        return pr

    CFG = {'repo': 'o/r',
           'branch_protection': {'required_approvals': 3},
           'manager': {'admins': ['boss']}}

    def test_release_allowed_for_admin(self):
        assert reviews.can_release(self._pr(), self.CFG, me='boss')

    def test_not_admin(self):
        assert not reviews.can_release(self._pr(), self.CFG, me='member')

    def test_not_enough_approvals(self):
        assert not reviews.can_release(self._pr(approved=['a', 'b']),
                                       self.CFG, me='boss')

    def test_rejection_blocks(self):
        pr = self._pr(rejected=[{'name': 'x', 'comment': 'ng'}])
        assert not reviews.can_release(pr, self.CFG, me='boss')

    def test_failing_checks_block(self):
        assert not reviews.can_release(self._pr(checks='failure'),
                                       self.CFG, me='boss')

    def test_conflict_blocks(self):
        assert not reviews.can_release(self._pr(conflicting=True),
                                       self.CFG, me='boss')

    def test_admins_default_is_repo_owner(self):
        assert reviews.admins({'repo': 'owner1/mgtkit'}) == ['owner1']


class TestNextStableVersion:
    def _patch(self, monkeypatch, releases):
        monkeypatch.setattr(reviews.ghcli, 'fetch_releases',
                            lambda repo, limit=30: releases)

    def test_increments_minor(self, monkeypatch):
        self._patch(monkeypatch, [
            {'tag': 'v1.3-beta.2', 'prerelease': True},
            {'tag': 'v1.2', 'prerelease': False},
            {'tag': 'v1.1', 'prerelease': False},
        ])
        assert reviews.next_stable_version({'repo': 'o/r'}) == 'v1.3'

    def test_no_releases(self, monkeypatch):
        self._patch(monkeypatch, [])
        assert reviews.next_stable_version({'repo': 'o/r'}) == 'v1.0'


def _git(args, cwd):
    subprocess.run(['git'] + args, cwd=cwd, check=True,
                   capture_output=True, text=True)


@pytest.fixture()
def conflict_env(tmp_path, monkeypatch):
    """main が先行して同一行を変更している衝突状態を作る."""
    origin = tmp_path / 'origin.git'
    _git(['init', '--bare', '-b', 'main', str(origin)], cwd=tmp_path)
    seed = tmp_path / 'seed'
    seed.mkdir()
    _git(['init', '-b', 'main', '.'], cwd=seed)
    (seed / 'app.py').write_text('value = 1\n')
    _git(['add', '-A'], cwd=seed)
    _git(['-c', 'user.name=t', '-c', 'user.email=t@e.com',
          'commit', '-m', 'base'], cwd=seed)
    # 提出ブランチ: value を 2 に
    _git(['checkout', '-b', 'feature/x-1'], cwd=seed)
    (seed / 'app.py').write_text('value = 2  # 提出者の変更\n')
    _git(['add', '-A'], cwd=seed)
    _git(['-c', 'user.name=t', '-c', 'user.email=t@e.com',
          'commit', '-m', 'feature'], cwd=seed)
    # main 先行: 同じ行を 3 に
    _git(['checkout', 'main'], cwd=seed)
    (seed / 'app.py').write_text('value = 3  # 最新版の変更\n')
    _git(['add', '-A'], cwd=seed)
    _git(['-c', 'user.name=t', '-c', 'user.email=t@e.com',
          'commit', '-m', 'main advance'], cwd=seed)
    _git(['remote', 'add', 'origin', str(origin)], cwd=seed)
    _git(['push', 'origin', 'main', 'feature/x-1'], cwd=seed)

    workrepo = tmp_path / 'workrepo'
    _git(['clone', str(origin), str(workrepo)], cwd=tmp_path)

    monkeypatch.setattr(conflicts, 'ensure_work_repo',
                        lambda slug, d: str(workrepo))
    monkeypatch.setattr(
        conflicts.ghcli, 'run_gh',
        lambda args, timeout=60: 'resolver\n'
        if args[:2] == ['api', 'user'] else (_ for _ in ()).throw(
            AssertionError('unexpected gh call: %r' % args)))
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    return {'origin': str(origin), 'workrepo': str(workrepo)}


class TestConflicts:
    def test_analyze_detects_conflict(self, conflict_env):
        analysis = conflicts.analyze('feature/x-1', {})
        try:
            assert analysis['conflicted'] == ['app.py']
            assert not analysis['merged_clean']
            assert 'app.py' in analysis['explanation']
        finally:
            conflicts.abort(analysis)

    def test_resolve_theirs(self, conflict_env):
        analysis = conflicts.analyze('feature/x-1', {})
        summary = conflicts.resolve(analysis, 'theirs', {})
        assert '最新版を優先' in summary
        content = run_git(['show', 'origin/feature/x-1:app.py'],
                          cwd=conflict_env['workrepo'])
        assert 'value = 3' in content

    def test_resolve_ours(self, conflict_env):
        analysis = conflicts.analyze('feature/x-1', {})
        summary = conflicts.resolve(analysis, 'ours', {})
        assert 'あなたの変更を優先' in summary
        content = run_git(['show', 'origin/feature/x-1:app.py'],
                          cwd=conflict_env['workrepo'])
        assert 'value = 2' in content

    def test_resolve_both_via_claude(self, conflict_env, monkeypatch):
        monkeypatch.setattr(
            conflicts.claude_helper, 'generate_merge',
            lambda files, policy: {
                'summary': '両方の値を保持する形に統合',
                'files': [{'path': 'app.py',
                           'content': 'value = 2\nlatest = 3\n'}]})
        analysis = conflicts.analyze('feature/x-1', {})
        summary = conflicts.resolve(analysis, 'both', {})
        assert '統合' in summary
        content = run_git(['show', 'origin/feature/x-1:app.py'],
                          cwd=conflict_env['workrepo'])
        assert 'latest = 3' in content

    def test_resolve_both_without_key_aborts(self, conflict_env,
                                             monkeypatch):
        monkeypatch.setattr(conflicts.claude_helper, 'generate_merge',
                            lambda files, policy: None)
        analysis = conflicts.analyze('feature/x-1', {})
        with pytest.raises(conflicts.ConflictError, match='API キー'):
            conflicts.resolve(analysis, 'both', {})
        # merge は取り消されている (作業ツリーが衝突状態のまま残らない)
        status = run_git(['status', '--porcelain'],
                         cwd=conflict_env['workrepo'])
        assert 'UU' not in status
