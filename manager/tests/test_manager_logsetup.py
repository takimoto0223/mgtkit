# -*- coding: utf-8 -*-
"""manager/logsetup.py のテスト (API キーのマスクとファイル出力)."""
import logging

import pytest

from manager import logsetup


@pytest.fixture()
def _isolated_root(monkeypatch, tmp_path):
    """root ロガーとログの置き場をテスト内に閉じ込める."""
    monkeypatch.setattr(logsetup.paths, 'install_root',
                        lambda config=None: str(tmp_path))
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    for h in root.handlers[:]:
        root.removeHandler(h)
    yield tmp_path
    for h in root.handlers[:]:
        h.close()
        root.removeHandler(h)
    for h in saved[0]:
        root.addHandler(h)
    root.setLevel(saved[1])


def _file_handler():
    """setup が付けたファイルハンドラを取り出す.

    テストは root ロガー経由でなくハンドラを直接駆動する (pytest の
    logging プラグインが root に介入するため。実運用の配線は
    test_setup_is_idempotent がハンドラの存在で確かめる)。
    """
    for h in logging.getLogger().handlers:
        if getattr(h, 'baseFilename', None):
            return h
    raise AssertionError('ファイルハンドラが見つかりません')


def _record(msg, *args, exc_info=None):
    return logging.LogRecord('manager.test', logging.INFO, 'x.py', 1,
                             msg, args, exc_info)


class TestMasking:
    def test_api_key_never_reaches_the_file(self, _isolated_root):
        """メッセージ・引数・例外のどの経路でもキーが残らないこと."""
        path = logsetup.setup({})
        fh = _file_handler()
        fh.handle(_record('キー sk-ant-api03-abcdef123456 を保存'))
        fh.handle(_record('引数経由: %s', 'sk-ant-api03-xyzXYZ_-789'))
        try:
            raise ValueError('bad key sk-ant-api03-hidden000')
        except ValueError:
            import sys
            fh.handle(_record('失敗', exc_info=sys.exc_info()))
        fh.flush()
        text = open(path, encoding='utf-8').read()
        assert 'abcdef' not in text
        assert 'xyzXYZ' not in text
        assert 'hidden000' not in text
        assert text.count('sk-ant-***') >= 3   # 消しつつ痕跡は分かる

    def test_normal_messages_pass_through(self, _isolated_root):
        path = logsetup.setup({})
        fh = _file_handler()
        fh.handle(_record('ふつうのメッセージ %d', 42))
        fh.flush()
        assert 'ふつうのメッセージ 42' in open(path, encoding='utf-8').read()


class TestSetup:
    def test_setup_is_idempotent(self, _isolated_root):
        """main が二度呼ばれてもハンドラが重複しないこと."""
        logsetup.setup({})
        logsetup.setup({})
        root = logging.getLogger()
        files = [h for h in root.handlers
                 if getattr(h, 'baseFilename', None)]
        assert len(files) == 1

    def test_unwritable_location_does_not_break_startup(self, monkeypatch,
                                                        _isolated_root):
        """ログを作れなくてもマネージャーは起動できること (補助にすぎない)."""
        def boom(*a, **k):
            raise OSError('作れない')
        monkeypatch.setattr(logsetup.os, 'makedirs', boom)
        assert logsetup.setup({}) is None    # 例外にならない

    def test_console_is_also_masked(self, _isolated_root):
        """コンソール側のハンドラにもマスクが掛かること."""
        logsetup.setup({})
        for h in logging.getLogger().handlers:
            assert isinstance(h.formatter, logsetup.MaskingFormatter)
