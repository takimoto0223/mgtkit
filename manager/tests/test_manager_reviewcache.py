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
