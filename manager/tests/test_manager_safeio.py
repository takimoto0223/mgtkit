"""manager/safeio.py (状態ファイルを壊さない保存) のテスト。

素の open(path, 'w') は開いた時点で中身を空にするため、書き込み中に
落ちると 0 バイトのファイルが残る。読み手はそれを「未登録」「記録なし」
と読み替えてしまうので、失ったことに誰も気づかない。ここでは
「落ちても前の中身が残る」「一時ファイルが散らからない」を固定する。
"""
import json
import os
import stat
import sys

import pytest

from manager import localstate, reviewcache, safeio, settings, usage


def _config(tmp_path):
    return {'manager': {'install_root': str(tmp_path)}}


def _leftovers(d):
    """書きかけの一時ファイルが残っていないかを見る."""
    return sorted(n for n in os.listdir(str(d)) if n.endswith('.tmp'))


def _crash_at_replace(monkeypatch, exc=OSError('差し替え失敗 (試験)')):
    """差し替えの直前で落ちる状況を作る (書き込み中の強制終了に相当)."""
    def boom(src, dst):
        raise exc
    monkeypatch.setattr(os, 'replace', boom)


class TestWriteJson:
    def test_writes_and_reads_back(self, tmp_path):
        path = str(tmp_path / 'a.json')
        assert safeio.write_json(path, {'x': 'あ'}, indent=2) == path
        with open(path, encoding='utf-8') as f:
            assert json.load(f) == {'x': 'あ'}
        # 日本語はそのまま (ensure_ascii=False)
        with open(path, encoding='utf-8') as f:
            assert 'あ' in f.read()

    def test_leaves_no_tmp_file(self, tmp_path):
        safeio.write_json(str(tmp_path / 'a.json'), {'x': 1})
        assert _leftovers(tmp_path) == []
        assert os.listdir(str(tmp_path)) == ['a.json']

    def test_creates_parent_dir(self, tmp_path):
        path = str(tmp_path / 'deep' / 'nest' / 'a.json')
        safeio.write_json(path, {'x': 1})
        assert os.path.isfile(path)

    def test_overwrites_existing(self, tmp_path):
        path = str(tmp_path / 'a.json')
        safeio.write_json(path, {'x': 1})
        safeio.write_json(path, {'x': 2})
        with open(path, encoding='utf-8') as f:
            assert json.load(f) == {'x': 2}
        assert _leftovers(tmp_path) == []

    def test_indent_none_is_one_line(self, tmp_path):
        path = str(tmp_path / 'a.json')
        safeio.write_json(path, {'x': 1, 'y': 2})
        with open(path, encoding='utf-8') as f:
            assert len(f.read().splitlines()) == 1

    def test_crash_keeps_previous_content(self, tmp_path, monkeypatch):
        path = str(tmp_path / 'a.json')
        safeio.write_json(path, {'x': '前の中身'})
        _crash_at_replace(monkeypatch)
        with pytest.raises(OSError):
            safeio.write_json(path, {'x': '新しい中身'})
        # 0 バイトにならず、前の中身がそのまま読める
        with open(path, encoding='utf-8') as f:
            assert json.load(f) == {'x': '前の中身'}

    def test_crash_leaves_no_tmp_file(self, tmp_path, monkeypatch):
        path = str(tmp_path / 'a.json')
        safeio.write_json(path, {'x': 1})
        _crash_at_replace(monkeypatch)
        with pytest.raises(OSError):
            safeio.write_json(path, {'x': 2})
        assert _leftovers(tmp_path) == []

    def test_crash_before_first_save_leaves_nothing(self, tmp_path,
                                                    monkeypatch):
        # まだ 1 度も保存していないときに落ちても、書きかけを残さない
        _crash_at_replace(monkeypatch)
        with pytest.raises(OSError):
            safeio.write_json(str(tmp_path / 'a.json'), {'x': 1})
        assert os.listdir(str(tmp_path)) == []

    def test_unexpected_crash_also_cleans_up(self, tmp_path, monkeypatch):
        # OSError 以外 (強制終了など) で落ちても後始末は同じ
        path = str(tmp_path / 'a.json')
        safeio.write_json(path, {'x': '前の中身'})
        _crash_at_replace(monkeypatch, KeyboardInterrupt())
        with pytest.raises(KeyboardInterrupt):
            safeio.write_json(path, {'x': '新しい中身'})
        assert _leftovers(tmp_path) == []
        with open(path, encoding='utf-8') as f:
            assert json.load(f) == {'x': '前の中身'}

    @pytest.mark.skipif(sys.platform == 'win32',
                        reason='Windows は NTFS の ACL に従うため')
    def test_mode_is_applied(self, tmp_path):
        path = str(tmp_path / 'a.json')
        safeio.write_json(path, {'x': 1},
                          mode=stat.S_IRUSR | stat.S_IWUSR)
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


class TestSettingsSurvivesCrash:
    """settings.json は API キー。失うと登録し直しになる。"""

    def test_previous_key_survives(self, tmp_path, monkeypatch):
        cfg = _config(tmp_path)
        settings.save_settings('山田太郎', 'sk-ant-first', cfg)
        _crash_at_replace(monkeypatch)
        with pytest.raises(OSError):
            settings.save_settings('鈴木花子', 'sk-ant-second', cfg)
        data = settings.load_settings(cfg)
        assert data == {'name': '山田太郎',
                        'anthropic_api_key': 'sk-ant-first'}
        assert _leftovers(tmp_path) == []

    @pytest.mark.skipif(sys.platform == 'win32',
                        reason='Windows は NTFS の ACL に従うため')
    def test_key_file_is_owner_only(self, tmp_path):
        cfg = _config(tmp_path)
        path = settings.save_settings('山田太郎', 'sk-ant-x', cfg)
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


class TestUsageSurvivesCrash:
    """usage.json は記録時点の単価で確定した額。計算し直せない。"""

    def test_previous_days_survive(self, tmp_path, monkeypatch):
        cfg = _config(tmp_path)
        usage.record({'input_tokens': 1000, 'output_tokens': 500}, cfg)
        before = usage.summary(cfg)['total_usd']
        assert before > 0

        _crash_at_replace(monkeypatch)
        # record() は決して例外を投げない (画面を止めない約束)
        assert usage.record({'input_tokens': 9999,
                             'output_tokens': 9999}, cfg) is None
        monkeypatch.undo()

        assert usage.summary(cfg)['total_usd'] == before
        assert usage.summary(cfg)['total_calls'] == 1
        assert _leftovers(tmp_path) == []


class TestLocalStateSurvivesCrash:
    def test_previous_records_survive(self, tmp_path, monkeypatch):
        cfg = _config(tmp_path)
        localstate.hide_pr(31, cfg)
        _crash_at_replace(monkeypatch)
        try:
            localstate.hide_pr(33, cfg)
        except OSError:
            pass        # 保存の失敗を誰が扱うかはここでは問わない
        monkeypatch.undo()
        assert localstate.hidden_prs(cfg) == {31}
        assert _leftovers(tmp_path) == []


class TestReviewCacheSurvivesCrash:
    def test_previous_snapshot_survives(self, tmp_path, monkeypatch):
        cfg = _config(tmp_path)
        reviewcache.put([{'number': 1}], [], 'yamada', config=cfg)
        reviewcache.clear()     # メモリを捨ててディスクの中身を見る

        _crash_at_replace(monkeypatch)
        # 保存の失敗は表示に影響しないため例外にしない (従来どおり)
        reviewcache.put([{'number': 2}], [], 'yamada', config=cfg)
        monkeypatch.undo()

        reviewcache.clear()
        data = reviewcache.load_from_disk(cfg)
        assert data['pending'] == [{'number': 1}]
        assert _leftovers(tmp_path) == []
