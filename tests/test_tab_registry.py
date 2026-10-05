"""タブの自動登録 (tabreg.discover_blueprints / TabFlask) のテスト.

本物のアプリでは「routes.py に make_blueprint を持つサブパッケージが全部
登録され、読み込み失敗が無い」ことを見る。タブを足す PR がこのファイルを
編集しなくて済むよう、期待値はファイルの置き場所から求める。
"""
import os
import sys
import textwrap

import pytest
from flask import Blueprint

from mgtkit import tabreg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tab_packages_on_disk():
    names = []
    for name in sorted(os.listdir(ROOT)):
        d = os.path.join(ROOT, name)
        if name in tabreg.NOT_TABS:
            continue
        if not (os.path.isfile(os.path.join(d, '__init__.py'))
                and os.path.isfile(os.path.join(d, 'routes.py'))):
            continue
        with open(os.path.join(d, 'routes.py'), encoding='utf-8') as f:
            if 'def make_blueprint(' in f.read():
                names.append(name)
    return names


# ---- 本物のアプリ ---------------------------------------------------------

def test_app_registers_every_tab_package(client):
    import app as app_module
    assert app_module._TAB_ERRORS == []
    assert app_module._TAB_PACKAGES == _tab_packages_on_disk()
    # 自動登録より前からあったタブ (基準値。消すときは要レビュー)
    assert {'loadmap', 'wallqty', 'vwdxf'} <= set(app_module.app.blueprints)


def test_app_tab_routes_answer(client):
    # 自動登録したタブの API がつながっている (中身の検証は各タブのテスト)
    r = client.post('/api/loadmap_cases', json={})
    assert r.status_code != 404


def test_app_ignores_old_style_second_registration(client):
    # 古い書き方 (app.py に register_blueprint を 1 行足す) が残っても落ちない
    import app as app_module
    from mgtkit.loadmap.routes import make_blueprint
    before = dict(app_module.app.blueprints)
    app_module.app.register_blueprint(make_blueprint(app_module))
    assert app_module.app.blueprints == before


# ---- TabFlask ------------------------------------------------------------

def _bp(name, import_name):
    bp = Blueprint(name, import_name)
    bp.add_url_rule('/api/%s_ping' % name, 'ping', lambda: 'ok')
    return bp


def test_second_registration_of_same_tab_is_skipped():
    a = tabreg.TabFlask('t')
    a.register_blueprint(_bp('x', 'pkg.x.routes'))
    a.register_blueprint(_bp('x', 'pkg.x.routes'))   # 別オブジェクトでも同じタブ
    assert list(a.blueprints) == ['x']
    assert len([r for r in a.url_map.iter_rules() if r.endpoint == 'x.ping']) == 1


def test_same_name_from_another_package_is_a_conflict():
    a = tabreg.TabFlask('t')
    a.register_blueprint(_bp('x', 'pkg.x.routes'))
    with pytest.raises(ValueError):
        a.register_blueprint(_bp('x', 'pkg.other.routes'))


# ---- discover_blueprints (仮のパッケージで) -------------------------------

@pytest.fixture()
def fake_pkg(tmp_path, monkeypatch):
    """tmp に仮のパッケージ fakekit/ を作り、import できるようにする."""
    root = tmp_path / 'fakekit'
    root.mkdir()
    (root / '__init__.py').write_text('')

    def add(name, routes, init=True):
        d = root / name
        d.mkdir()
        if init:
            (d / '__init__.py').write_text('')
        (d / 'routes.py').write_text(textwrap.dedent(routes), encoding='utf-8')

    monkeypatch.syspath_prepend(str(tmp_path))
    yield root, add
    for m in [m for m in sys.modules if m == 'fakekit' or m.startswith('fakekit.')]:
        del sys.modules[m]


_GOOD = '''
from flask import Blueprint
def make_blueprint(host):
    bp = Blueprint(__name__.split('.')[1], __name__)
    bp.add_url_rule('/api/%s_ping' % bp.name, 'ping', lambda: host.ANSWER)
    return bp
'''


def test_discover_registers_in_name_order_and_skips_non_tabs(fake_pkg):
    root, add = fake_pkg
    add('bbb', _GOOD)
    add('aaa', _GOOD)
    add('noinit', _GOOD, init=False)            # __init__.py が無い
    add('helper', 'X = 1\n')                     # make_blueprint が無い
    add('manager', _GOOD)                        # タブとして読まないフォルダ
    a = tabreg.TabFlask('t')
    host = type(sys)('host')
    host.ANSWER = 'ok'
    loaded, errors = tabreg.discover_blueprints(a, host, str(root), 'fakekit')
    assert loaded == ['aaa', 'bbb']
    assert errors == []
    assert a.test_client().get('/api/bbb_ping').get_data(as_text=True) == 'ok'


def test_discover_isolates_a_broken_tab(fake_pkg, caplog):
    root, add = fake_pkg
    add('aaa', _GOOD)
    add('broken', 'import no_such_module_for_test\n')
    add('ccc', _GOOD)
    a = tabreg.TabFlask('t')
    with caplog.at_level('ERROR', logger='mgtkit.tabs'):
        loaded, errors = tabreg.discover_blueprints(a, None, str(root), 'fakekit')
    assert loaded == ['aaa', 'ccc']
    assert [e['name'] for e in errors] == ['broken']
    assert 'no_such_module_for_test' in errors[0]['error']
    # 黙って握りつぶさない: トレースバック付きでログに出る
    assert any(r.exc_info for r in caplog.records)


def test_discover_then_old_style_line_does_not_crash(fake_pkg):
    root, add = fake_pkg
    add('aaa', _GOOD)
    a = tabreg.TabFlask('t')
    tabreg.discover_blueprints(a, None, str(root), 'fakekit')
    from fakekit.aaa.routes import make_blueprint
    a.register_blueprint(make_blueprint(None))   # 自動登録のあとに古い書き方
    assert list(a.blueprints) == ['aaa']
