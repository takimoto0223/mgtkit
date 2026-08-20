"""manager/localstate.py (PC 内だけの非表示記録) のテスト。"""
from manager import localstate


def _config(tmp_path):
    return {'manager': {'install_root': str(tmp_path)}}


class TestHiddenPrs:
    def test_hide_and_read(self, tmp_path):
        cfg = _config(tmp_path)
        assert localstate.hidden_prs(cfg) == set()
        localstate.hide_pr(33, cfg)
        localstate.hide_pr(31, cfg)
        localstate.hide_pr(33, cfg)  # 重複はまとまる
        assert localstate.hidden_prs(cfg) == {31, 33}

    def test_unhide_restores(self, tmp_path):
        cfg = _config(tmp_path)
        localstate.hide_pr(31, cfg)
        localstate.hide_pr(33, cfg)
        localstate.unhide_pr(31, cfg)
        assert localstate.hidden_prs(cfg) == {33}
        localstate.unhide_pr(99, cfg)  # 記録がない番号は無視
        assert localstate.hidden_prs(cfg) == {33}

    def test_prune_removes_closed(self, tmp_path):
        cfg = _config(tmp_path)
        localstate.hide_pr(31, cfg)
        localstate.hide_pr(33, cfg)
        localstate.prune_hidden([33, 40], cfg)  # 31 はクローズ済み
        assert localstate.hidden_prs(cfg) == {33}

    def test_auto_folded_tracking(self, tmp_path):
        # 却下確定の自動畳みは一度だけ (「一覧に戻す」後は再畳みしない)
        cfg = _config(tmp_path)
        assert localstate.auto_folded(cfg) == set()
        localstate.mark_auto_folded(33, cfg)
        assert localstate.auto_folded(cfg) == {33}
        localstate.prune_hidden([33, 40], cfg)
        assert localstate.auto_folded(cfg) == {33}
        localstate.prune_hidden([40], cfg)  # クローズされたら掃除
        assert localstate.auto_folded(cfg) == set()

    def test_unmark_auto_folded(self, tmp_path):
        # 却下取り消しで確定が解けたら記録を消し、再確定でまた畳める
        cfg = _config(tmp_path)
        localstate.mark_auto_folded(33, cfg)
        localstate.unmark_auto_folded(33, cfg)
        assert localstate.auto_folded(cfg) == set()
        localstate.unmark_auto_folded(99, cfg)  # 記録がない番号は無視
        assert localstate.auto_folded(cfg) == set()

    def test_broken_file_is_ignored(self, tmp_path):
        cfg = _config(tmp_path)
        (tmp_path / 'local_state.json').write_text('not json',
                                                   encoding='utf-8')
        assert localstate.hidden_prs(cfg) == set()
        localstate.hide_pr(5, cfg)  # 壊れていても書き直せる
        assert localstate.hidden_prs(cfg) == {5}


class TestSaveFailureDoesNotStopTheScreen:
    """保存できなくても例外にしない (画面が止まらないこと).

    この記録は「自分の画面でどれを畳んだか」だけで、失っても畳み直せば
    済む。一方、保存は run_bg の中や押した瞬間のハンドラから呼ばれる
    ため、例外を投げるとボタンが無効なまま戻らない止まり方をする。
    """

    def test_hide_pr_survives_a_write_failure(self, tmp_path, monkeypatch):
        import os

        cfg = _config(tmp_path)
        localstate.hide_pr(31, cfg)

        def boom(src, dst):
            raise OSError('書き込めません (試験)')
        monkeypatch.setattr(os, 'replace', boom)

        localstate.hide_pr(33, cfg)          # 例外を投げない
        localstate.unhide_pr(31, cfg)        # 逆向きも同じ
        localstate.mark_auto_folded(33, cfg)
        monkeypatch.undo()

        # 保存できていないので中身は変わらないまま (次回また畳める)
        assert localstate.hidden_prs(cfg) == {31}

    def test_save_reports_success(self, tmp_path, monkeypatch):
        import os

        cfg = _config(tmp_path)
        assert localstate._save({'hidden_prs': [1]}, cfg) is True

        def boom(src, dst):
            raise OSError('書き込めません (試験)')
        monkeypatch.setattr(os, 'replace', boom)
        assert localstate._save({'hidden_prs': [2]}, cfg) is False
