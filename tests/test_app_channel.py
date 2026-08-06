"""アプリ側の Phase 2 小改修 (β版バナー・アップロード先分離) のテスト。"""
import importlib
import os

import pytest


def test_beta_banner_shown_when_channel_beta(client, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, '_CHANNEL', 'beta')
    r = client.get('/')
    assert r.status_code == 200
    assert 'これはβ版です' in r.get_data(as_text=True)


def test_no_banner_on_stable(client):
    r = client.get('/')
    assert r.status_code == 200
    assert 'これはβ版です' not in r.get_data(as_text=True)


def test_upload_dir_env_override(monkeypatch, tmp_path):
    # _UPLOAD_DIR / _CHANNEL は import 時に環境変数から決まるため、
    # 環境変数を設定して再読込して検証し、終了後に元へ戻す
    import app as app_module
    monkeypatch.setenv('MGTKIT_UPLOAD_DIR', str(tmp_path / 'up'))
    monkeypatch.setenv('MGTKIT_CHANNEL', 'beta')
    try:
        importlib.reload(app_module)
        assert app_module._UPLOAD_DIR == str(tmp_path / 'up')
        assert app_module._CHANNEL == 'beta'
    finally:
        monkeypatch.delenv('MGTKIT_UPLOAD_DIR')
        monkeypatch.delenv('MGTKIT_CHANNEL')
        importlib.reload(app_module)
    assert app_module._CHANNEL == 'stable'
    assert app_module._UPLOAD_DIR.endswith('mgtkit_uploads')
