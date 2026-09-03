# -*- coding: utf-8 -*-
"""木造柱頭柱脚接合部のN値計算 (告示1460号第二号ただし書き。新規実装).

モデル化規約 (既存の壁量計算 qr_baisu と同じ):
  - 面材壁: 鉛直の板要素。厚み(mm表記値) = 壁倍率
  - 筋かい: ブレース要素。断面番号→壁倍率の指定 (brace_baisu)
  - 単位: m (節点座標)

計算式 (技術基準解説書 3.3(4)):
  最上階・平屋の柱:  N = A1*B1*(H1/2.7) - L
                     (B1=0.5/出隅0.8, L=0.6/出隅0.4)
  その他の階の柱:    N = A1*B1*(H1/2.7) + Σ AkBk*(Hk/2.7) - L
                     (L=1.6/出隅1.0)
  H = 当該階の横架材上端間の垂直距離 (柱の上下端間距離で近似)。
      3.2m以下は H=2.7 (係数1.0)、6.0m超はN値法適用外 (注記を出す)
  A = 当該柱の両側の壁倍率の差 (X・Y方向それぞれ評価し大きい方のNを採用)
    ※3階建て以上は上階の引抜きを全階累積し、押さえLは2階建て相当の
      まま (安全側)。厳密には許容応力度計算等による。
  筋かいの取り付き向きによる補正 (表3.3-4/3-5) は行わない
  (壁倍率換算モデルのため)

必要引張耐力 T = 5.3 * N [kN]。金物例は告示表三対応のZマーク金物
(同等以上の性能の金物で代替可)。

出隅判定: 各層の柱平面配置の凸包頂点を出隅と推定 (L字平面の凸角などは
画面で手動修正する)。
"""
import math

import numpy as np

from mgtkit.mgt import (mgtopen_element, mgtopen_node, mgtopen_plate,
                        mgtopen_thickness)

Z_TOL = 0.005     # 層・鉛直判定の許容 [m]
PLAN_TOL = 0.05   # 平面位置の一致許容 [m]

# 告示1460号 表三: (上限N値, 記号, 金物例)
_GRADES = [
    (0.0, 'い', '短ほぞ差し・かすがい'),
    (0.65, 'ろ', '長ほぞ差し込み栓・かど金物CP-L'),
    (1.0, 'は', 'かど金物CP-T・山形プレートVP'),
    (1.4, 'に', '羽子板ボルト・短冊金物 (釘なし)'),
    (1.6, 'ほ', '羽子板ボルト・短冊金物 (スクリュー釘)'),
    (1.8, 'へ', 'ホールダウンHD-B10 (S-HD10)'),
    (2.8, 'と', 'ホールダウンHD-B15 (S-HD15)'),
    (3.7, 'ち', 'ホールダウンHD-B20 (S-HD20)'),
    (4.7, 'り', 'ホールダウンHD-B25 (S-HD25)'),
    (5.6, 'ぬ', 'ホールダウンHD-B15×2'),
]


def n_grade(n):
    """N値 → (記号, 金物例)。5.6超は個別検討."""
    for lim, sym, hw in _GRADES:
        if n <= lim + 1e-9:
            return sym, hw
    return '－', '個別検討 (T=%.1fkN で金物設計)' % (5.3 * n)


def _convex_hull(points):
    """凸包の頂点集合 (Andrew's monotone chain)。points: [(x, y), ...]"""
    pts = sorted(set((round(x, 4), round(y, 4)) for x, y in points))
    if len(pts) <= 2:
        return set(pts)

    def cross(o, a, b):
        return ((a[0] - o[0]) * (b[1] - o[1])
                - (a[1] - o[1]) * (b[0] - o[0]))

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return set(lower[:-1] + upper[:-1])


def _cluster_levels(zs, tol=Z_TOL):
    """Z座標を層下端レベルにまとめる (昇順)."""
    out = []
    for z in sorted(zs):
        if out and abs(z - out[-1]) <= tol:
            continue
        out.append(float(z))
    return out


def _read_story_levels(mgt_path):
    """mgtの *STORY (層設定) から [(層名, レベル), ...] を昇順で返す.

    *STORYが無い場合は空list (呼び出し側で柱下端レベルの自動判定に
    フォールバックする)。
    """
    raw = open(mgt_path, 'rb').read()
    txt = None
    for enc in ('cp932', 'utf-8'):
        try:
            txt = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if txt is None:
        return []
    out = []
    in_sec = False
    for ln in txt.splitlines():
        s = ln.strip()
        u = s.upper()
        if u.startswith('*STORY') and not u.startswith('*STORY-'):
            in_sec = True
            continue
        if in_sec:
            if s.startswith('*'):
                break
            if not s or s.startswith(';'):
                continue
            if u.startswith('NAME='):
                parts = s.split(',')
                name = parts[0].split('=', 1)[1].strip()
                try:
                    out.append((name, float(parts[1])))
                except (IndexError, ValueError):
                    continue
    return sorted(out, key=lambda t: t[1])


def _walls_from_model(node_pos, plate, thickness, element, node,
                      brace_baisu):
    """壁 (面材+筋かい) を平面線分に変換する.

    戻り値: [{'z0','z1','p1':(x,y),'p2':(x,y),'axis':0/1,'beta','kind'}]
    axis 0=X方向壁, 1=Y方向壁 (斜めは長い方の軸に分類)
    """
    walls = []
    thick_map = {}
    th = np.atleast_2d(np.asarray(thickness, dtype=float)) \
        if np.size(thickness) else np.zeros((0, 3))
    for r in th:
        thick_map[int(r[0])] = float(r[1])

    pl = np.atleast_2d(np.asarray(plate, dtype=float)) \
        if np.size(plate) else np.zeros((0, 9))
    for r in pl:
        nds = [int(v) for v in r[3:7] if int(v) > 0]
        pts = [node_pos[n] for n in nds if n in node_pos]
        if len(pts) < 3:
            continue
        xy = np.asarray([p[:2] for p in pts])
        zvals = [float(p[2]) for p in pts]
        q = xy - xy.mean(axis=0)
        w = np.linalg.eigvalsh(q.T @ q)
        if math.sqrt(max(float(w[0]), 0.0) / len(pts)) > Z_TOL:
            continue  # 水平床など面的な板は対象外 (鉛直壁のみ)
        if (max(zvals) - min(zvals)) < 0.01:
            continue
        beta = thick_map.get(int(r[2]))
        if beta is None or beta <= 0:
            continue
        L_X = float(xy[:, 0].max() - xy[:, 0].min())
        L_Y = float(xy[:, 1].max() - xy[:, 1].min())
        if max(L_X, L_Y) < 1e-9:
            continue
        axis = 0 if L_X >= L_Y else 1
        walls.append({
            'z0': min(zvals), 'z1': max(zvals),
            'p1': (float(xy[:, 0].min()), float(xy[:, 1].min())),
            'p2': (float(xy[:, 0].max()), float(xy[:, 1].max())),
            'axis': axis, 'beta': float(beta), 'kind': '板'})

    if brace_baisu:
        el = np.atleast_2d(np.asarray(element, dtype=float))
        for r in el:
            beta = brace_baisu.get(int(r[2]))
            if beta is None or beta <= 0:
                continue
            n1, n2 = int(r[3]), int(r[4])
            if n1 not in node_pos or n2 not in node_pos:
                continue
            p1 = node_pos[n1]
            p2 = node_pos[n2]
            if p1[2] > p2[2]:
                p1, p2 = p2, p1
            if abs(p2[2] - p1[2]) < 0.01:
                continue  # 水平材は対象外
            L_X = abs(float(p2[0] - p1[0]))
            L_Y = abs(float(p2[1] - p1[1]))
            if max(L_X, L_Y) < 1e-9:
                continue  # 鉛直材 (柱扱い)
            axis = 0 if L_X >= L_Y else 1
            xs = sorted((float(p1[0]), float(p2[0])))
            ys = sorted((float(p1[1]), float(p2[1])))
            walls.append({
                'z0': float(p1[2]), 'z1': float(p2[2]),
                'p1': (xs[0], ys[0]), 'p2': (xs[1], ys[1]),
                'axis': axis, 'beta': float(beta), 'kind': '筋かい'})
    return walls


def _wall_sides_at(walls, story_z0, story_z1, x, y):
    """柱位置 (x, y) の両側の壁倍率合計を方向別に求める.

    戻り値: {0: (minus, plus), 1: (minus, plus)}
    壁線分の端部が柱位置に一致すれば伸びる側へ、柱が壁の中間に
    ある場合は両側へ加算する。
    """
    sides = {0: [0.0, 0.0], 1: [0.0, 0.0]}
    for w in walls:
        if w['z0'] > story_z1 - Z_TOL or w['z1'] < story_z0 + Z_TOL:
            continue
        ax = w['axis']
        along = 0 if ax == 0 else 1        # 壁が伸びる座標軸
        cross = 1 - along
        c_along = (x, y)[along]
        c_cross = (x, y)[cross]
        w_lo = w['p1'][along]
        w_hi = w['p2'][along]
        w_cross = (w['p1'][cross] + w['p2'][cross]) / 2.0
        if abs(c_cross - w_cross) > PLAN_TOL:
            continue  # 柱が壁の通りにない
        if c_along < w_lo - PLAN_TOL or c_along > w_hi + PLAN_TOL:
            continue  # 柱が壁の範囲外
        near_lo = abs(c_along - w_lo) <= PLAN_TOL
        near_hi = abs(c_along - w_hi) <= PLAN_TOL
        if near_lo and not near_hi:
            sides[ax][1] += w['beta']      # +側へ伸びる
        elif near_hi and not near_lo:
            sides[ax][0] += w['beta']      # -側へ伸びる
        elif not near_lo and not near_hi:
            sides[ax][0] += w['beta']      # 壁の中間: 両側
            sides[ax][1] += w['beta']
        # near_lo かつ near_hi (壁長<2*TOL) は無視
    return {k: (v[0], v[1]) for k, v in sides.items()}


def n_value_compute(mgt_path, brace_baisu=None, sumi_override=None,
                    limit_sec_no=9000.0):
    """N値計算の本体.

    brace_baisu   : {断面番号: 壁倍率} (筋かい)
    sumi_override : {'階|下端節点番号': bool} 出隅の手動指定
    戻り値: {'stories': 層数, 'columns': [dict, ...], 'n_wall': 壁数}
    """
    node = np.atleast_2d(mgtopen_node(mgt_path))
    element = np.atleast_2d(mgtopen_element(mgt_path))
    plate = mgtopen_plate(mgt_path)
    thickness = mgtopen_thickness(mgt_path)
    sumi_override = dict(sumi_override or {})

    node_pos = {int(r[0]): np.asarray(r[1:4], dtype=float) for r in node}

    # ---- 単一部材 (*MEMBER) の鉛直材は1本の柱に結合 ----
    # 柱が高さ方向に要素分割されていても、単一部材指定があれば
    # 通しの1本として扱う (H・階の判定・上下階の連続が正しくなる)
    ele_sec = {int(r[0]): float(r[2]) for r in element}
    unit_cols = []   # (ele_no, n_bot, n_top, x, y, z0, z1, [要素列])
    in_unit = set()
    try:
        from mgtkit.mgt import mgtopen_unit_2015
        units = mgtopen_unit_2015(mgt_path, element, node)
    except Exception:  # noqa: BLE001
        units = []
    for u in units:
        eles = [int(v) for v in np.atleast_1d(u[1]).ravel()]
        nds = [int(v) for v in np.atleast_1d(u[2]).ravel()
               if int(v) in node_pos]
        pts = [node_pos[n] for n in nds]
        if len(pts) < 2:
            continue
        xy = np.asarray([p[:2] for p in pts])
        if (float(np.ptp(xy[:, 0])) > PLAN_TOL
                or float(np.ptp(xy[:, 1])) > PLAN_TOL):
            continue  # 平面に広がる → 梁などの単一部材
        zs = [float(p[2]) for p in pts]
        if max(zs) - min(zs) < 0.1:
            continue
        secs = [ele_sec.get(e) for e in eles]
        if not any(s0 is not None and s0 <= float(limit_sec_no)
                   for s0 in secs):
            continue
        i_lo = int(np.argmin(zs))
        i_hi = int(np.argmax(zs))
        unit_cols.append((eles[0], nds[i_lo], nds[i_hi],
                          float(pts[i_lo][0]), float(pts[i_lo][1]),
                          float(zs[i_lo]), float(zs[i_hi]), eles))
        in_unit.update(eles)

    # ---- 柱 (鉛直部材) の抽出 ----
    cols = list(unit_cols)
    for r in element:
        if int(r[0]) in in_unit:
            continue  # 単一部材として結合済み
        if float(r[2]) > float(limit_sec_no):
            continue
        n1, n2 = int(r[3]), int(r[4])
        if n1 not in node_pos or n2 not in node_pos:
            continue
        p1, p2 = node_pos[n1], node_pos[n2]
        if math.hypot(p2[0] - p1[0], p2[1] - p1[1]) > PLAN_TOL:
            continue  # 平面移動あり → 柱でない
        if abs(p2[2] - p1[2]) < 0.1:
            continue
        if p1[2] > p2[2]:
            p1, p2 = p2, p1
            n1, n2 = n2, n1
        cols.append((int(r[0]), n1, n2, float(p1[0]), float(p1[1]),
                     float(p1[2]), float(p2[2]), [int(r[0])]))
    if not cols:
        raise ValueError('柱 (鉛直部材) が見つかりません。')

    # ---- 層: mgtの *STORY (層設定) があればそれを使う ----
    # 層設定がある場合、層レベルに下端が一致しない鉛直材 (小屋束・
    # 間柱など) はN値の対象外とする。無い場合は柱下端レベルから自動判定
    stories_def = _read_story_levels(mgt_path)
    if stories_def:
        levels = [lv for _n, lv in stories_def]
        story_names = [n for n, _lv in stories_def]
        story_tol = 0.1
    else:
        levels = _cluster_levels([c[5] for c in cols])
        story_names = None
        story_tol = Z_TOL
    n_story = len(levels)

    def story_of(z):
        for i, z0 in enumerate(levels):
            if abs(z - z0) <= story_tol:
                return i + 1
        return 0

    walls = _walls_from_model(node_pos, plate, thickness, element,
                              node, dict(brace_baisu or {}))

    # ---- 複数層にまたがる柱 (通し柱等) は層レベルで各階に分割 ----
    # 単一部材で結合した通し柱も、N値は階ごと (H=当該階の階高) に評価する
    split_cols = []
    for c in cols:
        z0, z1 = c[5], c[6]
        cuts = sorted(lv for lv in levels
                      if z0 + story_tol < lv < z1 - 0.05)
        if not cuts:
            split_cols.append(c)
            continue
        zs = [z0] + cuts + [z1]
        for k in range(len(zs) - 1):
            split_cols.append((c[0], c[1], c[2], c[3], c[4],
                               float(zs[k]), float(zs[k + 1]), c[7]))
    cols = split_cols

    # ---- 層ごとの柱と出隅推定 ----
    by_story = {}
    n_skip = 0
    for c in cols:
        s = story_of(c[5])
        if s:
            by_story.setdefault(s, []).append(c)
        else:
            n_skip += 1
    if stories_def and n_skip:
        print('注記: 層設定 (*STORY) のレベルに柱脚が一致しない鉛直材 '
              '%d本は小屋束・間柱等としてN値の対象外にしました' % n_skip)
    if not by_story:
        raise ValueError('層レベルに一致する柱がありません。'
                         '*STORYのレベルと柱脚レベルを確認してください。')
    sumi_auto = {}
    for s, cc in by_story.items():
        hull = _convex_hull([(c[3], c[4]) for c in cc])
        for c in cc:
            sumi_auto[(s, c[1])] = ((round(c[3], 4), round(c[4], 4))
                                    in hull)

    # ---- 各柱の A・B・L と N ----
    per_col = {}   # (story, x丸め, y丸め) -> 計算行 (連続柱の参照用)
    rows = []
    for s in sorted(by_story):
        z0 = levels[s - 1]
        z1 = levels[s] if s < n_story else max(c[6] for c in by_story[s])
        for c in by_story[s]:
            ele_no, n_bot, n_top, x, y = c[0], c[1], c[2], c[3], c[4]
            key = '%d|%d' % (s, n_bot)
            sumi = bool(sumi_override.get(key,
                                          sumi_auto.get((s, n_bot), False)))
            sides = _wall_sides_at(walls, z0, z1, x, y)
            a_x = abs(sides[0][1] - sides[0][0])
            a_y = abs(sides[1][1] - sides[1][0])
            h = float(c[6] - c[5])
            hf = (2.7 if h <= 3.2 + 1e-9 else h) / 2.7
            row = {'story': s,
                   'story_label': (story_names[s - 1] if story_names
                                   else str(s)),
                   'ele_no': ele_no, 'node': n_bot,
                   'eles': [int(v) for v in c[7]],
                   'x': round(x, 3), 'y': round(y, 3),
                   'sumi': sumi, 'sumi_auto': sumi_auto.get((s, n_bot),
                                                            False),
                   'h': round(h, 2), 'hf': round(hf, 3),
                   'h_over': h > 6.0 + 1e-9,
                   'a_x': round(a_x, 2), 'a_y': round(a_y, 2),
                   'wall_x': [round(v, 2) for v in sides[0]],
                   'wall_y': [round(v, 2) for v in sides[1]]}
            per_col[(s, round(x, 2), round(y, 2))] = row
            rows.append(row)

    # ---- 上階の累積 AkBk (連続柱) と N の確定 ----
    def ab_above(s, x, y):
        """直上階から最上階までの Ak*Bk*(Hk/2.7) 累積 (連続柱がある間)."""
        total_x = total_y = 0.0
        cur = s + 1
        while cur <= n_story:
            up = per_col.get((cur, round(x, 2), round(y, 2)))
            if up is None:
                break
            b_up = 0.8 if up['sumi'] else 0.5
            total_x += up['a_x'] * b_up * up['hf']
            total_y += up['a_y'] * b_up * up['hf']
            cur += 1
        return total_x, total_y

    h_over = []
    for row in rows:
        s = row['story']
        b1 = 0.8 if row['sumi'] else 0.5
        hf = row['hf']
        has_upper = per_col.get((s + 1, round(row['x'], 2),
                                 round(row['y'], 2))) is not None
        if not has_upper:
            L = 0.4 if row['sumi'] else 0.6
            n_x = row['a_x'] * b1 * hf - L
            n_y = row['a_y'] * b1 * hf - L
            ab2_x = ab2_y = 0.0
        else:
            L = 1.0 if row['sumi'] else 1.6
            ab2_x, ab2_y = ab_above(s, row['x'], row['y'])
            n_x = row['a_x'] * b1 * hf + ab2_x - L
            n_y = row['a_y'] * b1 * hf + ab2_y - L
        if row['h_over']:
            h_over.append(row['ele_no'])
        n = max(n_x, n_y)
        sym, hw = n_grade(max(n, 0.0) if n > 0 else 0.0)
        if n <= 0:
            sym, hw = _GRADES[0][1], _GRADES[0][2]
        row.update({
            'b1': b1, 'L': L, 'top': not has_upper,
            'ab2_x': round(ab2_x, 3), 'ab2_y': round(ab2_y, 3),
            'n_x': round(n_x, 3), 'n_y': round(n_y, 3),
            'n': round(n, 3),
            't_kn': round(5.3 * max(n, 0.0), 1),
            'grade': sym, 'hardware': hw})
    rows.sort(key=lambda r: (r['story'], r['x'], r['y']))
    if h_over:
        print('注意: 階高 (柱長さ) が6.0mを超える柱はN値法の適用外です '
              '(構造計算等による確認が必要): 要素 %s'
              % ', '.join(str(e) for e in sorted(set(h_over))))
    return {'stories': n_story, 'levels': levels,
            'story_names': story_names, 'columns': rows,
            'n_wall': len(walls)}


# ---------------------------------------------------------------------------
# 出力: CSV / TeX
# ---------------------------------------------------------------------------

_HEAD = ['階', '要素', '下端節点', 'X[m]', 'Y[m]', '出隅', 'H[m]',
         'H/2.7', 'Ax', 'Ay', 'B1', 'ΣABH(上階)', 'L', 'Nx', 'Ny', 'N',
         'T[kN]', '告示', '金物例']


def _row_cells(r):
    ab2 = max(r['ab2_x'], r['ab2_y'])
    return [r.get('story_label', r['story']), r['ele_no'], r['node'],
            '%.3f' % r['x'], '%.3f' % r['y'],
            '出隅' if r['sumi'] else '',
            '%.2f' % r['h'], '%.3f' % r['hf'],
            '%.2f' % r['a_x'], '%.2f' % r['a_y'], '%.1f' % r['b1'],
            '%.2f' % ab2, '%.1f' % r['L'],
            '%.2f' % r['n_x'], '%.2f' % r['n_y'], '%.2f' % r['n'],
            '%.1f' % r['t_kn'], '(%s)' % r['grade'], r['hardware']]


def n_value_csv(result, path):
    """N値計算表をCSV (cp932、Excel向け) で書き出す."""
    lines = [','.join(_HEAD)]
    for r in result['columns']:
        lines.append(','.join(str(c) for c in _row_cells(r)))
    lines.append('')
    lines.append('N = A1×B1×(H1/2.7) - L (最上階・平屋) / '
                 'A1×B1×(H1/2.7) + ΣAkBk×(Hk/2.7) - L (その他)')
    lines.append('B1: 0.5 (出隅0.8) / L: 0.6・0.4 (最上階) / 1.6・1.0 (その他)')
    lines.append('H: 階高 (3.2m以下はH=2.7で係数1.0、6.0m超はN値法適用外)')
    lines.append('T = 5.3×N [kN]。金物例は告示1460号表三対応 '
                 '(同等以上の性能の金物で代替可)')
    with open(path, 'w', encoding='cp932', errors='replace',
              newline='\r\n') as f:
        f.write('\n'.join(lines))
    return path


def _tex_escape(s):
    return str(s).replace('_', r'\_').replace('%', r'\%')


def n_value_tex(result, path):
    """N値計算表を計算書TeX (longtable) で書き出す."""
    L = []
    L.append('% N値計算表 (mgtkit 自動生成)')
    L.append(r'\begin{center}')
    L.append(r'{\small')
    L.append(r'\begin{longtable}{rrrrrc r rrr rrr r l}')
    L.append(r'\caption{柱頭柱脚接合部のN値計算表}\\')
    head = (r'階 & 要素 & 節点 & X & Y & 出隅 & $H$ & $A_x$ & $A_y$ & '
            r'$\Sigma A_kB_kH_k$ & $L$ & $N_x$ & $N_y$ & $N$ & 金物例 \\')
    L.append(r'\toprule')
    L.append(head)
    L.append(r'\midrule')
    L.append(r'\endfirsthead')
    L.append(r'\toprule')
    L.append(head)
    L.append(r'\midrule')
    L.append(r'\endhead')
    L.append(r'\bottomrule')
    L.append(r'\endfoot')
    for r in result['columns']:
        ab2 = max(r['ab2_x'], r['ab2_y'])
        L.append(' & '.join([
            _tex_escape(r.get('story_label', r['story'])),
            str(r['ele_no']), str(r['node']),
            '%.2f' % r['x'], '%.2f' % r['y'],
            r'○' if r['sumi'] else '',
            '%.2f' % r['h'],
            '%.2f' % r['a_x'], '%.2f' % r['a_y'],
            '%.2f' % ab2, '%.1f' % r['L'],
            '%.2f' % r['n_x'], '%.2f' % r['n_y'], '%.2f' % r['n'],
            '(%s) %s' % (r['grade'], _tex_escape(r['hardware']))]) + r' \\')
    L.append(r'\end{longtable}')
    L.append('}')
    L.append(r'\end{center}')
    L.append('')
    L.append(r'\noindent {\small '
             r'N = $A_1B_1 \cdot H_1/2.7 - L$ (最上階・平屋)、'
             r'$A_1B_1 \cdot H_1/2.7 + \Sigma A_kB_k \cdot H_k/2.7 - L$ '
             r'(その他の階)。'
             r'$B$=0.5 (出隅0.8)、$L$=0.6・0.4 (最上階)、'
             r'1.6・1.0 (その他)。$H$=階高 [m] (3.2m以下は $H$=2.7、'
             r'6.0m超はN値法適用外)。必要引張耐力 $T = 5.3N$ [kN]。'
             r'金物例は告示1460号表三対応 (同等以上の金物で代替可)。}')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(L))
    return path


# ---------------------------------------------------------------------------
# 出力: 平面図DXF (伏図に柱ごとのN値・金物ランクを注記)
# ---------------------------------------------------------------------------

# 金物ランク別のDXF色 (ACI)。軽微=緑系 → 大=赤系
_NV_ACI = {'い': 8, 'ろ': 3, 'は': 4, 'に': 5, 'ほ': 2, 'へ': 30,
           'と': 32, 'ち': 1, 'り': 12, 'ぬ': 14, '－': 6}
_NV_LAYER = 'S-N値'


def export_nvalue_dxf(mgt_path, out_dir, result, paper='A3', scale=None,
                      text_paper_mm=2.5, limit_sec_no=9000.0):
    """N値計算結果を階別の伏図DXFに落とし込む.

    既存の構造図DXF (dxf_struct.build_plan) の伏図を階ごとに横に並べ、
    柱位置に (告示記号)・N値・出隅を金物ランク別の色で注記する。
    伏図レベルは各階の柱脚レベルに最も近い梁レベルを使う。
    戻り値: (dxfファイルパス, info list)
    """
    import os

    from ezdxf.enums import TextEntityAlignment

    from mgtkit.dxf_struct import (_fig_to_msp, _new_doc, build_plan,
                                   load_struct_model, plan_levels)

    os.makedirs(out_dir, exist_ok=True)
    M = load_struct_model(mgt_path, limit_sec_no=limit_sec_no)
    lvls = plan_levels(M)
    by_story = {}
    for r in result['columns']:
        by_story.setdefault(r['story'], []).append(r)

    doc = _new_doc()
    if _NV_LAYER not in doc.layers:
        doc.layers.add(_NV_LAYER, color=1)
    # 日本語表示用の文字スタイル (標準のtxt.shxは和文が「?」になる)。
    # SHX+ビッグフォントは和文DXFの事実上の標準で、AutoCAD日本語版・
    # BricsCAD・Jw系で解決される (TTC指定は環境によって代替されるため)
    if 'MGT-JP' not in doc.styles:
        doc.styles.add('MGT-JP', font='romans.shx',
                       dxfattribs={'bigfont': 'extfont2.shx'})
    msp = doc.modelspace()

    x_cursor = 0.0
    info = []
    legend_pos = None
    for s in sorted(by_story):
        z0 = float(result['levels'][s - 1])
        lv = min(lvls, key=lambda z: abs(z - z0)) if lvls else z0
        try:
            fig = build_plan(M, lv, scale=scale, paper=paper,
                             text_paper_mm=text_paper_mm)
        except ValueError as e:
            print('注意: %d階の伏図を描けませんでした: %s' % (s, e))
            continue
        x0, y0, x1, y1 = fig['bounds']
        origin = (x_cursor - x0, -y0)
        _fig_to_msp(fig, msp, text_paper_mm, origin=origin)
        n = fig['scale']
        th = text_paper_mm * n
        r_sym = 0.9 * th
        for r in by_story[s]:
            x = r['x'] * 1000 + origin[0]
            y = r['y'] * 1000 + origin[1]
            aci = _NV_ACI.get(r['grade'], 6)
            attr = {'layer': _NV_LAYER, 'color': aci}
            if r['grade'] != 'い':
                msp.add_circle((x, y), r_sym, dxfattribs=attr)
            label = '(%s)' % r['grade']
            if r['n'] > 0:
                label += ' N=%.2f' % r['n']
            if r['sumi']:
                label = '出隅 ' + label
            t = msp.add_text(label, dxfattribs=dict(attr, height=th))
            t.set_placement((x + 1.3 * r_sym, y + 0.4 * th),
                            align=TextEntityAlignment.BOTTOM_LEFT)
        s_label = (result.get('story_names')[s - 1]
                   if result.get('story_names') else '%d階' % s)
        st = msp.add_text(
            '%s柱 柱脚N値・接合部金物 (伏図レベル %+.3fm)' % (s_label, lv),
            dxfattribs={'layer': _NV_LAYER, 'color': 1,
                        'height': text_paper_mm * n})
        st.set_placement((x_cursor, (y1 - y0)
                          + 5.0 * n * 1.5 + 3.5 * n),
                         align=TextEntityAlignment.BOTTOM_LEFT)
        if legend_pos is None:
            legend_pos = (x_cursor, -2.0 * th, th)
        x_cursor += (x1 - x0) + 0.15 * max(x1 - x0, 1000.0) + 20.0 * n
        info.append({'story': s, 'level': lv, 'scale': n})

    # 凡例 (使用ランクのみ、金物例つき)
    if legend_pos is not None:
        cnt = {}
        for r in result['columns']:
            cnt[r['grade']] = cnt.get(r['grade'], 0) + 1
        lx, ly, th = legend_pos
        row_i = 0
        for lim, sym, hw in _GRADES + [(9e9, '－', '個別検討')]:
            if sym not in cnt:
                continue
            label = '(%s) N≦%s %s ×%d本' % (
                sym, ('%.2f' % lim) if lim < 9e9 else '5.6超', hw,
                cnt[sym])
            t = msp.add_text(label, dxfattribs={
                'layer': _NV_LAYER, 'color': _NV_ACI.get(sym, 6),
                'height': th})
            t.set_placement((lx, ly - row_i * th * 1.6),
                            align=TextEntityAlignment.TOP_LEFT)
            row_i += 1

    # 伏図側の部材符号・タイトルも含め全テキストを和文対応スタイルへ
    for e in msp.query('TEXT'):
        e.dxf.style = 'MGT-JP'

    base = os.path.splitext(os.path.basename(mgt_path))[0]
    out = os.path.join(out_dir, base + '_N値伏図.dxf')
    doc.saveas(out)
    return out, info
