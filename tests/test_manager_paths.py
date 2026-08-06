"""manager/paths.py のテスト。"""
import os

from manager import paths


def test_install_root_override():
    cfg = {'manager': {'install_root': '/opt/mgtkit-root'}}
    assert paths.install_root(cfg) == '/opt/mgtkit-root'


def test_app_dir_is_named_mgtkit():
    # app.py の import 構造 (from mgtkit.x import y) のため、
    # アプリ本体フォルダ名は mgtkit 固定でなければならない
    assert os.path.basename(paths.app_dir('/x/stable')) == 'mgtkit'


def test_stable_and_beta_layout():
    cfg = {'manager': {'install_root': '/root0'}}
    assert paths.stable_dir(cfg) == os.path.join('/root0', 'stable')
    assert paths.beta_dir('v1.3-beta.1', cfg) == os.path.join(
        '/root0', 'beta', 'v1.3-beta.1')


def test_ports_default_and_config():
    assert paths.stable_port({}) == 8765
    assert paths.beta_port({}) == 8766
    cfg = {'app': {'port_stable': 9000, 'port_beta': 9001}}
    assert paths.stable_port(cfg) == 9000
    assert paths.beta_port(cfg) == 9001


def test_repo_slug_from_repo_config():
    # リポジトリ同梱の config.json が読めること + キーの存在
    cfg = paths.load_config()
    assert cfg.get('repo') == paths.repo_slug(cfg)
    assert paths.repo_slug({}) == 'takimoto0223/mgtkit'
