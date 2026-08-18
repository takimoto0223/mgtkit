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


def _walk_texts(control, out):
    """コントロール木の中の Text の文字列を集める (タブの中身の判別用)."""
    value = getattr(control, 'value', None)
    if isinstance(value, str):
        out.append(value)
    for name in ('content', 'label', 'title'):
        child = getattr(control, name, None)
        if isinstance(child, str):
            out.append(child)
        elif child is not None:
            _walk_texts(child, out)
    for name in ('controls', 'tabs', 'actions'):
        children = getattr(control, name, None) or []
        for child in children:
            _walk_texts(child, out)
    return out


def _tab_names(tab_bar):
    names = []
    for tab in tab_bar.tabs:
        label = tab.label
        names.append(label if isinstance(label, str)
                     else _walk_texts(label, [])[0])
    return names


def test_tabs_are_ordered_by_how_often_they_are_used(monkeypatch):
    """タブの並びは 起動 / β版の確認と承認 / 更新版を提出.

    提出は確認・承認より頻度が低いという管理者の判断で右端に置く。
    名前の並びと中身の並びは別々の list なので、片方だけ入れ替えて
    「名前と中身が食い違う」事故が起きないよう両方を固定する。
    """
    from manager import main as manager_main
    monkeypatch.setattr(manager_main.selfupdate, 'auto_update',
                        lambda *a, **k: {'stashed': []})
    page = _FakePage()
    manager_main.main(page)

    column = page.added[1].content
    tab_bar, tab_view = column.controls[0], column.controls[1]
    assert _tab_names(tab_bar) == ['起動', 'β版の確認と承認', '更新版を提出']

    # 中身も同じ並びであること (各タブにしかない文言で見分ける)
    panels = [' '.join(_walk_texts(c, [])) for c in tab_view.controls]
    assert '過去の更新ログ' in panels[0]
    assert 'β版は安定版とは別フォルダ' in panels[1]
    assert '提出済みの検証状況' in panels[2]


def test_opening_the_review_tab_refreshes_the_list(monkeypatch):
    """β版の確認と承認タブを開いたら承認待ち一覧を取り直すこと.

    並びを変えると on_tab_change の番号がずれ、「タブを開いても
    更新されない」に化ける (手動の更新ボタンは置いていない)。
    """
    import types

    from manager import main as manager_main
    monkeypatch.setattr(manager_main.selfupdate, 'auto_update',
                        lambda *a, **k: {'stashed': []})
    page = _FakePage()
    manager_main.main(page)

    # 裏スレッドはその場で最後まで走らせる (取得が呼ばれたか見るため)
    class _NowThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(manager_main.threading, 'Thread', _NowThread)
    called = []

    def _fetch(config, on_progress=None, known_forks=None):
        called.append(True)
        raise manager_main.reviews.ReviewError('テストでは取得しない')

    monkeypatch.setattr(manager_main.reviews, 'fetch_snapshot', _fetch)

    page.added[1].on_change(types.SimpleNamespace(
        control=types.SimpleNamespace(selected_index=1)))
    assert called, '承認待ち一覧の取得が呼ばれていない'


def _walk_controls(control, out):
    """コントロール木を平らに集める (ボタンを名前で探すため)."""
    out.append(control)
    for name in ('content', 'label'):
        child = getattr(control, name, None)
        if child is not None and not isinstance(child, str):
            _walk_controls(child, out)
    for name in ('controls', 'tabs', 'actions'):
        for child in getattr(control, name, None) or []:
            _walk_controls(child, out)
    return out


def _dialog_button(dialog, label):
    """ダイアログの操作ボタンを名前で取り出す."""
    for c in dialog.actions or []:
        if getattr(c, 'content', None) == label:
            return c
    raise AssertionError('「%s」ボタンが見つかりません' % label)


def _launch_button(page):
    for c in _walk_controls(page.added[1], []):
        if getattr(c, 'content', None) == '起動' and getattr(c, 'on_click',
                                                            None):
            return c
    raise AssertionError('「起動」ボタンが見つかりません')


class _NowThread:
    """裏の処理をその場で最後まで走らせる (取り込みの流れを見るため)."""

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


class _NoTimer:
    """定期確認の予約はテストでは動かさない."""

    def __init__(self, *a, **k):
        self.daemon = False

    def start(self):
        pass


def test_launch_after_an_update_tells_where_the_new_version_landed(
        monkeypatch):
    """更新版を取り込んだあと最初の「起動」で取り込み先を知らせること.

    黄色いタグだけでは見落とすという管理者の指摘への対応。同じ版で
    二度は出さない (押すたびに出ると邪魔になる)。
    """
    from manager import main as manager_main
    from manager import paths

    monkeypatch.setattr(manager_main.selfupdate, 'auto_update',
                        lambda *a, **k: {'stashed': []})
    monkeypatch.setattr(manager_main.threading, 'Thread', _NowThread)
    monkeypatch.setattr(manager_main.threading, 'Timer', _NoTimer)

    snap = {'pending': [], 'releases': [], 'me': 'yamada-taro', 'merged': []}
    monkeypatch.setattr(manager_main.reviews, 'fetch_snapshot',
                        lambda *a, **k: dict(snap))
    monkeypatch.setattr(manager_main.reviews, 'fork_memo', lambda *a, **k: {})
    monkeypatch.setattr(manager_main.reviewcache, 'put',
                        lambda *a, **k: dict(snap))
    monkeypatch.setattr(manager_main.reviewcache, 'load_from_disk',
                        lambda *a, **k: None)

    latest = {'tag': 'v1.2', 'prerelease': False, 'notes': ''}
    installed, launched = [], []
    monkeypatch.setattr(manager_main.updater, 'check_update',
                        lambda *a, **k: {'has_update': True,
                                         'latest': latest})
    monkeypatch.setattr(manager_main.updater, 'install_release',
                        lambda *a, **k: installed.append(latest['tag']))
    monkeypatch.setattr(manager_main.updater, 'local_version_info',
                        lambda *a, **k: {'version': 'v1.2',
                                         'distributed_at': '2026-08-18'})
    monkeypatch.setattr(manager_main.launcher, 'port_in_use',
                        lambda *a, **k: False)
    monkeypatch.setattr(
        manager_main.launcher, 'launch_app',
        lambda *a, **k: (launched.append(True),
                         (None, 'http://127.0.0.1:8765/'))[1])

    page = _FakePage()
    manager_main.main(page)
    assert installed == ['v1.2']          # 起動時に自動で取り込まれた
    before = len(page.dialogs)            # 初回登録ダイアログの分

    _launch_button(page).on_click(None)
    assert len(page.dialogs) == before + 1
    assert not launched                   # 読み終えるまでブラウザは開かない
    told = ' '.join(_walk_texts(page.dialogs[-1], []))
    assert 'ダウンロード' in told
    assert 'v1.2' in told
    assert paths.app_dir(paths.stable_dir(paths.load_config())) in told

    _dialog_button(page.dialogs[-1], 'アプリを開く').on_click(None)
    assert launched                       # 読み終えてから開く
    assert len(page.dialogs) == before     # 閉じてから札を下ろす

    _launch_button(page).on_click(None)
    assert len(page.dialogs) == before     # 同じ版で二度は出さない
    assert len(launched) == 2
