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
