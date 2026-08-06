# -*- coding: utf-8 -*-
"""利用者ごとのローカル設定 (名前・Anthropic API キー).

- 保存先: <install_root>/settings.json (各自の PC のみ。リポジトリや
  GitHub には一切送らない)
- 初回起動時に登録が完了するまでマネージャーの機能は使えない
- API キーは提出時のコミットメッセージ/PR 本文生成と、検証失敗時の
  自動修正ループに「本人のキー」として使われる (管理者キーの共用はしない)
"""
import json
import logging
import os
import stat

from . import paths

log = logging.getLogger(__name__)


def settings_path(config=None):
    return os.path.join(paths.install_root(config), 'settings.json')


def load_settings(config=None):
    """設定を読む。未登録なら None を返す."""
    try:
        with open(settings_path(config), encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not str(data.get('name', '')).strip():
        return None
    return data


def save_settings(name, api_key, config=None):
    """設定を保存する。API キーは形式を軽く検証する."""
    name = str(name or '').strip()
    api_key = str(api_key or '').strip()
    if not name:
        raise ValueError('名前を入力してください。')
    if not api_key:
        raise ValueError('Anthropic API キーを入力してください。')
    if not api_key.startswith('sk-ant-'):
        raise ValueError('API キーの形式が正しくありません '
                         '(sk-ant- で始まる文字列を入力してください)。')
    path = settings_path(config)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'name': name, 'anthropic_api_key': api_key}, f,
                  ensure_ascii=False, indent=2)
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 本人のみ読み書き
    except OSError:
        pass  # Windows では NTFS ACL に従う
    return path


def api_key(config=None):
    """本人の API キー (設定ファイル優先、無ければ環境変数)."""
    data = load_settings(config)
    if data and data.get('anthropic_api_key'):
        return data['anthropic_api_key']
    return os.environ.get('ANTHROPIC_API_KEY') or None


def user_name(config=None):
    data = load_settings(config)
    return (data or {}).get('name')
