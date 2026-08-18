"""manager/main.py の UI 構築テスト。

flet の API 変更 (属性名・シグネチャ) による構築時エラーを検出する。
flet は CI の依存に含めないため、未導入環境では自動スキップされる
(ローカルの開発 venv では manager/requirements.txt 導入後に実行される)。
"""
import pytest

flet = pytest.importorskip('flet')


class _FakeWindow:
    width = None
    height = None


class _FakePage:
    """ft.Page の代替。main() が触る属性/メソッドだけ持つ."""

    def __init__(self):
        self.title = ''
        self.padding = None
        self.window = _FakeWindow()
        self.services = []
        self.added = []
        self.dialogs = []
        self.tasks = []
        self.updates = 0

    def add(self, *controls):
        self.added.extend(controls)

    def show_dialog(self, dialog):
        self.dialogs.append(dialog)

    def pop_dialog(self):
        if self.dialogs:
            self.dialogs.pop()

    def update(self, *controls):
        self.updates += 1

    def run_task(self, handler, *args, **kwargs):
        # 実物は画面のループ上で実行する。ここでは同じ効果になるよう
        # その場で最後まで走らせる (中身が実行されることまで確かめる)
        import asyncio
        self.tasks.append(handler)
        asyncio.run(handler(*args, **kwargs))


def test_main_builds_ui_without_errors(monkeypatch):
    from manager import main as manager_main
    # 起動時の自動最新化はテストでは動かさない (実リポジトリに触るため)
    monkeypatch.setattr(manager_main.selfupdate, 'auto_update',
                        lambda *a, **k: {'stashed': []})
    page = _FakePage()
    manager_main.main(page)
    assert page.title == 'mgtkit アプリマネージャー'
    # ヘッダー + タブ構造が追加されていること
    assert len(page.added) == 2


def test_ui_updates_from_background_go_through_the_loop(monkeypatch):
    """裏スレッドからの画面更新は必ず画面のループ上で行うこと.

    直接書き換えると送信キューに積まれるだけで、利用者が次に何か
    操作するまで実機 (デスクトップ) に届かない。
    """
    import threading

    from manager import main as manager_main
    monkeypatch.setattr(manager_main.selfupdate, 'auto_update',
                        lambda *a, **k: {'stashed': []})
    page = _FakePage()
    manager_main.main(page)
    marker = object()
    before_updates, before_tasks = page.updates, len(page.tasks)

    # 裏スレッド (ループの外) からの更新・ダイアログ操作
    def work():
        page.update()
        page.show_dialog(marker)
        page.pop_dialog()
    t = threading.Thread(target=work)
    t.start()
    t.join(5)

    # ループ上で実行され、かつ中身がちゃんと効いていること
    assert len(page.tasks) >= before_tasks + 3
    assert page.updates > before_updates
    assert marker not in page.dialogs      # 開いて閉じたので残らない


def test_ui_updates_on_the_page_loop_are_direct(monkeypatch):
    """画面のループ上 (イベントハンドラ) からの更新は載せ替えないこと.

    毎回載せ替えると順序が狂い、押した瞬間の反応も 1 拍遅れる。
    """
    import asyncio
    import types

    from manager import main as manager_main
    monkeypatch.setattr(manager_main.selfupdate, 'auto_update',
                        lambda *a, **k: {'stashed': []})
    page = _FakePage()
    manager_main.main(page)
    loop = asyncio.new_event_loop()
    page.session = types.SimpleNamespace(
        connection=types.SimpleNamespace(loop=loop))
    tasks, updates = len(page.tasks), page.updates

    async def from_the_loop():
        page.update()
    try:
        loop.run_until_complete(from_the_loop())
    finally:
        loop.close()
    assert len(page.tasks) == tasks        # 載せ替えていない
    assert page.updates == updates + 1     # その場で実行された
