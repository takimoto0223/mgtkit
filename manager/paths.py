# -*- coding: utf-8 -*-
"""config.json の読み込みとインストール先パスの解決.

配置方針 (docs/app-manager-spec.md):
  安定版: <install_root>/stable/           (port: config app.port_stable)
  β版  : <install_root>/beta/<version>/   (port: config app.port_beta)
install_root は Windows では %LOCALAPPDATA%/mgtkit、それ以外 (開発環境) では
~/.local/share/mgtkit。config.json の manager.install_root で上書き可。

注意: app.py は「自身のフォルダ名が mgtkit で、その親が sys.path に入る」
構造 (from mgtkit.x import y) のため、アプリ本体は各インスタンスフォルダ
直下の mgtkit/ サブフォルダに展開する:
  <install_root>/stable/mgtkit/app.py
  <install_root>/stable/uploads_tmp/   (MGTKIT_UPLOAD_DIR)
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_HERE)


def load_config(path=None):
    """config.json を読む。マネージャー同梱の既定値へフォールバックする."""
    path = path or os.path.join(REPO_ROOT, 'config.json')
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def install_root(config=None):
    config = config or {}
    override = (config.get('manager') or {}).get('install_root')
    if override:
        return os.path.expandvars(os.path.expanduser(str(override)))
    if sys.platform == 'win32':
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser(
            r'~\AppData\Local')
        return os.path.join(base, 'mgtkit')
    return os.path.join(os.path.expanduser('~'), '.local', 'share', 'mgtkit')


def stable_dir(config=None):
    return os.path.join(install_root(config), 'stable')


def beta_dir(version, config=None):
    return os.path.join(install_root(config), 'beta', str(version))


def app_dir(instance_dir):
    """インスタンスフォルダ内のアプリ本体フォルダ (名前は mgtkit 固定)."""
    return os.path.join(instance_dir, 'mgtkit')


def upload_tmp_dir(instance_dir):
    """インスタンスごとのアップロード一時フォルダ (MGTKIT_UPLOAD_DIR 用)."""
    return os.path.join(instance_dir, 'uploads_tmp')


def stable_port(config=None):
    return int(((config or {}).get('app') or {}).get('port_stable', 8765))


def beta_port(config=None):
    return int(((config or {}).get('app') or {}).get('port_beta', 8766))


def repo_slug(config=None):
    return ((config or {}).get('repo')) or 'takimoto0223/mgtkit'
