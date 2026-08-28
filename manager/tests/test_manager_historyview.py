# -*- coding: utf-8 -*-
"""過去の更新ログの時系列図の描画 (manager/historyview.py) のテスト.

見た目そのものは目で確かめるしかないが、「帯の文字が帯の外へ出ない」
「線が版の丸に重なる」「要素どうしが重ならない」といった約束は座標で
検査できる。提出者の名前が長くても、正式版の間隔が短くても崩れないことを
固定する。

CI はマネージャーの依存 (flet) も入れるのでここは必ず実行される。
手元に flet を入れていない環境のためにスキップの逃げ道だけ残す。
"""
import datetime
import math

import pytest

pytest.importorskip('flet')

import flet as ft                                           # noqa: E402
import flet.canvas as cv                                    # noqa: E402

from manager import history, historyview                    # noqa: E402

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


class TestChipsNeverOverlap:
    """同じ時期の帯どうしが図の上で重ならない (提出 #167/#168 の再発防止).

    レーンは帯の実際の x で決めるので、日付が同じ (期間の幅が 0) でも
    重なりを見落とさない。同時に何本出ても重ねず、図が縦に伸びる。
    """

    LATER = D(2026, 8, 27)

    def _build(self, releases, merged, pending, current):
        tl = history.build_timeline(releases, merged, list(pending),
                                    today=self.LATER)
        fig = historyview.build_figure(tl, current, self.LATER,
                                       lambda *a: None)
        assert fig is not None
        return tl, fig

    def _chip_rects(self, canvas):
        """帯の矩形 (塗りと枠で 2 枚あるので同じ位置は 1 つに畳む)."""
        seen, out = set(), []
        for r in _rects(canvas):
            if r.height != historyview.CHIP_H:
                continue
            key = (round(r.x, 1), round(r.y, 1), round(r.width, 1))
            if key not in seen:
                seen.add(key)
                out.append(r)
        return out

    def _check(self, fig, count):
        canvas = _canvas(fig)
        boxes = [_bbox(r) for r in self._chip_rects(canvas)]
        assert len(boxes) == count
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                assert not _hits(a, b), '帯どうしが重なっています %s %s' % (a, b)
        # 帯の文字が載ってよいのは自分の帯だけ (外へ出した文字はどの帯にも
        # 載らない)
        for t in _texts(canvas):
            if '#' not in str(t.value):
                continue
            hit = sum(1 for b in boxes if _hits(_bbox(t), b))
            assert hit <= 1, '%s が他の帯に重なっています' % t.value
        # 「確認中」のピルも他の帯に載らない
        for pill in [r for r in _rects(canvas)
                     if r.width == historyview.PILL_W
                     and r.height == historyview.PILL_H]:
            for b in boxes:
                assert not _hits(_bbox(pill), b), '確認中が帯に重なっています'
        return boxes

    def test_two_submissions_on_the_release_day(self):
        # きょう公開した版から、きょう 2 本出された (画面で重なっていた並び)
        releases = [_rel('v1.7', '2026-08-27'), _rel('v1.6', '2026-08-21'),
                    _rel('v1.5', '2026-08-19')]
        merged = [_merged(161, 'tomiriri', '2026-08-21', '2026-08-27',
                          'v1.6')]
        pending = [{'number': 167, 'title': 'a', 'author': 'tomiriri',
                    'created_at': '2026-08-27', 'base_version': 'v1.7'},
                   {'number': 168, 'title': 'b', 'author': 'tomiriri',
                    'created_at': '2026-08-27', 'base_version': 'v1.7'}]
        _tl, fig = self._build(releases, merged, pending, 'v1.7')
        self._check(fig, 3)

    def test_three_branches_at_once_grow_the_figure(self):
        # 同時に 3 本 = 上下だけでは足りない。図の高さを増やして重ねない
        releases = [_rel('v1.1', '2026-08-20'), _rel('v1.0', '2026-08-06')]
        pending = [{'number': 170 + i, 'title': 't',
                    'author': 'teishutsusha%d' % i,
                    'created_at': '2026-08-21', 'base_version': 'v1.1'}
                   for i in range(3)]
        tl, fig = self._build(releases, [], pending, 'v1.1')
        boxes = self._check(fig, 3)
        assert len({round(b[1]) for b in boxes}) == 3    # 3 段に分かれる
        assert fig['height'] > historyview.FIG_H         # 図が縦に伸びる
        assert max(b[3] for b in boxes) < fig['height']  # はみ出さない
        assert sorted(c['lane'] for c in tl['chips']) == [-2, -1, 1]


class TestLinesMeetTheirArrowHeads:
    """枝の線と矢先がつながっている (線が矢先を追い越さない・届かないが無い).

    同じ版から 3 本・4 本と枝が出ると、出発位置を右へずらすぶんだけ
    帯の左端までの横幅が足りなくなり、1/4 円弧が矢先を追い越して
    「線と矢先が別々に浮いている」見え方になっていた (2026-08 実機)。
    """

    LATER = D(2026, 8, 27)

    def _fig(self, n):
        # 同じ版から n 本、同じ日に出した確認中の提出
        releases = [_rel('v1.7', '2026-08-27'), _rel('v1.6', '2026-08-21'),
                    _rel('v1.0', '2026-08-06')]
        merged = [_merged(150, 'tomiriri', '2026-08-06', '2026-08-21',
                          'v1.0'),
                  _merged(152, 'kanazawaryoma817', '2026-08-08',
                          '2026-08-27', 'v1.0')]
        pending = [{'number': 167 + i, 'title': 't',
                    'author': ['tomiriri', 'fujitaka213-sys', 'y-kunie',
                               'kanazawaryoma817'][i],
                    'created_at': '2026-08-27', 'base_version': 'v1.7'}
                   for i in range(n)]
        tl = history.build_timeline(releases, merged, pending,
                                    today=self.LATER)
        fig = historyview.build_figure(tl, 'v1.7', self.LATER,
                                       lambda *a: None)
        assert fig is not None
        return _canvas(fig)

    def _joints(self, canvas):
        """(枝の線の終点, その矢先の先端) の組.

        枝の線 = 太さ 2.4 の Path、矢先 = 塗りつぶしの三角。どちらも
        帯ごとに「線 → 矢先」の順に積まれる。
        """
        ends, tips = [], []
        for s in canvas.shapes:
            if not isinstance(s, cv.Path):
                continue
            paint = s.paint
            if paint.style == ft.PaintingStyle.STROKE:
                if abs((paint.stroke_width or 0) - 2.4) < 0.01:
                    ends.append((s.elements[-1].x, s.elements[-1].y))
            elif len(s.elements) == 4:
                tips.append((s.elements[0].x, s.elements[0].y))
        assert ends and len(ends) == len(tips)
        return list(zip(ends, tips))

    @pytest.mark.parametrize('n', [1, 2, 3, 4])
    def test_arrow_head_sits_on_the_end_of_its_line(self, n):
        for (ex, ey), (tx, ty) in self._joints(self._fig(n)):
            # 矢先の根元 (先端から 9px 手前) が線の終点と一致すること。
            # ずれていると線が矢先を追い越す / 届かないで途切れて見える
            d = math.hypot(tx - ex, ty - ey)
            assert abs(d - 9) < 0.01, \
                '線の終点 (%.1f, %.1f) と矢先 (%.1f, %.1f) が離れています' \
                % (ex, ey, tx, ty)


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
        depart_span = historyview._depart_span(tl['chips'])
        for c in tl['chips']:
            need = (historyview._deriv_lead(c['base_tag'], depart_span)
                    + historyview._chip_min_w(c)
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


class TestDateAxis:
    """薄い縦線と日付は「版が更新された日」だけに置く (提出 #135)."""

    # 初回配布の翌日にもう 1 版 (日付が隣同士) と、同じ日に 2 版
    RELEASES = [
        _rel('v1.7', '2026-08-14'),
        _rel('v1.6', '2026-08-14'),
        _rel('v1.5', '2026-08-10'),
        _rel('v1.4', '2026-07-29'),
        _rel('v1.3', '2026-07-28'),
    ]

    def _shapes(self):
        tl = history.build_timeline(self.RELEASES, [], [], today=TODAY)
        fig = historyview.build_figure(tl, 'v1.7', TODAY, lambda *a: None)
        return tl, _canvas(fig).shapes

    def _grid_lines(self, shapes):
        """日付の薄い縦線 (上端が目盛りの高さのもの)."""
        return [s for s in shapes
                if isinstance(s, cv.Line) and s.y1 == historyview.AXIS_Y
                and s.x1 == s.x2]

    def _date_labels(self, shapes):
        """目盛りの日付 (図の上端に置いた文字)."""
        return [s for s in shapes if isinstance(s, cv.Text) and s.y == 21]

    def test_grid_lines_sit_on_the_versions(self):
        """薄い縦線は「版が更新された日」だけ、しかも版の丸に重なること."""
        tl, shapes = self._shapes()
        node_x = {round(s.x) for s in shapes
                  if isinstance(s, cv.Circle)
                  and s.y == historyview.RAIL_Y}
        line_x = sorted(round(s.x1) for s in self._grid_lines(shapes))

        assert line_x, '日付の縦線が 1 本も無い'
        assert set(line_x) <= node_x          # 版のない日には線を引かない
        # 同じ日に出た版は線も 1 本 (v1.6 と v1.7 は同じ日)
        assert len(line_x) == len(set(line_x)) == len(
            {s['date'] for s in tl['stables']})

    def test_year_is_shown_only_when_it_changes(self):
        _, shapes = self._shapes()
        labels = sorted(self._date_labels(shapes), key=lambda s: s.x)
        values = [s.value for s in labels]
        assert values[0] == '2026/7/28'                  # 最初は年つき
        assert values[1:] == ['7/29', '8/10', '8/14']    # 以降は年なし

    def test_every_version_date_gets_a_label(self):
        """1 日違いで公開された版でも日付が出ること.

        ノードの最小間隔 (MIN_NODE_GAP) が版名 2 つぶんあるので、日付が
        隣とぶつかることは実質起きない。ぶつかったときに数字を間引く
        処理は残してあるが、こちらが通常の見え方。
        """
        tl, shapes = self._shapes()
        assert len(self._date_labels(shapes)) == len(
            {s['date'] for s in tl['stables']})
