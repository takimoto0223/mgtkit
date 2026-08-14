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

    def test_broken_file_is_ignored(self, tmp_path):
        cfg = _config(tmp_path)
        (tmp_path / 'local_state.json').write_text('not json',
                                                   encoding='utf-8')
        assert localstate.hidden_prs(cfg) == set()
        localstate.hide_pr(5, cfg)  # 壊れていても書き直せる
        assert localstate.hidden_prs(cfg) == {5}
