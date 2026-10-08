"""manager/main.py の UI 構築テスト。

flet の API 変更 (属性名・シグネチャ) による構築時エラーを検出する。
flet は CI の依存に含めないため、未導入環境では自動スキップされる
(ローカルの開発 venv では manager/requirements.txt 導入後に実行される)。
"""
import threading

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


def _page_with_a_downloaded_update(monkeypatch, notes=None):
    """更新版を自動で取り込み終えた直後の画面を作る (取得・起動は偽物).

    notes を渡すと、その版のリリースノートを持たせる (起動タブの
    「現行版の更新内容」の表示を見るため)。
    戻り値: (page, launched)。launched にはアプリを開いた回数が入る。
    """
    from manager import main as manager_main

    monkeypatch.setattr(manager_main.selfupdate, 'auto_update',
                        lambda *a, **k: {'stashed': []})
    monkeypatch.setattr(manager_main.threading, 'Thread', _NowThread)
    monkeypatch.setattr(manager_main.threading, 'Timer', _NoTimer)

    releases = ([{'tag': 'v1.2', 'prerelease': False, 'notes': notes}]
                if notes is not None else [])
    snap = {'pending': [], 'releases': releases, 'me': 'yamada-taro',
            'merged': []}
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


def test_launch_tab_shows_the_submission_title_first(monkeypatch):
    """起動タブの「現行版の更新内容」に、提出のタイトルが 1 行目で出ること.

    タイトルは見出し用に独立した行にする (markdown の # をそのまま
    見せない)。v1.4 以前の版名だけの見出しは行ごと出さない。
    """
    page, _ = _page_with_a_downloaded_update(
        monkeypatch,
        notes='# 荷重分布図の PDF 書き出しに対応\n\n'
              '## 更新内容\n\n- 荷重ケースごとに 1 図\n')
    texts = _walk_texts(page.added[1], [])
    assert '荷重分布図の PDF 書き出しに対応' in texts
    assert not any(t.startswith('# ') for t in texts)


def test_launch_tab_without_a_title_shows_only_the_notes(monkeypatch):
    """見出しの無い古い版 (「# mgtkit v1.2 リリースノート」) の表示."""
    page, _ = _page_with_a_downloaded_update(
        monkeypatch,
        notes='# mgtkit v1.2 リリースノート\n\n'
              '## 更新内容\n\n部材数量集計のアプリを追加\n')
    texts = _walk_texts(page.added[1], [])
    assert any('部材数量集計のアプリを追加' in t for t in texts)
    assert not any('リリースノート' in t for t in texts)


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


def test_launch_waits_for_an_install_instead_of_turning_the_user_away(
        monkeypatch):
    """裏で取り込み中に「起動」を押しても、断らずに待って続けること.

    以前は「取り込み中です。完了までお待ちください。」と出して戻って
    いたが、終わったことを誰も知らせないため、押した人は手を止めた
    ままになる (画面は「起動」を押して待つよう案内している)。
    """
    from manager import updater

    page, launched = _page_with_a_downloaded_update(monkeypatch)
    waited = []
    monkeypatch.setattr(updater, 'installing', lambda: True)
    monkeypatch.setattr(
        updater, 'install_if_needed',
        lambda *a, **k: (waited.append(True), False)[1])

    _launch_button(page).on_click(None)
    _dialog_button(page.dialogs[-1], 'アプリを開く').on_click(None)
    assert launched                       # 断られず、最後まで進んだ
    texts = _walk_texts(page.added[1], [])
    assert not any('完了までお待ちください' in t for t in texts)


def test_auto_update_gives_up_while_an_install_is_running(monkeypatch):
    """定期の自動更新は待たずに諦める (次の機会に回せばよい)."""
    from manager import main as manager_main, updater

    monkeypatch.setattr(manager_main.selfupdate, 'auto_update',
                        lambda *a, **k: {'stashed': []})
    monkeypatch.setattr(manager_main.threading, 'Thread', _NoThread)
    monkeypatch.setattr(manager_main.threading, 'Timer', _NoTimer)
    monkeypatch.setattr(updater, 'installing', lambda: True)
    monkeypatch.setattr(
        updater, 'try_install_lock',
        lambda: pytest.fail('取り込み中なのに錠を取りにいった'))
    monkeypatch.setattr(
        updater, 'install_release',
        lambda *a, **k: pytest.fail('取り込み中なのに重ねて取り込んだ'))
    monkeypatch.setattr(manager_main.updater, 'check_update',
                        lambda *a, **k: {'has_update': True,
                                         'latest': {'tag': 'v1.9',
                                                    'prerelease': False,
                                                    'notes': ''}})
    monkeypatch.setattr(manager_main.launcher, 'port_in_use',
                        lambda *a, **k: False)
    page = _FakePage()
    manager_main.main(page)               # 起動時の自動更新が走る


def test_update_check_shows_the_real_reason_not_network(monkeypatch):
    """gh 未導入・未ログインを「ネットワーク」で塗りつぶさないこと (#8).

    これらの例外は**正しい案内文**を持っている (承認タブは str(e) を
    出しており、起動タブだけが握り潰していた)。
    """
    from manager import main as manager_main

    monkeypatch.setattr(manager_main.selfupdate, 'auto_update',
                        lambda *a, **k: {'stashed': []})
    monkeypatch.setattr(manager_main.threading, 'Thread', _NowThread)
    monkeypatch.setattr(manager_main.threading, 'Timer', _NoTimer)

    def not_logged_in(*a, **k):
        raise manager_main.ghcli.GhError(
            'GitHub へのログインが必要です。「gh auth login」を'
            '実行してください。')
    monkeypatch.setattr(manager_main.reviews, 'fetch_snapshot',
                        not_logged_in)
    monkeypatch.setattr(manager_main.reviewcache, 'load_from_disk',
                        lambda *a, **k: None)

    page = _FakePage()
    manager_main.main(page)
    texts = _walk_texts(page.added[1], [])
    assert any('gh auth login' in t for t in texts)
    assert not any('ネットワーク接続をご確認' in t for t in texts)


def test_auto_update_failure_keeps_the_real_reason(monkeypatch):
    """取り込み失敗の理由 (空き容量など) をネットワークのせいにしない (#6)."""
    from manager import installer, main as manager_main

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
    monkeypatch.setattr(manager_main.updater, 'check_update',
                        lambda *a, **k: {
                            'has_update': True,
                            'latest': {'tag': 'v1.9', 'prerelease': False,
                                       'notes': ''}})
    monkeypatch.setattr(manager_main.launcher, 'port_in_use',
                        lambda *a, **k: False)

    def no_space(*a, **k):
        raise installer.InstallError(
            'パソコンの空き容量が足りないため、新しい版に'
            '置き換えられませんでした。')
    monkeypatch.setattr(manager_main.updater, 'install_release', no_space)

    page = _FakePage()
    manager_main.main(page)
    texts = _walk_texts(page.added[1], [])
    assert any('空き容量' in t for t in texts)
    assert not any('ネットワーク接続を確認してください' in t for t in texts)


def _page_without_settings(monkeypatch, name=None):
    """名前が未登録 (初回起動) の画面を作る。戻り値: (page, main, 送信記録)."""
    from manager import main as manager_main

    monkeypatch.setattr(manager_main.selfupdate, 'auto_update',
                        lambda *a, **k: {'stashed': []})
    monkeypatch.setattr(manager_main.threading, 'Thread', _NowThread)
    monkeypatch.setattr(manager_main.threading, 'Timer', _NoTimer)
    snap = {'pending': [], 'releases': [], 'me': 'yamada', 'merged': []}
    monkeypatch.setattr(manager_main.reviews, 'fetch_snapshot',
                        lambda *a, **k: dict(snap))
    monkeypatch.setattr(manager_main.reviews, 'fork_memo', lambda *a, **k: {})
    monkeypatch.setattr(manager_main.reviewcache, 'put',
                        lambda *a, **k: dict(snap))
    monkeypatch.setattr(manager_main.reviewcache, 'load_from_disk',
                        lambda *a, **k: None)
    monkeypatch.setattr(manager_main.updater, 'check_update',
                        lambda *a, **k: {'has_update': False,
                                         'latest': None})
    monkeypatch.setattr(manager_main.settings, 'user_name',
                        lambda config=None: name)
    monkeypatch.setattr(manager_main.settings, 'load_settings',
                        lambda config=None: (
                            {'name': name} if name else None))
    monkeypatch.setattr(manager_main.ghcli, 'accept_repo_invitation',
                        lambda *a, **k: False)
    monkeypatch.setattr(manager_main.ghcli, 'has_push_access',
                        lambda *a, **k: False)
    monkeypatch.setattr(manager_main.ghcli, 'find_my_join_request',
                        lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(manager_main.ghcli, 'create_join_request',
                        lambda repo, n: sent.append(n))
    return manager_main, sent


def test_join_request_waits_for_the_registered_name(monkeypatch):
    """名前を登録する前に参加申請を送らないこと (#10).

    以前は起動と同時に裏で送っていたため、初回登録の画面に名前を打ち
    終える前に「名前: (未登録)」の申請が飛んでいた。
    """
    manager_main, sent = _page_without_settings(monkeypatch, name=None)
    manager_main.main(_FakePage())
    assert sent == []                     # まだ送らない


def test_join_request_is_sent_once_the_name_is_registered(monkeypatch):
    """登録済みの PC では、これまでどおり起動時に送ること."""
    manager_main, sent = _page_without_settings(monkeypatch, name='山田太郎')
    manager_main.main(_FakePage())
    assert sent == ['山田太郎']


def _submit_confirm_dialog(monkeypatch):
    """提出タブで ZIP を選んだ直後 (提出内容の確認ダイアログ) を作る.

    GitHub にも API にもつながず、準備結果は canned 値を返す。
    戻り値: (page, 確認ダイアログ, 提出タブの Text をたどる関数)。
    """
    import flet as ft

    from manager import main as manager_main
    monkeypatch.setattr(manager_main.selfupdate, 'auto_update',
                        lambda *a, **k: {'stashed': []})
    monkeypatch.setattr(manager_main.threading, 'Timer', _NoTimer)
    monkeypatch.setattr(manager_main.submit, 'prepare_submission',
                        lambda *a, **k: {
                            'changes': {'added': ['csv_out.py'],
                                        'modified': ['app.py'],
                                        'deleted': []},
                            'skipped': [],
                            'safety': {'warnings': [], 'blockers': []}})
    monkeypatch.setattr(manager_main.autofix, 'list_my_submissions',
                        lambda *a, **k: [])
    # API キーが登録済みの PC を想定する (未登録だと自動作成を選べず、
    # 確認ダイアログの既定が「自分で入力する」になる)
    monkeypatch.setattr(manager_main.settings, 'api_key',
                        lambda config=None: 'dummy-not-a-real-key')

    class _PickedZip:
        path = 'C:/Users/yamada/Desktop/mgtkit.zip'
        name = 'mgtkit.zip'

    async def _pick(self, *a, **k):
        return [_PickedZip()]
    monkeypatch.setattr(ft.FilePicker, 'pick_files', _pick)

    page = _FakePage()
    manager_main.main(page)
    column = page.added[1].content
    submit_panel = column.controls[1].controls[2]

    button = None
    for c in _walk_controls(submit_panel, []):
        if getattr(c, 'content', None) == 'ZIP を選んで提出':
            button = c
    assert button is not None, '「ZIP を選んで提出」ボタンが見つかりません'
    page.run_task(button.on_click, None)
    # 確認ダイアログを出すところまでは本物どおり動かす (準備は
    # asyncio.to_thread 経由で走るため)。この先の送信だけ止める
    monkeypatch.setattr(manager_main.threading, 'Thread', _NoThread)
    return page, page.dialogs[-1], lambda: _walk_texts(submit_panel, [])


def test_the_draft_course_does_not_claim_the_submission_was_sent(monkeypatch):
    """下書きを作らせるコースでは「提出しています...」と言わないこと.

    「Claude で自動作成する」を選んだ提出は、このあと出る下書きの確認
    画面で取り消せば何も送られない (使い方ガイドにもそう書いてある)。
    以前はここで「提出しています...」と出ていたため、ガイドの説明と
    画面が食い違って見えていた。
    """
    page, dialog, texts = _submit_confirm_dialog(monkeypatch)
    _dialog_button(dialog, '提出する').on_click(None)   # 既定 = 自動作成
    shown = texts()
    assert '提出の準備をしています...' in shown
    assert '提出しています...' not in shown


def test_the_manual_course_still_says_it_is_submitting(monkeypatch):
    """自分で入力するコースは、押した時点で本当に送り始めるのでそのまま."""
    page, dialog, texts = _submit_confirm_dialog(monkeypatch)
    radios = [c for c in _walk_controls(dialog, [])
              if type(c).__name__ == 'RadioGroup']
    radios[0].value = 'manual'
    fields = [c for c in _walk_controls(dialog, [])
              if type(c).__name__ == 'TextField']
    fields[0].value = 'CSV 出力の桁数を直しました'
    _dialog_button(dialog, '提出する').on_click(None)
    assert '提出しています...' in texts()


def test_registering_a_name_sends_the_request_right_away(monkeypatch):
    """登録の保存が済んだら、その場で申請を送ること (次回起動まで待たない)."""
    manager_main, sent = _page_without_settings(monkeypatch, name=None)
    saved = {}

    def fake_save(name, key, config=None):
        saved['name'] = name
        # 保存後は名前が読めるようになる (実物と同じ振る舞い)
        monkeypatch.setattr(manager_main.settings, 'user_name',
                            lambda config=None: name)
        return 'settings.json'
    monkeypatch.setattr(manager_main.settings, 'save_settings', fake_save)

    page = _FakePage()
    manager_main.main(page)
    assert sent == []                     # 登録前は送っていない

    dialog = page.dialogs[-1]             # 初回登録ダイアログ
    fields = [c for c in _walk_controls(dialog, [])
              if type(c).__name__ == 'TextField']
    fields[0].value = '山田太郎'
    fields[1].value = 'sk-ant-api03-dummy'
    _dialog_button(dialog, '登録してはじめる').on_click(None)
    assert saved['name'] == '山田太郎'
    assert sent == ['山田太郎']           # 登録直後に送られた



# 差し替える前の本物 (読み込み時に取っておく。テスト中は差し替わっている)
_REAL_THREAD = threading.Thread


class _NowThread:
    """裏の処理をその場で最後まで走らせる (結果をすぐ確かめるため)."""

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


def _submit_panel_with_key(monkeypatch, key):
    """API キーが登録済みの PC の提出タブ (起動時の裏の処理は止める)."""
    from manager import main as manager_main
    monkeypatch.setattr(manager_main.selfupdate, 'auto_update',
                        lambda *a, **k: {'stashed': []})
    monkeypatch.setattr(manager_main.threading, 'Thread', _NoThread)
    monkeypatch.setattr(manager_main.threading, 'Timer', _NoTimer)
    monkeypatch.setattr(manager_main.settings, 'user_name',
                        lambda config=None: '山田太郎')
    monkeypatch.setattr(manager_main.settings, 'api_key',
                        lambda config=None: key)
    page = _FakePage()
    manager_main.main(page)
    submit_panel = page.added[1].content.controls[1].controls[2]
    monkeypatch.setattr(manager_main.threading, 'Thread', _NowThread)
    return manager_main, page, submit_panel


def test_the_api_key_can_be_registered_again_from_the_submit_tab(monkeypatch):
    """期限の切れたキーを、settings.json を手で直さずに入れ替えられること.

    名前はそのまま残し、キーだけを差し替える。いま入っているキーは
    Claude Console の一覧と同じ表記で見せる (どのキーか見比べられる)。
    """
    old = 'sk-ant-api03-Nxu' + 'x' * 80 + 'UwAA'
    manager_main, page, panel = _submit_panel_with_key(monkeypatch, old)
    saved = []
    monkeypatch.setattr(manager_main.settings, 'save_settings',
                        lambda name, key, config=None: saved.append(
                            (name, key)))

    buttons = [c for c in _walk_controls(panel, [])
               if getattr(c, 'content', None) == 'API キーを登録し直す']
    assert buttons, '提出タブに「API キーを登録し直す」がありません'
    buttons[0].on_click(None)
    dialog = page.dialogs[-1]
    assert any('sk-ant-api03-Nxu...UwAA' in t
               for t in _walk_texts(dialog, []))
    field = [c for c in _walk_controls(dialog, [])
             if type(c).__name__ == 'TextField'][0]
    field.value = 'sk-ant-api03-new-key'
    _dialog_button(dialog, '登録する').on_click(None)
    assert saved == [('山田太郎', 'sk-ant-api03-new-key')]
    assert dialog not in page.dialogs                 # 閉じた
    assert any('登録し直しました' in t for t in _walk_texts(panel, []))


def test_a_wrong_key_keeps_the_dialog_open_with_the_reason(monkeypatch):
    """形式の誤りはその場で理由を出し、ボタンを押せる状態に戻すこと."""
    manager_main, page, panel = _submit_panel_with_key(monkeypatch, None)
    buttons = [c for c in _walk_controls(panel, [])
               if getattr(c, 'content', None) == 'API キーを登録し直す']
    buttons[0].on_click(None)
    dialog = page.dialogs[-1]
    assert any('まだ登録されていません' in t for t in _walk_texts(dialog, []))
    field = [c for c in _walk_controls(dialog, [])
             if type(c).__name__ == 'TextField'][0]
    field.value = 'not-a-key'
    save = _dialog_button(dialog, '登録する')
    save.on_click(None)
    assert page.dialogs[-1] is dialog                 # 開いたまま
    assert save.disabled is False                     # 押し直せる
    assert any('形式' in t for t in _walk_texts(dialog, []))
    assert field.border_color == manager_main.RED     # 欄を赤枠に
    field.value = 'sk-ant-'
    field.on_change(None)                             # 打ち直したら戻す
    assert field.border_color != manager_main.RED


def test_pressing_enter_twice_saves_only_once(monkeypatch):
    """Enter の連打で保存が二重に走らないこと (ボタンと同じ入口で止める)."""
    manager_main, page, panel = _submit_panel_with_key(monkeypatch, None)
    started = []

    class _CountThread:
        def __init__(self, target=None, daemon=None):
            pass

        def start(self):
            started.append(True)       # 走らせずに数えるだけ (保存中のまま)
    monkeypatch.setattr(manager_main.threading, 'Thread', _CountThread)
    buttons = [c for c in _walk_controls(panel, [])
               if getattr(c, 'content', None) == 'API キーを登録し直す']
    buttons[0].on_click(None)
    dialog = page.dialogs[-1]
    field = [c for c in _walk_controls(dialog, [])
             if type(c).__name__ == 'TextField'][0]
    field.value = 'sk-ant-api03-new-key'
    field.on_submit(None)
    field.on_submit(None)
    _dialog_button(dialog, '登録する').on_click(None)
    assert started == [True]
    assert dialog.title.value == 'API キーを登録する'   # 未登録の PC
    assert any('登録しています' in t for t in _walk_texts(dialog, []))


def test_a_rejected_key_offers_to_register_it_again(monkeypatch):
    """キーが原因で自動作成できなかったら、その場で登録し直せること.

    ZIP を選び直しても同じキーでは通らないので、失敗の画面の主ボタンを
    「API キーを登録し直す」にする。登録できたら、そのまま ZIP の
    選び直し (同じ提出のやり直し) に進む。
    """
    page, dialog, texts = _submit_confirm_dialog(monkeypatch)
    from manager import claude_helper
    from manager import main as manager_main

    def _reject(*a, **k):
        raise claude_helper._status_error(401)
    monkeypatch.setattr(manager_main.submit, 'finalize_submission', _reject)

    def _now_or_real(target=None, daemon=None, **kw):
        # run_bg の裏の処理はその場で走らせる。ZIP の選び直しが使う
        # asyncio.to_thread の作業スレッド (name 付き) は本物のまま
        if kw:
            return _REAL_THREAD(target=target, daemon=daemon, **kw)
        return _NowThread(target=target)
    monkeypatch.setattr(manager_main.threading, 'Thread', _now_or_real)
    saved = []
    monkeypatch.setattr(manager_main.settings, 'save_settings',
                        lambda name, key, config=None: saved.append(key))
    monkeypatch.setattr(manager_main.settings, 'user_name',
                        lambda config=None: '山田太郎')
    _dialog_button(dialog, '提出する').on_click(None)   # 既定 = 自動作成
    err = page.dialogs[-1]
    shown = _walk_texts(err, [])
    assert any('有効期限' in t for t in shown)
    # 行き先はボタンが示す (本文で別の場所へ誘導しない)
    assert not any('提出タブ' in t for t in shown)
    _dialog_button(err, 'API キーを登録し直す').on_click(None)
    rekey = page.dialogs[-1]
    assert any('いま登録されているキー' in t for t in _walk_texts(rekey, []))
    field = [c for c in _walk_controls(rekey, [])
             if type(c).__name__ == 'TextField'][0]
    field.value = 'sk-ant-api03-new-key'
    _dialog_button(rekey, '登録して ZIP を選ぶ').on_click(None)
    assert saved == ['sk-ant-api03-new-key']
    # ZIP を選び直した結果、提出内容の確認ダイアログがもう一度出る
    again = page.dialogs[-1]
    assert again is not rekey
    _dialog_button(again, '提出する')


def test_notes_are_drawn_with_headings_and_indented_sub_items():
    """更新内容の見出し行と子項目を描き分けること (字下げだけに頼らない)."""
    from manager import notesview
    col = notesview.notes_column(
        '- 梁応力の読み込み (util.py)\n  - 8列はそのまま読む\n- 応力図タブ')
    rows = col.controls
    assert len(rows) == 3
    head, sub, head2 = rows
    # 見出し行: 紺の印 + やや太い字、字下げなし
    assert head.padding.left == 0
    assert head.content.controls[0].content.value == '■'
    assert head.content.controls[1].weight == flet.FontWeight.W_600
    # 子項目: 1 段ぶん字下げ + 灰色の「・」
    assert sub.padding.left == notesview._INDENT
    assert sub.content.controls[0].content.value == '•'
    assert sub.content.controls[1].value == '8列はそのまま読む'
    # 次の見出しの上は子項目どうしより広く空ける
    assert head2.margin is not None and head2.margin.top > 0


_SPLIT_PREP = {
    'changes': {'added': ['rcslab/routes.py', 'colbase/routes.py'],
                'modified': ['mgt.py'], 'deleted': []},
    'skipped': [],
    'safety': {'warnings': [], 'blockers': []},
    'split': {'splittable': True, 'units': [
        {'key': 'common', 'kind': 'common',
         'label': '既存の機能の修正・共通部分', 'tabs': [],
         'files': ['mgt.py'], 'deleted': [], 'shared': []},
        {'key': 'tab:rcslab', 'kind': 'tab', 'label': '新しいタブ「RCスラブ」',
         'tabs': ['rcslab'], 'files': ['rcslab/routes.py'], 'deleted': [],
         'shared': ['util.py']},
        {'key': 'tab:colbase', 'kind': 'tab',
         'label': '新しいタブ「S露出柱脚」', 'tabs': ['colbase'],
         'files': ['colbase/routes.py'], 'deleted': [], 'shared': []},
    ]},
}


def _split_confirm_dialog(monkeypatch, my_prs=()):
    """タブごとに分けられる提出の確認ダイアログ (送信は記録だけ)."""
    import flet as ft

    from manager import main as manager_main
    monkeypatch.setattr(manager_main.selfupdate, 'auto_update',
                        lambda *a, **k: {'stashed': []})
    monkeypatch.setattr(manager_main.threading, 'Timer', _NoTimer)
    monkeypatch.setattr(manager_main.submit, 'prepare_submission',
                        lambda *a, **k: dict(_SPLIT_PREP))
    monkeypatch.setattr(manager_main.autofix, 'list_my_submissions',
                        lambda *a, **k: list(my_prs))
    monkeypatch.setattr(manager_main.settings, 'api_key',
                        lambda config=None: 'dummy-not-a-real-key')
    calls = []
    monkeypatch.setattr(
        manager_main.submit, 'finalize_split',
        lambda prep, units, *a, **k: calls.append(('split', units, k))
        or {'pr_url': 'u1', 'branch': 'b1',
            'prs': [{'label': u['label'], 'url': 'u%d' % i}
                    for i, u in enumerate(units, 1)]})
    monkeypatch.setattr(
        manager_main.submit, 'finalize_submission',
        lambda prep, *a, **k: calls.append(('one', None, k))
        or {'pr_url': 'u1', 'branch': 'b1'})

    class _PickedZip:
        path = 'C:/Users/yamada/Desktop/mgtkit.zip'
        name = 'mgtkit.zip'

    async def _pick(self, *a, **k):
        return [_PickedZip()]
    monkeypatch.setattr(ft.FilePicker, 'pick_files', _pick)

    page = _FakePage()
    manager_main.main(page)
    column = page.added[1].content
    submit_panel = column.controls[1].controls[2]
    button = next(c for c in _walk_controls(submit_panel, [])
                  if getattr(c, 'content', None) == 'ZIP を選んで提出')
    page.run_task(button.on_click, None)
    monkeypatch.setattr(manager_main.threading, 'Thread', _NowThread)
    return page, page.dialogs[-1], calls, \
        lambda: _walk_texts(submit_panel, [])


def _radio_groups(dialog):
    return [c for c in _walk_controls(dialog, [])
            if type(c).__name__ == 'RadioGroup']


def test_split_choice_is_offered_and_chosen_by_default(monkeypatch):
    """2 本以上に分けられる提出では、分けて出すのを既定で勧めること."""
    page, dialog, calls, texts = _split_confirm_dialog(monkeypatch)
    shown = _walk_texts(dialog, [])
    assert 'タブごとに分けて出しますか?' in shown
    assert '3 本に分けて出す (おすすめ)' in shown
    assert '新しいタブ「RCスラブ」' in shown
    assert any('util.py' in t for t in shown)       # 複製する共通部分
    # 自動作成なら 1 本ずつ作ることと料金が少し増えることを先に言う
    assert any('数十円 × 3 本' in t for t in shown)
    _dialog_button(dialog, '提出する').on_click(None)
    assert calls and calls[0][0] == 'split'
    assert [u['key'] for u in calls[0][1]] == ['common', 'tab:rcslab',
                                               'tab:colbase']
    assert calls[0][2]['use_ai'] is True
    assert any('3 本に分けて提出しました' in t for t in texts())


def test_choosing_one_submission_keeps_the_old_flow(monkeypatch):
    page, dialog, calls, _texts = _split_confirm_dialog(monkeypatch)
    split_rg = _radio_groups(dialog)[0]
    split_rg.value = 'one'
    split_rg.on_change(None)
    _dialog_button(dialog, '提出する').on_click(None)
    assert calls and calls[0][0] == 'one'


def test_manual_text_warns_it_goes_to_every_part(monkeypatch):
    page, dialog, calls, _texts = _split_confirm_dialog(monkeypatch)
    gen_rg = _radio_groups(dialog)[1]
    gen_rg.value = 'manual'
    gen_rg.on_change(None)
    assert any('同じ文章が 3 本すべてに付きます' in t
               for t in _walk_texts(dialog, []))


def test_resubmission_is_not_split(monkeypatch):
    """修正版として前の提出に積むときは分けない."""
    page, dialog, calls, _texts = _split_confirm_dialog(
        monkeypatch, my_prs=[{'branch': 'feature/x-1', 'number': 7,
                              'title': '前の提出'}])
    dd = next(c for c in _walk_controls(dialog, [])
              if type(c).__name__ == 'Dropdown')
    dd.value = 'feature/x-1'
    dd.on_select(None)
    _dialog_button(dialog, '提出する').on_click(None)
    assert calls and calls[0][0] == 'one'
    assert calls[0][2]['existing_branch'] == 'feature/x-1'
