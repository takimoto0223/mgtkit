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


class _NoThread:
    """裏の処理を走らせない (走る時機で結果が変わるのを断つ)."""

    def __init__(self, target=None, daemon=None):
        pass

    def start(self):
        pass


def test_ui_updates_on_the_page_loop_are_direct(monkeypatch):
    """画面のループ上 (イベントハンドラ) からの更新は載せ替えないこと.

    毎回載せ替えると順序が狂い、押した瞬間の反応も 1 拍遅れる。

    裏の処理は走らせない。起動時の確認 (新しい版・参加状態) は裏
    スレッドで動き、終わったときに画面を更新する。その更新は
    「ループ上ではない」ので run_task に載る = ここで数えている
    tasks が 1 増える。いつ終わるかは gh の応答と CI の混み具合しだい
    なので、走らせたままだと計測の窓に入るかどうかが運になり、
    負荷の高いときだけ落ちるテストになる (実際に落ちた)。
    """
    import asyncio
    import types

    from manager import main as manager_main
    monkeypatch.setattr(manager_main.selfupdate, 'auto_update',
                        lambda *a, **k: {'stashed': []})
    monkeypatch.setattr(manager_main.threading, 'Thread', _NoThread)
    monkeypatch.setattr(manager_main.threading, 'Timer', _NoTimer)
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


def _page_with_a_downloaded_update(monkeypatch):
    """更新版を自動で取り込み終えた直後の画面を作る (取得・起動は偽物).

    戻り値: (page, launched)。launched にはアプリを開いた回数が入る。
    """
    from manager import main as manager_main

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
    return page, launched


def test_launch_after_an_update_tells_where_the_new_version_landed(
        monkeypatch):
    """更新版が届いたあと最初の「起動」で取り込み先を知らせること.

    黄色いタグだけでは見落とすという管理者の指摘への対応。知らせを
    読み終えて (ボタンを押して) からアプリを開き、同じ版で二度は
    出さない。
    """
    from manager import paths

    page, launched = _page_with_a_downloaded_update(monkeypatch)
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


def test_closing_the_update_notice_leaves_the_launch_button_usable(
        monkeypatch):
    """知らせをボタン以外で閉じても「起動」が押せなくならないこと.

    閉じ方によっては (Esc など) ボタンが無効のまま固まり、マネージャーを
    開き直すまで起動できなくなる。そのときは知らせを未読のまま残し、
    次の「起動」でもう一度出す。
    """
    page, launched = _page_with_a_downloaded_update(monkeypatch)
    before = len(page.dialogs)
    button = _launch_button(page)

    button.on_click(None)
    dialog = page.dialogs[-1]
    assert button.disabled                 # 知らせを出しているあいだは止める

    page.pop_dialog()                      # ボタンを押さずに閉じられた
    dialog.on_dismiss(None)
    assert not button.disabled             # 押せる状態に戻る
    assert not launched

    button.on_click(None)                  # 未読なのでもう一度出す
    assert len(page.dialogs) == before + 1
    _dialog_button(page.dialogs[-1], 'アプリを開く').on_click(None)
    assert launched


def _open_review_tab(page):
    """β版の確認と承認タブを開く (最新の取得が走る)."""
    import types
    page.added[1].on_change(types.SimpleNamespace(
        control=types.SimpleNamespace(selected_index=1)))


def _page_with_a_review_snapshot(monkeypatch, releases):
    """一覧の取得が成功する画面を作る。戻り値: (page, pruned, manager_main).

    pruned には片付けに渡された「残すβ版」の一覧が入る。
    """
    from manager import main as manager_main

    monkeypatch.setattr(manager_main.selfupdate, 'auto_update',
                        lambda *a, **k: {'stashed': []})
    monkeypatch.setattr(manager_main.threading, 'Thread', _NoThread)
    monkeypatch.setattr(manager_main.threading, 'Timer', _NoTimer)
    page = _FakePage()
    manager_main.main(page)

    snap = {'pending': [], 'releases': releases, 'me': 'yamada-taro',
            'merged': []}
    monkeypatch.setattr(manager_main.reviews, 'fetch_snapshot',
                        lambda *a, **k: dict(snap))
    monkeypatch.setattr(manager_main.reviews, 'fork_memo', lambda *a, **k: {})
    monkeypatch.setattr(manager_main.reviewcache, 'put',
                        lambda *a, **k: dict(snap))
    monkeypatch.setattr(manager_main.reviewcache, 'load_from_disk',
                        lambda *a, **k: None)

    pruned = []
    monkeypatch.setattr(manager_main.updater, 'prune_betas',
                        lambda keep, config=None: pruned.append(list(keep)))
    # 取得はその場で最後まで走らせる (片付けが呼ばれたか見るため)
    monkeypatch.setattr(manager_main.threading, 'Thread', _NowThread)
    return page, pruned, manager_main


_BETA = {'tag': 'v1.2-beta.2', 'prerelease': True, 'notes': '',
         'published_at': '2026-08-18', 'assets': []}
_STABLE = {'tag': 'v1.1', 'prerelease': False, 'notes': '',
           'published_at': '2026-08-14', 'assets': []}


def test_fetching_the_list_tidies_up_old_betas(monkeypatch):
    """一覧を取り直すたびに、一覧に無いβ版の置き場を片付けること.

    β版は試すたびに増える。正式版になった版は GitHub 側でも消えるため、
    手元に残っても起動できないゴミになる。
    """
    page, pruned, main = _page_with_a_review_snapshot(
        monkeypatch, [_BETA, _STABLE])
    monkeypatch.setattr(main.launcher, 'port_in_use', lambda *a, **k: False)

    _open_review_tab(page)
    # 残すのは「いま一覧にあるβ版」だけ (正式版は対象外)
    assert pruned == [['v1.2-beta.2']]


def test_no_tidying_while_a_beta_is_running(monkeypatch):
    """β版を起動しているあいだは片付けない (使用中のフォルダを消さない)."""
    page, pruned, main = _page_with_a_review_snapshot(monkeypatch, [_BETA])
    monkeypatch.setattr(main.launcher, 'port_in_use', lambda *a, **k: True)

    _open_review_tab(page)
    assert pruned == []


def _beta_button(page, label_part):
    """β版カードのボタンを名前の一部で取り出す."""
    for c in _walk_controls(page.added[1], []):
        text = getattr(c, 'content', None)
        if isinstance(text, str) and label_part in text and getattr(
                c, 'on_click', None):
            return c
    raise AssertionError('「%s」のボタンが見つかりません' % label_part)


# 提出 #147 に対応するβ版 (対応付けはリリースノートの #N で行われる)
_TRY_BETA = {'tag': 'v1.5-beta.1', 'prerelease': True, 'assets': [],
             'notes': '#147 v1.4 を基点とした機能追加の提出',
             'published_at': '2026-08-19'}
_TRY_PENDING = {
    'number': 147, 'title': '機能追加の提出', 'url': 'https://x/147',
    'branch': 'feature/147', 'author': 'hanako',
    'created_at': '2026-08-19', 'created_at_full': '2026-08-19T00:00:00Z',
    'head_sha': 'abc123', 'body': '', 'base_version': 'v1.4',
    'base_commit': 'def456', 'approved': [], 'rejected': [],
    'rejected_final': False, 'rejected_since': None, 'feedback': [],
    'checks': 'success', 'conflicting': False,
}


def _page_with_a_beta_to_try(monkeypatch):
    """取得済みのβ版カードが出ている画面を作る。戻り値: (page, main)."""
    page, _pruned, main = _page_with_a_review_snapshot(
        monkeypatch, [_TRY_BETA, _STABLE])
    snap = {'pending': [_TRY_PENDING], 'releases': [_TRY_BETA, _STABLE],
            'me': 'yamada-taro', 'merged': []}
    monkeypatch.setattr(main.reviews, 'fetch_snapshot',
                        lambda *a, **k: dict(snap))
    monkeypatch.setattr(main.reviewcache, 'put', lambda *a, **k: dict(snap))
    monkeypatch.setattr(main.updater, 'local_version_info',
                        lambda *a, **k: {'version': _TRY_BETA['tag']})
    monkeypatch.setattr(
        main.updater, 'install_release',
        lambda *a, **k: pytest.fail('取得済みのβ版を取り直している'))
    monkeypatch.setattr(main.launcher, 'port_in_use', lambda *a, **k: False)
    _open_review_tab(page)
    return page, main


def test_trying_a_beta_stops_another_one_first(monkeypatch):
    """別のβ版が動いていたら止めてから起動すること.

    止めないとポートが塞がったままで、開くのは**前の版の画面**になる
    (画面に版名が出ないため利用者は気づけない)。
    """
    page, main = _page_with_a_beta_to_try(monkeypatch)

    stopped = []
    monkeypatch.setattr(main.launcher, 'stop_other_beta',
                        lambda tag, port, config=None: (
                            stopped.append((tag, port)), True)[1])
    monkeypatch.setattr(main.launcher, 'remember_beta', lambda *a, **k: None)
    monkeypatch.setattr(main.launcher, 'launch_app',
                        lambda *a, **k: (object(), 'http://127.0.0.1:8766/'))

    _beta_button(page, 'を試す').on_click(None)
    assert stopped == [(_TRY_BETA['tag'], 8766)]


def test_message_does_not_claim_a_launch_that_did_not_happen(monkeypatch):
    """すでに同じ版が動いていたら「起動しました」と言わないこと."""
    page, main = _page_with_a_beta_to_try(monkeypatch)

    monkeypatch.setattr(main.launcher, 'stop_other_beta',
                        lambda *a, **k: False)     # 動いているのは同じ版
    monkeypatch.setattr(main.launcher, 'remember_beta', lambda *a, **k: None)
    # 既存の画面を開いただけ (新しく起動していない) を表す戻り値
    monkeypatch.setattr(main.launcher, 'launch_app',
                        lambda *a, **k: (None, 'http://127.0.0.1:8766/'))

    _beta_button(page, 'を試す').on_click(None)
    texts = _walk_texts(page.added[1], [])
    assert any('すでに起動している' in t for t in texts)
    assert not any('を起動しました' in t for t in texts)


def test_the_running_beta_is_remembered_after_a_real_launch(monkeypatch):
    """実際に起動したときは版を控える (次に別の版か判断するため)."""
    page, main = _page_with_a_beta_to_try(monkeypatch)

    remembered = []
    monkeypatch.setattr(main.launcher, 'stop_other_beta',
                        lambda *a, **k: False)
    monkeypatch.setattr(main.launcher, 'remember_beta',
                        lambda tag, config=None: remembered.append(tag))
    monkeypatch.setattr(main.launcher, 'launch_app',
                        lambda *a, **k: (object(), 'http://127.0.0.1:8766/'))

    _beta_button(page, 'を試す').on_click(None)
    assert remembered == [_TRY_BETA['tag']]
    texts = _walk_texts(page.added[1], [])
    assert any('を起動しました' in t for t in texts)
