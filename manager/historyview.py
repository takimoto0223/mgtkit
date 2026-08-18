# -*- coding: utf-8 -*-
"""過去の更新ログの時系列図 (Flet canvas 描画).

モデルは manager/history.py。見た目の原本は
manager/docs/mockups/history_flow.html (座標・角度・色はモックに従う):
- 横軸 = 時間 (42px/日)。初回配布より左には何も描かない。ただし帯の
  「名前 #番号」が箱に収まらない間隔になるところは、収まるまで右へ
  広げる (等縮尺よりも読めることを優先する = 管理者指示)
- グレーの本線 = 正式版の列。白丸 = 版、大きい紺丸 + 橙リング = 現行版
- 人ごとの色の帯 = 提出された更新 (左端 = 提出日)。点線 = 確認中
- 本線から S カーブで降りる線 + 矢先 = その版から派生
- 帯の右端から水平に出て本線へ滑らかに合流する矢印 = 正式版として公開
- 本線は枝より太い (主従の強弱)
- 文字が入らない幅の帯はラベルを外に出し、塗りを少し濃くする
"""
import datetime
import math

import flet as ft
import flet.canvas as cv

from . import history

NAVY = '#2b4a6f'
RAIL = '#94a3b8'
GRID = '#eef2f7'
AXIS = '#475569'

PX_PER_DAY = 42
X0 = 120                # 初回配布ノードの x (左端でラベルが切れない余白)
AXIS_Y = 40
RAIL_Y = 180
CHIP_H = 30
LANE_STEP = 57          # 2 段目以降の下レーンの間隔
FIG_H = 330             # 下レーンが 1 段のときの図の高さ
RIGHT_PAD = 114
CHIP_PAD = 24           # 帯の中で「名前 #番号」の左右に取る余白の合計
DERIV_LEAD = 55         # 基点ノード → 帯の左端 (S カーブ + 矢先の分)
MERGE_LEAD = 46         # 帯の右端 → 合流ノード (合流カーブの分)
CHIP_SLACK = 12         # 帯の左右に残す最低限の水平線 (詰まって見えない)
MIN_NODE_GAP = 68       # 隣り合う正式版ノードの最小間隔 (版名が並ぶ幅)
DEPART_DX = 7           # 分岐がノードを離れる x (中心から右へ)
DEPART_STEP = 9         # 同じノードから複数分岐するときのずらし幅
ARRIVE_DX = 7           # 合流がノードに刺さる x (中心から左へ)
BADGE_W, BADGE_H = 96, 24   # 現行版バッジの大きさ
TODAY_GAP = 16          # 帯・バッジ と きょう線の間に必ず取る間隔


def _lane_geom(lane):
    """レーン番号 → (帯の上端 y, 線の y)。-1=下 1 段目, 1=上, -2=下 2 段目.

    上レーンはモックより少し下げてある。「確認中」のピルを帯の外側 (上) に
    置くため、日付の軸線との間にピル 1 個分の余地が要るため。
    """
    if lane >= 1:
        top = 72
    else:
        top = 235 + (-lane - 1) * LANE_STEP
    return top, top + CHIP_H / 2


# 半角文字 1 字ぶんの幅の見積もり (太字の実測より少し広めに取る。
# 見積もりが足りないと文字が箱からはみ出すため、外す側を安全側に)
ASCII_EM = 0.6


def _est_w(text, size=11.5):
    """canvas に測定 API が無いための概算幅 (和文=全角、他=半角)."""
    w = 0.0
    for ch in text or '':
        w += size if ord(ch) > 0x2500 else size * ASCII_EM
    return w


def _chip_label(c):
    """帯に入れる文字 (帯の幅はこれが収まることを保証する)."""
    return '%s #%s' % (c['author'], c['number'])


def _chip_min_w(c):
    """帯の幅。「名前 #番号」+ 左右の余白 (どの帯も同じ作り)."""
    return _est_w(_chip_label(c)) + CHIP_PAD


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


def _arrow_head(tipx, tipy, ang, color):
    """向き ang (ラジアン・進行方向) の塗りつぶし矢先。先端 = (tipx, tipy)."""
    ux, uy = math.cos(ang), math.sin(ang)
    px, py = -uy, ux
    bx, by = tipx - 9 * ux, tipy - 9 * uy
    return cv.Path([
        cv.Path.MoveTo(tipx, tipy),
        cv.Path.LineTo(bx + 5.2 * px, by + 5.2 * py),
        cv.Path.LineTo(bx - 5.2 * px, by - 5.2 * py),
        cv.Path.Close(),
    ], paint=_fill(color))


def _derivation(base_x, arrow_back, lane, color):
    """基点ノード → 帯へ降りる (上がる) 滑らかな 1/4 円弧 + 矢先.

    ノードを縦に出て、水平になってから帯に刺さる (路線図の分岐と
    同じ形)。基点と帯が近くても遠くても同じ形になり、急角度の
    直線に見えない。
    """
    _, line_y = _lane_geom(lane)
    dy = line_y - RAIL_Y
    dx = min(46, max(24, arrow_back - base_x))
    elements = [cv.Path.MoveTo(base_x, RAIL_Y),
                cv.Path.CubicTo(base_x, RAIL_Y + dy * 0.55,
                                base_x + dx * 0.45, line_y,
                                base_x + dx, line_y)]
    if arrow_back > base_x + dx:
        elements.append(cv.Path.LineTo(arrow_back, line_y))
    shapes = [cv.Path(elements, paint=_stroke(color, 2.4))]
    shapes.append(_arrow_head(arrow_back + 9, line_y, 0, color))
    return shapes


def _merge_arrow(chip_right, node_x, lane, color, big_node):
    """帯の右端 → 水平に出て 1/4 円弧でノードに縦に刺さる矢印.

    派生 (_derivation) の鏡映で、左右のカーブの滑らかさをそろえる
    (管理者指示)。矢先はノードの縁に縦向きで刺さる。刺す位置はノードの
    中心より少し左にする (分岐は右へ出るので、同じ版に出入りがあっても
    線が重ならない)。
    """
    _, line_y = _lane_geom(lane)
    sign = 1 if lane < 0 else -1    # +1 = 本線より下
    r = 14 if big_node else 9
    ax = node_x - ARRIVE_DX                 # 刺さる x
    edge = math.sqrt(max(r * r - ARRIVE_DX ** 2, 0.0))  # その x での縁
    tip_y = RAIL_Y + sign * (edge + 1)      # 矢先の先端 = ノードの縁
    back_y = RAIL_Y + sign * (edge + 10)    # 矢先の根元 = 曲線の終点
    dy = line_y - back_y
    dx = min(46, max(18, ax - chip_right))
    elements = [cv.Path.MoveTo(chip_right, line_y)]
    if ax - dx > chip_right:
        elements.append(cv.Path.LineTo(ax - dx, line_y))
    elements.append(cv.Path.CubicTo(ax - dx * 0.45, line_y,
                                    ax, back_y + dy * 0.55, ax, back_y))
    shapes = [cv.Path(elements, paint=_stroke(color, 2.4))]
    shapes.append(_arrow_head(ax, tip_y, math.radians(-90 * sign), color))
    return shapes


PILL_W, PILL_H = 50, 16     # 「確認中」ピルの大きさ
PILL_GAP = 8                # ピルと帯のすき間


def _wait_pill(cx, top):
    """「確認中」の琥珀色ピル (中心 x, 上端 y)."""
    return [cv.Rect(cx - PILL_W / 2, top, PILL_W, PILL_H, border_radius=8,
                    paint=_fill('#fef3c7')),
            _text(cx, top + 2, '確認中', 10.5, '#92400e',
                  weight=ft.FontWeight.BOLD, center=True)]


def _chip(c, chip_left, chip_right, color, today_x):
    """更新の帯 + 文字。戻り値: (shapes, overlay の当たり判定範囲).

    帯には「名前 #番号」だけを入れる (内容の説明は帯に書かない =
    管理者指示。内容はツールチップ・一覧・クリック先の更新内容で読む)。
    ラベルが入らない幅ならラベルを帯の外へ。
    """
    lane = c['lane']
    top, line_y = _lane_geom(lane)
    w = chip_right - chip_left
    dash = [5, 4] if c['pending'] else None
    label = _chip_label(c)
    label_w = _est_w(label)
    fits_label = label_w + 14 <= w
    # 文字が入らない帯は塗りを濃くして空箱に見えないようにする
    alpha = 0.08 if fits_label else 0.20
    shapes = [cv.Rect(chip_left, top, w, CHIP_H, border_radius=8,
                      paint=_fill(ft.Colors.with_opacity(alpha, color))),
              cv.Rect(chip_left, top, w, CHIP_H, border_radius=8,
                      paint=_stroke(color, 1.5, dash=dash))]
    ty = top + 8
    hit = [chip_left, top, w, CHIP_H]
    if fits_label:
        # 幅は概算 (canvas に測定 API が無い) なので、実際の文字が
        # 見積もりより広くても箱から出ないよう max_w で止める
        shapes.append(_text((chip_left + chip_right) / 2, ty, label,
                            11.5, color, weight=ft.FontWeight.BOLD,
                            center=True, max_w=w - 10))
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
        # 「確認中」は帯の外側 (本線から遠い側) に、帯の左端をそろえて
        # 置く。図によって位置が動くと、どの帯の状態なのか読み取れない。
        # 左そろえなのは、右はきょうのラベルと近すぎるため
        if lane >= 1:
            py = top - PILL_GAP - PILL_H
            hit = [chip_left, py, max(w, PILL_W), CHIP_H + PILL_GAP
                   + PILL_H]
        else:
            py = top + CHIP_H + PILL_GAP
            hit = [chip_left, top, max(w, PILL_W), CHIP_H + PILL_GAP
                   + PILL_H]
        shapes += _wait_pill(chip_left + PILL_W / 2, py)
    return shapes, hit


def _node_positions(stables, chips):
    """正式版ノードの x 座標 {tag: x}.

    基本は日付に比例 (PX_PER_DAY)。ただし正式版の間隔が短いと、その間に
    入る帯が「名前 #番号」を書けない幅になり、文字が箱の外へ出てしまう。
    そこで帯が収まらない区間だけ右へ広げる (以降のノードも同じ分だけ
    ずらすので、他の区間の日数の比は保たれる)。きょう線の手前を可変に
    しているのと同じ考え方 = 図は等縮尺よりも読めることを優先する。
    """
    t0 = stables[0]['date']

    def ideal(s):
        return X0 + (s['date'] - t0).days * PX_PER_DAY

    # (基点タグ, 公開タグ) → その区間に必要な最小の幅
    need = {}
    for c in chips:
        if c['pending'] or not c['base_tag'] or not c['target_tag']:
            continue
        w = (DERIV_LEAD + _chip_min_w(c) + CHIP_SLACK + MERGE_LEAD)
        key = (c['base_tag'], c['target_tag'])
        need[key] = max(need.get(key, 0), w)

    node_x = {}
    shift = 0.0
    prev = None
    for s in stables:
        want = ideal(s) + shift
        if prev is not None:
            want = max(want, node_x[prev] + MIN_NODE_GAP)
        for (base, target), w in need.items():
            if target == s['tag'] and base in node_x:
                want = max(want, node_x[base] + w)
        shift = want - ideal(s)
        node_x[s['tag']] = want
        prev = s['tag']
    return node_x


def _date_scale(stables, node_x):
    """日付 → x の関数。正式版ノードを必ず通る折れ線.

    ノードを右へ広げた区間では日付の目盛りも同じだけ伸びるので、
    週のグリッド線とノードの前後関係が食い違わない。
    """
    pts = [(s['date'], node_x[s['tag']]) for s in stables]

    def X(d):
        if d <= pts[0][0]:
            return pts[0][1] - (pts[0][0] - d).days * PX_PER_DAY
        for (d0, x0), (d1, x1) in zip(pts, pts[1:]):
            if d <= d1:
                span = (d1 - d0).days
                return x1 if span <= 0 else \
                    x0 + (x1 - x0) * ((d - d0).days / span)
        return pts[-1][1] + (d - pts[-1][0]).days * PX_PER_DAY

    return X


def _chip_span(c, base_x, node_x, today_x):
    """帯の左右の x。帯はどれも「名前 #番号 + 同じ余白」の同じ作りの箱で、
    期間は枝の線の長さが表す (モックの文法)."""
    min_w = _chip_min_w(c)
    if c['pending']:
        # 確認中はきょう線側に寄せる (点線がきょうまで続く文法)
        chip_right = today_x - TODAY_GAP
        chip_left = max(chip_right - min_w, base_x + 30)
        return chip_left, max(chip_right, chip_left + 12)
    # 取り込み済みは基点ノードと合流ノードの中央に置き、両側の水平部分が
    # 同じ長さになるようにする (_node_positions が幅を確保している)
    span_l = base_x + DERIV_LEAD             # S カーブ + 矢先の分
    span_r = node_x[c['target_tag']] - MERGE_LEAD   # 合流カーブの分
    if span_r - span_l >= min_w:
        chip_left = span_l + (span_r - span_l - min_w) / 2
        return chip_left, chip_left + min_w
    return span_l, max(span_r, span_l + 12)


def _depart_slots(chips):
    """同じノードから出る枝を何本目として描くか {提出番号: 0,1,2...}.

    同じ点から複数の枝が出ると、1 本の線がノードを素通りしているように
    見えてしまう (同じ人の色だと特に)。路線図と同じで、分岐の位置を
    少しずつずらして「ここで分かれた」を見せる。
    """
    by_base = {}
    for c in chips:
        if c['base_tag']:
            by_base.setdefault(c['base_tag'], []).append(c)
    slots = {}
    for sibs in by_base.values():
        # 本線に近いレーンから順に、内側の出発位置を使う
        for i, c in enumerate(sorted(sibs,
                                     key=lambda c: (abs(c['lane']),
                                                    c['lane']))):
            slots[c['number']] = i
    return slots


def _badge_rect(side, cur_x):
    """現行版バッジの矩形 (x, y, w, h)."""
    if side == 'top':
        return (cur_x - BADGE_W / 2, RAIL_Y - 72, BADGE_W, BADGE_H)
    if side == 'bottom':
        return (cur_x - BADGE_W / 2, RAIL_Y + 48, BADGE_W, BADGE_H)
    return (cur_x + 22, RAIL_Y - BADGE_H / 2, BADGE_W, BADGE_H)


def _chip_rect(c, span):
    """帯の占める矩形 (確認中のピルの場所も含む)."""
    top, _ = _lane_geom(c['lane'])
    y0, y1 = top, top + CHIP_H
    if c['pending']:
        if c['lane'] >= 1:
            y0 -= PILL_GAP + PILL_H
        else:
            y1 += PILL_GAP + PILL_H
    return (span[0], y0, max(span[1] - span[0], PILL_W), y1 - y0)


def _overlaps(a, b, pad=0):
    return (a[0] - pad < b[0] + b[2] and b[0] < a[0] + a[2] + pad
            and a[1] - pad < b[1] + b[3] and b[1] < a[1] + a[3] + pad)


def _badge_side(current_tag, node_x, chips, spans):
    """現行版バッジの置き場所 'right' | 'top' | 'bottom'.

    既定は本線の右 (駅名標と同じ形で、図が変わっても位置が動かない)。
    右に別の版のノードが来てしまうときだけ、帯の空いている側へ逃がす。
    """
    cur_x = node_x.get(current_tag)
    if cur_x is None:
        return 'right'
    if not any(cur_x < x < cur_x + BADGE_W + 30
               for tag, x in node_x.items() if tag != current_tag):
        return 'right'
    for side in ('top', 'bottom'):
        rect = _badge_rect(side, cur_x)
        if not any(_overlaps(rect, _chip_rect(c, spans[c['number']]), 6)
                   for c in chips if c['number'] in spans):
            return side
    return 'top'


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
    node_x = _node_positions(stables, chips)
    X = _date_scale(stables, node_x)

    # きょうの線: 基本は「きょう」の実位置。ただし確認中の帯に
    # 「名前 #番号」が入るだけの幅と、最新ノード・現行版バッジとの
    # 間隔が足りなければ右へ広げる (可変でよい = 管理者指示)
    today_x = max(X(today), X(stables[-1]['date']),
                  node_x[stables[-1]['tag']] + 24)
    for c in chips:
        if not c['pending']:
            continue
        base_x = node_x.get(c['base_tag'])
        left = max(X(c['start']),
                   (base_x + 30) if base_x is not None else X0)
        today_x = max(today_x, left + _chip_min_w(c) + TODAY_GAP)

    def _spans(tx):
        return {c['number']: _chip_span(c, node_x[c['base_tag']],
                                        node_x, tx)
                for c in chips if node_x.get(c['base_tag']) is not None}

    # バッジの置き場所は帯の実際の位置で決める (レーンだけで決めると、
    # 現行版と関係のない帯の上に重ねてしまう)
    badge_side = _badge_side(current_tag, node_x, chips, _spans(today_x))
    if current_tag in node_x:
        bx, _by, bw, _bh = _badge_rect(badge_side, node_x[current_tag])
        today_x = max(today_x, bx + bw + TODAY_GAP)
    spans = _spans(today_x)     # きょう線が動いた分、確認中の帯も動く
    depart_slots = _depart_slots(chips)
    width = today_x + RIGHT_PAD
    # 上レーンの枝が付くノード (出ていく基点 + 入ってくる合流先)。
    # ラベルを真上に置くと枝の線や矢先と重なるため、左へ逃がす目印
    upper_bases = {t for c in chips if c['lane'] >= 1
                   for t in (c['base_tag'], c['target_tag']) if t}
    depth = max([-c['lane'] for c in chips if c['lane'] < 0] or [1])
    # 2 段目以降はラベル外出しの分も含めて下へ広げる
    fig_h = FIG_H + (depth - 1) * (LANE_STEP + 17)
    grid_bottom = fig_h - 22

    shapes = []
    overlays = []

    # 週ごとのグリッドと日付 (初回配布の日から 7 日刻み)。きょう線の
    # 手前のマージンは可変 (等縮尺でない) ためグリッドは最新版まで
    d = t0
    while d <= stables[-1]['date'] and X(d) <= width - 40:
        x = X(d)
        shapes.append(cv.Line(x, AXIS_Y, x, grid_bottom,
                              paint=_stroke(GRID, 1)))
        shapes.append(_text(x, 21, history.fmt_date(d, with_year=True),
                            10.5, AXIS, center=True))
        d += datetime.timedelta(days=7)
    shapes.append(cv.Line(X0, AXIS_Y, min(width - 10, today_x), AXIS_Y,
                          paint=_stroke('#e5e7eb', 1)))

    # きょう線は波線にする (確認中の帯の破線と見分けるため。
    # 文字を貫通しないよう、線はラベルの下から始める)
    wave = [cv.Path.MoveTo(today_x, 62)]
    wy, amp, step, side = 62, 2.5, 8, 1
    while wy < grid_bottom:
        wave.append(cv.Path.QuadraticTo(today_x + side * amp * 2,
                                        wy + step / 2, today_x,
                                        wy + step))
        wy += step
        side = -side
    shapes.append(cv.Path(wave, paint=_stroke('#64748b', 1.5)))
    shapes.append(_text(today_x, 47, 'きょう %s' % history.fmt_date(today),
                        11, AXIS, weight=ft.FontWeight.BOLD, center=True))

    # 本線 (正式版の列)。枝より太くして主従を付ける
    shapes.append(cv.Line(X0, RAIL_Y, today_x, RAIL_Y,
                          paint=_stroke(RAIL, 5.5)))

    # 帯と線 (ノードより先に描く)
    for c in chips:
        color = history.person_color(c['author'], authors)
        base_x = node_x.get(c['base_tag'])
        if base_x is None:
            continue
        chip_left, chip_right = spans[c['number']]
        # 同じノードから出る枝は出発位置を少しずつ右へずらす
        depart = base_x + DEPART_DX + DEPART_STEP * depart_slots.get(
            c['number'], 0)
        shapes += _derivation(depart, chip_left - 9, c['lane'], color)
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
            # バッジは枝と重ならない側に置く: 上が空いていれば上、
            # 次に下、両方ふさがっていればノードの右 (本線の上)
            bx0, by0, bw, bh = _badge_rect(badge_side, x)
            shapes.append(cv.Rect(bx0, by0, bw, bh, border_radius=7,
                                  paint=_fill('#fef08a')))
            shapes.append(cv.Rect(bx0, by0, bw, bh, border_radius=7,
                                  paint=_stroke('#a16207', 1)))
            shapes.append(_text(bx0 + bw / 2, by0 + 4,
                                '%s 現行版' % s['tag'], 12.5,
                                '#713f12', weight=ft.FontWeight.BOLD,
                                center=True))
            overlays.append(([min(bx0, x - 16), min(by0, RAIL_Y - 16),
                              max(bx0 + bw, x + 16) - min(bx0, x - 16),
                              max(by0 + bh, RAIL_Y + 16)
                              - min(by0, RAIL_Y - 16)], 'stable', s))
        else:
            shapes.append(cv.Circle(x, RAIL_Y, 9, paint=_fill('#ffffff')))
            shapes.append(cv.Circle(x, RAIL_Y, 9, paint=_stroke(NAVY, 3)))
            label = s['tag'] + (' 初回配布' if s is stables[0] and
                                not s['pr'] else '')
            ly = RAIL_Y - 31
            lw = _est_w(label, 12.5)
            # 上へ枝が出るノードはラベルを左へ逃がす。ただし図の左端で
            # 切れるとき (初回配布など) は右側に置く。中央配置も左端で
            # 切れるなら右側へ (どの端でも切れない・被らないように)
            # 上に枝が付くノードは、合流の縦線 (ノード中心の ARRIVE_DX 左)
            # より左までラベルを逃がす
            dodge = ARRIVE_DX + 10
            if s['tag'] in upper_bases and x - dodge - lw >= 4:
                shapes.append(_text(x - dodge, ly, label, 12.5, NAVY,
                                    weight=ft.FontWeight.BOLD, end=True))
            elif s['tag'] not in upper_bases and x - lw / 2 >= 4:
                shapes.append(_text(x, ly, label, 12.5, NAVY,
                                    weight=ft.FontWeight.BOLD,
                                    center=True))
            else:
                shapes.append(_text(x + 12, ly, label, 12.5, NAVY,
                                    weight=ft.FontWeight.BOLD))
            overlays.append(([x - 26, ly, 52, 45], 'stable', s))

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
        # それ以上動けない端では移動マーク自体を消す (管理者指示)
        btn.disabled = disabled
        btn.visible = not disabled
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
            ft.Container(width=34, gradient=grad),
            left=0 if left else None, right=None if left else 0,
            top=0, bottom=12)

    viewwrap = ft.Stack([
        ft.Container(scroll_row, left=0, top=0, right=0, bottom=0),
        fade(True), fade(False),
        ft.Container(left_btn, left=4, top=RAIL_Y - 26),
        ft.Container(right_btn, right=4, top=RAIL_Y - 26),
    ], expand=True, height=fig_h + 12)

    control = ft.Container(
        border=ft.Border.all(1, '#e5e7eb'), border_radius=8,
        bgcolor='#ffffff', clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        content=viewwrap)
    return {'control': control, 'scroll_row': scroll_row,
            'initial_offset': initial_offset, 'height': fig_h + 12}


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
                    % (history.base_label(chip),
                       history.fmt_date(s['date']), s['tag']))
        return ('%s に配布\nクリックで更新内容'
                % history.fmt_date(s['date']))
    c = payload
    if c['pending']:
        return ('%s を基に作成 · %s 提出 · 確認と承認の途中\n'
                'クリックで詳細' % (history.base_label(c),
                                    history.fmt_date(c['start'])))
    return ('%s を基に作成 → %s に %s として公開\nクリックで更新内容'
            % (history.base_label(c), history.fmt_date(c['end']),
               c['target_tag']))


def build_legend():
    """図の凡例。文章ではなく図と同じ見た目のアイコンで示す.

    ラベルは最小限の言葉に留める (説明は帯のツールチップと
    クリック先の更新内容が担う)。
    """
    def glyph(w, shapes):
        return cv.Canvas(shapes=shapes, width=w, height=22)

    def item(canvas, label):
        return ft.Row([canvas,
                       ft.Text(label, size=11.5, color='#4b5563')],
                      spacing=5, tight=True)

    teal = '#0f766e'
    g_stable = glyph(20, [
        cv.Circle(10, 11, 6, paint=_fill('#ffffff')),
        cv.Circle(10, 11, 6, paint=_stroke(NAVY, 2.5))])
    g_cur = glyph(24, [
        cv.Circle(12, 11, 6.5, paint=_fill(NAVY)),
        cv.Circle(12, 11, 6.5, paint=_stroke('#f59e0b', 3.5))])
    g_deriv = glyph(34, [
        cv.Circle(5, 5, 3.5, paint=_stroke(NAVY, 2)),
        cv.Path([cv.Path.MoveTo(7, 8), cv.Path.LineTo(13, 16)],
                paint=_stroke(teal, 2)),
        cv.Path([cv.Path.MoveTo(19, 17), cv.Path.LineTo(13, 13.5),
                 cv.Path.LineTo(13, 20.5), cv.Path.Close()],
                paint=_fill(teal)),
        cv.Rect(19, 12, 13, 10, border_radius=4,
                paint=_fill(ft.Colors.with_opacity(0.15, teal))),
        cv.Rect(19, 12, 13, 10, border_radius=4,
                paint=_stroke(teal, 1.5))])
    g_merge = glyph(34, [
        cv.Rect(2, 12, 13, 10, border_radius=4,
                paint=_fill(ft.Colors.with_opacity(0.15, teal))),
        cv.Rect(2, 12, 13, 10, border_radius=4,
                paint=_stroke(teal, 1.5)),
        cv.Path([cv.Path.MoveTo(15, 16), cv.Path.LineTo(21, 8)],
                paint=_stroke(teal, 2)),
        cv.Path([cv.Path.MoveTo(24, 4), cv.Path.LineTo(17.5, 6),
                 cv.Path.LineTo(22.5, 11), cv.Path.Close()],
                paint=_fill(teal)),
        cv.Circle(28, 4, 3.5, paint=_stroke(NAVY, 2))])
    g_wait = glyph(26, [
        cv.Rect(2, 6, 22, 12, border_radius=5,
                paint=_stroke('#6d28d9', 1.5, dash=[4, 3]))])
    legend = ft.Row([
        item(g_stable, '正式版'),
        item(g_cur, '現行版'),
        item(g_deriv, 'その版を基に提出 (色 = 人)'),
        item(g_merge, '正式版として公開'),
        item(g_wait, '確認中'),
    ], spacing=16, wrap=True, run_spacing=4)
    hint = ft.Text('帯や丸を押すと更新内容が開きます '
                   '(ZIP 保存は下の一覧から)',
                   size=11, color='#4b5563')
    return ft.Column([legend, hint], spacing=4, tight=True)
