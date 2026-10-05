"""見出し (tabreg.read_tab_tag / collect_tabs) の単体テスト."""
import pytest

from mgtkit import tabreg

BUILTIN = [{'id': 'model', 'label': 'モデル', 'order': 10, 'active': True},
           {'id': 'dxf', 'label': 'DXF', 'order': 70},
           {'id': 'quantity', 'label': '数量集計', 'order': 90}]


def _write(d, name, first, body='<section class="tab"></section>\n'):
    (d / name).write_text(first + '\n' + body, encoding='utf-8')


def test_read_tag(tmp_path):
    _write(tmp_path, '_tab_a.html',
           '{# tab: id="a" label="A タブ (試用)" order="85" out="a_out" #}')
    assert tabreg.read_tab_tag(str(tmp_path / '_tab_a.html')) == {
        'id': 'a', 'label': 'A タブ (試用)', 'order': 85.0, 'out': 'a_out'}


def test_read_tag_out_is_optional(tmp_path):
    _write(tmp_path, '_tab_a.html', '{#tab: id="a" label="A" order="1"#}')
    assert tabreg.read_tab_tag(str(tmp_path / '_tab_a.html'))['out'] == ''


def test_no_tag_is_none(tmp_path):
    _write(tmp_path, '_tab_a.html', '<!-- タブ: A -->')
    assert tabreg.read_tab_tag(str(tmp_path / '_tab_a.html')) is None


@pytest.mark.parametrize('first, why', [
    ('{# tab: id="a" order="1" #}', 'label'),
    ('{# tab: id="a" label="A" #}', 'order'),
    ('{# tab: id="a" label="A" order="右端" #}', '数値'),
    ('{# tab: id="a" lable="A" order="1" #}', '知らない項目'),
    ('{# tab: id="a b" label="A" order="1" #}', 'id'),
    ('{# tab: id=a label="A" order="1" #}', '読めない'),
    ('{# tab: id="a" label="A" order="1"', '書き方'),
])
def test_bad_tag_says_why(tmp_path, first, why):
    _write(tmp_path, '_tab_a.html', first)
    with pytest.raises(ValueError, match=why):
        tabreg.read_tab_tag(str(tmp_path / '_tab_a.html'))


def test_collect_interleaves_builtin_and_tags(tmp_path):
    _write(tmp_path, '_tab_vw.html', '{# tab: id="vw" label="VW" order="80" #}')
    _write(tmp_path, '_tab_lm.html', '{# tab: id="lm" label="LM" order="100" out="lm" #}')
    _write(tmp_path, '_tab_first.html', '{# tab: id="first" label="F" order="5" #}')
    _write(tmp_path, 'index.html', '<html>')                  # 対象外
    _write(tmp_path, '_colbase_figure.html', '{# tab: x #}')  # 対象外 (_tab_ でない)
    tabs, errors = tabreg.collect_tabs(str(tmp_path), BUILTIN)
    assert errors == []
    assert [t['id'] for t in tabs] == ['first', 'model', 'dxf', 'vw', 'quantity', 'lm']
    assert [t['template'] for t in tabs] == [
        '_tab_first.html', None, None, '_tab_vw.html', None, '_tab_lm.html']
    assert tabs[1]['active'] is True


def test_collect_same_order_keeps_builtin_then_file_name(tmp_path):
    _write(tmp_path, '_tab_b.html', '{# tab: id="b" label="B" order="70" #}')
    _write(tmp_path, '_tab_a.html', '{# tab: id="a" label="A" order="70" #}')
    tabs, _ = tabreg.collect_tabs(str(tmp_path), BUILTIN)
    assert [t['id'] for t in tabs] == ['model', 'dxf', 'a', 'b', 'quantity']


def test_collect_duplicate_ids_keep_first(tmp_path):
    _write(tmp_path, '_tab_a.html', '{# tab: id="x" label="X1" order="1" #}')
    _write(tmp_path, '_tab_b.html', '{# tab: id="x" label="X2" order="2" #}')
    _write(tmp_path, '_tab_c.html', '{# tab: id="dxf" label="DXF2" order="3" #}')
    tabs, errors = tabreg.collect_tabs(str(tmp_path), BUILTIN)
    ids = [t['id'] for t in tabs]
    assert len(ids) == len(set(ids))
    assert [t['label'] for t in tabs if t['id'] in ('x', 'dxf')] == ['X1', 'DXF']
    assert [e['name'] for e in errors] == ['_tab_b.html', '_tab_c.html']


def test_collect_reports_bad_tag_and_skips_failed_package(tmp_path):
    _write(tmp_path, '_tab_bad.html', '{# tab: id="bad" order="1" #}')
    _write(tmp_path, '_tab_broken.html', '{# tab: id="broken" label="B" order="1" #}')
    _write(tmp_path, '_tab_untagged.html', '<section></section>')
    tabs, errors = tabreg.collect_tabs(str(tmp_path), BUILTIN, skip={'broken'})
    assert [t['id'] for t in tabs] == ['model', 'dxf', 'quantity']
    assert [e['name'] for e in errors] == ['_tab_bad.html']


def test_collect_handwritten_lines_are_not_doubled(tmp_path):
    # 古い書き方の提出で index.html に手書きのボタン・include が残った場合
    _write(tmp_path, '_tab_lm.html', '{# tab: id="lm" label="LM" order="100" #}')
    _write(tmp_path, '_tab_vw.html', '{# tab: id="vw" label="VW" order="80" #}')
    _write(tmp_path, 'index.html', '''<nav>
  <button data-tab="model" class="active">モデル</button>
  <button data-tab="lm">LM</button>
{%- for t in tabs if t.nav %}<button data-tab="{{ t.id }}">{% endfor %}
</nav>
{% include '_tab_lm.html' %}''')
    hw = tabreg.find_handwritten(str(tmp_path / 'index.html'))
    assert hw == {'ids': {'model', 'lm'}, 'templates': {'_tab_lm.html'}}
    tabs, errors = tabreg.collect_tabs(str(tmp_path), BUILTIN, handwritten=hw)
    assert errors == []
    assert [t['id'] for t in tabs if t['nav']] == ['dxf', 'vw', 'quantity']
    assert [t['template'] for t in tabs if t['include']] == ['_tab_vw.html']
