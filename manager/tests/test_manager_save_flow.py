"""保存 (ダウンロード) の共通ルールのテスト。

ダウンロードの印を押したら、まず**どこに保存するか**をたずねてから
作業を始める (管理者指示 2026-08)。勝手にダウンロードフォルダへ置くと
あとで探せなくなるため。取り消したときは何も書かないこと。

flet は CI の依存に含めないため、未導入環境では自動スキップされる。
"""
import asyncio
import os
import subprocess
import time

import pytest

pytest.importorskip('flet')

from manager import diffdialog, diffview                    # noqa: E402
from manager import main as manager_main                    # noqa: E402

META = {'number': 33, 'title': '組立断面の対応', 'author': 'fujitaka',
        'beta': 'v1.1-beta.2'}


@pytest.fixture
def repo(tmp_path):
    """基点 (main) と提出 (feature) がある小さな作業リポジトリ."""
    root = tmp_path / 'work'
    root.mkdir()

    def g(*args):
        subprocess.run(('git',) + args, cwd=str(root), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    g('init', '-b', 'main')
    g('config', 'user.email', 't@example.com')
    g('config', 'user.name', 'tester')
    (root / 'calc.py').write_text('a = 1\n', encoding='utf-8')
    g('add', '-A')
    g('commit', '-m', 'base')
    g('checkout', '-b', 'feature')
    (root / 'calc.py').write_text('a = 2\n', encoding='utf-8')
    g('add', '-A')
    g('commit', '-m', 'change')
    return str(root)


class _FakePage:
    """ft.Page の代替 (build_dialog と保存の流れが触る分だけ)."""

    web = False
    platform = None
    theme = None
    window = None

    def __init__(self):
        self.updates = 0
        self.dialogs = []

    def update(self, *a):
        self.updates += 1

    def show_dialog(self, d):
        self.dialogs.append(d)

    def pop_dialog(self):
        if self.dialogs:
            self.dialogs.pop()


def _save_handler(page, model, repo, asked, chosen):
    """差分ダイアログを組み、「保存」ボタンの on_click を取り出す."""
    def ask(file_name, title=None):
        asked.append((file_name, title))

        async def _():
            return chosen
        return _()

    dlg = diffdialog.build_dialog(page, model, repo, (900, 600),
                                  run_ui=lambda fn, **k: fn(),
                                  ask_save_path=ask)
    # 「確認用データを保存...」→ リスク確認 → 「リスクを理解した上で保存」
    save_btn = next(a for a in dlg.actions
                    if getattr(a, 'content', None) == '確認用データを保存...')
    save_btn.on_click(None)
    risk = page.dialogs[-1]
    return next(a for a in risk.actions
                if getattr(a, 'content', None) == 'リスクを理解した上で保存')


@pytest.fixture
def model(repo):
    return diffview.collect_model(META, 'main', 'feature', repo)


def test_asks_where_to_save_before_building(model, repo, tmp_path):
    """保存先を先にたずね、選ばれた場所に書くこと."""
    page = _FakePage()
    dest = str(tmp_path / 'えらんだ場所' / '確認用.zip')
    os.makedirs(os.path.dirname(dest))
    asked = []
    btn = _save_handler(page, model, repo, asked, dest)
    asyncio.run(btn.on_click(None))
    assert asked, '保存先をたずねていない'
    assert asked[0][0] == '#33_確認用.zip'      # 既定のファイル名
    # ZIP の組み立ては裏スレッド (画面を止めないため) なので待つ
    for _ in range(200):
        if os.path.exists(dest):
            break
        time.sleep(0.05)
    assert os.path.exists(dest), '選んだ場所に保存されていない'


def test_cancel_writes_nothing(model, repo, tmp_path, monkeypatch):
    """保存先の選択を取り消したら、何も作らず何も書かないこと."""
    page = _FakePage()

    def _boom(*a, **k):
        raise AssertionError('取り消したのに ZIP を作っている')
    monkeypatch.setattr(diffview, 'build_review_zip', _boom)
    asked = []
    btn = _save_handler(page, model, repo, asked, None)
    asyncio.run(btn.on_click(None))
    assert asked, '保存先をたずねていない'
    time.sleep(0.3)                       # 裏で走り出していないことも見る
    assert not list(tmp_path.rglob('*.zip'))


class _FakePicker:
    """OS の保存画面の代わり (何を渡して呼ばれたかを控える)."""

    def __init__(self, result='/tmp/えらんだ.zip', raises=None):
        self.result, self.raises, self.calls = result, raises, []

    async def save_file(self, **kw):
        self.calls.append(kw)
        if self.raises is not None:
            raise self.raises
        return self.result


class TestSavePathDialog:
    """提出の「提出する ZIP を選択」と同じ、OS の画面を出すこと."""

    def test_opens_the_os_dialog(self):
        picker = _FakePicker()
        got = asyncio.run(manager_main.save_path_dialog(
            picker, 'mgtkit-v1.2.zip', '保存先'))
        assert got == '/tmp/えらんだ.zip'
        assert len(picker.calls) == 1, 'OS の画面を出していない'
        kw = picker.calls[0]
        assert kw['file_name'] == 'mgtkit-v1.2.zip'   # 名前の初期値
        assert kw['dialog_title'] == '保存先'
        assert kw['allowed_extensions'] == ['zip']

    def test_cancel_returns_none(self):
        """取り消し (None) はそのまま返し、置き場所を勝手に決めないこと."""
        assert asyncio.run(manager_main.save_path_dialog(
            _FakePicker(result=None), 'a.zip')) is None

    def test_falls_back_only_when_dialog_is_impossible(self, monkeypatch,
                                                       tmp_path):
        """OS の画面を出せない環境 (web) でだけ既定の置き場所へ."""
        monkeypatch.setattr(manager_main, 'downloads_dir',
                            lambda: str(tmp_path))
        got = asyncio.run(manager_main.save_path_dialog(
            _FakePicker(raises=ValueError('web')), 'a.zip'))
        assert got == str(tmp_path / 'a.zip')

    def test_other_failures_are_not_swallowed(self):
        """それ以外の失敗は握りつぶさない (黙って別の場所に保存しない)."""
        with pytest.raises(RuntimeError):
            asyncio.run(manager_main.save_path_dialog(
                _FakePicker(raises=RuntimeError('boom')), 'a.zip'))


class TestUniquePath:
    def test_shifts_when_the_name_is_taken(self, tmp_path):
        p = tmp_path / 'a.zip'
        p.write_text('x')
        assert manager_main.unique_path(str(p)) == str(tmp_path / 'a (2).zip')

    def test_keeps_the_name_when_free(self, tmp_path):
        p = str(tmp_path / 'b.zip')
        assert manager_main.unique_path(p) == p
