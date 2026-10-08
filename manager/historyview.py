# -*- coding: utf-8 -*-
"""過去の更新ログの時系列図 (Flet canvas 描画).

モデルは manager/history.py。見た目の原本は
manager/docs/mockups/history_flow.html (座標・角度・色はモックに従う):
- 横軸 = 時間 (42px/日)。初回配布より左には何も描かない。ただし帯の
  「名前 #番号」が箱に収まらない間隔になるところは、収まるまで右へ
  広げる (等縮尺よりも読めることを優先する = 管理者指示)
- 薄い縦線と日付は版が更新された日だけ (線は版の丸に重なる)
- グレーの本線 = 正式版の列。白丸 = 版、大きい紺丸 + 橙リング = 現行版
- 人ごとの色の帯 = 提出された更新 (左端 = 提出日)。点線 = 確認中
- 本線から S カーブで降りる線 + 矢先 = その版から派生
- 帯の右端から水平に出て本線へ滑らかに合流する矢印 = 正式版として公開
- 本線は枝より太い (主従の強弱)
- 文字が入らない幅の帯はラベルを外に出し、塗りを少し濃くする
"""
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
RAIL_Y = 180            # 上の段が 1 段のときの本線の y
UPPER_TOP = 72          # いちばん上の段の帯の上端 y
LOWER_TOP = 235         # 上の段が 1 段のときの、下 1 段目の帯の上端 y
CHIP_H = 30
# 2 段目以降の下レーンの間隔。帯の下に置く「確認中」のピル・外へ出した
# ラベルが、下の段の帯よりも自分の帯に近く見える高さを取る
LANE_STEP = 70
FIG_H = 330             # 上下とも 1 段のときの図の高さ
RIGHT_PAD = 114
CHIP_PAD = 24           # 帯の中で「名前 #番号」の左右に取る余白の合計
DERIV_RUN = 55          # 枝の出発点 → 帯の左端 (1/4 円弧 46 + 矢先 9)
MERGE_LEAD = 46         # 帯の右端 → 合流ノード (合流カーブの分)
CHIP_SLACK = 12         # 帯の左右に残す最低限の水平線 (詰まって見えない)
MIN_NODE_GAP = 68       # 隣り合う正式版ノードの最小間隔 (版名が並ぶ幅)
DEPART_DX = 7           # 分岐がノードを離れる x (中心から右へ)
DEPART_STEP = 9         # 同じノードから複数分岐するときのずらし幅
ARRIVE_DX = 7           # 合流がノードに刺さる x (中心から左へ)
BADGE_W, BADGE_H = 96, 24   # 現行版バッジの大きさ
TODAY_GAP = 16          # 帯・バッジ と きょう線の間に必ず取る間隔


def _lane_geom(lane, rail_y=RAIL_Y):
    """レーン番号 → (帯の上端 y, 線の y)。-1=下 1 段目, 1=上 1 段目,
    -2=下 2 段目, 2=上 2 段目 ...

    rail_y = 本線の y。上の段が増えると本線を下げる (_rail_y)。上の
    1 段目はモックより少し本線から離してある。「確認中」のピルを帯の
    外側 (上) に置くため、日付の軸線との間にピル 1 個分の余地が要るため。
    """
    if lane >= 1:
        top = rail_y - (RAIL_Y - UPPER_TOP) - (lane - 1) * LANE_STEP
    else:
        top = rail_y + (LOWER_TOP - RAIL_Y) + (-lane - 1) * LANE_STEP
    return top, top + CHIP_H / 2


def _rail_y(chips):
    """本線の y。上の段が 2 段以上なら、その分だけ本線を下げる.

    いちばん上の段の帯は常に UPPER_TOP に来る (日付の軸との間は一定)。
    """
    up = max([c['lane'] for c in chips if c.get('lane', 0) >= 1] or [1])
    return RAIL_Y + (up - 1) * LANE_STEP


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
    """帯の幅。「名前 #番号」+ 左右の余白 (どの帯も同じ作り).

    確認中の帯はきょう線に寄せて置き、帯の左端の上 (いちばん上の段の
    とき) に「確認中」のピルが来る。名前が短いとピルが「きょう 〜」の
    文字に届いて一続きに読めるので、ピルが文字の手前で止まる幅を取る。
    """
    w = _est_w(_chip_label(c)) + CHIP_PAD
    if c.get('pending'):
        w = max(w, PILL_W + 8 + _est_w(TODAY_LABEL_MAX, 11) / 2
                - TODAY_GAP)
    return w


TODAY_LABEL_MAX = 'きょう 12/31'   # きょう線のラベルのいちばん長い形


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


HOP_R = 4               # 線どうしの交差で横の線に入れる山の半径 (= 高さ)


def _deriv_geom(base_x, arrow_back, lane, rail_y=RAIL_Y):
    """派生線の形: (1/4 円弧の 3 次曲線の 4 点, 横の区間 (x0, x1, y) か None)."""
    _, line_y = _lane_geom(lane, rail_y)
    dy = line_y - rail_y
    # 横に取れる幅より広い円弧は描かない。描くと線が矢先を追い越して
    # 「線と矢先がつながっていない」見え方になる (帯の左端は
    # _deriv_lead ぶん空けてあるので、通常は 46 = 一定の形になる)
    dx = min(46, max(0, arrow_back - base_x))
    curve = ((base_x, rail_y), (base_x, rail_y + dy * 0.55),
             (base_x + dx * 0.45, line_y), (base_x + dx, line_y))
    run = ((base_x + dx, arrow_back, line_y)
           if arrow_back > base_x + dx else None)
    return curve, run


def _merge_geom(chip_right, node_x, lane, big_node, rail_y=RAIL_Y):
    """合流線の形: (横の区間 (x0, x1, y) か None, 3 次曲線の 4 点,
    矢先の先端 (x, y), 矢先の向き)."""
    _, line_y = _lane_geom(lane, rail_y)
    sign = 1 if lane < 0 else -1    # +1 = 本線より下
    r = 14 if big_node else 9
    ax = node_x - ARRIVE_DX                 # 刺さる x
    edge = math.sqrt(max(r * r - ARRIVE_DX ** 2, 0.0))  # その x での縁
    tip_y = rail_y + sign * (edge + 1)      # 矢先の先端 = ノードの縁
    back_y = rail_y + sign * (edge + 10)    # 矢先の根元 = 曲線の終点
    dy = line_y - back_y
    dx = min(46, max(0, ax - chip_right))
    run = (chip_right, ax - dx, line_y) if ax - dx > chip_right else None
    x0 = ax - dx if run else chip_right
    curve = ((x0, line_y), (ax - dx * 0.45, line_y),
             (ax, back_y + dy * 0.55), (ax, back_y))
    return run, curve, (ax, tip_y), math.radians(-90 * sign)


def _cubic_x_at(curve, y):
    """縦に単調な 3 次曲線が高さ y を横切る x。端で触れるだけ・通らないなら None."""
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = curve
    if not min(y0, y3) < y < max(y0, y3):
        return None

    def at(t, a, b, c, d):
        u = 1 - t
        return u * u * u * a + 3 * u * u * t * b + 3 * u * t * t * c + t * t * t * d

    lo, hi = 0.0, 1.0
    up = y3 > y0
    for _ in range(40):
        mid = (lo + hi) / 2
        if (at(mid, y0, y1, y2, y3) < y) == up:
            lo = mid
        else:
            hi = mid
    return at((lo + hi) / 2, x0, x1, x2, x3)


def _hops_on(run, curves):
    """横の区間 run を他の線の曲線が横切る所に入れる山の [(左端 x, 右端 x)].

    山は交差の x を中心に幅 2 * HOP_R。同じ版から並んで出た枝は
    縦の線どうしが HOP_R * 2 より近いので、近い交差は 1 つの広い山で
    まとめて跨ぐ (小さな山が重なって潰れないように)。山が区間の端から
    はみ出す交差には入れない (線の曲がり始め・矢先と重なるため)。
    """
    x0, x1, y = run
    xs = sorted(x for x in (_cubic_x_at(c, y) for c in curves)
                if x is not None and x0 + HOP_R <= x <= x1 - HOP_R)
    hops = []
    for x in xs:
        if hops and x - HOP_R < hops[-1][1]:
            hops[-1] = (hops[-1][0], x + HOP_R)
        else:
            hops.append((x - HOP_R, x + HOP_R))
    return [h for h in hops if h[1] <= x1]


def _run_elements(run, hops):
    """横の区間を左 → 右に描く要素。hops の位置で上へ膨らむ山を挟む.

    縦の線は切らずに真っ直ぐ通し、横の線のほうが跨ぐ (ライン ジャンプ)。
    山は半径 HOP_R の 1/4 円 2 つ (右向きに進んで時計回り = 画面の上へ
    膨らむ)。1 本を跨ぐ山は半円、まとめて跨ぐ広い山は上辺を平らにした
    橋にして、高さをどれも HOP_R にそろえる (広い半円は背が高く不揃い)。
    """
    _x0, x1, y = run
    top = y - HOP_R
    elements = []
    for left, right in hops:
        elements.append(cv.Path.LineTo(left, y))
        elements.append(cv.Path.ArcTo(left + HOP_R, top, radius=HOP_R,
                                      clockwise=True))
        if right - left > 2 * HOP_R:
            elements.append(cv.Path.LineTo(right - HOP_R, top))
        elements.append(cv.Path.ArcTo(right, y, radius=HOP_R,
                                      clockwise=True))
    elements.append(cv.Path.LineTo(x1, y))
    return elements


def _derivation(base_x, arrow_back, lane, color, hops=(),
                rail_y=RAIL_Y):
    """基点ノード → 帯へ降りる (上がる) 滑らかな 1/4 円弧 + 矢先.

    ノードを縦に出て、水平になってから帯に刺さる (路線図の分岐と
    同じ形)。基点と帯が近くても遠くても同じ形になり、急角度の
    直線に見えない。hops = 横の区間に入れる山 (_hops_on)。
    """
    curve, run = _deriv_geom(base_x, arrow_back, lane, rail_y)
    (sx, sy), p1, p2, p3 = curve
    elements = [cv.Path.MoveTo(sx, sy), cv.Path.CubicTo(*p1, *p2, *p3)]
    if run:
        elements += _run_elements(run, hops)
    _, line_y = _lane_geom(lane, rail_y)
    shapes = [cv.Path(elements, paint=_stroke(color, 2.4))]
    shapes.append(_arrow_head(arrow_back + 9, line_y, 0, color))
    return shapes


def _merge_arrow(chip_right, node_x, lane, color, big_node, hops=(),
                 rail_y=RAIL_Y):
    """帯の右端 → 水平に出て 1/4 円弧でノードに縦に刺さる矢印.

    派生 (_derivation) の鏡映で、左右のカーブの滑らかさをそろえる
    (管理者指示)。矢先はノードの縁に縦向きで刺さる。刺す位置はノードの
    中心より少し左にする (分岐は右へ出るので、同じ版に出入りがあっても
    線が重ならない)。hops = 横の区間に入れる山 (_hops_on)。
    """
    run, curve, (tx, ty), ang = _merge_geom(chip_right, node_x, lane,
                                            big_node, rail_y)
    _, line_y = _lane_geom(lane, rail_y)
    elements = [cv.Path.MoveTo(chip_right, line_y)]
    if run:
        elements += _run_elements(run, hops)
    _p0, p1, p2, p3 = curve
    elements.append(cv.Path.CubicTo(*p1, *p2, *p3))
    shapes = [cv.Path(elements, paint=_stroke(color, 2.4))]
    shapes.append(_arrow_head(tx, ty, ang, color))
    return shapes


PILL_W, PILL_H = 50, 16     # 「確認中」ピルの大きさ
PILL_GAP = 8                # ピルと帯のすき間


def _wait_pill(cx, top):
    """「確認中」の琥珀色ピル (中心 x, 上端 y)."""
    return [cv.Rect(cx - PILL_W / 2, top, PILL_W, PILL_H, border_radius=8,
                    paint=_fill('#fef3c7')),
            _text(cx, top + 2, '確認中', 10.5, '#92400e',
                  weight=ft.FontWeight.BOLD, center=True)]


def _chip(c, chip_left, chip_right, color, today_x, rail_y=RAIL_Y):
    """更新の帯 + 文字。戻り値: (shapes, overlay の当たり判定範囲).

    帯には「名前 #番号」だけを入れる (内容の説明は帯に書かない =
    管理者指示。内容はツールチップ・一覧・クリック先の更新内容で読む)。
    ラベルが入らない幅ならラベルを帯の外へ。
    """
    lane = c['lane']
    top, line_y = _lane_geom(lane, rail_y)
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
    elif lane >= -1:
        # 幅が狭い帯: ラベルを外に出す (上の段と下 1 段目は帯の上、
        # 下の深いレーンは下)
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
    depart_span = _depart_span(chips)
    need = {}
    for c in chips:
        if c['pending'] or not c['base_tag'] or not c['target_tag']:
            continue
        w = (_deriv_lead(c['base_tag'], depart_span) + _chip_min_w(c)
             + CHIP_SLACK + MERGE_LEAD)
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


def _chip_span(c, base_x, node_x, today_x, lead):
    """帯の左右の x。帯はどれも「名前 #番号 + 同じ余白」の同じ作りの箱で、
    期間は枝の線の長さが表す (モックの文法).

    lead = 基点ノード → 帯の左端に取る距離 (_deriv_lead)。派生線の
    1/4 円弧と矢先がここに収まる。
    """
    min_w = _chip_min_w(c)
    if c['pending']:
        # 確認中はきょう線側に寄せる (点線がきょうまで続く文法)
        chip_right = today_x - TODAY_GAP
        chip_left = max(chip_right - min_w, base_x + lead)
        return chip_left, max(chip_right, chip_left + 12)
    # 取り込み済みは基点ノードと合流ノードの中央に置き、両側の水平部分が
    # 同じ長さになるようにする (_node_positions が幅を確保している)
    span_l = base_x + lead                   # S カーブ + 矢先の分
    span_r = node_x[c['target_tag']] - MERGE_LEAD   # 合流カーブの分
    if span_r - span_l >= min_w:
        chip_left = span_l + (span_r - span_l - min_w) / 2
        return chip_left, chip_left + min_w
    return span_l, max(span_r, span_l + 12)


def _chip_extent(c, span, node_x):
    """帯がレーンの高さで横に占める範囲 (x0, x1).

    帯の箱だけでなく、同じ高さに描かれるもの全部を含める:
    基点ノードから帯まで走る派生線、帯から合流ノードへ走る線、箱に
    入らずに外へ出したラベル、確認中の「確認中」ピル。ここが重なると
    図の上で帯や線が重なって見えるので、この範囲でレーンを分ける。

    両端はノードの中心ではなく線が実際に離れる / 刺さる位置にする
    (前の帯の合流先 = 次の帯の基点、という連続した提出を「重なり」に
    しないため)。
    """
    left, right = span
    base_x = node_x.get(c['base_tag'])
    if base_x is not None:
        left = min(left, base_x + DEPART_DX)
    if c['pending']:
        right = max(right, span[0] + PILL_W)    # ピルが帯より広いとき
    else:
        target_x = node_x.get(c['target_tag'])
        if target_x is not None:
            right = max(right, target_x - ARRIVE_DX)
    label_w = _est_w(_chip_label(c))
    if label_w + 14 > span[1] - span[0]:
        # 箱に入らないラベルは外 (帯の右端そろえ) に出る = 左へ張り出す
        left = min(left, span[1] - label_w)
    return left, right


def _assign_lanes(chips, node_x, spans):
    """帯のレーンを実際の x 座標で決め直す (モデルの日付での割当を上書き).

    日付で分けるだけでは足りない: 確認中の帯はきょうの線に寄せて置く、
    ノードの間隔は帯が入る幅まで広げる、といった理由で「日付は重なって
    いないのに px では重なる」「その逆」が起きる。とくに同じ日に出された
    提出どうしは日付の幅が 0 で、重なりを見落として同じレーンに載って
    いた (提出 #167/#168 の重なり)。

    公開が早い帯ほど本線に近いレーンに置き、本線の下と上を交互に使う
    (history.pack_lanes)。合流の線がほかの帯を横切らず、本線が図の
    なるべく中央に来る。同時に何本出ても重ねないぶん、図は縦に伸びる
    (許容 = 管理者指示)。
    """
    drawn = [c for c in chips if c['number'] in spans]
    lanes = history.pack_lanes(
        [_chip_extent(c, spans[c['number']], node_x) for c in drawn],
        open_ends=[c['pending'] for c in drawn],
        hard=[_chip_hard(c, spans[c['number']], node_x) for c in drawn])
    for c, lane in zip(drawn, lanes):
        c['lane'] = lane


CURVE_PAD = 20      # 曲線の範囲の見積もりに足す余裕 (深い段の曲線の膨らみ)


def _chip_hard(c, span, node_x):
    """帯のレーンの高さで、ほかの枝の縦の線が通ると山で跨げない x 範囲.

    帯の箱 (外に出したラベル・確認中のピルを含む) と、分岐・合流の
    1/4 円弧のあたり。横の直線の部分だけが山で跨げる (図の作法 5)。
    箱を突き抜けたり曲線どうしが斜めに交わったりする並びを
    history.pack_lanes が重く数えて避ける。
    """
    left, right = span
    label_w = _est_w(_chip_label(c))
    if label_w + 14 > right - left:
        left = min(left, right - label_w)
    if c['pending']:
        right = max(right, span[0] + PILL_W)
    out = [(left, right)]
    base_x = node_x.get(c['base_tag'])
    if base_x is not None:
        out.append((base_x, base_x + DERIV_RUN + DEPART_DX + CURVE_PAD))
    target_x = node_x.get(c['target_tag'])
    if not c['pending'] and target_x is not None:
        out.append((target_x - MERGE_LEAD - ARRIVE_DX - CURVE_PAD,
                    target_x))
    return out


def _depart_span(chips):
    """基点ノードごとの、いちばん外側の出発位置のずれ幅 {タグ: px}.

    同じノードから出る枝は出発位置を右へずらす (_depart_slots)。
    """
    n = {}
    for c in chips:
        if c['base_tag']:
            n[c['base_tag']] = n.get(c['base_tag'], 0) + 1
    return {tag: (k - 1) * DEPART_STEP for tag, k in n.items()}


def _deriv_lead(base_tag, depart_span):
    """基点ノードの中心 → 帯の左端に取る距離.

    いちばん外側の出発位置から測るので、同じノードから何本出ても
    どの枝も 1/4 円弧 + 矢先ぶんの横幅を持てる (足りないと円弧が
    矢先を追い越して線が途切れて見える)。同じノードから出る帯は
    左端がそろい、ずれの割り当てが変わっても帯の位置は動かない。
    """
    return DEPART_DX + depart_span.get(base_tag, 0) + DERIV_RUN


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
        # 本線から遠いレーンほど左 (ノード寄り) から出す。深く降りる枝が
        # 浅い枝の内側を通って曲線が同心に並び、曲線どうしが交わらない
        # (逆の順だと深い枝が浅い枝の曲線を斜めに横切り、分岐に見える)
        for i, c in enumerate(sorted(sibs,
                                     key=lambda c: (-abs(c['lane']),
                                                    c['lane']))):
            slots[c['number']] = i
    return slots


def _badge_rect(side, cur_x, depart_off=0, rail_y=RAIL_Y):
    """現行版バッジの矩形 (x, y, w, h).

    右に置くときは、その版から出る枝のいちばん外側の出発位置
    (depart_off) より右へ寄せる。枝が本線を離れるところにバッジを
    重ねない。帯との重なりは本線からの相対位置で決まるので、置き場所の
    判定 (_badge_side) は rail_y の既定値のままでよい。
    """
    if side in ('top', 'bottom', 'top_r', 'bottom_r', 'top_l', 'bottom_l'):
        # _r / _l = 丸の右 / 左に寄せる (合流は丸の左に、分岐は右に縦に
        # 付くので、片方だけ付く側でも線を避けて置ける)
        y = rail_y - 72 if side.startswith('top') else rail_y + 48
        x = {'r': cur_x + 6, 'l': cur_x + 2 - BADGE_W}.get(
            side[-1], cur_x - BADGE_W / 2)
        return (x, y, BADGE_W, BADGE_H)
    return (cur_x + 22 + depart_off, rail_y - BADGE_H / 2,
            BADGE_W, BADGE_H)


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


BADGE_CURVE_PAD = 6     # 曲線がバッジの高さで横へ膨らむ分の見込み
BADGE_CHIP_W = 3        # 帯の箱との重なりの重さ (線との重なり = 1)


def _badge_side(current_tag, node_x, chips, spans, depart_off=0,
                depart_span=None):
    """現行版バッジの置き場所 'right' | 'top' | 'bottom' (| '_r' '_l' 付き).

    既定は本線の右 (駅名標と同じ形で、図が変わっても位置が動かない)。
    右に別の版のノードが来てしまうときだけ、丸の上・下へ逃がす。上 (下)
    では丸の真上、右寄せ、左寄せの順に、帯の箱にも、本線と上 (下) の段を
    つなぐ縦の線 (分岐・合流) にも重ならない所を選ぶ。線を見ていなかった
    ころは、現行版が履歴の途中の版のとき (更新が遅れたメンバーの画面) に
    合流の矢印がバッジを縦に貫いていた (UI レビュー 2026-10)。
    """
    cur_x = node_x.get(current_tag)
    if cur_x is None:
        return 'right'
    right_blocked = any(cur_x < x < cur_x + BADGE_W + 30 + depart_off
                        for tag, x in node_x.items() if tag != current_tag)
    if not right_blocked:
        return 'right'
    depart_span = depart_span or {}
    # 上 (下) の段へ出入りする縦の線の x 範囲。曲線はバッジの高さで少し
    # 横へ膨らむので、その分を見込む
    lines = {'top': [], 'bottom': []}
    drawn = [c for c in chips if c['number'] in spans]
    for c in drawn:
        side = 'top' if c['lane'] >= 1 else 'bottom'
        bx = node_x.get(c['base_tag'])
        if bx is not None:
            x0 = bx + DEPART_DX
            lines[side].append((x0 - 3, x0 + depart_span.get(
                c['base_tag'], 0) + BADGE_CURVE_PAD))
        tx = node_x.get(c['target_tag'])
        if not c['pending'] and tx is not None:
            lines[side].append((tx - ARRIVE_DX - BADGE_CURVE_PAD,
                                tx - ARRIVE_DX + 3))

    def conflicts(side):
        rect = _badge_rect(side, cur_x)
        n = BADGE_CHIP_W * sum(
            _overlaps(rect, _chip_rect(c, spans[c['number']]), 6)
            for c in drawn)
        n += sum(rect[0] - 2 < b and a < rect[0] + rect[2] + 2
                 for a, b in lines[side.split('_')[0]])
        return n

    cands = [side + tail for side in ('top', 'bottom')
             for tail in ('', '_r', '_l')]
    for side in cands:
        if not conflicts(side):
            return side
    # どこも空かなければ、重なりのいちばん少ない所 (右は隣の版の丸に
    # 掛かるので 1 つの重なりと数える)
    return min(cands + ['right'],
               key=lambda sd: 1 if sd == 'right' else conflicts(sd))


LABEL_GAP = 10      # 版名ラベルどうし・ラベルと線の間に取るすき間


def _node_label_place(tag, x, label, rail_y, attach, up_lines, placed):
    """版名ラベルの置き場所 (左端 x, 上端 y, 揃え 'center'|'start'|'end').

    第一候補は丸の真上の中央。上の段から枝が出入りするノードでは、真上は
    線と重なるので、次の順で空いている所を選ぶ:
    丸の真下 (下の段の線が無いとき。どの丸の名前か迷わない) →
    右上 (上へ出ていく線が無いとき。合流は丸の左に刺さるので右は空く) →
    左上 (合流の縦線のさらに左) → もう 1 段上。
    隣の版名・上の段の縦の線から LABEL_GAP 以上離れない所は避ける
    (上の段を常に使うようになって左へ逃がすことが増え、隣の版名と 6px
    まで詰まって、どちらの丸の名前か読めなかった = UI レビュー 2026-10)。
    どこも空かなければ、図の左端で切れない候補のうちいちばんゆとりの
    ある所にする (版が 1 日違いで並ぶと、最初の候補のままでは名前が
    重なって読めなかった = UI レビュー 2 巡目)。
    """
    lw = _est_w(label, 12.5)
    above = rail_y - 31
    up = tag in attach['up_dep'] or tag in attach['up_merge']
    down = tag in attach['down_dep'] or tag in attach['down_merge']
    opts = []
    if not up:
        opts.append((x - lw / 2, above, 'center'))
    if not down:
        opts.append((x - lw / 2, rail_y + 13, 'center'))
    if tag not in attach['up_dep']:
        opts.append((x + 12, above, 'start'))
    opts.append((x - ARRIVE_DX - 10 - lw, above, 'end'))
    # もう 1 段上 (上の段の帯との間)。真上が隣の版名とぶつかるとき用。
    # 上へ出ていく線 (丸の右) を避けて丸の左上に寄せた形も候補にする
    opts.append((x - lw / 2, rail_y - 49, 'center'))
    opts.append((x + 2 - lw, rail_y - 49, 'end'))

    def room(o):
        """候補のゆとり (隣の版名・上の段の縦の線までのいちばん狭い間)."""
        x0, y0 = o[0], o[1]
        box = (x0, y0, x0 + lw, y0 + 16)
        gaps = [max(p[0] - box[2], box[0] - p[2]) for p in placed
                if box[1] < p[3] and p[1] < box[3]]
        if y0 < rail_y:
            gaps += [max(a - box[2], box[0] - b) - 2 + LABEL_GAP
                     for a, b in up_lines]
        return min(gaps or [LABEL_GAP])

    ok = [o for o in opts if o[0] >= 4] or opts
    for o in ok:
        if room(o) >= LABEL_GAP:
            return o
    # どこも空かなければ、いちばんゆとりのある所 (重ねて読めなくしない)
    return max(ok, key=room)


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
    node_x = _node_positions(stables, chips)
    X = _date_scale(stables, node_x)
    depart_span = _depart_span(chips)

    # きょうの線: 基本は「きょう」の実位置。ただし確認中の帯に
    # 「名前 #番号」が入るだけの幅と、派生線 (1/4 円弧 + 矢先) の
    # 横幅、最新ノード・現行版バッジとの間隔が足りなければ右へ広げる
    # (可変でよい = 管理者指示)
    today_x = max(X(today), X(stables[-1]['date']),
                  node_x[stables[-1]['tag']] + 24)
    for c in chips:
        if not c['pending']:
            continue
        base_x = node_x.get(c['base_tag'])
        left = max(X(c['start']),
                   (base_x + _deriv_lead(c['base_tag'], depart_span))
                   if base_x is not None else X0)
        today_x = max(today_x, left + _chip_min_w(c) + TODAY_GAP)

    def _spans(tx):
        return {c['number']: _chip_span(
                    c, node_x[c['base_tag']], node_x, tx,
                    _deriv_lead(c['base_tag'], depart_span))
                for c in chips if node_x.get(c['base_tag']) is not None}

    # レーンは帯の実際の x で決める (日付だけでは重なりを見落とす)。
    # バッジの置き場所もレーンではなく帯の実際の位置で決める
    # (レーンだけで決めると現行版と関係のない帯の上に重ねてしまう)
    badge_off = depart_span.get(current_tag, 0)
    # バッジの置き場所は段で決まり (枝が出入りする側には置かない)、段は
    # きょう線の位置で決まり、きょう線はバッジの右端で決まる。置き場所が
    # 変わらなくなるまで決め直す (段が入れ替わったのに古い置き場所の
    # ままだと、合流の矢印がバッジを貫く)
    badge_side = None
    for _ in range(3):
        spans = _spans(today_x)     # きょう線が動いた分、確認中の帯も動く
        _assign_lanes(chips, node_x, spans)
        side = _badge_side(current_tag, node_x, chips, spans, badge_off,
                           depart_span)
        if side == badge_side:
            break
        badge_side = side
        if current_tag in node_x:
            bx, _by, bw, _bh = _badge_rect(badge_side, node_x[current_tag],
                                           badge_off)
            today_x = max(today_x, bx + bw + TODAY_GAP)
    depart_slots = _depart_slots(chips)
    width = today_x + RIGHT_PAD
    # 上レーンの枝が付くノード (出ていく基点 + 入ってくる合流先)。
    # ラベルを真上に置くと枝の線や矢先と重なるため、左へ逃がす目印
    # 段数は描く帯だけで数える (基点の無い帯は描かない)
    drawn = [c for c in chips if c['number'] in spans]
    depth = max([-c['lane'] for c in drawn if c['lane'] < 0] or [1])
    # 上の段が増えたぶんだけ本線を下げ、下の段が増えたぶんだけ図を
    # 下へ広げる (いちばん下の帯とその下に置くピル・ラベルの余地は
    # FIG_H が 1 段目のぶんとして持っている)
    rail_y = _rail_y(drawn)
    fig_h = FIG_H + (depth - 1) * LANE_STEP + (rail_y - RAIL_Y)
    grid_bottom = fig_h - 22

    shapes = []
    overlays = []

    # 縦線と日付は「版が更新された日」だけ (線は必ずその版に重なる)。
    # 7 日刻みの等間隔グリッドは、線がどの版の日か分からず読めなかった
    # (管理者指示)。同じ日に複数の版が出たら線も日付も 1 本にまとめる
    label_right = None      # 直前に描いた日付の右端 (重なりの間引き用)
    labeled_year = None     # 直前に描いた日付の年 (年は変わった時だけ)
    for d in sorted({s['date'] for s in stables}):
        x = X(d)
        shapes.append(cv.Line(x, AXIS_Y, x, grid_bottom,
                              paint=_stroke(GRID, 1)))
        label = history.fmt_date(d, with_year=(d.year != labeled_year))
        half = _est_w(label, 10.5) / 2
        if label_right is not None and x - half < label_right + 6:
            continue        # 隣とぶつかる日付は線だけにする (数字の重なり防止)
        shapes.append(_text(x, 21, label, 10.5, AXIS, center=True))
        label_right, labeled_year = x + half, d.year
    # 軸線はきょう線で止める (未来側の空白まで伸ばさない)
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
    shapes.append(cv.Line(X0, rail_y, today_x, rail_y,
                          paint=_stroke(RAIL, 5.5)))

    # 帯と線 (ノードより先に描く)。先に全部の線の形を集めて、横の線を
    # 他の帯の縦寄りの曲線が横切る所に山 (半円) を入れる (図の作法 5)。
    # 本線・きょう線・日付の縦線は線どうしではないので跨がない
    geoms = []
    attach = {k: set() for k in ('up_dep', 'up_merge', 'down_dep',
                                 'down_merge')}
    up_lines = []
    for c in chips:
        base_x = node_x.get(c['base_tag'])
        if base_x is None:
            continue
        chip_left, chip_right = spans[c['number']]
        # 同じノードから出る枝は出発位置を少しずつ右へずらす
        depart = base_x + DEPART_DX + DEPART_STEP * depart_slots.get(
            c['number'], 0)
        d_curve, d_run = _deriv_geom(depart, chip_left - 9, c['lane'],
                                       rail_y)
        m = None
        if not c['pending']:
            big = c['target_tag'] == current_tag
            m = (node_x[c['target_tag']], big)
            m_run, m_curve, _tip, _ang = _merge_geom(
                chip_right, m[0], c['lane'], big, rail_y)
        else:
            m_run = m_curve = None
        geoms.append((c, depart, chip_left, chip_right, m,
                      [d_run, m_run], [d_curve, m_curve]))
        # 版名ラベルの置き場所を決めるための、ノードに付く線の目印
        side = 'up' if c['lane'] >= 1 else 'down'
        attach[side + '_dep'].add(c['base_tag'])
        if m:
            attach[side + '_merge'].add(c['target_tag'])
        if side == 'up':
            # 本線の少し上を通る縦寄りの線 (ラベルの高さで少し曲がる分)
            up_lines.append((depart - 3, depart + 6))
            if m:
                up_lines.append((m[0] - ARRIVE_DX - 6,
                                 m[0] - ARRIVE_DX + 3))
    for c, depart, chip_left, chip_right, m, runs, _own in geoms:
        others = [cu for g in geoms if g[0] is not c for cu in g[6] if cu]
        d_hops, m_hops = [_hops_on(r, others) if r else [] for r in runs]
        color = history.person_color(c['author'], authors)
        shapes += _derivation(depart, chip_left - 9, c['lane'], color,
                              d_hops, rail_y)
        if m:
            shapes += _merge_arrow(chip_right, m[0], c['lane'], color, m[1],
                                   m_hops, rail_y)
        chip_shapes, hit = _chip(c, chip_left, chip_right, color,
                                  today_x, rail_y)
        shapes += chip_shapes
        overlays.append((hit, 'chip', c))

    # 駅ノード (最後に描いて線の上に載せる)
    placed = []     # 置いた版名ラベルの矩形 (隣の版名と詰まらないように)
    for s in stables:
        x = node_x[s['tag']]
        if s['tag'] == current_tag:
            shapes.append(cv.Circle(x, rail_y, 14, paint=_fill(NAVY)))
            shapes.append(cv.Circle(x, rail_y, 14,
                                    paint=_stroke('#f59e0b', 6)))
            # バッジは枝と重ならない側に置く: 上が空いていれば上、
            # 次に下、両方ふさがっていればノードの右 (本線の上)
            bx0, by0, bw, bh = _badge_rect(badge_side, x, badge_off,
                                           rail_y)
            shapes.append(cv.Rect(bx0, by0, bw, bh, border_radius=7,
                                  paint=_fill('#fef08a')))
            shapes.append(cv.Rect(bx0, by0, bw, bh, border_radius=7,
                                  paint=_stroke('#a16207', 1)))
            shapes.append(_text(bx0 + bw / 2, by0 + 4,
                                '%s 現行版' % s['tag'], 12.5,
                                '#713f12', weight=ft.FontWeight.BOLD,
                                center=True))
            overlays.append(([min(bx0, x - 16), min(by0, rail_y - 16),
                              max(bx0 + bw, x + 16) - min(bx0, x - 16),
                              max(by0 + bh, rail_y + 16)
                              - min(by0, rail_y - 16)], 'stable', s))
        else:
            shapes.append(cv.Circle(x, rail_y, 9, paint=_fill('#ffffff')))
            shapes.append(cv.Circle(x, rail_y, 9, paint=_stroke(NAVY, 3)))
            label = s['tag'] + (' 初回配布' if s is stables[0] and
                                not s['pr'] else '')
            lx, ly, align = _node_label_place(
                s['tag'], x, label, rail_y, attach, up_lines, placed)
            lw = _est_w(label, 12.5)
            placed.append((lx, ly, lx + lw, ly + 16))
            tx = {'start': lx, 'center': lx + lw / 2, 'end': lx + lw}[align]
            shapes.append(_text(tx, ly, label, 12.5, NAVY,
                                weight=ft.FontWeight.BOLD,
                                center=align == 'center',
                                end=align == 'end'))
            hx0, hy0 = min(x - 16, lx), min(ly, rail_y - 16)
            hx1, hy1 = max(x + 16, lx + lw), max(ly + 16, rail_y + 16)
            overlays.append(([hx0, hy0, hx1 - hx0, hy1 - hy0], 'stable', s))

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
        ft.Container(left_btn, left=4, top=rail_y - 26),
        ft.Container(right_btn, right=4, top=rail_y - 26),
    ], expand=True, height=fig_h + 12)

    control = ft.Container(
        border=ft.Border.all(1, '#e5e7eb'), border_radius=8,
        bgcolor='#ffffff', clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        content=viewwrap)
    return {'control': control, 'scroll_row': scroll_row,
            'initial_offset': initial_offset, 'height': fig_h + 12,
            'rail_y': rail_y}


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
