"""manager/safeio.py (状態ファイルの保存と後片付け) のテスト。

保存: 素の open(path, 'w') は開いた時点で中身を空にするため、書き込み中に
落ちると 0 バイトのファイルが残る。読み手はそれを「未登録」「記録なし」
と読み替えてしまうので、失ったことに誰も気づかない。ここでは
「落ちても前の中身が残る」「一時ファイルが散らからない」を固定する。

後片付け: 読み取り専用のファイルを含むフォルダ (git のクローン) を
消せること、消せなかったときに例外ではなく False で分かることを固定する。
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

    def test_mode_is_set_when_the_file_is_created(self, tmp_path,
                                                  monkeypatch):
        """権限は一時ファイルを作る瞬間に当たること.

        中身を書いてから chmod すると、その間だけ他人から読める窓が
        残る。settings.json は API キーそのものなので、作成時に渡して
        いることを直接確かめる。
        """
        seen = []
        real_open = os.open

        def spy(p, flags, mode=0o777, *a, **kw):
            if str(p).endswith('.tmp'):
                seen.append(mode)
            return real_open(p, flags, mode, *a, **kw)

        monkeypatch.setattr(os, 'open', spy)
        safeio.write_json(str(tmp_path / 'a.json'), {'k': 'sk-ant-x'},
                          mode=stat.S_IRUSR | stat.S_IWUSR)
        assert seen == [0o600]

    def test_mode_is_optional(self, tmp_path):
        # mode を渡さない保存はふつうの権限のまま (usage.json など)
        path = str(tmp_path / 'a.json')
        safeio.write_json(path, {'x': 1})
        assert os.path.isfile(path)


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


def _is_root():
    return getattr(os, 'geteuid', lambda: 1)() == 0


def _clone_like_tree(root):
    """git のクローンを模した、読み取り専用の中身を含むフォルダ.

    git は Windows でクローンの pack / loose object に読み取り専用属性を
    付ける。POSIX でも「中のものを消せないフォルダ」を作れば同じ形の
    詰まり方を再現できる。
    """
    objects = root / '.git' / 'objects' / 'ab'
    objects.mkdir(parents=True)
    obj = objects / 'cdef0123456789'
    obj.write_bytes(b'object')
    (root / 'app.py').write_text('x', encoding='utf-8')
    os.chmod(str(obj), stat.S_IRUSR)                        # 読み取り専用
    os.chmod(str(objects), stat.S_IRUSR | stat.S_IXUSR)     # 消せないフォルダ
    return root


class TestRmtree:
    def test_removes_a_plain_folder(self, tmp_path):
        d = tmp_path / 'work'
        (d / 'sub').mkdir(parents=True)
        (d / 'sub' / 'a.txt').write_text('x', encoding='utf-8')
        assert safeio.rmtree(str(d)) is True
        assert not d.exists()

    def test_missing_folder_counts_as_removed(self, tmp_path):
        assert safeio.rmtree(str(tmp_path / 'nope')) is True

    def test_empty_path_is_harmless(self):
        # submit.cleanup は prep.get('tmp', '') を渡すことがある
        assert safeio.rmtree('') is True
        assert safeio.rmtree(None) is True

    def test_removes_read_only_tree(self, tmp_path):
        root = _clone_like_tree(tmp_path / 'workrepo')
        assert safeio.rmtree(str(root)) is True
        assert not root.exists()

    @pytest.mark.skipif(_is_root(),
                        reason='root は権限に関係なく消せるため')
    def test_plain_rmtree_cannot_do_it(self, tmp_path):
        """素の shutil.rmtree では消しきれないこと (この対処が要る根拠)."""
        import shutil

        root = _clone_like_tree(tmp_path / 'workrepo')
        with pytest.raises(OSError):
            shutil.rmtree(str(root))
        assert root.exists()
        # ignore_errors=True は例外を出さないだけで、やはり消えない
        shutil.rmtree(str(root), ignore_errors=True)
        assert root.exists()
        # 後片付け (残すと pytest の一時フォルダ掃除が詰まる)
        assert safeio.rmtree(str(root)) is True

    def test_reports_failure_instead_of_raising(self, tmp_path,
                                                monkeypatch):
        d = tmp_path / 'stubborn'
        d.mkdir()
        (d / 'a.txt').write_text('x', encoding='utf-8')

        def boom(path, **kw):
            raise OSError('消せません (試験)')
        monkeypatch.setattr(safeio.shutil, 'rmtree', boom)

        assert safeio.rmtree(str(d)) is False    # 例外は投げない
        assert d.exists()

    def test_silent_failure_is_detected(self, tmp_path, monkeypatch):
        """例外が出なくても、残っていれば False を返すこと.

        消えたことにして先へ進むと、移行の後片付けで旧フォルダが
        残ったまま「片付いた」と誤解する。消したあとに存在を確かめて
        から成否を決める。
        """
        d = tmp_path / 'quiet'
        d.mkdir()
        monkeypatch.setattr(safeio.shutil, 'rmtree',
                            lambda path, **kw: None)
        assert safeio.rmtree(str(d)) is False
        assert d.exists()

    def test_uses_the_handler_this_python_understands(self, tmp_path):
        """失敗ハンドラの引数名は Python 3.12 で onerror → onexc に変わった.

        取り違えると shutil.rmtree が TypeError になるため、実際に
        呼んで通ることを確かめる。
        """
        seen = {}

        def spy(path, **kw):
            seen.update(kw)
        import shutil
        real = shutil.rmtree
        try:
            shutil.rmtree = spy
            d = tmp_path / 'x'
            d.mkdir()
            safeio.rmtree(str(d))
        finally:
            shutil.rmtree = real
        assert list(seen) == [safeio._RMTREE_HANDLER]
        assert safeio._RMTREE_HANDLER in ('onexc', 'onerror')
        # 本物にその名前の引数があること
        import inspect
        params = inspect.signature(real).parameters
        assert safeio._RMTREE_HANDLER in params
