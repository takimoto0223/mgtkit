"""manager/diffview.py (差分ビューワ HTML 生成) のテスト。"""
import pytest

from manager import diffview
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
        assert '更新データをダウンロード' in page
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
        assert 'line15 = 9999' in zf.read(
            '一式/mgtkit/calc.py').decode('utf-8')
        # 説明テキスト: 素性・構成・version.json を入れない理由・注意書き
        readme = zf.read('更新データについて.txt').decode('utf-8')
        assert '#33 の確認用データ' in readme.split('\n')[0]
        assert 'version.json' in readme
        assert '3 つのリスク' in readme

    def test_download_hidden_when_too_large(self, diff_repo, monkeypatch):
        monkeypatch.setattr(diffview, 'MAX_DL_MB', 0)
        page = diffview.build_html(self.META, 'main', 'feature', diff_repo)
        assert '更新データをダウンロード' not in page

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
        """git/gh をモックし、build_html へ渡った meta を記録する."""
        calls = {'git': [], 'meta': None}
        monkeypatch.setattr(diffview, 'run_git',
                            lambda args, cwd=None, timeout=120:
                            calls['git'].append(args) or '')

        def fake_build(meta, b, h, w):
            calls['meta'] = meta
            return '<html>ok</html>'

        monkeypatch.setattr(diffview, 'build_html', fake_build)
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
