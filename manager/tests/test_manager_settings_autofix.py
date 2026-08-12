"""manager/settings.py・autofix.py のテスト。"""
import os
import subprocess

import pytest

from manager import autofix, settings, submit
from manager.gitcli import run_git


class TestSettings:
    def _cfg(self, tmp_path):
        return {'manager': {'install_root': str(tmp_path)}}

    def test_roundtrip(self, tmp_path):
        cfg = self._cfg(tmp_path)
        settings.save_settings('山田太郎', 'sk-ant-test-key-123', cfg)
        data = settings.load_settings(cfg)
        assert data['name'] == '山田太郎'
        assert settings.api_key(cfg) == 'sk-ant-test-key-123'
        assert settings.user_name(cfg) == '山田太郎'

    def test_unregistered_returns_none(self, tmp_path):
        cfg = self._cfg(tmp_path)
        assert settings.load_settings(cfg) is None
        assert settings.user_name(cfg) is None

    @pytest.mark.parametrize('name,key,match', [
        ('', 'sk-ant-x', '名前'),
        ('山田', '', 'API キー'),
        ('山田', 'not-a-key', '形式'),
    ])
    def test_validation(self, tmp_path, name, key, match):
        with pytest.raises(ValueError, match=match):
            settings.save_settings(name, key, self._cfg(tmp_path))

    def test_env_fallback(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path)
        monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-from-env')
        assert settings.api_key(cfg) == 'sk-ant-from-env'
        # 登録済みならそちらが優先される
        settings.save_settings('山田', 'sk-ant-registered', cfg)
        assert settings.api_key(cfg) == 'sk-ant-registered'


class TestSummarizeChecks:
    def test_states(self):
        f = autofix._summarize_checks
        assert f([]) == 'pending'
        assert f([{'conclusion': 'SUCCESS'}]) == 'success'
        assert f([{'conclusion': 'SUCCESS'},
                  {'conclusion': 'FAILURE'}]) == 'failure'
        assert f([{'conclusion': '', 'status': 'IN_PROGRESS'}]) == 'pending'
        assert f([{'conclusion': 'SUCCESS'},
                  {'conclusion': 'SKIPPED'}]) == 'success'


class TestFailedRunId:
    def test_parses_run_id_from_details_url(self):
        checks = [
            {'conclusion': 'SUCCESS',
             'detailsUrl': 'https://github.com/o/r/actions/runs/1/job/2'},
            {'conclusion': 'FAILURE',
             'detailsUrl': 'https://github.com/o/r/actions/runs/31056/job/9'},
        ]
        assert autofix._failed_run_id(checks) == '31056'
        assert autofix._failed_run_id([{'conclusion': 'SUCCESS'}]) is None


class TestValidateFixPaths:
    def test_ok_path(self):
        fix = {'files': [{'path': 'app.py', 'content': 'x'}]}
        autofix._validate_fix_paths(fix)

    @pytest.mark.parametrize('path,match', [
        ('tests/test_x.py', '保護'),
        ('.github/workflows/test.yml', '保護'),
        ('manager/main.py', '保護'),
        ('../evil.py', '不正なパス'),
        ('tool.exe', '実行ファイル'),
    ])
    def test_rejects(self, path, match):
        fix = {'files': [{'path': path, 'content': 'x'}]}
        with pytest.raises(autofix.AutofixError, match=match):
            autofix._validate_fix_paths(fix)

    def test_normalizes_backslash(self):
        fix = {'files': [{'path': 'data\\x.json', 'content': 'x'}]}
        autofix._validate_fix_paths(fix)
        assert fix['files'][0]['path'] == 'data/x.json'


def _git(args, cwd):
    subprocess.run(['git'] + args, cwd=cwd, check=True,
                   capture_output=True, text=True)


class TestAttemptFix:
    @pytest.fixture()
    def repo_env(self, tmp_path):
        origin = tmp_path / 'origin.git'
        _git(['init', '--bare', '-b', 'main', str(origin)], cwd=tmp_path)
        seed = tmp_path / 'seed'
        seed.mkdir()
        _git(['init', '-b', 'main', '.'], cwd=seed)
        (seed / 'app.py').write_text('print("v1")\n')
        (seed / 'tests').mkdir()
        (seed / 'tests' / 'test_a.py').write_text('def test():\n    pass\n')
        _git(['add', '-A'], cwd=seed)
        _git(['-c', 'user.name=t', '-c', 'user.email=t@e.com',
              'commit', '-m', 'base'], cwd=seed)
        _git(['checkout', '-b', 'feature/x-1'], cwd=seed)
        (seed / 'app.py').write_text('print("broken"\n')
        _git(['add', '-A'], cwd=seed)
        _git(['-c', 'user.name=t', '-c', 'user.email=t@e.com',
              'commit', '-m', 'feature'], cwd=seed)
        _git(['remote', 'add', 'origin', str(origin)], cwd=seed)
        _git(['push', 'origin', 'main', 'feature/x-1'], cwd=seed)
        workrepo = tmp_path / 'workrepo'
        _git(['clone', str(origin), str(workrepo)], cwd=tmp_path)
        return {'origin': str(origin), 'workrepo': str(workrepo)}

    @pytest.fixture()
    def gh_mock(self, monkeypatch):
        def fake_run_gh(args, timeout=60):
            if args[:2] == ['api', 'user']:
                return 'fixer\n'
            raise AssertionError('unexpected gh call: %r' % args)
        monkeypatch.setattr(autofix.ghcli, 'run_gh', fake_run_gh)

    def test_applies_fix_and_pushes(self, repo_env, monkeypatch, gh_mock):
        monkeypatch.setattr(
            autofix.claude_helper, 'generate_fix',
            lambda log, files: {
                'cause': '括弧の閉じ忘れ',
                'summary': 'print の構文エラーを修正',
                'files': [{'path': 'app.py',
                           'content': 'print("fixed")\n'}]})
        summary = autofix.attempt_fix(repo_env['workrepo'], 'feature/x-1',
                                      'SyntaxError: ...', {})
        assert '構文エラー' in summary
        log_out = run_git(['log', '--format=%s', '-1',
                           'origin/feature/x-1'],
                          cwd=repo_env['workrepo'])
        assert log_out.startswith(autofix.AUTOFIX_PREFIX)
        content = run_git(['show', 'origin/feature/x-1:app.py'],
                          cwd=repo_env['workrepo'])
        assert 'fixed' in content

    def test_rejects_protected_path_fix(self, repo_env, monkeypatch,
                                        gh_mock):
        monkeypatch.setattr(
            autofix.claude_helper, 'generate_fix',
            lambda log, files: {
                'cause': 'x', 'summary': 'y',
                'files': [{'path': 'tests/test_a.py',
                           'content': 'def test():\n    assert True\n'}]})
        with pytest.raises(autofix.AutofixError, match='保護'):
            autofix.attempt_fix(repo_env['workrepo'], 'feature/x-1',
                                'log', {})

    def test_no_key_raises_friendly_error(self, repo_env, monkeypatch,
                                          gh_mock):
        monkeypatch.setattr(autofix.claude_helper, 'generate_fix',
                            lambda log, files: None)
        with pytest.raises(autofix.AutofixError, match='API キー'):
            autofix.attempt_fix(repo_env['workrepo'], 'feature/x-1',
                                'log', {})
