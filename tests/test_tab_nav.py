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
