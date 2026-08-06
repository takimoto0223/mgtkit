"""manager/submit.py (提出パイプライン) のテスト。

ローカルの bare リポジトリを origin に見立てて git 操作を実際に行い、
gh CLI (ユーザー名取得・PR 作成) はモックする。
"""
import datetime
import json
import os
import subprocess
import zipfile

import pytest

from manager import claude_helper, submit
from manager.gitcli import run_git


def _git(args, cwd):
    subprocess.run(['git'] + args, cwd=cwd, check=True,
                   capture_output=True, text=True)


BASE_FILES = {
    'app.py': 'print("app v1")\n',
    'util.py': 'def f():\n    return 1\n',
    'data/x.json': '{"a": 1}\n',
    'requirements.txt': 'flask==3.1.3\n',
    'tests/test_x.py': 'def test():\n    pass\n',   # 配布対象外 (開発用)
    'docs/spec.md': '# spec\n',                      # 配布対象外
}


@pytest.fixture()
def repo_env(tmp_path):
    """bare origin + 作業クローン + 基点コミット SHA を用意する."""
    origin = tmp_path / 'origin.git'
    _git(['init', '--bare', '-b', 'main', str(origin)], cwd=tmp_path)

    seed = tmp_path / 'seed'
    seed.mkdir()
    _git(['init', '-b', 'main', '.'], cwd=seed)
    for rel, content in BASE_FILES.items():
        p = seed / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding='utf-8')
    _git(['add', '-A'], cwd=seed)
    _git(['-c', 'user.name=t', '-c', 'user.email=t@example.com',
          'commit', '-m', 'base'], cwd=seed)
    base_sha = subprocess.run(
        ['git', 'rev-parse', 'HEAD'], cwd=seed, check=True,
        capture_output=True, text=True).stdout.strip()
    _git(['remote', 'add', 'origin', str(origin)], cwd=seed)
    _git(['push', 'origin', 'main'], cwd=seed)

    workrepo = tmp_path / 'workrepo'
    _git(['clone', str(origin), str(workrepo)], cwd=tmp_path)
    return {'origin': str(origin), 'workrepo': str(workrepo),
            'base_sha': base_sha, 'tmp': tmp_path}


def _make_zip(tmp_path, files, name='submission.zip'):
    zpath = tmp_path / name
    with zipfile.ZipFile(zpath, 'w') as zf:
        for rel, content in files.items():
            zf.writestr(rel, content)
    return str(zpath)


def _dist_files(base_sha, **overrides):
    """基点の配布相当ファイル一式 + version.json に上書きを適用."""
    files = {rel: content for rel, content in BASE_FILES.items()
             if not rel.startswith(('tests/', 'docs/'))}
    files['version.json'] = json.dumps(
        {'version': 'v1.0', 'commit': base_sha,
         'distributed_at': '2026-08-06'})
    for rel, content in overrides.items():
        if content is None:
            files.pop(rel, None)
        else:
            files[rel] = content
    return files


class TestInspectZip:
    def test_missing_version_json_rejected(self, tmp_path):
        z = _make_zip(tmp_path, {'app.py': 'x'})
        with pytest.raises(submit.SubmitError, match='version.json'):
            submit.inspect_zip(z)

    def test_missing_commit_rejected(self, tmp_path):
        z = _make_zip(tmp_path, {
            'app.py': 'x',
            'version.json': '{"version": "v1.0"}'})
        with pytest.raises(submit.SubmitError, match='基点'):
            submit.inspect_zip(z)

    def test_reads_base_info(self, tmp_path):
        z = _make_zip(tmp_path, {
            'app.py': 'x',
            'version.json':
                '{"version": "v1.2", "commit": "abc123"}'})
        prep = submit.inspect_zip(z)
        try:
            assert prep['base_version'] == 'v1.2'
            assert prep['base_commit'] == 'abc123'
        finally:
            submit.cleanup(prep)


class TestComputeChanges:
    def test_classification(self, repo_env, tmp_path):
        z = _make_zip(tmp_path, _dist_files(
            repo_env['base_sha'],
            **{'app.py': 'print("app v2")\n',      # 変更
               'new_feature.py': 'print("new")\n',  # 追加
               'util.py': None}))                   # 削除
        prep = submit.inspect_zip(z)
        try:
            ch = submit.compute_changes(
                repo_env['workrepo'], repo_env['base_sha'],
                prep['extract_dir'])
            assert ch['added'] == ['new_feature.py']
            assert ch['modified'] == ['app.py']
            assert ch['deleted'] == ['util.py']
            assert 'data/x.json' in ch['unchanged']
            # 開発用ファイル (tests/, docs/) は ZIP に無くても削除扱いしない
            assert not any(d.startswith(('tests/', 'docs/'))
                           for d in ch['deleted'])
        finally:
            submit.cleanup(prep)

    def test_unknown_base_commit_rejected(self, repo_env, tmp_path):
        z = _make_zip(tmp_path, {
            'app.py': 'x',
            'version.json':
                '{"version": "v1.0", "commit": "%s"}' % ('0' * 40)})
        prep = submit.inspect_zip(z)
        try:
            with pytest.raises(submit.SubmitError, match='履歴に見つかりません'):
                submit.compute_changes(
                    repo_env['workrepo'], '0' * 40, prep['extract_dir'])
        finally:
            submit.cleanup(prep)


class TestSafetyCheck:
    def _check(self, tmp_path, files, changes=None):
        d = tmp_path / 'extract'
        d.mkdir(exist_ok=True)
        for rel, content in files.items():
            p = d / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            data = content.encode() if isinstance(content, str) else content
            p.write_bytes(data)
        changes = changes or {'added': list(files), 'modified': [],
                              'deleted': [], 'unchanged': []}
        return submit.safety_check(changes, str(d), {})

    def test_blocked_executable(self, tmp_path):
        result = self._check(tmp_path, {'tool.exe': b'MZ'})
        assert any('実行ファイル' in b for b in result['blockers'])

    def test_unexpected_extension(self, tmp_path):
        result = self._check(tmp_path, {'data.xlsx': b'PK'})
        assert any('想定外' in b for b in result['blockers'])

    def test_clean_python_file_passes(self, tmp_path):
        result = self._check(tmp_path, {'feature.py': 'def g():\n    pass\n'})
        assert result['blockers'] == []
        assert result['warnings'] == []

    def test_secret_warning(self, tmp_path):
        result = self._check(
            tmp_path, {'config.py': 'api_key = "abcdefghijklmnop"\n'})
        assert any('パスワード様' in w or 'キー' in w
                   for w in result['warnings'])

    def test_requirements_change_warned(self, tmp_path):
        result = self._check(tmp_path,
                             {'requirements.txt': 'flask==3.1.3\nrich==1.0\n'})
        assert any('requirements.txt' in w for w in result['warnings'])

    def test_size_limit(self, tmp_path):
        result = self._check(tmp_path, {'big.py': 'x' * 1024})
        assert result['blockers'] == []
        d = tmp_path / 'extract'
        changes = {'added': ['big.py'], 'modified': [], 'deleted': [],
                   'unchanged': []}
        out = submit.safety_check(changes, str(d),
                                  {'manager': {'max_upload_mb': 0.0001}})
        assert any('合計サイズ' in b for b in out['blockers'])


class TestFullFlow:
    @pytest.fixture()
    def gh_mock(self, monkeypatch):
        calls = []

        def fake_run_gh(args, timeout=60):
            calls.append(args)
            if args[:2] == ['api', 'user']:
                return 'testuser\n'
            if args[:2] == ['pr', 'create']:
                return 'https://github.com/o/r/pull/99\n'
            raise AssertionError('unexpected gh call: %r' % args)

        monkeypatch.setattr(submit.ghcli, 'run_gh', fake_run_gh)
        # Claude 生成はネットワークを使わないようフォールバックへ固定
        monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
        return calls

    def test_submit_end_to_end(self, repo_env, tmp_path, gh_mock):
        z = _make_zip(tmp_path, _dist_files(
            repo_env['base_sha'],
            **{'app.py': 'print("app v2")\n',
               'new_feature.py': 'print("new")\n',
               'util.py': None,        # 意図的削除にする
               'data/x.json': None}))  # 入れ忘れにする
        prep = submit.prepare_submission(z, {}, repo_env['workrepo'])
        assert prep['safety']['blockers'] == []
        assert set(prep['changes']['deleted']) == {'util.py', 'data/x.json'}

        result = submit.finalize_submission(
            prep, ['util.py'], 'テスト提出\n\n- 機能追加', {})

        today = datetime.date.today().strftime('%Y%m%d')
        assert result['branch'] == 'feature/testuser-%s-1' % today
        assert result['pr_url'].endswith('/pull/99')

        # origin へ push されていること
        heads = run_git(['ls-remote', '--heads', repo_env['origin']])
        assert result['branch'] in heads

        # ブランチ内容: 変更反映 / 意図的削除は消え / 入れ忘れは維持
        workrepo = repo_env['workrepo']
        tree = run_git(['ls-tree', '-r', '--name-only', result['branch']],
                       cwd=workrepo).splitlines()
        assert 'new_feature.py' in tree
        assert 'util.py' not in tree
        assert 'data/x.json' in tree
        content = run_git(['show', '%s:app.py' % result['branch']],
                          cwd=workrepo)
        assert 'app v2' in content
        # 開発用ファイルは基点のまま維持される
        assert 'tests/test_x.py' in tree

    def test_sequence_number_increments(self, repo_env, tmp_path, gh_mock):
        today = datetime.date.today().strftime('%Y%m%d')
        for expected_seq in (1, 2):
            z = _make_zip(tmp_path, _dist_files(
                repo_env['base_sha'],
                **{'app.py': 'print("rev %d")\n' % expected_seq}),
                name='s%d.zip' % expected_seq)
            prep = submit.prepare_submission(z, {}, repo_env['workrepo'])
            result = submit.finalize_submission(prep, [], 'msg', {})
            assert result['branch'] == (
                'feature/testuser-%s-%d' % (today, expected_seq))

    def test_no_changes_rejected(self, repo_env, tmp_path, gh_mock):
        z = _make_zip(tmp_path, _dist_files(repo_env['base_sha']))
        with pytest.raises(submit.SubmitError, match='変更された'):
            submit.prepare_submission(z, {}, repo_env['workrepo'])


class TestClaudeHelperFallback:
    def test_no_api_key_returns_none(self, monkeypatch):
        monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
        assert claude_helper._client() is None
        assert claude_helper.generate_commit_message('a', 'b') is None
        assert claude_helper.generate_pr_body('a', 'b', 'v1.0') is None
