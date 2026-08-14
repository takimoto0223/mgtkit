# -*- coding: utf-8 -*-
"""過去の更新ログの時系列図 (Flet canvas 描画).

モデルは manager/history.py。見た目の原本は
manager/docs/mockups/history_flow.html (座標・角度・色はモックに従う):
- 横軸 = 時間 (42px/日)。初回配布より左には何も描かない
- グレーの本線 = 正式版の列。白丸 = 版、大きい紺丸 + 橙リング = 現行版
- 人ごとの色の帯 = 提出された更新 (左端 = 提出日)。点線 = 確認中
- 本線から 60° で降りる線 (角は滑らかなカーブ) + 矢先 = その版から派生
- 帯の右端から 60° で本線に上がる矢印 = 正式版として公開
- 文字が入らない幅の帯はラベルを外に出し、塗りを少し濃くする
"""
import datetime

import flet as ft
import flet.canvas as cv

from . import history

NAVY = '#2b4a6f'
RAIL = '#94a3b8'
GRID = '#eef2f7'
AXIS = '#475569'

PX_PER_DAY = 42
X0 = 70                 # 初回配布ノードの x
AXIS_Y = 40
RAIL_Y = 150
CHIP_H = 30
LANE_STEP = 57          # 2 段目以降の下レーンの間隔
FIG_H = 268             # 下レーンが 1 段のときの図の高さ
RIGHT_PAD = 114


def _lane_geom(lane):
    """レーン番号 → (帯の上端 y, 線の y)。-1=下 1 段目, 1=上, -2=下 2 段目."""
    if lane >= 1:
        top = 65
    else:
        top = 195 + (-lane - 1) * LANE_STEP
    return top, top + CHIP_H / 2


def _merge_dx(lane):
    """帯の右端 → 合流先ノードの水平距離 (60° をレーンの深さに合わせる)."""
    _, line_y = _lane_geom(lane)
    return abs(line_y - RAIL_Y) / 1.7321


def _est_w(text, size=11.5):
    """canvas に測定 API が無いための概算幅 (和文=全角、他=半角)."""
    w = 0.0
    for ch in text or '':
        w += size if ord(ch) > 0x2500 else size * 0.55
    return w


def _stroke(color, width=3, dash=None):
    return ft.Paint(color=color, stroke_width=width,
                    style=ft.PaintingStyle.STROKE,
                    stroke_cap=ft.StrokeCap.ROUND,
                    stroke_dash_pattern=dash)


def _fill(color):
    return ft.Paint(color=color, style=ft.PaintingStyle.FILL)


# canvas の Text は page.theme のフォントを継承しないため、build_figure で
# 明示的に受け取ってここへ反映する (日本語の豆腐化対策)
_font_family = None


def _text(x, y, value, size, color, weight=None, center=False, end=False,
          spans=None, max_w=None):
    """canvas Text。y は上端。center/end で x を中央/右端の基準にする.

    max_w を与えると 1 行に収めて省略記号で切る (他の要素への食み出し
    防止)。
    """
    align = ft.Alignment(0, -1) if center else (
        ft.Alignment(1, -1) if end else ft.Alignment(-1, -1))
    style = ft.TextStyle(size=size, color=color, weight=weight,
                         font_family=_font_family)
    kw = {}
    if max_w is not None:
        kw = {'max_lines': 1, 'max_width': max_w, 'ellipsis': '…'}
    return cv.Text(x, y, value, style=style, alignment=align, spans=spans,
                   **kw)


def _derivation(base_x, arrow_back, lane, color):
    """基点ノード → 帯へ降りる (上がる) 60° の角丸カーブ + 矢先.

    モックの形: 60° の直線で降り、最後の縦 24px を三次ベジェで水平へ
    つなぐ。水平の余白が足りないときは同じ形のまま矢先の根元に収める。
    """
    _, line_y = _lane_geom(lane)
    sign = 1 if lane < 0 else -1            # 下レーンは +y 方向
    p1x = base_x + (abs(line_y - RAIL_Y) - 24) / 1.7321
    p1y = line_y - sign * 24
    elements = [cv.Path.MoveTo(base_x, RAIL_Y), cv.Path.LineTo(p1x, p1y)]
    if arrow_back >= p1x + 14:
        elements += [cv.Path.CubicTo(p1x + 4, p1y + sign * 6.9,
                                     p1x + 4.2, line_y,
                                     p1x + 12.2, line_y),
                     cv.Path.LineTo(arrow_back, line_y)]
    else:
        elements += [cv.Path.CubicTo(p1x + 4, p1y + sign * 6.9,
                                     arrow_back - 8, line_y,
                                     arrow_back, line_y)]
    shapes = [cv.Path(elements, paint=_stroke(color))]
    tip = arrow_back + 9
    shapes.append(cv.Path([
        cv.Path.MoveTo(tip, line_y),
        cv.Path.LineTo(arrow_back, line_y - 5.2),
        cv.Path.LineTo(arrow_back, line_y + 5.2),
        cv.Path.Close(),
    ], paint=_fill(color)))
    return shapes


def _merge_arrow(chip_right, node_x, lane, color, big_node):
    """帯の右端 → 合流先ノードへ 60° で上がる (下がる) 矢印."""
    _, line_y = _lane_geom(lane)
    sign = 1 if lane < 0 else -1
    if big_node:                    # 現行版 (r=14 + リング) は手前で止める
        tip = (node_x - 9.8, RAIL_Y + sign * 16.8)
        end = (node_x - 14.3, RAIL_Y + sign * 24.6)
    else:                           # 通常ノード (r=9)
        tip = (node_x - 5.5, RAIL_Y + sign * 9.5)
        end = (node_x - 10, RAIL_Y + sign * 17.3)
    shapes = [cv.Path([
        cv.Path.MoveTo(chip_right, line_y),
        cv.Path.LineTo(end[0], end[1]),
    ], paint=_stroke(color))]
    shapes.append(cv.Path([
        cv.Path.MoveTo(tip[0], tip[1]),
        cv.Path.LineTo(tip[0] - 1.5, tip[1] + sign * 9.6),
        cv.Path.LineTo(tip[0] - 7.5, tip[1] + sign * 6.1),
        cv.Path.Close(),
    ], paint=_fill(color)))
    return shapes


def _wait_pill(cx, top):
    """「確認中」の琥珀色ピル (中心 x, 上端 y)."""
    return [cv.Rect(cx - 25, top, 50, 16, border_radius=8,
                    paint=_fill('#fef3c7')),
            _text(cx, top + 2, '確認中', 10.5, '#92400e',
                  weight=ft.FontWeight.BOLD, center=True)]


def _chip(c, chip_left, chip_right, color, today_x):
    """更新の帯 + 文字。戻り値: (shapes, overlay の当たり判定範囲).

    説明は帯の中に入れて省略記号で切る (帯の外に説明を書くと線と
    重なって図が読めなくなるため置かない。全文は一覧とクリック先へ)。
    幅が足りなければ「名前 #番号」だけ、それも入らなければラベルを外へ。
    """
    lane = c['lane']
    top, line_y = _lane_geom(lane)
    w = chip_right - chip_left
    dash = [5, 4] if c['pending'] else None
    label = '%s #%s' % (c['author'], c['number'])
    label_w = _est_w(label)
    fits_desc = w >= label_w + 92       # 説明の先頭が意味を持って入る幅
    fits_label = label_w + 14 <= w
    # 文字が入らない帯は塗りを濃くして空箱に見えないようにする
    alpha = 0.08 if fits_label else 0.20
    shapes = [cv.Rect(chip_left, top, w, CHIP_H, border_radius=8,
                      paint=_fill(ft.Colors.with_opacity(alpha, color))),
              cv.Rect(chip_left, top, w, CHIP_H, border_radius=8,
                      paint=_stroke(color, 1.5, dash=dash))]
    ty = top + 8
    hit = [chip_left, top, w, CHIP_H]
    pill_inside = c['pending'] and w >= label_w + 170
    if fits_desc:
        pad = 70 if pill_inside else 22
        shapes.append(_text(
            chip_left + 12, ty, None, 11.5, color, max_w=w - pad,
            spans=[ft.TextSpan(label + ' ', ft.TextStyle(
                size=11.5, color=color, weight=ft.FontWeight.BOLD,
                font_family=_font_family)),
                   ft.TextSpan(c['title'], ft.TextStyle(
                       size=11.5, color=color,
                       font_family=_font_family))]))
    elif fits_label:
        shapes.append(_text((chip_left + chip_right) / 2, ty, label,
                            11.5, color, weight=ft.FontWeight.BOLD,
                            center=True))
    elif lane in (-1, 1):
        # 幅が狭い帯: ラベルを外に出す (1 段目は帯の上、深いレーンは下)
        shapes.append(_text(chip_right, top - 18, label, 11.5, color,
                            weight=ft.FontWeight.BOLD, end=True))
        hit = [chip_left - 60, top - 18, w + 62, CHIP_H + 18]
    else:
        shapes.append(_text(chip_right, top + CHIP_H + 5, label, 11.5,
                            color, weight=ft.FontWeight.BOLD, end=True))
        hit = [chip_left - 60, top, w + 62, CHIP_H + 22]
    if c['pending']:
        if pill_inside:
            shapes += _wait_pill(chip_right - 40, top + 7)
        elif lane < 0:
            y = top + CHIP_H + (5 if fits_label else 22)
            shapes += _wait_pill(chip_right - 25, y)
        elif fits_label:
            # 上レーンの帯の下は現行版バッジの列と重なるため、ピルは
            # 帯の上 (きょうラベルより左) に置く
            shapes += _wait_pill(chip_right - 60, top - 21)
        # 上レーンの幅が狭い帯はピルを置かない (点線の帯 = 確認中の
        # 文法と一覧・ツールチップで伝わる。ラベルと重なるため)
    return shapes, hit


def build_figure(tl, current_tag, today, on_item_click, viewport_w=552,
                 font_family=None):
    """図全体 (レーン見出し + 横スクロール + ◀▶) を組み立てる.

    戻り値: dict(control, scroll_row, initial_offset)。
    正式版が 1 つも無ければ None (呼び出し側で文言表示に切り替える)。
    on_item_click(kind, payload): kind = 'stable'|'chip'。
    """
    global _font_family
    _font_family = font_family
    stables = tl['stables']
    if not stables:
        return None
    chips = tl['chips']
    authors = tl['authors']
    t0 = stables[0]['date']

    def X(d):
        return X0 + (d - t0).days * PX_PER_DAY

    # きょうの線は正直に「きょう」の位置に置く (最新版の公開日 = きょう
    # なら現行版の丸の真上を通る)。時計ずれで過去に行くのだけ防ぐ
    today_x = max(X(today), X(stables[-1]['date']))
    width = today_x + RIGHT_PAD
    node_x = {s['tag']: X(s['date']) for s in stables}
    upper_bases = {c['base_tag'] for c in chips if c['lane'] == 1}
    depth = max([-c['lane'] for c in chips if c['lane'] < 0] or [1])
    # 2 段目以降はラベル外出しの分も含めて下へ広げる
    fig_h = FIG_H + (depth - 1) * (LANE_STEP + 17)
    grid_bottom = fig_h - 22

    shapes = []
    overlays = []

    # 週ごとのグリッドと日付 (初回配布の日から 7 日刻み)
    d = t0
    while X(d) <= width - 40:
        x = X(d)
        shapes.append(cv.Line(x, AXIS_Y, x, grid_bottom,
                              paint=_stroke(GRID, 1)))
        shapes.append(_text(x, 21, history.fmt_date(d, with_year=True),
                            10.5, AXIS, center=True))
        d += datetime.timedelta(days=7)
    shapes.append(cv.Line(X0, AXIS_Y, width - 10, AXIS_Y,
                          paint=_stroke('#e5e7eb', 1)))

    # きょう線 (文字を貫通しないよう、線はラベルの下から始める)
    shapes.append(cv.Line(today_x, 62, today_x, grid_bottom,
                          paint=_stroke('#64748b', 1.5, dash=[5, 4])))
    shapes.append(_text(today_x, 47, 'きょう %s' % history.fmt_date(today),
                        11, AXIS, weight=ft.FontWeight.BOLD, center=True))

    # 本線 (正式版の列)
    shapes.append(cv.Line(X0, RAIL_Y, today_x, RAIL_Y,
                          paint=_stroke(RAIL, 3.5)))

    # 帯と線 (ノードより先に描く)
    for c in chips:
        color = history.person_color(c['author'], authors)
        base_x = node_x.get(c['base_tag'])
        if base_x is None:
            continue
        if c['pending']:
            chip_right = today_x - 3
        else:
            chip_right = node_x[c['target_tag']] - _merge_dx(c['lane'])
        chip_left = min(X(c['start']), chip_right - 24)
        shapes += _derivation(base_x, chip_left - 9, c['lane'], color)
        if not c['pending']:
            big = c['target_tag'] == current_tag
            shapes += _merge_arrow(chip_right, node_x[c['target_tag']],
                                   c['lane'], color, big)
        chip_shapes, hit = _chip(c, chip_left, chip_right, color, today_x)
        shapes += chip_shapes
        overlays.append((hit, 'chip', c))

    # 駅ノード (最後に描いて線の上に載せる)
    for s in stables:
        x = node_x[s['tag']]
        if s['tag'] == current_tag:
            shapes.append(cv.Circle(x, RAIL_Y, 14, paint=_fill(NAVY)))
            shapes.append(cv.Circle(x, RAIL_Y, 14,
                                    paint=_stroke('#f59e0b', 6)))
            shapes.append(cv.Rect(x - 48, 108, 96, 24, border_radius=7,
                                  paint=_fill('#fef08a')))
            shapes.append(cv.Rect(x - 48, 108, 96, 24, border_radius=7,
                                  paint=_stroke('#a16207', 1)))
            shapes.append(_text(x, 112, '%s 現行版' % s['tag'], 12.5,
                                '#713f12', weight=ft.FontWeight.BOLD,
                                center=True))
            overlays.append(([x - 48, 108, 96, 60], 'stable', s))
        else:
            shapes.append(cv.Circle(x, RAIL_Y, 9, paint=_fill('#ffffff')))
            shapes.append(cv.Circle(x, RAIL_Y, 9, paint=_stroke(NAVY, 3)))
            label = s['tag'] + (' 初回配布' if s is stables[0] and
                                not s['pr'] else '')
            if s['tag'] in upper_bases:
                shapes.append(_text(x - 8, 119, label, 12.5, NAVY,
                                    weight=ft.FontWeight.BOLD, end=True))
            else:
                shapes.append(_text(x, 119, label, 12.5, NAVY,
                                    weight=ft.FontWeight.BOLD,
                                    center=True))
            overlays.append(([x - 26, 119, 52, 45], 'stable', s))

    chip_by_target = {c['target_tag']: c for c in chips
                      if not c['pending']}
    canvas = cv.Canvas(shapes=shapes, width=width, height=fig_h)
    stack_children = [ft.Container(canvas, left=0, top=0)]
    for hit, kind, payload in overlays:
        stack_children.append(_overlay(hit, kind, payload, on_item_click,
                                       chip_by_target))
    inner = ft.Stack(stack_children, width=width, height=fig_h)

    scroll_row = ft.Row([inner], scroll=ft.ScrollMode.ALWAYS, spacing=0)
    cur_x = node_x.get(current_tag, today_x)
    initial_offset = max(0, cur_x - viewport_w / 2)

    left_btn = _nav_btn(ft.Icons.CHEVRON_LEFT)
    right_btn = _nav_btn(ft.Icons.CHEVRON_RIGHT)
    max_offset = max(0, width - viewport_w)

    def set_nav(btn, disabled):
        # 端に達したらグレーアウト (disabled だけでは色が変わらない)
        btn.disabled = disabled
        btn.icon_color = '#cbd5e1' if disabled else '#475569'

    set_nav(left_btn, initial_offset <= 1)
    set_nav(right_btn, initial_offset >= max_offset - 1)

    def scroll_by(delta):
        async def go(_):
            pos = getattr(scroll_row, '_hist_pos', initial_offset)
            pos = max(0.0, pos + delta)
            await scroll_row.scroll_to(offset=pos, duration=250)
        return go

    def on_scroll(e):
        scroll_row._hist_pos = e.pixels
        at_left = e.pixels <= 1
        at_right = e.pixels >= (e.max_scroll_extent or 0) - 1
        if (left_btn.disabled, right_btn.disabled) != (at_left, at_right):
            set_nav(left_btn, at_left)
            set_nav(right_btn, at_right)
            left_btn.update()
            right_btn.update()

    left_btn.on_click = scroll_by(-viewport_w * 0.7)
    right_btn.on_click = scroll_by(viewport_w * 0.7)
    scroll_row.on_scroll = on_scroll

    def fade(left):
        # 端の見切れ文字を柔らげる白フェード (クリックは透過させる)
        grad = ft.LinearGradient(
            begin=ft.Alignment(-1 if left else 1, 0),
            end=ft.Alignment(1 if left else -1, 0),
            colors=[ft.Colors.with_opacity(1.0, '#ffffff'),
                    ft.Colors.with_opacity(0.0, '#ffffff')])
        return ft.TransparentPointer(
            ft.Container(width=26, gradient=grad),
            left=0 if left else None, right=None if left else 0,
            top=0, bottom=12)

    viewwrap = ft.Stack([
        ft.Container(scroll_row, left=0, top=0, right=0, bottom=0),
        fade(True), fade(False),
        ft.Container(left_btn, left=4, top=104),
        ft.Container(right_btn, right=4, top=104),
    ], expand=True, height=fig_h + 12)

    lane_head = ft.Container(
        width=86, bgcolor='#fbfcfd',
        border=ft.Border(right=ft.BorderSide(1, '#e5e7eb')),
        content=ft.Stack([
            _head_text('時期', 20, False),
            _head_text('提出された\n更新', 62, False),
            _head_text('正式版', 141, True),
            _head_text('提出された\n更新', 196, False),
        ], height=fig_h + 12))

    control = ft.Container(
        border=ft.Border.all(1, '#e5e7eb'), border_radius=8,
        bgcolor='#ffffff', clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        content=ft.Row([lane_head, viewwrap], spacing=0,
                       vertical_alignment=ft.CrossAxisAlignment.START))
    return {'control': control, 'scroll_row': scroll_row,
            'initial_offset': initial_offset}


def _head_text(value, top, strong):
    return ft.Container(
        right=8, top=top,
        content=ft.Text(value, size=11.5, text_align=ft.TextAlign.RIGHT,
                        weight=ft.FontWeight.BOLD if strong else None,
                        color='#475569' if strong else '#64748b'))


def _nav_btn(icon):
    return ft.IconButton(
        icon, icon_size=18, icon_color='#475569',
        bgcolor=ft.Colors.with_opacity(0.92, '#ffffff'),
        style=ft.ButtonStyle(shape=ft.CircleBorder(),
                             side=ft.BorderSide(1, '#cbd5e1')))


def _overlay(hit, kind, payload, on_item_click, chip_by_target):
    x, y, w, h = hit

    def click(_):
        on_item_click(kind, payload)

    return ft.Container(
        left=x, top=y, width=w, height=h,
        bgcolor=ft.Colors.with_opacity(0.003, '#ffffff'),
        tooltip=_tooltip_text(kind, payload, chip_by_target),
        on_click=click)


def _tooltip_text(kind, payload, chip_by_target):
    """帯・ノードのマウスオーバー文。帯に書いてある情報は繰り返さない."""
    if kind == 'stable':
        s = payload
        chip = chip_by_target.get(s['tag'])
        if chip:
            return ('%s を基に作成 → %s に %s として公開\n'
                    'クリックで更新内容'
                    % (chip['base_tag'] or '前の版',
                       history.fmt_date(s['date']), s['tag']))
        return ('%s に配布\nクリックで更新内容'
                % history.fmt_date(s['date']))
    c = payload
    if c['pending']:
        return ('%s を基に作成 · %s 提出 · 確認と承認の途中\n'
                'クリックで詳細' % (c['base_tag'] or '?',
                                    history.fmt_date(c['start'])))
    return ('%s を基に作成 → %s に %s として公開\nクリックで更新内容'
            % (c['base_tag'] or '?', history.fmt_date(c['end']),
               c['target_tag']))
