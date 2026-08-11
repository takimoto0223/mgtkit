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
        assert '提出 #33' in page and '組立断面の対応' in page
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

    def test_html_escapes_code(self, diff_repo):
        # コード内の <> が HTML として解釈されないこと
        import os
        with open(os.path.join(diff_repo, 'calc.py'), 'a',
                  encoding='utf-8') as f:
            f.write('if a < b: print("<b>")\n')
        run_git(['commit', '-am', 'esc'], cwd=diff_repo)
        page = diffview.build_html(self.META, 'main', 'feature', diff_repo)
        assert '<b>' not in page.split('ファイル比較')[1]


class TestWriteDiffHtml:
    def test_writes_file_and_returns_path(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(diffview, 'run_git',
                            lambda args, cwd=None, timeout=120:
                            calls.append(args) or '')
        monkeypatch.setattr(diffview, 'build_html',
                            lambda meta, b, h, w: '<html>ok</html>')
        monkeypatch.setattr(diffview.tempfile, 'gettempdir',
                            lambda: str(tmp_path))
        pr = {'number': 33, 'title': 't', 'author': 'a',
              'branch': 'feature/x-1'}
        out = diffview.write_diff_html(pr, {'repo': 'o/r'}, workrepo='wr')
        assert out.endswith('mgtkit_diff_33.html')
        with open(out, encoding='utf-8') as f:
            assert f.read() == '<html>ok</html>'
        # 最新の main と提出ブランチを取得してから比較している
        assert calls[0][:2] == ['fetch', 'origin']
        assert 'feature/x-1' in calls[0]
