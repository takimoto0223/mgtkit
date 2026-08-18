"""manager/reviewcache.py のテスト (承認タブの即時表示用スナップショット)。"""
from manager import reviewcache


def _cfg(tmp_path):
    return {'manager': {'install_root': str(tmp_path)}}


PENDING = [{'number': 33, 'title': '組立断面', 'approved': ['yamada']}]
RELEASES = [{'tag': 'v1.1-beta.1', 'prerelease': True}]


class TestMemory:
    def test_put_then_get(self, tmp_path):
        data = reviewcache.put(PENDING, RELEASES, 'yamada',
                               config=_cfg(tmp_path))
        assert reviewcache.get() == data
        assert data['pending'] == PENDING
        assert data['releases'] == RELEASES
        assert data['me'] == 'yamada'
        assert data['fetched_at']

    def test_get_empty(self):
        assert reviewcache.get() is None

    def test_clear(self, tmp_path):
        reviewcache.put(PENDING, RELEASES, 'yamada', config=_cfg(tmp_path))
        reviewcache.clear()
        assert reviewcache.get() is None


class TestSeqGuard:
    """応答の追い越し防止: 古い取得結果が新しい表示を上書きしない."""

    def test_late_response_is_discarded(self, tmp_path):
        cfg = _cfg(tmp_path)
        seq_old = reviewcache.next_seq()
        seq_new = reviewcache.next_seq()
        newer = reviewcache.put([{'number': 2}], [], 'me', seq=seq_new,
                                config=cfg)
        assert newer is not None
        # 先に始まって後から届いた古い取得は破棄される
        assert reviewcache.put([{'number': 1}], [], 'me', seq=seq_old,
                               config=cfg) is None
        assert reviewcache.get()['pending'] == [{'number': 2}]

    def test_in_order_responses_apply(self, tmp_path):
        cfg = _cfg(tmp_path)
        s1, s2 = reviewcache.next_seq(), reviewcache.next_seq()
        reviewcache.put([{'number': 1}], [], 'me', seq=s1, config=cfg)
        assert reviewcache.put([{'number': 2}], [], 'me', seq=s2,
                               config=cfg) is not None
        assert reviewcache.get()['pending'] == [{'number': 2}]


class TestLocalChange:
    """楽観的更新: 押す前に始まっていた取得が表示を巻き戻さないこと."""

    def test_in_flight_fetch_is_discarded(self, tmp_path):
        """タブを開いてすぐ承認を取り消したときの並び.

        取得A (押す前の「承認済み」) が押した後に着陸して採用されると、
        画面が一度「承認を取り消す」に戻ってしまう (実機で発生した挙動)。
        """
        cfg = _cfg(tmp_path)
        reviewcache.put([{'number': 33, 'approved': ['yamada']}], [],
                        'yamada', seq=reviewcache.next_seq(), config=cfg)
        seq_a = reviewcache.next_seq()          # タブを開いた取得A が離陸

        # 取り消しを押す: 手元を書き換えて画面を先に切り替える
        reviewcache.pending_of(33)['approved'] = []
        reviewcache.note_local_change()

        # 取得A が着陸 (中身は押す前の「承認済み」) → 採用しない
        assert reviewcache.put([{'number': 33, 'approved': ['yamada']}],
                               [], 'yamada', seq=seq_a, config=cfg) is None
        assert reviewcache.get()['pending'][0]['approved'] == []

    def test_fetch_started_after_the_press_is_applied(self, tmp_path):
        """操作の後に始まった取得 (送信後の取り直し) は反映されること."""
        cfg = _cfg(tmp_path)
        reviewcache.put([{'number': 33, 'approved': ['yamada']}], [],
                        'yamada', seq=reviewcache.next_seq(), config=cfg)
        reviewcache.note_local_change()
        seq_b = reviewcache.next_seq()          # 送信後の取り直しが離陸
        assert reviewcache.put([{'number': 33, 'approved': []}], [],
                               'yamada', seq=seq_b, config=cfg) is not None
        assert reviewcache.get()['pending'][0]['approved'] == []


class TestPendingOf:
    """楽観的更新は「今のスナップショットの中の dict」に当てること."""

    def test_returns_the_live_dict(self, tmp_path):
        cfg = _cfg(tmp_path)
        reviewcache.put([{'number': 33, 'approved': ['yamada']},
                         {'number': 84, 'approved': []}], [], 'yamada',
                        config=cfg)
        got = reviewcache.pending_of(33)
        got['approved'] = []
        # 書き換えがスナップショットに乗っている (画面の描き直しに効く)
        assert reviewcache.get()['pending'][0]['approved'] == []

    def test_stale_dict_from_an_old_snapshot_is_not_used(self, tmp_path):
        """取得で入れ替わった後も、番号で引けば最新の dict が返ること.

        画面は内容が同じなら一覧を作り直さないため、ボタンが前のスナップ
        ショットの dict を掴んだままになる。それを書き換えても表示には
        乗らないので、番号で引き直す必要がある。
        """
        cfg = _cfg(tmp_path)
        old = reviewcache.put([{'number': 33, 'approved': ['yamada']}], [],
                              'yamada', config=cfg)
        old_pr = old['pending'][0]
        reviewcache.put([{'number': 33, 'approved': ['yamada']}], [],
                        'yamada', config=cfg)     # 取得で dict が入れ替わる
        assert reviewcache.pending_of(33) is not old_pr

    def test_unknown_number_and_empty_cache(self, tmp_path):
        assert reviewcache.pending_of(33) is None      # スナップショット無し
        reviewcache.put([{'number': 33}], [], 'yamada', config=_cfg(tmp_path))
        assert reviewcache.pending_of(999) is None


class TestDiskSnapshot:
    def test_roundtrip(self, tmp_path):
        cfg = _cfg(tmp_path)
        reviewcache.put(PENDING, RELEASES, 'yamada', config=cfg)
        # プロセス再起動を想定してメモリを破棄 → ディスクから復元
        reviewcache.clear()
        data = reviewcache.load_from_disk(cfg)
        assert data['pending'] == PENDING
        assert data['me'] == 'yamada'
        assert reviewcache.get() == data

    def test_missing_file(self, tmp_path):
        assert reviewcache.load_from_disk(_cfg(tmp_path)) is None

    def test_broken_file_is_ignored(self, tmp_path):
        (tmp_path / 'reviews_cache.json').write_text('{{{', encoding='utf-8')
        assert reviewcache.load_from_disk(_cfg(tmp_path)) is None

    def test_wrong_shape_is_ignored(self, tmp_path):
        (tmp_path / 'reviews_cache.json').write_text(
            '{"pending": "x"}', encoding='utf-8')
        assert reviewcache.load_from_disk(_cfg(tmp_path)) is None

    def test_memory_wins_over_disk(self, tmp_path):
        """メモリに新しいデータがあればディスクの古い内容で戻さない."""
        cfg = _cfg(tmp_path)
        reviewcache.put(PENDING, RELEASES, 'yamada', config=cfg)
        newer = reviewcache.put([{'number': 99}], [], 'sato', config=cfg)
        assert reviewcache.load_from_disk(cfg) == newer

    def test_save_failure_keeps_memory(self, tmp_path, monkeypatch):
        """ディスク保存に失敗しても表示用のメモリは更新される."""
        def boom(*a, **k):
            raise OSError('disk full')
        monkeypatch.setattr(reviewcache.os, 'makedirs', boom)
        data = reviewcache.put(PENDING, RELEASES, 'yamada',
                               config=_cfg(tmp_path))
        assert data is not None
        assert reviewcache.get() == data


class TestAgeMinutes:
    def test_age(self):
        import datetime
        now = datetime.datetime(2026, 8, 14, 12, 30,
                                tzinfo=datetime.timezone.utc)
        data = {'fetched_at': '2026-08-14T12:05:00+00:00'}
        assert reviewcache.age_minutes(data, now=now) == 25

    def test_unknown(self):
        assert reviewcache.age_minutes({}) is None
        assert reviewcache.age_minutes({'fetched_at': 'broken'}) is None
