"""manager/uiguard.py のテスト (ボタン連打で処理が二重に走らないこと).

画面部品に依存しないため flet 無しでも実行される (CI もこの構成)。
main.py 側は import すると flet が要るため、構文木を読んで確かめる。
"""
import ast
import os
import threading

from manager.uiguard import ActionGuard

_MAIN_PY = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'main.py')


class TestActionGuard:
    def test_second_press_is_refused(self):
        g = ActionGuard()
        assert g.begin(33) is True
        assert g.begin(33) is False      # 連打の 2 回目
        assert g.begin(33) is False      # 3 回目も

    def test_other_target_is_not_blocked(self):
        # 別の提出への操作は同時に行える (カードごとに独立)
        g = ActionGuard()
        assert g.begin(33) is True
        assert g.begin(84) is True

    def test_can_press_again_after_end(self):
        g = ActionGuard()
        g.begin(33)
        g.end(33)
        assert g.begin(33) is True

    def test_end_without_begin_is_harmless(self):
        # 失敗経路から二重に end されても落ちない
        g = ActionGuard()
        g.end(33)
        g.begin(33)
        g.end(33)
        g.end(33)
        assert g.busy(33) is False

    def test_busy_reports_state(self):
        g = ActionGuard()
        assert g.busy(33) is False
        g.begin(33)
        assert g.busy(33) is True
        g.end(33)
        assert g.busy(33) is False

    def test_clear_releases_everything(self):
        g = ActionGuard()
        g.begin(33)
        g.begin(84)
        g.clear()
        assert g.begin(33) is True and g.begin(84) is True

    def test_only_one_thread_wins(self):
        """裏スレッドから同時に押されても 1 つしか通らない.

        画面のループと送信スレッドの両方から触るため、取りこぼすと
        同じ差し戻しが 2 件 GitHub に登録されてしまう。
        """
        g = ActionGuard()
        start = threading.Event()
        won = []

        def press():
            start.wait()
            if g.begin(33):
                won.append(threading.current_thread().name)

        threads = [threading.Thread(target=press) for _ in range(20)]
        for t in threads:
            t.start()
        start.set()
        for t in threads:
            t.join(5)
        assert len(won) == 1


def _guard_calls(func_name):
    """main.py の関数 (入れ子も含む) が呼ぶ _review_guard のメソッド名."""
    tree = ast.parse(open(_MAIN_PY, encoding='utf-8').read())
    target = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef)
                   and n.name == func_name), None)
    assert target is not None, '%s が main.py に見つからない' % func_name
    return {n.func.attr for n in ast.walk(target)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == '_review_guard'}


class TestReviewActionsAreGuarded:
    """提出に対する操作は必ず二重実行ガードを通すこと.

    承認・却下・取り消しが連打で二重に飛ぶと、GitHub に同じレビューが
    並び、却下の場合は自動削除までの残り日数まで押し戻される。
    提出に対する操作を新しく足すときは、この一覧にも追加すること。
    """

    def test_each_action_begins_and_ends_the_guard(self):
        for name in ('on_approve',          # 承認
                     'on_cancel_review',    # 承認を取り消す / 却下を取り消す
                     'on_reject',           # 却下 (中の do_reject)
                     'on_resolve_conflict'):  # 最新版と統合 (中の execute)
            calls = _guard_calls(name)
            assert 'begin' in calls, '%s に開始の札がない' % name
            assert 'end' in calls, '%s に終了の札がない (無効のまま残る)' % name
