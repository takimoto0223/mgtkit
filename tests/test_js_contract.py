"""タブと static/app.js の約束 (data-sync / data-persist / CustomEvent) の静的検査.

タブ側は app.js を編集せず、入力欄の属性とイベントで共通欄につながる。
書き間違い (存在しないグループ名・id の無い欄・知らないイベント名) は画面では
黙って効かないだけなので、ここで見つける。
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*p):
    with open(os.path.join(ROOT, *p), encoding='utf-8') as f:
        return f.read()


def _templates():
    tdir = os.path.join(ROOT, 'templates')
    return {fn: _read('templates', fn) for fn in sorted(os.listdir(tdir))
            if fn.endswith('.html')}


def _sync_group_names():
    m = re.search(r'const SYNC_GROUPS = \{(.*?)\n\};', _read('static', 'app.js'), re.S)
    assert m, 'app.js の SYNC_GROUPS が見つからない'
    return set(re.findall(r'^\s*(\w+):', m.group(1), re.M))


def _tags_with(attr):
    for fn, src in _templates().items():
        for tag in re.findall(r'<input\b[^>]*>', src, re.S):
            if re.search(r'\s%s(=|\s|>)' % attr, tag):
                yield fn, tag


def test_sync_groups_are_the_documented_ones():
    assert _sync_group_names() == {'beam', 'truss', 'plate', 'wall'}


def test_data_sync_names_exist():
    groups = _sync_group_names()
    for fn, tag in _tags_with('data-sync'):
        name = re.search(r'data-sync="([^"]*)"', tag)
        assert name and name.group(1) in groups, (fn, tag)


def test_data_sync_and_persist_inputs_have_ids():
    for attr in ('data-sync', 'data-persist'):
        for fn, tag in _tags_with(attr):
            assert re.search(r'\sid="[^"]+"', tag), (fn, attr, tag)


def test_tab_scripts_listen_only_to_known_events():
    known = set(re.findall(r"new CustomEvent\('(mgtkit:[\w-]+)'", _read('static', 'app.js')))
    assert known == {'mgtkit:mgt-loaded', 'mgtkit:stress-changed'}
    sdir = os.path.join(ROOT, 'static')
    for fn in sorted(os.listdir(sdir)):
        if fn.endswith('.js'):
            for ev in re.findall(r"addEventListener\('(mgtkit:[\w-]+)'", _read('static', fn)):
                assert ev in known, (fn, ev)
