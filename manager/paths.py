# -*- coding: utf-8 -*-
"""config.json の読み込みとインストール先パスの解決.

配置は 2 種類あり、**どちらかは「自分 (クローン) がどこにいるか」だけで
決まる** (manager/docs/decisions.md)。

新しい配置 … クローンが ``<home>/.manager/mgtkit`` にあるとき
    <home>/                     利用者から見えるフォルダ (既定は C:\\mgtkit_appmanager)
      マネージャー起動.bat のショートカット
      アプリマネージャー使い方ガイド.pdf
      stable/mgtkit/            正式版のアプリ (port: config app.port_stable)
      .manager/                 マネージャーの持ち物 (通常は非表示)
        mgtkit/                 このリポジトリの複製
        beta/<版>/mgtkit/       試用中のβ版 (port: config app.port_beta)
        tmp/stable/ tmp/beta/   読み込んだファイルの一時置き場
        workrepo/ diffcache/ settings.json usage.json ...

これまでの配置 … 上記に当てはまらないとき (移行前の PC と開発環境)
    <install_root>/stable/mgtkit/
    <install_root>/stable/uploads_tmp/
    <install_root>/beta/<版>/mgtkit/
    install_root は Windows では %LOCALAPPDATA%/mgtkit、
    それ以外 (開発環境) では ~/.local/share/mgtkit

**置き場所の既定値を無条件に新しい方へ変えてはいけない。** 移行の版を
main に入れると、まだ移行していない PC も起動のたびの git pull で新しい
コードを取り込む。そのとき既定値が変わっていると、setup.bat を押す前に
settings.json が見つからなくなり API キーの入れ直しになる。自分の居場所
から導けば、移行前の PC は今までどおり動く。

config.json の manager.install_root / manager.home_root で上書きできる
(テストと開発用)。install_root を直接指定したときは、これまでの配置と
みなす (その下に stable/ と beta/ を作る)。

注意: app.py は「自身のフォルダ名が mgtkit で、その親が sys.path に入る」
構造 (from mgtkit.x import y) のため、アプリ本体は各インスタンスフォルダ
直下の mgtkit/ サブフォルダに展開する:
  <stable_dir>/mgtkit/app.py
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_HERE)

# 新しい配置でマネージャーの持ち物を入れるフォルダ (通常は非表示)
MANAGER_DIR_NAME = '.manager'
# 新しい配置の既定の置き場所に付ける名前 (中の mgtkit と区別するため別名)
HOME_DIR_NAME = 'mgtkit_appmanager'


def load_config(path=None):
    """config.json を読む。マネージャー同梱の既定値へフォールバックする."""
    path = path or os.path.join(REPO_ROOT, 'config.json')
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _override(config, key):
    value = ((config or {}).get('manager') or {}).get(key)
    if not value:
        return None
    return os.path.expandvars(os.path.expanduser(str(value)))


def default_home_root():
    """新しく置くときの既定の場所 (setup.bat と移行が使う).

    C ドライブの直下にするのは、ユーザーフォルダより浅く、パスが短く
    なるため (Windows の 260 文字制限に強い)。標準ユーザーでも作れる。
    """
    if sys.platform == 'win32':
        drive = os.environ.get('SystemDrive') or 'C:'
        return os.path.join(drive + os.sep, HOME_DIR_NAME)
    return os.path.join(os.path.expanduser('~'), HOME_DIR_NAME)


def home_root(config=None):
    """新しい配置のルート。これまでの配置なら None を返す.

    判定はクローンの位置だけで行う (親フォルダが .manager かどうか)。
    """
    override = _override(config, 'home_root')
    if override:
        return override
    if _override(config, 'install_root'):
        return None             # 置き場所を直接指定 = これまでの配置
    parent = os.path.dirname(REPO_ROOT)
    if os.path.basename(parent) == MANAGER_DIR_NAME:
        return os.path.dirname(parent)
    return None


def install_root(config=None):
    override = _override(config, 'install_root')
    if override:
        return override
    home = home_root(config)
    if home:
        return os.path.join(home, MANAGER_DIR_NAME)
    if sys.platform == 'win32':
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser(
            r'~\AppData\Local')
        return os.path.join(base, 'mgtkit')
    return os.path.join(os.path.expanduser('~'), '.local', 'share', 'mgtkit')


def stable_dir(config=None):
    """正式版のインスタンスフォルダ (中の mgtkit/ がアプリ本体).

    新しい配置では**利用者から見える場所** (<home>/stable) に置く。
    改造したい人はここをコピーして持ち出す。
    """
    home = home_root(config)
    if home:
        return os.path.join(home, 'stable')
    return os.path.join(install_root(config), 'stable')


def beta_root(config=None):
    """試用中のβ版を入れるフォルダ (中に版ごとのフォルダができる)."""
    return os.path.join(install_root(config), 'beta')


def beta_dir(version, config=None):
    return os.path.join(beta_root(config), str(version))


def app_dir(instance_dir):
    """インスタンスフォルダ内のアプリ本体フォルダ (名前は mgtkit 固定)."""
    return os.path.join(instance_dir, 'mgtkit')


def upload_tmp_dir(instance_dir, channel='stable', config=None):
    """読み込んだファイルの一時置き場 (MGTKIT_UPLOAD_DIR 用).

    新しい配置では、利用者から見える stable/ を散らかさないよう
    <install_root>/tmp/<channel>/ に分ける。これまでの配置では
    インスタンスフォルダ直下の uploads_tmp/。
    """
    if home_root(config):
        return os.path.join(install_root(config), 'tmp', str(channel))
    return os.path.join(instance_dir, 'uploads_tmp')


def stable_port(config=None):
    return int(((config or {}).get('app') or {}).get('port_stable', 8765))


def beta_port(config=None):
    return int(((config or {}).get('app') or {}).get('port_beta', 8766))


def repo_slug(config=None):
    return ((config or {}).get('repo')) or 'takimoto0223/mgtkit'
