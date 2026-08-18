# -*- coding: utf-8 -*-
"""過去の更新ログの時系列図の描画 (manager/historyview.py) のテスト.

見た目そのものは目で確かめるしかないが、「帯の文字が帯の外へ出ない」
「きょう線が現行版バッジに重ならない」という約束は座標で検査できる。
提出者の名前が長くても、正式版の間隔が短くても崩れないことを固定する。
"""
import datetime

import flet.canvas as cv

from manager import history, historyview

D = datetime.date
TODAY = D(2026, 8, 18)


def _rel(tag, date, sha=''):
    return {'tag': tag, 'name': tag, 'prerelease': False, 'notes': '',
            'published_at': date, 'published_at_full': date + 'T00:00:00Z',
            'assets': [], 'tag_sha': sha}


def _merged(number, author, created, merged_at, base=''):
    return {'number': number, 'title': 't%d' % number, 'author': author,
            'created_at': created, 'merged_at': merged_at,
            'base_version': base}


def _rects(canvas):
    return [s for s in canvas.shapes if isinstance(s, cv.Rect)]


def _texts(canvas, value=None):
    return [s for s in canvas.shapes if isinstance(s, cv.Text)
            and (value is None or str(s.value) == value)]


def _paths(canvas):
    return [s for s in canvas.shapes if isinstance(s, cv.Path)]


def _bbox(shape):
    if isinstance(shape, cv.Rect):
        return (shape.x, shape.y, shape.x + shape.width,
                shape.y + shape.height)
    size = shape.style.size
    w = historyview._est_w(str(shape.value), size)
    ax = shape.alignment.x
    x0 = shape.x - (w / 2 if ax == 0 else (w if ax == 1 else 0))
    return (x0, shape.y, x0 + w, shape.y + size * 1.25)


def _hits(a, b):
    return (a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3])


def _canvas(fig):
    """build_figure の戻り値から描画キャンバスを取り出す."""
    seen, stack = set(), [fig['control']]
    while stack:
        c = stack.pop(0)
        if id(c) in seen:
            continue
        seen.add(id(c))
        if isinstance(c, cv.Canvas):
            return c
        for attr in ('content', 'controls'):
            v = getattr(c, attr, None)
            if v is None:
                continue
            stack += v if isinstance(v, list) else [v]
        stack = stack  # 幅優先 (Canvas は Stack の先頭にある)
    raise AssertionError('Canvas が見つかりませんでした')


def _build(releases, merged, pending=(), current='v1.2'):
    tl = history.build_timeline(releases, merged, list(pending),
                                today=TODAY)
    fig = historyview.build_figure(tl, current, TODAY, lambda *a: None)
    assert fig is not None
    return tl, fig


def _label_boxes(canvas):
    """(帯の文字, その文字を囲む帯の矩形) の組。囲む矩形が無ければ None."""
    rects = [s for s in canvas.shapes if isinstance(s, cv.Rect)]
    out = []
    for s in canvas.shapes:
        if not isinstance(s, cv.Text) or '#' not in str(s.value):
            continue
        size = s.style.size
        w = historyview._est_w(str(s.value), size)
        x0 = s.x - w / 2 if s.alignment.x == 0 else (
            s.x - w if s.alignment.x == 1 else s.x)
        box = (x0, s.y, x0 + w, s.y + size)
        holder = next((r for r in rects
                       if r.x <= box[0] and r.y <= box[1]
                       and r.x + r.width >= box[2]
                       and r.y + r.height >= box[3]), None)
        out.append((str(s.value), holder))
    return out


class TestChipLabelsStayInside:
    """帯の「名前 #番号」が帯の外へ出ない (管理者指示 2026-08)."""

    def test_short_interval_between_releases(self):
        # v1.1 → v1.2 が 4 日しか離れていない (日付どおりの間隔では
        # 「tomiriri #83」が箱に入らなくなる並び)
        releases = [_rel('v1.2', '2026-08-18'), _rel('v1.1', '2026-08-14'),
                    _rel('v1.0', '2026-08-06')]
        merged = [_merged(83, 'tomiriri', '2026-08-14', '2026-08-18'),
                  _merged(31, 'fujitaka213-sys', '2026-08-07',
                          '2026-08-14')]
        _, fig = _build(releases, merged)
        labels = _label_boxes(_canvas(fig))
        assert [n for n, _ in labels] == ['fujitaka213-sys #31',
                                          'tomiriri #83']
        for name, holder in labels:
            assert holder is not None, '%s が帯の外に出ています' % name

    def test_same_day_releases_and_long_name(self):
        # 同じ日に 2 版公開 + 長い提出者名 (いちばん詰まる組み合わせ)
        releases = [_rel('v1.2', '2026-08-14'), _rel('v1.1', '2026-08-14'),
                    _rel('v1.0', '2026-08-14')]
        merged = [_merged(9, 'nagai-namae-no-teishutsusha', '2026-08-14',
                          '2026-08-14'),
                  _merged(8, 'fujitaka213-sys', '2026-08-14',
                          '2026-08-14')]
        _, fig = _build(releases, merged)
        for name, holder in _label_boxes(_canvas(fig)):
            assert holder is not None, '%s が帯の外に出ています' % name

    def test_pending_chip_label_stays_inside(self):
        releases = [_rel('v1.2', '2026-08-18'), _rel('v1.1', '2026-08-14'),
                    _rel('v1.0', '2026-08-06')]
        merged = [_merged(83, 'tomiriri', '2026-08-14', '2026-08-18')]
        pending = [{'number': 84, 'title': 't', 'author': 'tomiriri',
                    'created_at': '2026-08-14'}]
        _, fig = _build(releases, merged, pending)
        labels = _label_boxes(_canvas(fig))
        assert 'tomiriri #84' in [n for n, _ in labels]
        for name, holder in labels:
            assert holder is not None, '%s が帯の外に出ています' % name


class TestNoCollisions:
    """図の中で重なってはいけないものが重なっていないこと.

    どれも実機のレビューで見つかった重なり (2026-08)。並びが変わっても
    再発しないよう座標で固定する。
    """

    def _busy(self):
        # 3 本の提出がすべて v1.0 から分岐し、現行版もその途中にある並び
        releases = [_rel('v1.2', '2026-08-18', 's12'),
                    _rel('v1.1', '2026-08-14', 's11'),
                    _rel('v1.0', '2026-08-06', 's10')]
        merged = [_merged(83, 'tomiriri', '2026-08-14', '2026-08-18',
                          'v1.0'),
                  _merged(31, 'fujitaka213-sys', '2026-08-07',
                          '2026-08-14', 'v1.0')]
        pending = [{'number': 84, 'title': 't', 'author': 'tomiriri',
                    'created_at': '2026-08-14', 'base_version': 'v1.0'}]
        return _build(releases, merged, pending)

    def test_branches_leave_a_node_at_different_x(self):
        tl, fig = self._busy()
        assert [c['base_tag'] for c in tl['chips']] == ['v1.0'] * 3
        slots = historyview._depart_slots(tl['chips'])
        assert sorted(slots.values()) == [0, 1, 2]

    def test_merge_arrow_and_branch_do_not_share_x(self):
        # 同じ版に「合流」と「分岐」があるとき、線が同じ x を通ると
        # 矢先が塗り潰されて 1 本の線に見える
        releases = [_rel('v1.2', '2026-08-18', 's12'),
                    _rel('v1.1', '2026-08-14', 's11'),
                    _rel('v1.0', '2026-08-06', 's10')]
        merged = [_merged(83, 'tomiriri', '2026-08-14', '2026-08-18',
                          'v1.1'),
                  _merged(31, 'fujitaka213-sys', '2026-08-07',
                          '2026-08-14', 'v1.0')]
        tl, _fig = _build(releases, merged)
        node_x = historyview._node_positions(tl['stables'], tl['chips'])
        # v1.1 は #31 の合流先であり #83 の分岐元でもある
        arrive = node_x['v1.1'] - historyview.ARRIVE_DX
        depart = node_x['v1.1'] + historyview.DEPART_DX
        assert depart - arrive >= 12

    def test_wait_pill_clears_the_today_label(self):
        _tl, fig = self._busy()
        canvas = _canvas(fig)
        pill = next(r for r in _rects(canvas)
                    if r.width == historyview.PILL_W)
        today = next(t for t in _texts(canvas)
                     if str(t.value).startswith('きょう'))
        assert _bbox(today)[0] - (pill.x + pill.width) >= 32

    def test_wait_pill_stays_below_the_date_axis(self):
        _tl, fig = self._busy()
        pill = next(r for r in _rects(_canvas(fig))
                    if r.width == historyview.PILL_W)
        assert pill.y >= historyview.AXIS_Y + 4

    def test_current_badge_does_not_sit_on_a_chip(self):
        tl, fig = self._busy()
        canvas = _canvas(fig)
        badge = next(r for r in _rects(canvas)
                     if r.width == historyview.BADGE_W)
        chips = [r for r in _rects(canvas) if r.height == historyview.CHIP_H]
        for c in chips:
            assert not _hits(_bbox(badge), _bbox(c))

    def test_date_axis_stops_at_the_today_line(self):
        _tl, fig = self._busy()
        canvas = _canvas(fig)
        axis = next(ln for ln in canvas.shapes
                    if isinstance(ln, cv.Line)
                    and ln.y1 == ln.y2 == historyview.AXIS_Y)
        wave = next(p for p in _paths(canvas)
                    if any(isinstance(e, cv.Path.QuadraticTo)
                           for e in p.elements))
        assert axis.x2 <= wave.elements[0].x + 1


class TestNodePositions:
    """ノードの x 座標: 日付に比例させつつ、帯が入る幅は必ず確保する."""

    def test_keeps_date_order_and_reserves_chip_width(self):
        releases = [_rel('v1.2', '2026-08-18'), _rel('v1.1', '2026-08-14'),
                    _rel('v1.0', '2026-08-06')]
        merged = [_merged(83, 'tomiriri', '2026-08-14', '2026-08-18'),
                  _merged(31, 'fujitaka213-sys', '2026-08-07',
                          '2026-08-14')]
        tl = history.build_timeline(releases, merged, [], today=TODAY)
        node_x = historyview._node_positions(tl['stables'], tl['chips'])
        assert node_x['v1.0'] < node_x['v1.1'] < node_x['v1.2']
        for c in tl['chips']:
            need = (historyview.DERIV_LEAD + historyview._chip_min_w(c)
                    + historyview.CHIP_SLACK + historyview.MERGE_LEAD)
            assert (node_x[c['target_tag']] - node_x[c['base_tag']]
                    >= need - 0.001)

    def test_no_stretch_when_dates_already_roomy(self):
        # 間隔が十分なら日付どおり (42px/日) のまま = 図が無駄に伸びない
        releases = [_rel('v1.1', '2026-08-30'), _rel('v1.0', '2026-08-06')]
        merged = [_merged(31, 'tomiriri', '2026-08-07', '2026-08-30')]
        tl = history.build_timeline(releases, merged, [], today=TODAY)
        node_x = historyview._node_positions(tl['stables'], tl['chips'])
        assert node_x['v1.1'] - node_x['v1.0'] == 24 * historyview.PX_PER_DAY

    def test_date_scale_passes_through_nodes(self):
        releases = [_rel('v1.2', '2026-08-18'), _rel('v1.1', '2026-08-14'),
                    _rel('v1.0', '2026-08-06')]
        merged = [_merged(83, 'tomiriri', '2026-08-14', '2026-08-18')]
        tl = history.build_timeline(releases, merged, [], today=TODAY)
        node_x = historyview._node_positions(tl['stables'], tl['chips'])
        X = historyview._date_scale(tl['stables'], node_x)
        for s in tl['stables']:
            assert X(s['date']) == node_x[s['tag']]
        # 間の日付は前後のノードの間に入る (日付と位置の前後が食い違わない)
        assert node_x['v1.1'] < X(D(2026, 8, 16)) < node_x['v1.2']


class TestTodayLine:
    """きょう線が現行版バッジや最新ノードに重ならない."""

    def test_today_line_clears_current_badge(self):
        # 現行版をきょう公開した = ノードときょうが同じ日付になる並び
        releases = [_rel('v1.2', '2026-08-18'), _rel('v1.1', '2026-08-14'),
                    _rel('v1.0', '2026-08-06')]
        merged = [_merged(83, 'tomiriri', '2026-08-14', '2026-08-18',
                          'v1.0'),
                  _merged(31, 'fujitaka213-sys', '2026-08-07',
                          '2026-08-14')]
        _, fig = _build(releases, merged)
        canvas = _canvas(fig)
        badge = next(r for r in canvas.shapes
                     if isinstance(r, cv.Rect)
                     and r.width == historyview.BADGE_W)
        # きょうの波線 (縦に長い Path) の x
        wave = next(p for p in canvas.shapes
                    if isinstance(p, cv.Path)
                    and any(isinstance(e, cv.Path.QuadraticTo)
                            for e in p.elements))
        today_x = wave.elements[0].x
        assert badge.x + badge.width < today_x
