# -*- coding: utf-8 -*-
"""manager/diffwindow.py のテスト (差分のアプリ専用ウィンドウ表示)."""
from manager import diffwindow


class TestOpenDiffWindow:
    def test_returns_none_without_pywebview(self, monkeypatch):
        # pywebview 未導入の環境ではブラウザへフォールバックさせる
        monkeypatch.setattr(diffwindow.importlib.util, 'find_spec',
                            lambda name: None)
        assert diffwindow.open_diff_window('C:/x.html', 't') is None

    def test_spawns_child_process(self, monkeypatch):
        monkeypatch.setattr(diffwindow.importlib.util, 'find_spec',
                            lambda name: object())
        calls = {}

        class FakeProc:
            pass

        def fake_popen(args, **kwargs):
            calls['args'] = args
            calls['kwargs'] = kwargs
            return FakeProc()

        monkeypatch.setattr(diffwindow.subprocess, 'Popen', fake_popen)
        proc = diffwindow.open_diff_window('/tmp/d.html', 'β版の差分 #83')
        assert isinstance(proc, FakeProc)
        # 子プロセスは manager.diffwindow を HTML パスとタイトル付きで起動
        assert calls['args'][1:] == ['-m', 'manager.diffwindow',
                                     '/tmp/d.html', 'β版の差分 #83']

    def test_none_on_launch_failure(self, monkeypatch):
        monkeypatch.setattr(diffwindow.importlib.util, 'find_spec',
                            lambda name: object())

        def boom(args, **kwargs):
            raise OSError('spawn failed')

        monkeypatch.setattr(diffwindow.subprocess, 'Popen', boom)
        assert diffwindow.open_diff_window('/tmp/d.html', 't') is None
