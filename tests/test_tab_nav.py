"""画面上部のタブ並び (nav) の characterization test.

タブの nav ボタン・本体 (section) は、組み込みタブ (index.html に直書き) と
後から足したタブ (templates/_tab_<x>.html) が交互に並んでいる。並べ方の仕組みを
変えても、利用者から見える並びと表示名が変わらないことをここで固定する。

新しいタブを足しても既存タブの並びは変わらない、という形で書いてあるので、
タブを足す PR がこのファイルを編集する必要はない。
"""
import re

# 2026-10 時点 (v1.12、#185 の main) の並びと表示名。基準値の書き換えは要レビュー
MAIN_NAV = [
    ('model', 'モデル'),
    ('stress', '応力図'),
    ('qr', 'QR図 (反力・分担・偏心)'),
    ('check', '断面検定 (S/木/RC/SRC)'),
    ('ratio', '検定比図'),
    ('tex', 'TeX表'),
    ('dxf', 'DXF'),
    ('vwdxf', 'DXF(VW10J)'),
    ('quantity', '数量集計'),
    ('loadmap', '荷重分布図'),
    ('wallqty', '木造壁量計算'),
    ('nvalue', 'N値計算'),
]
MAIN_IDS = [i for i, _ in MAIN_NAV]

_BUTTON = re.compile(r'<button data-tab="([^"]+)"( class="active")?>([^<]*)</button>')


def _render(client):
    r = client.get('/')
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _nav(html):
    m = re.search(r'<nav>(.*?)</nav>', html, re.S)
    assert m, 'nav が見つからない'
    return [(i, label, bool(act)) for i, act, label in _BUTTON.findall(m.group(1))]


def test_nav_keeps_main_tabs_in_order(client):
    nav = _nav(_render(client))
    kept = [(i, label) for i, label, _ in nav if i in MAIN_IDS]
    assert kept == MAIN_NAV


def test_nav_first_tab_is_active(client):
    nav = _nav(_render(client))
    assert [i for i, _, act in nav if act] == ['model']
    assert nav[0][0] == 'model'


def test_nav_ids_are_unique(client):
    ids = [i for i, _, _ in _nav(_render(client))]
    assert len(ids) == len(set(ids)), ids


def test_every_nav_button_has_one_section(client):
    html = _render(client)
    for i, _, _ in _nav(html):
        n = len(re.findall(r'<section class="tab[^"]*" id="tab-%s"' % re.escape(i), html))
        assert n == 1, (i, n)


def test_output_folder_hint_starts_with_builtin_folders(client):
    html = _render(client)
    m = re.search(r'\(model / stress / ratio_tex / ratio_plot / tex / dxf / qr'
                  r'([^)]*)\) に保存されます。', html)
    assert m, '出力フォルダの案内文が見つからない'


# ---- 見出し (templates/_tab_<x>.html 先頭行) から作る部分 --------------------

import os
import shutil

from jinja2 import FileSystemLoader

from mgtkit import tabreg

TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'templates')


def _tag_files():
    return sorted(f for f in os.listdir(TEMPLATES)
                  if f.startswith('_tab_') and f.endswith('.html'))


def test_every_tab_template_has_a_name_tag():
    for fn in _tag_files():
        tag = tabreg.read_tab_tag(os.path.join(TEMPLATES, fn))
        assert tag is not None, '%s の先頭行に見出し {# tab: ... #} が無い' % fn


def test_index_html_has_no_handwritten_tab_buttons_or_includes():
    # 手書きのボタン・include があると見出しのタブと 2 重になりうる
    with open(os.path.join(TEMPLATES, 'index.html'), encoding='utf-8') as f:
        src = f.read()
    assert re.findall(r'<button data-tab="(?!\{\{)', src) == []
    assert re.findall(r"\{%\s*include\s+'_tab_", src) == []


def test_extra_nav_tabs_come_from_name_tags(client):
    tagged = [tabreg.read_tab_tag(os.path.join(TEMPLATES, fn)) for fn in _tag_files()]
    nav = [i for i, _, _ in _nav(_render(client))]
    extra = [i for i in nav if i not in MAIN_IDS]
    assert sorted(extra) == sorted(t['id'] for t in tagged if t['id'] not in MAIN_IDS)
    # 並びは order の順 (同じ order なら組み込み → ファイル名順)
    import app as app_module
    order = {t['id']: t['order'] for t in app_module._TABS}
    assert [order[i] for i in nav] == sorted(order[i] for i in nav)


def test_output_folder_hint_lists_name_tag_folders(client):
    import app as app_module
    html = _render(client)
    m = re.search(r'\(model / stress / ratio_tex / ratio_plot / tex / dxf / qr'
                  r'([^)]*)\) に保存されます。', html)
    want = ''.join(' / ' + t['out'] for t in app_module._TABS if t['out'])
    assert m.group(1) == want


def test_loop_template_is_not_counted_as_handwritten(tmp_path):
    p = tmp_path / 'index.html'
    p.write_text('<button data-tab="{{ t.id }}">\n', encoding='utf-8')
    assert tabreg.find_handwritten(str(p)) == {'ids': set(), 'templates': set()}


def test_no_warning_banner_when_all_tabs_load(client):
    assert 'タブを読み込めなかった' not in _render(client)


def test_same_id_twice_shows_one_button_and_a_warning(client, tmp_path, monkeypatch):
    # 見出しの id が組み込みタブや別の見出しと重なっても nav に 2 つ並ばない
    import app as app_module
    tdir = tmp_path / 'templates'
    shutil.copytree(TEMPLATES, tdir)
    (tdir / '_tab_dxf2.html').write_text(
        '{# tab: id="dxf" label="DXF2" order="75" #}\n'
        '<section class="tab" id="tab-dxf">x</section>\n', encoding='utf-8')
    (tdir / '_tab_zzloadmap.html').write_text(
        '{# tab: id="loadmap" label="荷重2" order="101" #}\n'
        '<section class="tab" id="tab-loadmap">x</section>\n', encoding='utf-8')
    # 古い書き方の手書きのボタン・include (wallqty) が index.html に残った場合も 2 重にしない
    idx = (tdir / 'index.html').read_text(encoding='utf-8')
    idx = idx.replace('<nav>', '<nav>\n  <button data-tab="wallqty">木造壁量計算</button>', 1)
    idx = idx.replace('</main>', "{% include '_tab_wallqty.html' %}\n</main>", 1)
    (tdir / 'index.html').write_text(idx, encoding='utf-8')
    tabs, errors = tabreg.collect_tabs(
        str(tdir), app_module.BUILTIN_TABS,
        handwritten=tabreg.find_handwritten(str(tdir / 'index.html')))
    assert sorted(e['name'] for e in errors) == ['_tab_dxf2.html', '_tab_zzloadmap.html']
    monkeypatch.setattr(app_module, '_TABS', tabs)
    monkeypatch.setattr(app_module, '_TAB_ERRORS', errors)
    monkeypatch.setattr(app_module.app.jinja_env, 'loader', FileSystemLoader(str(tdir)))
    html = _render(client)
    nav = _nav(html)
    ids = [i for i, _, _ in nav]
    assert len(ids) == len(set(ids)), ids
    assert set(MAIN_IDS) <= set(ids)          # 手書きの wallqty は先頭に来るので並びは見ない
    assert html.count('id="tab-wallqty"') == 1
    assert 'DXF2' not in html and '荷重2' not in html
    assert '_tab_dxf2.html / _tab_zzloadmap.html' in html   # 警告の帯に名前が出る
