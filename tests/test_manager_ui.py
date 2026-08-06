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

    def add(self, *controls):
        self.added.extend(controls)

    def update(self):
        pass


def test_main_builds_ui_without_errors():
    from manager import main as manager_main
    page = _FakePage()
    manager_main.main(page)
    assert page.title == 'mgtkit アプリマネージャー'
    # ヘッダー + タブ構造が追加されていること
    assert len(page.added) == 2
