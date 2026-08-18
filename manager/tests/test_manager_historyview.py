# -*- coding: utf-8 -*-
"""過去の更新ログの図 (manager/historyview.py) の描画テスト。

flet は CI の依存に含めないため、未導入環境では自動スキップされる。
"""
import datetime

import pytest

flet = pytest.importorskip('flet')

import flet.canvas as cv                                    # noqa: E402

from manager import history, historyview                    # noqa: E402

TODAY = datetime.date(2026, 8, 18)


def _rel(tag, date):
    return {'tag': tag, 'name': tag, 'prerelease': False,
            'notes': '', 'published_at': date, 'assets': []}


# 初回配布の翌日にもう 1 版 (日付が隣同士) と、同じ日に 2 版 (v1.6/v1.7)
RELEASES = [
    _rel('v1.7', '2026-08-14'),
    _rel('v1.6', '2026-08-14'),
    _rel('v1.5', '2026-08-10'),
    _rel('v1.4', '2026-07-29'),
    _rel('v1.3', '2026-07-28'),
]


def _figure():
    tl = history.build_timeline(RELEASES, [], [], today=TODAY)
    fig = historyview.build_figure(tl, 'v1.7', TODAY, lambda *a: None)
    stack = fig['scroll_row'].controls[0]
    return tl, stack.controls[0].content.shapes


def _grid_lines(shapes):
    """日付の薄い縦線 (上端が目盛りの高さのもの)."""
    return [s for s in shapes
            if isinstance(s, cv.Line) and s.y1 == historyview.AXIS_Y
            and s.x1 == s.x2]


def _date_labels(shapes):
    """目盛りの日付 (図の上端に置いた文字)."""
    return [s for s in shapes
            if isinstance(s, cv.Text) and s.y == 21]


def test_grid_lines_sit_on_the_versions():
    """薄い縦線は「版が更新された日」だけ、しかも版の丸に重なること."""
    tl, shapes = _figure()
    node_x = {round(s.x) for s in shapes
              if isinstance(s, cv.Circle) and s.y == historyview.RAIL_Y}
    line_x = sorted(round(s.x1) for s in _grid_lines(shapes))

    assert line_x, '日付の縦線が 1 本も無い'
    assert set(line_x) <= node_x          # 版のない日には線を引かない
    # 同じ日に出た版は線も 1 本 (v1.6 と v1.7 は同じ日)
    assert len(line_x) == len(set(line_x)) == len(
        {s['date'] for s in tl['stables']})


def test_dates_show_the_year_once_and_skip_overlaps():
    """年は変わったときだけ。隣とぶつかる日付は線だけにすること."""
    _, shapes = _figure()
    labels = sorted(_date_labels(shapes), key=lambda s: s.x)
    values = [s.value for s in labels]

    assert values[0] == '2026/7/28'        # 最初は年つき
    assert values[1:] == ['8/10', '8/14']  # 以降は年なし
    # 7/29 は 7/28 の日付と重なるため、線だけにして数字は出さない
    assert '7/29' not in values
    assert len(labels) < len(_grid_lines(shapes))
