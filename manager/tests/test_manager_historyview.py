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


class TestLineJumps:
    """線どうしの交差は、横の線が小さな山で跨ぐ (ライン ジャンプ).

    深い段の枝は、浅い段の横の線を縦に横切って降りる。山が無いと
    どちらの線が曲がって続くのか読めない (管理者指示 2026-10、規約は
    manager/docs/図の作法.md の 5)。縦の線は切らずに真っ直ぐ通し、
    本線・きょう線・日付の縦線・合流と分岐には山を入れない。
    """

    LATER = D(2026, 8, 30)
    RELEASES = [_rel('v1.3', '2026-08-27'), _rel('v1.2', '2026-08-20'),
                _rel('v1.1', '2026-08-13'), _rel('v1.0', '2026-08-06')]

    def _canvas(self, merged, pending, releases=None):
        tl = history.build_timeline(releases or self.RELEASES, merged,
                                    pending, today=self.LATER)
        fig = historyview.build_figure(tl, 'v1.3', self.LATER,
                                       lambda *a: None)
        assert fig is not None
        return _canvas(fig)

    # 3 本が互い違いに重なる (どの 2 本も入れ子にならない) と、上下に
    # 分けても 2 本は同じ側に来るので、交差はどう並べても残る
    STAGGER = [_rel('v1.4', '2026-08-27'), _rel('v1.3', '2026-08-20'),
               _rel('v1.2', '2026-08-15'), _rel('v1.1', '2026-08-10'),
               _rel('v1.0', '2026-08-06')]
    STAGGER_MERGED = [_merged(150, 'fujitaka213-sys', '2026-08-06',
                              '2026-08-20', 'v1.0'),
                      _merged(151, 'kanazawaryoma817', '2026-08-10',
                              '2026-08-27', 'v1.1')]

    def _two_close_branches(self):
        # v1.0 → v1.3 と v1.1 → v1.4 の帯に、v1.2 から確認中が 3 本。
        # 同じ版から出る 2 本が同じ帯の横の線を並んで横切る
        pending = [{'number': n, 'title': 't', 'author': a,
                    'created_at': '2026-08-24', 'base_version': 'v1.2'}
                   for n, a in ((170, 'y-kunie'), (171, 'tomiriri'),
                                (172, 'y-kunie'))]
        return self._canvas(self.STAGGER_MERGED, pending, self.STAGGER)

    def _interleaved(self):
        # v1.0 → v1.3、v1.1 → v1.4、v1.2 → 確認中 の 3 本が互い違い
        pending = [{'number': 170, 'title': 't', 'author': 'y-kunie',
                    'created_at': '2026-08-24', 'base_version': 'v1.2'}]
        return self._canvas(self.STAGGER_MERGED, pending, self.STAGGER)

    def _mixed(self):
        merged = [_merged(150, 'fujitaka213-sys', '2026-08-06',
                          '2026-08-13', 'v1.0'),
                  _merged(151, 'kanazawaryoma817', '2026-08-13',
                          '2026-08-20', 'v1.1'),
                  _merged(152, 'kanazawaryoma817', '2026-08-13',
                          '2026-08-27', 'v1.1')]
        # 作者名は dict の外で組む ('author': '長い名前' を gitleaks が鍵と誤検知する)
        pending = [{'number': n, 'title': 't', 'author': a,
                    'created_at': d, 'base_version': v}
                   for n, a, d, v in ((170, 'y-kunie', '2026-08-24', 'v1.0'),
                                      (171, 'fujitaka213-sys', '2026-08-28',
                                       'v1.1'))]
        return self._canvas(merged, pending)

    @staticmethod
    def _lines(canvas):
        """枝の線ごとに (横の直線の区間, 曲線, 山) を図から読み直す.

        枝の線 = 太さ 2.4 の Path。区間は (x0, x1, y)、曲線は 3 次
        ベジェの 4 点、山は (左端 x, 右端 x, y, 山の要素の並び)。山は
        上がる 1/4 円 → (平らな上辺) → 下りる 1/4 円 で、上辺は区間に数えない。
        """
        out = []
        for s in _paths(canvas):
            paint = s.paint
            if (paint.style != ft.PaintingStyle.STROKE
                    or abs((paint.stroke_width or 0) - 2.4) > 0.01):
                continue
            runs, curves, hops = [], [], []
            px = py = None
            hop = None          # 山の途中 = (左端 x, 山の y, 要素)
            for e in s.elements:
                if hop is not None:
                    hop[2].append(e)
                    if (isinstance(e, cv.Path.ArcTo)
                            and abs(e.y - hop[1]) < 1e-6):
                        hops.append((hop[0], e.x, hop[1], hop[2]))
                        hop = None
                elif isinstance(e, cv.Path.ArcTo):
                    hop = (px, py, [e])
                elif isinstance(e, cv.Path.LineTo):
                    if abs(e.y - py) < 1e-6 and e.x > px:
                        runs.append((px, e.x, e.y))
                elif isinstance(e, cv.Path.CubicTo):
                    curves.append(((px, py), (e.cp1x, e.cp1y),
                                   (e.cp2x, e.cp2y), (e.x, e.y)))
                px, py = e.x, e.y
            assert hop is None, '山が閉じていません'
            out.append((runs, curves, hops))
        return out

    @staticmethod
    def _curve_points(curve, n=40):
        (x0, y0), (x1, y1), (x2, y2), (x3, y3) = curve
        pts = []
        for i in range(n + 1):
            t = i / n
            u = 1 - t
            pts.append((u ** 3 * x0 + 3 * u * u * t * x1
                        + 3 * u * t * t * x2 + t ** 3 * x3,
                        u ** 3 * y0 + 3 * u * u * t * y1
                        + 3 * u * t * t * y2 + t ** 3 * y3))
        return pts

    def _crossings(self, canvas):
        """(山の無い交差, 山の一覧と、その下を通る他の線の x) を返す."""
        lines = self._lines(canvas)
        bare, hops = [], []
        for i, (runs, _curves, hs) in enumerate(lines):
            others = [c for j, ln in enumerate(lines) if j != i
                      for c in ln[1]]
            for x0, x1, y in runs:
                for c in others:
                    x = historyview._cubic_x_at(c, y)
                    if x is not None and x0 < x < x1:
                        bare.append((round(x, 1), y))
            for left, right, y, arc in hs:
                under = [x for x in (historyview._cubic_x_at(c, y)
                                     for c in others)
                         if x is not None and left < x < right]
                hops.append((left, right, y, arc, under))
        return bare, hops

    @pytest.mark.parametrize('case', ['_two_close_branches', '_interleaved'])
    def test_every_crossing_has_a_hop(self, case):
        bare, hops = self._crossings(getattr(self, case)())
        assert hops, '交差のある図なのに山が 1 つもありません'
        assert not bare, '山の無い交差があります: %s' % bare

    def test_release_order_lanes_avoid_crossings(self):
        # 公開の早い帯ほど本線寄りに置くと、入れ子になる帯どうしは
        # 交差しない (以前は提出順に置いていて、この図に交差が出ていた)
        bare, hops = self._crossings(self._mixed())
        assert not bare and not hops

    def _many_branches(self, names=None):
        releases = [_rel('v1.%d' % (i + 1), '2026-08-%02d' % (20 + i))
                    for i in range(6)] + [_rel('v1.0', '2026-08-06')]
        merged = [_merged(150 + i, 'a%d' % i, '2026-08-07',
                          '2026-08-%02d' % (20 + i), 'v1.0')
                  for i in range(6)]
        pending = [{'number': 170 + i, 'title': 't',
                    'author': (names or ['p%d' % k for k in range(4)])[i],
                    'created_at': '2026-08-08', 'base_version': 'v1.0'}
                   for i in range(4)]
        tl = history.build_timeline(releases, merged, pending,
                                    today=self.LATER)
        fig = historyview.build_figure(tl, 'v1.6', self.LATER,
                                       lambda *a: None)
        return tl, fig, _canvas(fig)

    def test_version_labels_keep_apart(self):
        # 上の段から合流が来る版が並んでも、版名どうしは LABEL_GAP 以上
        # 離し、どの版名も自分の丸にいちばん近い (UI レビュー 2026-10:
        # 左へ逃がした「v2.3」が隣の「v2.2」と 6px まで詰まっていた)
        tl, fig, canvas = self._many_branches()
        node_x = dict(zip([s['tag'] for s in tl['stables']],
                          sorted({sh.x for sh in canvas.shapes
                                  if isinstance(sh, cv.Circle)})))
        labels = {str(t.value).split()[0]: _bbox(t) for t in _texts(canvas)
                  if str(t.value).split()[0] in node_x
                  and '現行版' not in str(t.value)}
        assert len(labels) == len(node_x) - 1       # 現行版はバッジ
        boxes = list(labels.values())
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                if a[1] < b[3] and b[1] < a[3]:     # 同じ高さに並ぶもの
                    gap = max(b[0] - a[2], a[0] - b[2])
                    assert gap >= historyview.LABEL_GAP - 1e-6
        for tag, b in labels.items():
            cx = (b[0] + b[2]) / 2
            nearest = min(node_x, key=lambda t: abs(node_x[t] - cx))
            assert nearest == tag, '%s の名前が %s の丸に近い' % (tag, nearest)

    def test_pending_pill_clear_of_today_label(self):
        # 名前が短い確認中がいちばん上の段に来ても、「確認中」のピルが
        # 「きょう 〜」の文字に届かない (一続きに読めてしまうため)
        tl, fig, canvas = self._many_branches(['kj', 'kk', 'km', 'kn'])
        today = [_bbox(t) for t in _texts(canvas)
                 if str(t.value).startswith('きょう')]
        pills = [_bbox(r) for r in _rects(canvas)
                 if r.height == historyview.PILL_H]
        assert today and pills
        for p in pills:
            assert not _hits((p[0] - 6, p[1], p[2] + 6, p[3]), today[0])

    def test_many_branches_from_one_version(self):
        # 1 つの版から 10 本 (6 本は順に公開・4 本は確認中) = 2026-10 の
        # 実データの形。提出順に下へ積んでいたときは、合流の線がほかの
        # 帯の横の線を何本も横切っていた。公開順に本線寄り・上下交互に
        # 置くと交差が無く、本線が図の中央に来る
        releases = [_rel('v1.%d' % (i + 1), '2026-08-%02d' % (20 + i))
                    for i in range(6)] + [_rel('v1.0', '2026-08-06')]
        merged = [_merged(150 + i, 'a%d' % i, '2026-08-07',
                          '2026-08-%02d' % (20 + i), 'v1.0')
                  for i in range(6)]
        pending = [{'number': 170 + i, 'title': 't', 'author': 'p%d' % i,
                    'created_at': '2026-08-08', 'base_version': 'v1.0'}
                   for i in range(4)]
        tl = history.build_timeline(releases, merged, pending,
                                    today=self.LATER)
        fig = historyview.build_figure(tl, 'v1.6', self.LATER,
                                       lambda *a: None)
        canvas = _canvas(fig)
        bare, hops = self._crossings(canvas)
        assert not bare and not hops
        lanes = {c['number']: c['lane'] for c in tl['chips']}
        up = [n for n, ln in lanes.items() if ln > 0]
        down = [n for n, ln in lanes.items() if ln < 0]
        assert abs(len(up) - len(down)) <= 1
        # 先に公開された帯ほど本線寄り (同じ側の中で段が浅い)
        for side in (up, down):
            merged_side = sorted((n for n in side if n < 170),
                                 key=lambda n: abs(lanes[n]))
            assert merged_side == sorted(merged_side)
            assert all(abs(lanes[m]) < abs(lanes[p])
                       for m in side if m < 170
                       for p in side if p >= 170)
        # 本線は上の段が増えた分だけ下がり、版の丸もそこに載る
        rail_y = fig['rail_y']
        assert rail_y > historyview.RAIL_Y
        nodes = [sh for sh in canvas.shapes if isinstance(sh, cv.Circle)]
        assert nodes and all(sh.y == rail_y for sh in nodes)
        # いちばん上の帯は軸の下、いちばん下の帯は図の中に収まる
        boxes = [_bbox(r) for r in _rects(canvas)
                 if r.height == historyview.CHIP_H]
        assert min(b[1] for b in boxes) >= historyview.UPPER_TOP
        assert max(b[3] for b in boxes) < canvas.height
        # 本線から上端・下端までの高さの差は 1 段ぶん以内 (中央に来る)
        top = min(b[1] for b in boxes)
        bottom = max(b[3] for b in boxes)
        assert abs((rail_y - top) - (bottom - rail_y)) <= \
            historyview.LANE_STEP + 40

    def _taking_turns(self):
        # 2 人が互い違いに出す連続提出 (前の人の公開の前に次の人が出す)
        releases = [_rel('v1.%d' % i, '2026-08-%02d' % (6 + 4 * i))
                    for i in range(5)]
        merged = [_merged(150 + i, ('fujitaka213-sys', 'tomiriri')[i % 2],
                          '2026-08-%02d' % (6 + 4 * i),
                          '2026-08-%02d' % (10 + 4 * i), 'v1.%d' % b)
                  for i, b in enumerate((0, 0, 1, 2))]
        pending = [{'number': 170, 'title': 't', 'author': 'tomiriri',
                    'created_at': '2026-08-20', 'base_version': 'v1.3'}]
        return self._canvas(merged, pending, releases[::-1])

    @pytest.mark.parametrize('case', ['_two_close_branches', '_interleaved',
                                      '_mixed', '_taking_turns'])
    def test_lines_do_not_pass_through_chip_boxes(self, case):
        # 山で跨げるのは横の線どうしだけ。線が帯の箱 (文字) を突き抜ける
        # 並びは避ける (UI レビュー 2026-10: 以前は 2 人交互の連続提出で
        # 合流の線が帯の名前を貫いていた)
        canvas = getattr(self, case)()
        boxes = [(r.x + 2, r.y + 2, r.x + r.width - 2, r.y + r.height - 2)
                 for r in _rects(canvas) if r.height == historyview.CHIP_H]
        for _runs, curves, _hops in self._lines(canvas):
            for cu in curves:
                for px, py in self._curve_points(cu):
                    assert not any(b[0] < px < b[2] and b[1] < py < b[3]
                                   for b in boxes), \
                        '線が帯の箱を通っています (%.0f, %.0f)' % (px, py)

    def test_taking_turns_has_no_crossings(self):
        canvas = self._taking_turns()
        bare, hops = self._crossings(canvas)
        assert not bare and not hops
        ys = {round(r.y) for r in _rects(canvas)
              if r.height == historyview.CHIP_H}
        assert len(ys) == 2                 # 上下 1 段ずつで足りる

    @pytest.mark.parametrize('case', ['_two_close_branches', '_interleaved',
                                      '_mixed'])
    def test_hop_is_an_upward_half_circle_over_a_line(self, case):
        for left, right, y, arc, under in self._crossings(
                getattr(self, case)())[1]:
            # 左 → 右へ時計回りの 1/4 円 2 つ = 上へ膨らむ。高さはどの山も
            # HOP_R (2 本をまとめて跨ぐ広い山は上辺が平らな橋)
            r = historyview.HOP_R
            arcs = [e for e in arc if isinstance(e, cv.Path.ArcTo)]
            assert len(arcs) == 2 and len(arc) in (2, 3)
            assert all(a.clockwise and a.radius == r for a in arcs)
            assert abs(arcs[0].x - (left + r)) < 1e-6
            assert abs(arcs[0].y - (y - r)) < 1e-6 and arcs[1].y == y
            if len(arc) == 3:
                assert abs(arc[1].y - (y - r)) < 1e-6
            assert right - left >= 2 * r - 1e-6
            # 山の下には必ず他の線が通り、山の縁から HOP_R 以上内側
            assert under, '線の通らない所に山があります (x=%.1f)' % left
            assert min(under) >= left + historyview.HOP_R - 1e-6
            assert max(under) <= right - historyview.HOP_R + 1e-6

    def test_close_crossings_share_one_flat_bridge(self):
        # 2 * HOP_R より近い交差は 1 つの山で跨ぐ (小さな山を重ねない)。
        # 広い山も高さは HOP_R のまま、上辺を平らにした橋にする
        r = historyview.HOP_R
        down = [((x, 200), (x, 230), (x, 270), (x, 300)) for x in (100, 105)]
        hops = historyview._hops_on((50, 200, 250), down)
        assert hops == [(100 - r, 105 + r)]
        els = historyview._run_elements((50, 200, 250), hops)
        assert [type(e).__name__ for e in els] == [
            'LineTo', 'ArcTo', 'LineTo', 'ArcTo', 'LineTo']
        assert els[2].y == 250 - r and els[2].x == 105
        # 同じ版から出る枝は出発を 9 px ずつずらすので、図では離れた山になる
        hops = self._crossings(self._two_close_branches())[1]
        by_y = {}
        for h in hops:
            by_y.setdefault(h[2], []).append(h)
        pair = [hs for hs in by_y.values() if len(hs) == 2]
        assert pair and all(len(h[4]) == 1 for h in pair[0])

    @pytest.mark.parametrize('case', ['_two_close_branches', '_interleaved',
                                      '_mixed'])
    def test_curves_do_not_cross_each_other(self, case):
        # 曲線どうしは山で跨げない (斜めに交わると分岐と見分けが付かない)。
        # 同じ版から出る枝は深い段ほど左から出し、同心に並べて交差を無くす
        lines = self._lines(getattr(self, case)())
        polys = [(i, self._curve_points(c))
                 for i, ln in enumerate(lines) for c in ln[1]]

        def side(a, b, c):
            return ((b[0] - a[0]) * (c[1] - a[1])
                    - (b[1] - a[1]) * (c[0] - a[0]))

        def cross(p, q, r, t):
            return (side(p, q, r) * side(p, q, t) < 0
                    and side(r, t, p) * side(r, t, q) < 0)

        for a in range(len(polys)):
            for b in range(a + 1, len(polys)):
                (i, pa), (j, pb) = polys[a], polys[b]
                if i == j:
                    continue
                assert not any(cross(pa[k], pa[k + 1], pb[m], pb[m + 1])
                               for k in range(len(pa) - 1)
                               for m in range(len(pb) - 1)), \
                    '曲線どうしが交わっています (%.1f, %.1f)' % pa[0]

    def test_vertical_lines_are_not_cut(self):
        # 山は横の線にだけ入る。縦寄りの曲線は 1 本の曲線のまま通る
        for _runs, curves, hops in self._lines(self._mixed()):
            for left, right, y, _arc in hops:
                assert all(abs(c[0][1] - y) > 1e-6 or c[0][0] > right
                           or c[3][0] < left for c in curves)

    def test_no_hop_without_crossings(self):
        # 交差の無い図 (既存の 1 段だけの図) には山を入れない
        canvas = self._canvas(
            [_merged(150, 'tomiriri', '2026-08-06', '2026-08-13', 'v1.0')],
            [])
        assert not any(isinstance(e, cv.Path.ArcTo)
                       for p in _paths(canvas) for e in p.elements)
