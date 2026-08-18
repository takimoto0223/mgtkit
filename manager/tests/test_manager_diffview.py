"""manager/diffview.py (差分ビューワ HTML 生成) のテスト。"""
import pytest

from manager import diffcache, diffview
from manager.gitcli import run_git


class TestSideBySide:
    def test_equal_change_add_del(self):
        old = ['a = 1', 'b = 2', 'c = 3']
        new = ['a = 1', 'b = 9', 'c = 3', 'd = 4']
        rows = diffview.side_by_side(old, new)
        kinds = [r[0] for r in rows]
        assert kinds == ['same', 'change', 'same', 'add']
        # 変更行は左右の行番号がそれぞれ付く
        assert rows[1][1] == 2 and rows[1][3] == 2
        # 追加行は左が空
        assert rows[3][1] is None and rows[3][4] == 'd = 4'

    def test_delete(self):
        rows = diffview.side_by_side(['a', 'b'], ['a'])
        assert [r[0] for r in rows] == ['same', 'del']
        assert rows[1][2] == 'b' and rows[1][3] is None


class TestCollapseContext:
    def test_long_same_run_is_collapsed(self):
        old = ['line%d' % i for i in range(30)]
        new = list(old)
        new[15] = 'changed'
        rows = diffview.collapse_context(diffview.side_by_side(old, new))
        gaps = [r for r in rows if r[0] == 'gap']
        # 変更点の前後 (先頭側・末尾側) が折りたたまれる
        assert len(gaps) == 2
        assert gaps[0][2] == 15 - diffview.MAX_HUNK_CONTEXT
        # 変更行と前後の文脈は残る
        assert any(r[0] == 'change' for r in rows)

    def test_no_gap_for_small_file(self):
        rows = diffview.collapse_context(
            diffview.side_by_side(['a'], ['b']))
        assert all(r[0] != 'gap' for r in rows)


class TestParseFileNotes:
    BODY = ('## 更新内容\n- 断面算定の追加\n\n'
            '## 変更ファイルの説明\n'
            '- s_check.py — TC2_analysis() の断面算定の追加\n'
            '- `mgtkit/util.py` : 共通の丸め処理 round_sig() の追加\n'
            '- data/x.json - 材料定数の更新\n'
            '- 形式が崩れていて説明のない行\n'
            '\n## 注意\n- app.py — これは説明ではない\n')

    def test_parses_dash_backtick_colon(self):
        notes = diffview.parse_file_notes(self.BODY)
        assert notes['s_check.py'] == 'TC2_analysis() の断面算定の追加'
        # `パス` の装飾と mgtkit/ 接頭辞は取り除かれる
        assert notes['util.py'] == '共通の丸め処理 round_sig() の追加'
        assert notes['data/x.json'] == '材料定数の更新'

    def test_stops_at_next_heading(self):
        notes = diffview.parse_file_notes(self.BODY)
        assert 'app.py' not in notes

    def test_missing_section_or_empty_body(self):
        assert diffview.parse_file_notes('## 更新内容\n- x') == {}
        assert diffview.parse_file_notes('') == {}
        assert diffview.parse_file_notes(None) == {}

    def test_fullwidth_space_separator(self):
        body = ('## 変更ファイルの説明\n'
                '- s_check.py　組立断面の算定の追加\n')
        assert diffview.parse_file_notes(body) == {
            's_check.py': '組立断面の算定の追加'}


@pytest.fixture()
def diff_repo(tmp_path):
    """main と feature の 2 ブランチを持つ実 git リポジトリ."""
    repo = tmp_path / 'repo'
    repo.mkdir()

    def g(*args):
        return run_git(list(args), cwd=str(repo))

    g('init', '-b', 'main')
    g('config', 'user.email', 't@example.com')
    g('config', 'user.name', 'テスト')
    lines = ['line%d = %d' % (i, i) for i in range(30)]
    (repo / 'calc.py').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    (repo / 'stable.py').write_text('KEEP = 1\n', encoding='utf-8')
    g('add', '-A')
    g('commit', '-m', 'base')
    g('checkout', '-b', 'feature')
    lines[15] = 'line15 = 9999  # 変更'
    lines.append('line30 = 30')
    (repo / 'calc.py').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    (repo / 'img.png').write_bytes(b'\x89PNG\r\n\x1a\n')
    # 日本語ファイル名 (git の quotepath エスケープ対策の回帰確認用)
    (repo / '集計表.csv').write_text('a,b\n1,2\n', encoding='utf-8')
    g('add', '-A')
    g('commit', '-m', 'change')
    return str(repo)


class TestBuildHtml:
    META = {'number': 33, 'title': '組立断面の対応', 'author': 'fujitaka',
            'beta': 'v1.1-beta.2'}

    def test_contains_summary_and_diff(self, diff_repo):
        page = diffview.build_html(self.META, 'main', 'feature', diff_repo)
        assert '<h1>#33 組立断面の対応</h1>' in page
        assert '提出 #33' not in page      # 見出しに「提出」は付けない
        assert 'フォルダ比較' in page and 'ファイル比較' in page
        assert 'calc.py' in page
        assert 'line15&nbsp;=&nbsp;9999' in page      # 変更後の行
        assert '変更のない' in page                     # 折りたたみ
        assert 'β版 v1.1-beta.2' in page

    def test_binary_is_marked_out_of_scope(self, diff_repo):
        page = diffview.build_html(self.META, 'main', 'feature', diff_repo)
        assert 'img.png' in page
        assert '表示対象外' in page

    def test_huge_file_is_truncated(self, diff_repo, monkeypatch):
        monkeypatch.setattr(diffview, 'MAX_CHANGED_LINES', 1)
        page = diffview.build_html(self.META, 'main', 'feature', diff_repo)
        assert '大きすぎるため' in page

    def test_download_button_with_risk_modal(self, diff_repo):
        page = diffview.build_html(self.META, 'main', 'feature', diff_repo)
        assert 'β版データ (確認用) をダウンロード' in page
        assert 'data:application/zip;base64,' in page
        # クリックで資料 4.2 の 3 リスクをイラスト付きモーダルで確認する
        assert 'id="dlovl"' in page
        assert '基点の消滅' in page
        assert '親リリース後の縮退' in page
        assert 'レビュー責任の曖昧化' in page
        assert 'リスクを理解した上でダウンロード' in page
        assert 'download="#33_確認用.zip"' in page

    def test_download_zip_contents(self, diff_repo):
        import base64
        import io
        import re
        import zipfile
        page = diffview.build_html(self.META, 'main', 'feature', diff_repo)
        m = re.search(r'data:application/zip;base64,([A-Za-z0-9+/=]+)', page)
        assert m
        zf = zipfile.ZipFile(io.BytesIO(base64.b64decode(m.group(1))))
        names = zf.namelist()
        # 一式 = β版まるごと (未変更ファイルも入る)、変更のみ = 差分のみ
        assert '一式/mgtkit/calc.py' in names
        assert '一式/mgtkit/stable.py' in names
        assert '一式/mgtkit/img.png' in names
        assert '変更のみ/mgtkit/calc.py' in names
        assert '変更のみ/mgtkit/img.png' in names
        assert '変更のみ/mgtkit/stable.py' not in names
        # 日本語ファイル名もエスケープされず正しく入る
        assert '一式/mgtkit/集計表.csv' in names
        assert '変更のみ/mgtkit/集計表.csv' in names
        assert 'line15 = 9999' in zf.read(
            '一式/mgtkit/calc.py').decode('utf-8')
        # 説明テキスト: 素性・構成・version.json を入れない理由・注意書き
        readme = zf.read('確認用データについて.txt').decode('utf-8')
        assert '#33 の確認用データ' in readme.split('\n')[0]
        assert 'version.json' in readme
        assert '3 つのリスク' in readme

    def test_full_set_follows_latest_main(self, tmp_path):
        """一式 = 最新の正式版 + この提出の変更.

        基点の後に main 側で削除されたファイルが紛れ込まず、
        main 側で追加されたファイルは入る (実際のβ版と同じ中身)。
        """
        import base64
        import io
        import re
        import zipfile
        repo = tmp_path / 'repo2'
        repo.mkdir()

        def g(*args):
            return run_git(list(args), cwd=str(repo))

        g('init', '-b', 'main')
        g('config', 'user.email', 't@example.com')
        g('config', 'user.name', 'テスト')
        (repo / 'calc.py').write_text('x = 1\n', encoding='utf-8')
        (repo / 'obsolete-spec.md').write_text('# 旧仕様\n',
                                               encoding='utf-8')
        g('add', '-A')
        g('commit', '-m', 'base')
        # 基点から提出ブランチを切って calc.py を変更
        g('checkout', '-b', 'feature')
        (repo / 'calc.py').write_text('x = 2\n', encoding='utf-8')
        g('add', '-A')
        g('commit', '-m', 'change')
        # main 側はその後、旧仕様を削除し新ファイルを追加
        g('checkout', 'main')
        g('rm', '-q', 'obsolete-spec.md')
        (repo / 'newmod.py').write_text('y = 1\n', encoding='utf-8')
        g('add', '-A')
        g('commit', '-m', 'advance main')

        page = diffview.build_html(self.META, 'main', 'feature', str(repo))
        m = re.search(r'data:application/zip;base64,([A-Za-z0-9+/=]+)', page)
        zf = zipfile.ZipFile(io.BytesIO(base64.b64decode(m.group(1))))
        names = zf.namelist()
        assert '一式/mgtkit/obsolete-spec.md' not in names
        assert '一式/mgtkit/newmod.py' in names
        assert zf.read('一式/mgtkit/calc.py').decode('utf-8') == 'x = 2\n'

    def test_download_hidden_when_too_large(self, diff_repo, monkeypatch):
        monkeypatch.setattr(diffview, 'MAX_DL_MB', 0)
        page = diffview.build_html(self.META, 'main', 'feature', diff_repo)
        assert 'β版データ (確認用) をダウンロード' not in page

    def test_file_notes_are_shown(self, diff_repo):
        meta = dict(self.META,
                    notes={'calc.py': '断面算定ロジックの拡張'})
        page = diffview.build_html(meta, 'main', 'feature', diff_repo)
        # フォルダ比較とファイル比較の両方に説明が出る
        assert page.count('断面算定ロジックの拡張') == 2
        assert '提出時に自動生成' in page

    def test_no_notes_no_caveat(self, diff_repo):
        page = diffview.build_html(self.META, 'main', 'feature', diff_repo)
        assert '提出時に自動生成' not in page

    def test_html_escapes_code(self, diff_repo):
        # コード内の <> が HTML として解釈されないこと
        import os
        with open(os.path.join(diff_repo, 'calc.py'), 'a',
                  encoding='utf-8') as f:
            f.write('if a < b: print("<b>")\n')
        run_git(['commit', '-am', 'esc'], cwd=diff_repo)
        page = diffview.build_html(self.META, 'main', 'feature', diff_repo)
        # 検査対象はファイル比較の差分表のみ (後続のモーダルには正当な
        # <b> タグがある)
        tail = page.split('ファイル比較')[1].split('<div class="ovl"')[0]
        assert '<b>' not in tail


class TestWriteDiffHtml:
    @pytest.fixture()
    def env(self, tmp_path, monkeypatch):
        """git/gh をモックし、collect_model へ渡った meta を記録する."""
        calls = {'git': [], 'meta': None}
        monkeypatch.setattr(diffview, 'run_git',
                            lambda args, cwd=None, timeout=120:
                            calls['git'].append(args) or '')

        def fake_collect(meta, b, h, w, merge_base=None):
            calls['meta'] = meta
            return {'number': meta['number']}

        def fake_render(model, w):
            return '<html>ok</html>'

        monkeypatch.setattr(diffview, 'collect_model', fake_collect)
        monkeypatch.setattr(diffview, 'render_html', fake_render)
        monkeypatch.setattr(diffview.tempfile, 'gettempdir',
                            lambda: str(tmp_path))
        monkeypatch.setattr(
            diffview.ghcli, 'run_gh',
            lambda args, timeout=60:
            '## 変更ファイルの説明\n- app.py — CSV 出力の追加\n')
        return calls

    PR = {'number': 33, 'title': 't', 'author': 'a',
          'branch': 'feature/x-1'}

    def test_writes_file_and_returns_path(self, env):
        out = diffview.write_diff_html(self.PR, {'repo': 'o/r'},
                                       workrepo='wr')
        assert out.endswith('mgtkit_diff_33.html')
        with open(out, encoding='utf-8') as f:
            assert f.read() == '<html>ok</html>'
        # 最新の main と提出ブランチを取得してから比較している
        assert env['git'][0][:2] == ['fetch', 'origin']
        assert 'feature/x-1' in env['git'][0]

    def test_pr_body_notes_are_passed(self, env):
        diffview.write_diff_html(self.PR, {'repo': 'o/r'}, workrepo='wr')
        assert env['meta']['notes'] == {'app.py': 'CSV 出力の追加'}

    def test_gh_failure_falls_back_to_no_notes(self, env, monkeypatch):
        def boom(args, timeout=60):
            raise diffview.ghcli.GhError('down')
        monkeypatch.setattr(diffview.ghcli, 'run_gh', boom)
        diffview.write_diff_html(self.PR, {'repo': 'o/r'}, workrepo='wr')
        assert env['meta']['notes'] == {}

    def test_snapshot_body_avoids_gh_call(self, env, monkeypatch):
        # スナップショットに body が同乗していれば gh を呼ばない
        def boom(args, timeout=60):
            raise AssertionError('gh が呼ばれた')
        monkeypatch.setattr(diffview.ghcli, 'run_gh', boom)
        pr = dict(self.PR,
                  body='## 変更ファイルの説明\n- s.py — 断面算定の追加\n')
        diffview.write_diff_html(pr, {'repo': 'o/r'}, workrepo='wr')
        assert env['meta']['notes'] == {'s.py': '断面算定の追加'}


class TestBuildModelFastPath:
    """head_sha / fork_sha が手元にあるときはネットワークに出ない."""

    def test_skips_fetch_when_commits_local(self, diff_repo, monkeypatch):
        head = run_git(['rev-parse', 'feature'], cwd=diff_repo).strip()
        fork = run_git(['rev-parse', 'main'], cwd=diff_repo).strip()
        real = diffview.run_git
        calls = []

        def spy(args, cwd=None, timeout=120):
            calls.append(args)
            return real(args, cwd=cwd, timeout=timeout)
        monkeypatch.setattr(diffview, 'run_git', spy)
        pr = {'number': 7, 'title': 't', 'author': 'a',
              'branch': 'feature', 'head_sha': head, 'fork_sha': fork,
              'body': ''}
        model, _ = diffview.build_model(pr, {'repo': 'o/r'},
                                        workrepo=diff_repo)
        # fetch も merge-base 計算も行われない (分岐点は fork_sha)
        assert not [a for a in calls if a[0] == 'fetch']
        assert not [a for a in calls if a[0] == 'merge-base']
        assert model['head_ref'] == head
        assert any(e['path'] == 'calc.py' for e in model['files'])

    def test_fetches_when_commits_missing(self, diff_repo, monkeypatch):
        calls = []
        real = diffview.run_git

        def spy(args, cwd=None, timeout=120):
            calls.append(args)
            if args[0] == 'fetch':
                return ''       # origin が無いので fetch だけ握りつぶす
            return real(args, cwd=cwd, timeout=timeout)
        monkeypatch.setattr(diffview, 'run_git', spy)
        pr = {'number': 7, 'title': 't', 'author': 'a',
              'branch': 'feature', 'head_sha': 'ffff' * 10,
              'fork_sha': None, 'body': ''}
        monkeypatch.setattr(
            diffview, 'collect_model',
            lambda meta, b, h, w, merge_base=None: {'number': 7,
                                                    'head_ref': h})
        model, _ = diffview.build_model(pr, {'repo': 'o/r'},
                                        workrepo=diff_repo)
        assert [a for a in calls if a[0] == 'fetch']
        # 見つからない head_sha は使わずブランチ参照で比較する
        assert model['head_ref'] == 'origin/feature'

    def test_stale_fork_sha_not_used_with_branch_ref(self, diff_repo,
                                                     monkeypatch):
        """提出が置き換えられて先端がブランチ参照に落ちたら、
        古い分岐点 (fork_sha) は使わず merge-base 計算に戻す.
        (古い分岐点 × 新しい先端だと他人の変更が混ざった差分になる)
        """
        fork = run_git(['rev-parse', 'main'], cwd=diff_repo).strip()
        real = diffview.run_git

        def spy(args, cwd=None, timeout=120):
            if args[0] == 'fetch':
                return ''
            return real(args, cwd=cwd, timeout=timeout)
        monkeypatch.setattr(diffview, 'run_git', spy)
        seen = {}

        def fake_collect(meta, b, h, w, merge_base=None):
            seen['merge_base'] = merge_base
            return {'number': 7}
        monkeypatch.setattr(diffview, 'collect_model', fake_collect)
        pr = {'number': 7, 'title': 't', 'author': 'a',
              'branch': 'feature', 'head_sha': 'ffff' * 10,
              'fork_sha': fork, 'body': ''}
        diffview.build_model(pr, {'repo': 'o/r'}, workrepo=diff_repo)
        assert seen['merge_base'] is None


class TestBatchBlobs:
    """ファイル内容を git 1 回でまとめて取り出す (起動回数の削減)."""

    def test_matches_git_show_and_counts_one_launch(self, diff_repo,
                                                    monkeypatch):
        paths_ = ['calc.py', 'stable.py', '集計表.csv', 'img.png']
        specs = ['feature:%s' % p for p in paths_]
        launches = []
        real = diffview.subprocess.run

        def spy(args, **kw):
            launches.append(args)
            return real(args, **kw)
        monkeypatch.setattr(diffview.subprocess, 'run', spy)
        blobs = diffview.batch_blobs(diff_repo, specs)
        monkeypatch.undo()
        # ファイル数によらず git は 1 回だけ
        assert len(launches) == 1
        # 中身は 1 件ずつ git show したものと同じ (日本語名・バイナリ含む)
        for spec, path in zip(specs, paths_):
            assert blobs[spec] == diffview._git_bytes(
                diff_repo, ['show', 'feature:%s' % path])

    def test_missing_paths_are_skipped(self, diff_repo):
        blobs = diffview.batch_blobs(
            diff_repo, ['feature:calc.py', 'feature:nope.py'])
        assert 'feature:calc.py' in blobs
        assert 'feature:nope.py' not in blobs

    def test_falls_back_to_one_by_one_when_batch_fails(self, diff_repo,
                                                       monkeypatch):
        """まとめ読みが失敗しても中身は必ず取れること.

        ここで取りこぼすと、テキストが全部「バイナリ」扱いになったり、
        中身の無い確認用データを配ったりする。
        """
        real = diffview.subprocess.run

        def broken(args, **kw):
            if args[:2] == ['git', 'cat-file']:
                raise OSError('まとめ読みできない環境')
            return real(args, **kw)
        monkeypatch.setattr(diffview.subprocess, 'run', broken)
        blobs = diffview.batch_blobs(diff_repo, ['feature:calc.py'])
        monkeypatch.undo()
        assert b'line15 = 9999' in blobs['feature:calc.py']

    def test_zip_is_not_built_empty(self, diff_repo, monkeypatch):
        model = diffview.collect_model(
            {'number': 1, 'title': 't', 'author': 'a', 'beta': None},
            'main', 'feature', diff_repo)
        monkeypatch.setattr(diffview, 'batch_blobs',
                            lambda workrepo, specs: {})
        with pytest.raises(diffview.GitError):
            diffview.build_review_zip(model, diff_repo)


class TestCollectModelUnchanged:
    """高速化しても表示内容が 1 行も変わらないこと (回帰の要)."""

    META = {'number': 33, 'title': 't', 'author': 'a', 'beta': None}

    def _slow_reference(self, diff_repo, model):
        """従来方式 (ファイルごとに git show) で同じ行を組み直す."""
        mb = run_git(['merge-base', 'main', 'feature'],
                     cwd=diff_repo).strip()
        out = {}
        for e in model['files']:
            path, status = e['path'], e['status']
            ext = ('.' + path.rsplit('.', 1)[-1].lower()
                   if '.' in path else '')
            old = ([] if status == 'A'
                   else diffview._file_lines(diff_repo, mb, path))
            new = ([] if status == 'D'
                   else diffview._file_lines(diff_repo, 'feature', path))
            if ext in diffview.BINARY_EXTS or old is None or new is None:
                out[path] = None
                continue
            out[path] = diffview.collapse_context(
                diffview.side_by_side(old, new))
        return out

    def test_rows_identical_to_per_file_reading(self, diff_repo):
        model = diffview.collect_model(self.META, 'main', 'feature',
                                       diff_repo)
        want = self._slow_reference(diff_repo, model)
        for e in model['files']:
            assert e['rows'] == want[e['path']], e['path']
        # バイナリ・日本語ファイル名も従来どおりの扱い
        kinds = {e['path']: e['kind'] for e in model['files']}
        assert kinds['img.png'] == 'binary'
        assert kinds['集計表.csv'] == 'diff'

    def test_git_launches_do_not_grow_with_file_count(self, diff_repo,
                                                      monkeypatch):
        # 実際に起動した git の数を数える (subprocess は全モジュール共通)
        launches = []
        real_run = diffview.subprocess.run

        def spy_run(args, **kw):
            launches.append(args)
            return real_run(args, **kw)
        monkeypatch.setattr(diffview.subprocess, 'run', spy_run)
        diffview.collect_model(self.META, 'main', 'feature', diff_repo)
        monkeypatch.undo()
        # merge-base + name-status + まとめ読み 1 回 = 3 回で頭打ち
        # (従来はファイル数 × 2 + 2 回。25 ファイルなら 52 回)
        assert len(launches) <= 3, launches


class TestDialogRowBudget:
    """開くのに数秒かかるのを避ける仕組み (見ない行は作らない)."""

    def _model(self, sizes):
        files = []
        for i, n in enumerate(sizes):
            rows = [('change', j + 1, 'old%d' % j, j + 1, 'new%d' % j)
                    for j in range(n)]
            files.append({'path': 'f%d.py' % i, 'status': 'M',
                          'status_jp': '変更', 'tag_cls': 'tagM',
                          'note': None, 'kind': 'diff', 'adds': n,
                          'dels': n, 'changed': n, 'rows': rows})
        return {'number': 1, 'title': 't', 'author': 'a', 'beta': None,
                'has_notes': False, 'n_add': 0, 'n_del': 0,
                'files': files, 'dl_paths': [], 'dl_deleted': [],
                'base_ref': 'origin/main', 'head_ref': 'h'}

    def test_large_files_after_budget_become_buttons(self):
        diffdialog = pytest.importorskip('manager.diffdialog')
        model = self._model([1000, 1000, 1000])
        items, _ = diffdialog.build_items(
            model, lambda p: None, on_expand=lambda *a: None)
        no_limit, _ = diffdialog.build_items(model, lambda p: None)
        # 3 つ目は後回しになるぶん、項目数がはっきり減る
        assert len(items) < len(no_limit) - 900

    def _expand_labels(self, diffdialog, items):
        """「この差分を表示」ボタンの文言を集める."""
        out = []
        for c in items:
            row = getattr(c, 'content', None)
            ctrls = getattr(row, 'controls', None) or []
            for x in ctrls:
                if isinstance(x, diffdialog.ft.TextButton):
                    out.append('%s / %s' % (ctrls[0].value, x.content))
        return out

    def test_small_files_are_always_shown(self):
        """大きいファイルの後に続く小さいファイルはそのまま出す
        (ボタンだらけにしない)."""
        diffdialog = pytest.importorskip('manager.diffdialog')
        model = self._model([1500, 5, 5, 5])
        items, _ = diffdialog.build_items(
            model, lambda p: None, on_expand=lambda *a: None)
        # 畳むのは大きいファイルの残りだけ (小さい 3 つはそのまま)
        assert len(self._expand_labels(diffdialog, items)) == 1

    def test_huge_single_file_is_partly_shown(self):
        """1 ファイルだけ巨大な提出でも、開いた画面が空にならない."""
        diffdialog = pytest.importorskip('manager.diffdialog')
        model = self._model([4000])
        items, _ = diffdialog.build_items(
            model, lambda p: None, on_expand=lambda *a: None)
        # 目安ぶんは出したうえで、残りはボタンにする
        assert diffdialog.MAX_INITIAL_ROWS * 0.8 < len(items) \
            < diffdialog.MAX_INITIAL_ROWS * 1.2

    def test_empty_file_never_becomes_a_button(self):
        """押しても何も出ないボタンを作らない."""
        diffdialog = pytest.importorskip('manager.diffdialog')
        model = self._model([1500, 1500, 0])
        items, _ = diffdialog.build_items(
            model, lambda p: None, on_expand=lambda *a: None)
        labels = self._expand_labels(diffdialog, items)
        assert all('変更が 0 行' not in x and '残り 0 行' not in x
                   for x in labels), labels

    def test_jump_targets_are_fixed_after_expanding(self):
        """展開したら、後ろのファイルのジャンプ先もその分ずらす.

        直さないと一覧からのジャンプが展開した差分の途中に着地する。
        """
        diffdialog = pytest.importorskip('manager.diffdialog')
        model = self._model([1500, 1500, 5])
        items, offsets = diffdialog.build_items(
            model, lambda p: None, on_expand=lambda *a: None)
        holder = [c for c in items if getattr(c, 'data', None)][-1]
        before = dict(offsets)
        # 畳んでいた 1300 行 (約 21450px) を展開した場合
        diffdialog.shift_offsets(offsets, holder.data, 21450 - 54)
        # 展開した位置より後ろだけがずれる
        assert offsets['f2.py'] == before['f2.py'] + 21450 - 54
        assert offsets['f0.py'] == before['f0.py']

    def test_every_file_can_be_reached(self):
        diffdialog = pytest.importorskip('manager.diffdialog')
        model = self._model([1000, 1000, 1000])
        _, offsets = diffdialog.build_items(
            model, lambda p: None, on_expand=lambda *a: None)
        # 後回しにしたファイルも見出しは出るのでジャンプできる
        assert set(offsets) == {e['path'] for e in model['files']}


class TestDiffCache:
    """再起動をまたぐ保存 (起動直後のクリックも待たせない)."""

    def test_round_trip_restores_rows_as_tuples(self, tmp_path):
        cfg = {'manager': {'install_root': str(tmp_path)}}
        model = {'number': 7, 'files': [
            {'path': 'a.py', 'rows': [('same', 1, 'x', 1, 'x')]},
            {'path': 'b.png', 'rows': None}]}
        diffcache.save('7-aaa-bbb', model, cfg)
        got = diffcache.load('7-aaa-bbb', cfg)
        assert got['files'][0]['rows'] == [('same', 1, 'x', 1, 'x')]
        assert got['files'][1]['rows'] is None

    def test_missing_and_broken_return_none(self, tmp_path):
        cfg = {'manager': {'install_root': str(tmp_path)}}
        assert diffcache.load('nope', cfg) is None
        d = tmp_path / 'diffcache'
        d.mkdir()
        (d / 'broken.json').write_text('{こわれ', encoding='utf-8')
        assert diffcache.load('broken', cfg) is None

    def test_prunes_old_entries(self, tmp_path, monkeypatch):
        cfg = {'manager': {'install_root': str(tmp_path)}}
        monkeypatch.setattr(diffcache, '_MAX_FILES', 3)
        for i in range(6):
            diffcache.save('k%d' % i, {'number': i, 'files': []}, cfg)
        left = list((tmp_path / 'diffcache').glob('*.json'))
        assert len(left) <= 3

    def test_too_large_is_not_saved(self, tmp_path, monkeypatch):
        cfg = {'manager': {'install_root': str(tmp_path)}}
        monkeypatch.setattr(diffcache, '_MAX_BYTES', 10)
        diffcache.save('big', {'number': 1, 'files': [], 'x': 'y' * 100},
                       cfg)
        assert diffcache.load('big', cfg) is None


class TestBuildModelCached:
    def test_same_head_builds_once(self, monkeypatch):
        monkeypatch.setattr(diffview, '_model_cache', {})
        built = []

        def fake_build(pr, config=None, workrepo=None, beta_tag=None):
            built.append(pr['number'])
            return {'number': pr['number'], 'beta': beta_tag}, 'wr'
        monkeypatch.setattr(diffview, 'build_model', fake_build)
        monkeypatch.setattr(diffview.diffcache, 'load',
                            lambda key, config=None: None)
        monkeypatch.setattr(diffview.diffcache, 'save',
                            lambda key, model, config=None: None)
        pr = {'number': 7, 'head_sha': 'abc', 'fork_sha': 'def'}
        m1, _ = diffview.build_model_cached(pr, beta_tag=None)
        m2, _ = diffview.build_model_cached(pr, beta_tag='v1.7-beta.1')
        assert built == [7]                      # 2 回目はキャッシュ
        assert m2['beta'] == 'v1.7-beta.1'       # β版タグは呼び出し側の値

    def test_no_clone_when_nothing_saved(self, monkeypatch):
        """保存済みが無いときに作業クローンを触らないこと.

        触ると、初回起動やテスト環境で本物の clone が走ってしまう。
        """
        monkeypatch.setattr(diffview, '_model_cache', {})
        monkeypatch.setattr(diffview.diffcache, 'load',
                            lambda key, config=None: None)
        monkeypatch.setattr(diffview.diffcache, 'save',
                            lambda key, model, config=None: None)

        def boom(*a, **k):
            raise AssertionError('ensure_work_repo が呼ばれた')
        monkeypatch.setattr(diffview, 'ensure_work_repo', boom)
        monkeypatch.setattr(
            diffview, 'build_model',
            lambda pr, config=None, workrepo=None, beta_tag=None:
            ({'number': 7, 'pinned': True}, 'wr'))
        model, _ = diffview.build_model_cached(
            {'number': 7, 'head_sha': 'a', 'fork_sha': 'f'})
        assert model['number'] == 7

    def test_unpinned_model_is_not_saved(self, monkeypatch):
        """基点を SHA で固定できなかったモデルは保存しない.

        手元の main の状態しだいで内容が変わるため、再起動をまたいで
        出し続けると誤った差分を見せることになる。
        """
        monkeypatch.setattr(diffview, '_model_cache', {})
        saved = []
        monkeypatch.setattr(diffview.diffcache, 'load',
                            lambda key, config=None: None)
        monkeypatch.setattr(diffview.diffcache, 'save',
                            lambda key, model, config=None:
                            saved.append(key))
        monkeypatch.setattr(
            diffview, 'build_model',
            lambda pr, config=None, workrepo=None, beta_tag=None:
            ({'number': 7, 'pinned': False}, 'wr'))
        diffview.build_model_cached({'number': 7, 'head_sha': 'a',
                                     'fork_sha': 'f'})
        assert saved == []

    def test_saved_is_skipped_when_fork_missing(self, monkeypatch):
        """分岐点が手元に無ければ保存済みを使わず組み直す."""
        monkeypatch.setattr(diffview, '_model_cache', {})
        monkeypatch.setattr(diffview.diffcache, 'load',
                            lambda key, config=None: {'number': 7})
        monkeypatch.setattr(diffview.diffcache, 'save',
                            lambda key, model, config=None: None)
        monkeypatch.setattr(diffview, 'ensure_work_repo',
                            lambda slug, d, fetch=True: 'wr')
        monkeypatch.setattr(                       # 先端はあるが分岐点が無い
            diffview, '_have_commits',
            lambda repo, shas: {s: (s == 'a') for s in shas})
        built = []
        monkeypatch.setattr(
            diffview, 'build_model',
            lambda pr, config=None, workrepo=None, beta_tag=None:
            (built.append(1), ({'number': 7, 'pinned': True}, 'wr'))[1])
        diffview.build_model_cached({'number': 7, 'head_sha': 'a',
                                     'fork_sha': 'f'})
        assert built == [1]

    def test_new_head_rebuilds(self, monkeypatch):
        monkeypatch.setattr(diffview, '_model_cache', {})
        built = []

        def fake_build(pr, config=None, workrepo=None, beta_tag=None):
            built.append(pr['head_sha'])
            return {'number': pr['number'], 'beta': beta_tag}, 'wr'
        monkeypatch.setattr(diffview, 'build_model', fake_build)
        monkeypatch.setattr(diffview.diffcache, 'load',
                            lambda key, config=None: None)
        monkeypatch.setattr(diffview.diffcache, 'save',
                            lambda key, model, config=None: None)
        diffview.build_model_cached({'number': 7, 'head_sha': 'a',
                                     'fork_sha': 'f'})
        diffview.build_model_cached({'number': 7, 'head_sha': 'b',
                                     'fork_sha': 'f'})
        assert built == ['a', 'b']               # 提出が更新されたら作り直す


class TestDiffDialogItems:
    """アプリ内ダイアログ (diffdialog) が同じモデルから組み立つこと.

    flet は CI の依存に含めないため、未導入環境では自動スキップされる
    (test_manager_ui.py と同じ方針)。
    """

    META = {'number': 33, 'title': '組立断面の対応', 'author': 'fujitaka',
            'beta': 'v1.1-beta.2'}

    def test_items_and_offsets(self, diff_repo):
        pytest.importorskip('flet')
        from manager import diffdialog
        model = diffview.collect_model(self.META, 'main', 'feature',
                                       diff_repo)
        items, offsets = diffdialog.build_items(model, lambda p: None)
        # 全ファイルにジャンプ先があり、並び順どおり単調増加
        assert set(offsets) == {e['path'] for e in model['files']}
        vals = [offsets[e['path']] for e in model['files']]
        assert vals == sorted(vals)
        # 一覧 + 見出し + ファイルごとの本体で、ファイル数より十分多い
        assert len(items) > len(model['files']) * 2

    def test_model_matches_html_numbers(self, diff_repo):
        # ダイアログとブラウザ (HTML) が同じ数字を出すこと
        model = diffview.collect_model(self.META, 'main', 'feature',
                                       diff_repo)
        page = diffview.render_html(model, diff_repo)
        assert ('+%d' % model['n_add']) in page
        assert ('-%d' % model['n_del']) in page
