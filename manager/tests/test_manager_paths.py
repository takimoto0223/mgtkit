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


class TestNewLayout:
    """新しい配置 (<home>/stable と <home>/.manager) の決まりごと。

    どちらの配置になるかは **クローンの居場所だけ** で決まる。移行の版を
    main に入れても、まだ移行していない PC は今までどおり動く必要がある
    (でないと setup.bat を押す前に API キーの入れ直しになる)。
    """

    def test_layout_follows_where_the_clone_is(self, monkeypatch):
        monkeypatch.setattr(paths, 'REPO_ROOT', '/x/home/.manager/mgtkit')
        assert paths.home_root({}) == os.path.join('/x', 'home')
        assert paths.install_root({}) == os.path.join('/x/home', '.manager')
        assert paths.stable_dir({}) == os.path.join('/x/home', 'stable')

    def test_an_ordinary_checkout_keeps_the_old_layout(self, monkeypatch):
        # 開発用のクローンや、まだ移行していない PC
        monkeypatch.setattr(paths, 'REPO_ROOT', '/home/me/mgtkit')
        assert paths.home_root({}) is None
        assert paths.stable_dir({}) == os.path.join(
            paths.install_root({}), 'stable')

    def test_home_root_override(self):
        cfg = {'manager': {'home_root': '/opt/appmanager'}}
        assert paths.home_root(cfg) == '/opt/appmanager'
        assert paths.install_root(cfg) == os.path.join(
            '/opt/appmanager', '.manager')
        assert paths.stable_dir(cfg) == os.path.join('/opt/appmanager', 'stable')
        assert paths.beta_dir('v1.3-beta.1', cfg) == os.path.join(
            '/opt/appmanager', '.manager', 'beta', 'v1.3-beta.1')

    def test_install_root_override_means_the_old_layout(self):
        # 既存のテストが使っている書き方。これまでどおり install_root の
        # 下に stable/ と beta/ ができる
        cfg = {'manager': {'install_root': '/root0'}}
        assert paths.home_root(cfg) is None
        assert paths.stable_dir(cfg) == os.path.join('/root0', 'stable')

    def test_default_home_root_is_named_for_the_manager(self):
        # 中の mgtkit フォルダと区別できる名前であること
        assert os.path.basename(paths.default_home_root()) == 'mgtkit_appmanager'

    def test_upload_tmp_is_split_by_channel(self):
        cfg = {'manager': {'home_root': '/opt/appmanager'}}
        stable = paths.upload_tmp_dir('/ignored', 'stable', cfg)
        beta = paths.upload_tmp_dir('/ignored', 'beta', cfg)
        assert stable == os.path.join('/opt/appmanager', '.manager',
                                      'tmp', 'stable')
        assert beta != stable, '安定版とβ版の一時置き場は分ける'

    def test_upload_tmp_stays_beside_the_app_in_the_old_layout(self):
        cfg = {'manager': {'install_root': '/root0'}}
        assert paths.upload_tmp_dir('/root0/stable', 'stable', cfg) == \
            os.path.join('/root0/stable', 'uploads_tmp')

    def test_beta_root_is_one_place(self):
        # 版のフォルダを消す側 (updater.prune_betas) と作る側で
        # 別々に組み立てると、片方だけ旧い場所を見る事故になる
        cfg = {'manager': {'home_root': '/opt/appmanager'}}
        assert paths.beta_dir('v9', cfg).startswith(paths.beta_root(cfg))
