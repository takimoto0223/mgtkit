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

from manager import claude_helper, reviews, submit
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

    def test_dev_files_are_out_of_dist_scope(self):
        # アプリマネージャーの開発メモ等は配布・提出の対象外
        assert not submit._is_dist_scope('CLAUDE.md')
        assert not submit._is_dist_scope('.gitattributes')
        assert not submit._is_dist_scope('manager/main.py')
        assert submit._is_dist_scope('README.md')
        assert submit._is_dist_scope('起動.bat')

    def test_settings_json_is_always_excluded(self, repo_env, tmp_path):
        # マネージャーの個人設定 (API キー入り) が ZIP に紛れても提出されない
        z = _make_zip(tmp_path, _dist_files(
            repo_env['base_sha'],
            **{'settings.json':
               '{"name": "x", "anthropic_api_key": "sk-ant-xxxx"}'}))
        prep = submit.inspect_zip(z)
        try:
            ch = submit.compute_changes(
                repo_env['workrepo'], repo_env['base_sha'],
                prep['extract_dir'])
            assert 'settings.json' not in ch['added']
            assert 'settings.json' not in ch['modified']
        finally:
            submit.cleanup(prep)

    def test_junk_dirs_are_excluded(self, repo_env, tmp_path):
        # 計算結果 (mgtkit_out) や .git 等が丸ごと ZIP に入っても差分にしない
        z = _make_zip(tmp_path, _dist_files(
            repo_env['base_sha'],
            **{'mgtkit_out/result.csv': 'a,b\n',
               'sub/mgtkit_out/fig.dxf': '0\n',
               '.git/config': '[core]\n',
               'stash/20260812-100000/app.py': 'old\n',
               '__pycache__/app.cpython-311.pyc': 'xx'}))
        prep = submit.inspect_zip(z)
        try:
            ch = submit.compute_changes(
                repo_env['workrepo'], repo_env['base_sha'],
                prep['extract_dir'])
            assert ch['added'] == []
            assert ch['modified'] == []
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


class TestFilterUnsupported:
    """実行ファイル・PDF などはエラーにせず差分から除外する."""

    def test_blocked_and_unexpected_are_skipped(self):
        changes = {'added': ['tool.exe', 'feature.py'],
                   'modified': ['readme/manual.pdf', 'app.py'],
                   'deleted': ['起動.bat'], 'unchanged': []}
        skipped = submit.filter_unsupported(changes, {})
        assert skipped == sorted(['tool.exe', 'readme/manual.pdf',
                                  '起動.bat'])
        assert changes['added'] == ['feature.py']
        assert changes['modified'] == ['app.py']
        assert changes['deleted'] == []

    def test_no_extension_is_kept(self):
        changes = {'added': ['LICENSE'], 'modified': [], 'deleted': [],
                   'unchanged': []}
        assert submit.filter_unsupported(changes, {}) == []
        assert changes['added'] == ['LICENSE']

    def test_only_skipped_changes_is_friendly_error(self, tmp_path,
                                                    monkeypatch):
        # 変更が対象外の種類だけなら、その旨を伝えるエラーになる
        monkeypatch.setattr(submit, 'inspect_zip', lambda p: {
            'tmp': str(tmp_path), 'extract_dir': str(tmp_path),
            'base_version': 'v1', 'base_commit': 'abc'})
        monkeypatch.setattr(submit, 'compute_changes',
                            lambda w, c, e: {'added': [], 'deleted': [],
                                             'modified': ['manual.pdf'],
                                             'unchanged': []})
        with pytest.raises(submit.SubmitError, match='提出対象外の種類'):
            submit.prepare_submission('x.zip', {}, workrepo='wr')


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

    def test_clean_python_file_passes(self, tmp_path):
        result = self._check(tmp_path, {'feature.py': 'def g():\n    pass\n'})
        assert result['blockers'] == []
        assert result['warnings'] == []

    def test_secret_warning(self, tmp_path):
        result = self._check(
            tmp_path, {'config.py': 'api_key = "abcdefghijklmnop"\n'})
        assert any('パスワード様' in w or 'キー' in w
                   for w in result['warnings'])

    def test_api_key_is_blocked_not_warned(self, tmp_path):
        # sk-ant- キーの誤提出は警告でなく即ブロック (流出防止)
        result = self._check(
            tmp_path,
            {'helper.py': 'KEY = "sk-ant-api03-abcdefghijklmnop123"\n'})
        assert any('Claude API キー' in b for b in result['blockers'])

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
            if args[:2] == ['pr', 'edit']:
                return ''
            if args[:2] == ['pr', 'list']:
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

    def test_resubmission_updates_pr_body(self, repo_env, tmp_path,
                                          gh_mock):
        # 初回提出でブランチを作る
        z1 = _make_zip(tmp_path, _dist_files(
            repo_env['base_sha'], **{'app.py': 'print("app v2")\n'}),
            name='first.zip')
        prep = submit.prepare_submission(z1, {}, repo_env['workrepo'])
        first = submit.finalize_submission(prep, [], 'msg', {})

        # 同じブランチへ修正版を積むと、新規 PR は作らず本文を更新する
        z2 = _make_zip(tmp_path, _dist_files(
            repo_env['base_sha'], **{'app.py': 'print("app v3")\n'}),
            name='second.zip')
        prep2 = submit.prepare_submission(z2, {}, repo_env['workrepo'])
        gh_mock.clear()
        result = submit.finalize_submission(
            prep2, [], 'msg2', {}, existing_branch=first['branch'])

        assert result['branch'] == first['branch']
        edits = [c for c in gh_mock if c[:2] == ['pr', 'edit']]
        assert len(edits) == 1
        assert first['branch'] in edits[0]
        assert '--body' in edits[0]
        assert not any(c[:2] == ['pr', 'create'] for c in gh_mock)

    def test_no_changes_rejected(self, repo_env, tmp_path, gh_mock):
        z = _make_zip(tmp_path, _dist_files(repo_env['base_sha']))
        with pytest.raises(submit.SubmitError, match='変更された'):
            submit.prepare_submission(z, {}, repo_env['workrepo'])

    def test_without_use_ai_claude_is_never_called(self, repo_env, tmp_path,
                                                   gh_mock, monkeypatch):
        # 本人が選ばない限り API 使用料が発生しないこと (空欄提出)
        def boom(*a, **k):
            raise AssertionError('use_ai=False で Claude が呼ばれた')
        monkeypatch.setattr(submit.claude_helper,
                            'generate_commit_message', boom)
        monkeypatch.setattr(submit.claude_helper, 'generate_pr_body', boom)
        z = _make_zip(tmp_path, _dist_files(
            repo_env['base_sha'], **{'app.py': 'print("v2")\n'}))
        prep = submit.prepare_submission(z, {}, repo_env['workrepo'])
        result = submit.finalize_submission(prep, [], '', {})
        assert result['commit_message'] == 'v1.0 を基点とした機能追加の提出'
        create = next(c for c in gh_mock if c[:2] == ['pr', 'create'])
        body = create[create.index('--body') + 1]
        assert '## 更新内容' not in body  # 空欄のまま提出

    def test_reviewed_text_goes_into_pr(self, repo_env, tmp_path, gh_mock,
                                        monkeypatch):
        # 自動作成の結果を提出者が手直しすると、その文章が PR 本文に入る
        monkeypatch.setattr(submit.claude_helper, 'generate_pr_body',
                            lambda *a, **k: _AI_BODY)
        z = _make_zip(tmp_path, _dist_files(
            repo_env['base_sha'], **{'app.py': 'print("v2")\n'}))
        prep = submit.prepare_submission(z, {}, repo_env['workrepo'])
        seen = []

        def review(update, limits):
            seen.append((update, limits))
            return ('- 手で直した更新内容', '- 手で直した制限事項')

        submit.finalize_submission(prep, [], 'msg', {}, use_ai=True,
                                   on_review=review)
        assert seen == [('- 二丁山形鋼の断面算定に対応', '- 等辺のみ対応')]
        create = next(c for c in gh_mock if c[:2] == ['pr', 'create'])
        body = create[create.index('--body') + 1]
        assert '- 手で直した更新内容' in body
        assert '- 二丁山形鋼の断面算定に対応' not in body

    def test_review_cancel_leaves_nothing_pushed(self, repo_env, tmp_path,
                                                 gh_mock, monkeypatch):
        # 確認画面で取り消したら、ブランチも PR も残らない
        monkeypatch.setattr(submit.claude_helper, 'generate_pr_body',
                            lambda *a, **k: _AI_BODY)
        z = _make_zip(tmp_path, _dist_files(
            repo_env['base_sha'], **{'app.py': 'print("v2")\n'}))
        prep = submit.prepare_submission(z, {}, repo_env['workrepo'])
        with pytest.raises(submit.SubmitCancelled):
            submit.finalize_submission(prep, [], 'msg', {}, use_ai=True,
                                       on_review=lambda u, li: None)
        heads = run_git(['ls-remote', '--heads', repo_env['origin']])
        assert 'feature/' not in heads
        assert not any(c[:2] == ['pr', 'create'] for c in gh_mock)


_AI_BODY = '''## 更新内容

- 二丁山形鋼の断面算定に対応

## ご利用にあたっての制限事項

- 等辺のみ対応

## 影響範囲

s_check.py を変更

## 変更ファイルの説明

- s_check.py — 断面算定の追加
'''


class TestReviewAiText:
    """自動作成した本文を、提出者が確認・修正してから送れること."""

    def test_user_sections_extracted(self):
        update, limits = submit.user_sections(_AI_BODY)
        assert update == '- 二丁山形鋼の断面算定に対応'
        assert limits == '- 等辺のみ対応'

    def test_body_with_user_sections_keeps_reviewer_sections(self):
        body = submit.body_with_user_sections(
            _AI_BODY, '- 二丁山形鋼(2L)に対応しました', '- 不等辺は未対応')
        assert '## 更新内容\n\n- 二丁山形鋼(2L)に対応しました' in body
        assert ('## ご利用にあたっての制限事項\n\n- 不等辺は未対応'
                in body)
        # 手直しした 2 節だけが入れ替わり、レビュー用の節は順序ごと残る
        assert body.index('## 影響範囲') < body.index('## 変更ファイルの説明')
        assert 's_check.py — 断面算定の追加' in body
        assert '- 二丁山形鋼の断面算定に対応' not in body
        # 手直しした内容がそのままリリースノートへ転載できること
        notes = reviews.release_notes_from_pr(body, 'v1.2')
        assert '- 二丁山形鋼(2L)に対応しました' in notes
        assert '- 不等辺は未対応' in notes
        assert '影響範囲' not in notes


class TestClaudeHelperFallback:
    def test_no_api_key_returns_none(self, monkeypatch):
        monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
        assert claude_helper._client() is None
        assert claude_helper.generate_commit_message('a', 'b') is None
        assert claude_helper.generate_pr_body('a', 'b', 'v1.0') is None


class TestFallbackPrBody:
    """手書き・空欄提出 (Claude なし) の PR 本文が様式を満たすこと."""

    def test_handwritten_becomes_sections(self):
        body = submit.fallback_pr_body(
            '2C 断面の対応\n\n- 断面算定を追加', '等辺のみ対応です',
            'v1.1', '追加: s_check.py')
        assert '## 更新内容\n\n2C 断面の対応' in body
        assert ('## ご利用にあたっての制限事項\n\n等辺のみ対応です'
                in body)
        assert '追加: s_check.py' in body

    def test_limitations_default_to_none_item(self):
        body = submit.fallback_pr_body('改善しました', '', 'v1.1', 'x')
        assert '## ご利用にあたっての制限事項\n\n- なし' in body

    def test_blank_submission_has_no_update_section(self):
        # 空欄のまま提出 → 更新内容の節を作らない (リリースノートも空欄)
        from manager import reviews
        body = submit.fallback_pr_body('', '', 'v1.1', '追加: a.py')
        assert '## 更新内容' not in body
        assert '制限事項' not in body
        assert 'マネージャー経由の提出です (基点: v1.1)' in body
        assert '追加: a.py' in body
        assert reviews.release_notes_from_pr(body, 'v1.2') is None

    def test_limitations_only_still_recorded(self):
        body = submit.fallback_pr_body('', '等辺のみ対応です', 'v1.1', 'x')
        assert '## ご利用にあたっての制限事項\n\n等辺のみ対応です' in body
        assert '## 更新内容' not in body

    def test_release_notes_extract_from_fallback_body(self):
        # 手書きの様式からもリリースノートが機械抽出できる (一気通貫)
        from manager import reviews
        body = submit.fallback_pr_body(
            '2C 断面の対応', '等辺のみ対応です', 'v1.1', '追加: a.py')
        notes = reviews.release_notes_from_pr(body, 'v1.2')
        assert '2C 断面の対応' in notes
        assert '等辺のみ対応です' in notes
        assert '追加: a.py' not in notes  # ファイル一覧は載せない

    def test_release_notes_omit_empty_limitations(self):
        from manager import reviews
        body = submit.fallback_pr_body('改善しました', '', 'v1.1', 'x')
        notes = reviews.release_notes_from_pr(body, 'v1.2')
        assert '制限事項' not in notes
