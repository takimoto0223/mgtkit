# -*- coding: utf-8 -*-
"""木合板 (面材耐力壁) の検定.

モデル上の板要素の厚みが壁倍率として入力されている前提
(例: THICKNESS 0.005m → 壁倍率5) で、水平荷重時の許容耐力を

    Pa = qa_base (=1.96 kN/m) × 壁倍率 × 壁長 L

とし、plate_stress (面内せん断応力 Fxy [kN/m]) と比較する。

作用せん断力は壁 (辺を共有して連結した同一直線上・同厚の板要素群。
上下階は連結せず階ごとに別の壁として扱う) ごとに Fxy の平均値 × L で
評価するため、検定比は

    ratio = |mean Fxy| / (qa_base × 壁倍率)

となる (L は約分される)。参考として節点値の最大 |Fxy| も出力する。

新規実装 (MATLAB原典なし)。
"""

import math
import os

import numpy as np

from mgtkit.mgt import mgtopen_node, mgtopen_plate, mgtopen_thickness
from mgtkit.util import loadtxt_tolerant

XY_TOL = 0.005    # 同一直線判定 (m)
Z_TOL = 0.010     # 鉛直壁判定の最小高さ (m)
ANG_TOL = math.radians(2.0)


def parse_case_labels(text):
    """'1:長期, 11:+X, 12:+Y' 形式 → {1: '長期', 11: '+X', ...}."""
    out = {}
    if not text:
        return out
    for tok in str(text).replace('、', ',').replace(' ', ',').split(','):
        tok = tok.strip()
        if not tok or ':' not in tok:
            continue
        k, v = tok.split(':', 1)
        try:
            out[int(float(k))] = v.strip()
        except ValueError:
            continue
    return out


def _load_stress(stress_path):
    """plate_stress txt (4列: 要素, ケース, 節点, Fxy[kN/m]) を読む."""
    data = np.atleast_2d(loadtxt_tolerant(stress_path))
    if data.size == 0:
        raise ValueError('plate_stress が空です')
    if data.shape[1] < 4:
        raise ValueError('plate_stress は [要素, 荷重ケース, 節点, Fxy] の'
                         '4列が必要です (%d列でした)' % data.shape[1])
    return data[:, :4]


def _element_geo(nodes_xyz):
    """板要素の平面配置 → (鉛直壁か, 方向単位ベクトルu(2,), 代表点c(2,))."""
    xy = np.asarray([p[:2] for p in nodes_xyz], dtype=float)
    zs = [float(p[2]) for p in nodes_xyz]
    c = xy.mean(axis=0)
    q = xy - c
    w, v = np.linalg.eigh(q.T @ q)
    rms_perp = math.sqrt(max(float(w[0]), 0.0) / len(xy))
    if rms_perp > XY_TOL or (max(zs) - min(zs)) < Z_TOL:
        return False, None, None
    u = v[:, 1]
    if abs(u[0]) >= abs(u[1]):
        u = u if u[0] > 0 else -u
    else:
        u = u if u[1] > 0 else -u
    return True, u, c


def _same_line(g1, g2):
    """2要素が同一直線上の壁か (平行かつ横ずれが小さい)."""
    u1, c1 = g1
    u2, c2 = g2
    cross = abs(float(u1[0] * u2[1] - u1[1] * u2[0]))
    if cross > math.sin(ANG_TOL):
        return False
    d = c2 - c1
    perp = abs(float(d[0] * u1[1] - d[1] * u1[0]))
    return perp < 2 * XY_TOL


def plywood_check(mgt_path, stress_path, labels='', qa_base=1.96):
    """木合板耐力壁の検定.

    戻り値 dict:
      cases: [{'case': 11, 'label': '+X'}, ...]
      walls: [{'name','loc','range','L','H','beta','qa',
               'cases': [{'case','label','mean','amax','ratio','ok'}],
               'worst_ratio','worst_case','eles'}]
    """
    node = mgtopen_node(mgt_path)
    plate = mgtopen_plate(mgt_path)
    thick = mgtopen_thickness(mgt_path)
    if plate.size == 0:
        raise ValueError('mgtに板要素 (*ELEMENT の PLATE) がありません')
    node_xyz = {int(r[0]): np.asarray(r[1:4], dtype=float) for r in node}
    thick_map = {int(r[0]): float(r[1]) for r in np.atleast_2d(thick)} \
        if thick.size else {}
    lab = parse_case_labels(labels)

    data = _load_stress(stress_path)
    cases = sorted(set(int(v) for v in data[:, 1]))
    stressed = sorted(set(int(v) for v in data[:, 0]))

    plates = {}
    for r in np.atleast_2d(plate):
        ele = int(r[0])
        nds = [int(x) for x in r[3:7] if int(x) > 0]
        plates[ele] = {'tid': int(r[2]), 'nodes': nds}

    missing = [e for e in stressed if e not in plates]
    if missing:
        print('注意: plate_stress の要素 %s はmgtの板要素にありません '
              '(無視します)' % missing[:10])

    # 鉛直壁要素のみ対象
    eles = []
    skipped_flat = []
    for e in stressed:
        if e not in plates:
            continue
        pl = plates[e]
        try:
            pts = [node_xyz[n] for n in pl['nodes']]
        except KeyError:
            print('注意: 要素 %d の節点がmgtにありません (無視します)' % e)
            continue
        ok, u, c = _element_geo(pts)
        if not ok:
            skipped_flat.append(e)
            continue
        beta = thick_map.get(pl['tid'])
        if beta is None:
            print('注意: 要素 %d の厚みID %d が *THICKNESS にありません'
                  % (e, pl['tid']))
            continue
        zs = [float(pt[2]) for pt in pts]
        eles.append({'ele': e, 'tid': pl['tid'], 'beta': beta,
                     'nodes': pl['nodes'], 'u': u, 'c': c,
                     'pts': pts, 'zmin': min(zs), 'zmax': max(zs)})
    if skipped_flat:
        print('注意: 水平な板要素 (床・屋根など) %d 件は木合板検定の対象外'
              'としました: %s' % (len(skipped_flat), skipped_flat[:10]))
    if not eles:
        raise ValueError('検定対象の鉛直板要素がありません')

    big = sorted(set(x['beta'] for x in eles if x['beta'] > 10))
    if big:
        print('mgt記述の注意: 厚み(=壁倍率)が %s の板要素があります。'
              '「モデルの厚み=壁倍率」の規約と異なる可能性があります'
              ' (そのまま倍率として検定します)'
              % ', '.join('%g' % b for b in big))

    # 壁グループ化 (辺共有 + 同倍率 + 同一直線)
    parent = list(range(len(eles)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    node_map = {}
    for i, x in enumerate(eles):
        for n in x['nodes']:
            node_map.setdefault(n, []).append(i)
    for i, x in enumerate(eles):
        for j in set(k for n in x['nodes'] for k in node_map[n] if k > i):
            y = eles[j]
            if len(set(x['nodes']) & set(y['nodes'])) < 2:
                continue
            if x['tid'] != y['tid']:
                continue
            if not _same_line((x['u'], x['c']), (y['u'], y['c'])):
                continue
            # 上下階 (z範囲が重ならない) は連結しない: 階ごとに検定する
            if (min(x['zmax'], y['zmax'])
                    - max(x['zmin'], y['zmin'])) < Z_TOL:
                continue
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[rj] = ri

    groups = {}
    for i in range(len(eles)):
        groups.setdefault(find(i), []).append(i)

    # ケース×要素 → 節点Fxy値
    vals = {}
    for row in data:
        vals.setdefault((int(row[0]), int(row[1])), []).append(float(row[3]))

    walls = []
    for idxs in groups.values():
        mem = [eles[i] for i in idxs]
        pts = [p for x in mem for p in x['pts']]
        xy = np.asarray([p[:2] for p in pts])
        zs = [float(p[2]) for p in pts]
        c = xy.mean(axis=0)
        q = xy - c
        w, v = np.linalg.eigh(q.T @ q)
        u = v[:, 1]
        s = q @ u
        L = float(s.max() - s.min())
        H = float(max(zs) - min(zs))
        beta = mem[0]['beta']
        qa = qa_base * beta
        xr = (min(p[0] for p in pts), max(p[0] for p in pts))
        yr = (min(p[1] for p in pts), max(p[1] for p in pts))
        if abs(u[1]) < 0.05:         # X方向の壁 (Y=一定)
            loc = 'Y=%.3f' % c[1]
            rng = 'X %.3f〜%.3f' % xr
            skey = (1, round(c[1], 3), xr[0], min(zs))
        elif abs(u[0]) < 0.05:       # Y方向の壁 (X=一定)
            loc = 'X=%.3f' % c[0]
            rng = 'Y %.3f〜%.3f' % yr
            skey = (0, round(c[0], 3), yr[0], min(zs))
        else:                        # 斜め壁
            loc = '斜め%.0f度' \
                % (math.degrees(math.atan2(u[1], u[0])) % 180.0)
            rng = 'X %.3f〜%.3f / Y %.3f〜%.3f' % (xr + yr)
            skey = (2, xr[0], yr[0], min(zs))
        rows = []
        worst = None
        for cs in cases:
            vv = []
            for x in mem:
                vv.extend(vals.get((x['ele'], cs), []))
            if not vv:
                continue
            mean = float(np.mean(vv))
            amax = float(np.max(np.abs(vv)))
            ratio = abs(mean) / qa if qa > 0 else float('inf')
            rows.append({'case': cs, 'label': lab.get(cs, ''),
                         'mean': mean, 'amax': amax,
                         'ratio': ratio, 'ok': ratio <= 1.0})
            if worst is None or ratio > worst[1]:
                worst = (cs, ratio)
        walls.append({'loc': loc, 'range': rng,
                      'zrange': '%.3f〜%.3f' % (min(zs), max(zs)),
                      'L': L, 'H': H, 'beta': beta, 'qa': qa,
                      'eles': sorted(x['ele'] for x in mem),
                      'cases': rows,
                      'worst_ratio': worst[1] if worst else 0.0,
                      'worst_case': worst[0] if worst else None,
                      '_skey': skey})
    walls.sort(key=lambda wl: wl['_skey'])
    for i, wl in enumerate(walls):
        wl['name'] = 'W%02d' % (i + 1)
        del wl['_skey']
    return {'cases': [{'case': cs, 'label': lab.get(cs, '')}
                      for cs in cases],
            'walls': walls, 'qa_base': qa_base}


def plywood_csv(res, csv_path):
    """検定結果をCSV (UTF-8 BOM) に書き出す."""
    lines = ['壁,位置,範囲,Z範囲,L[m],壁倍率,許容qa[kN/m],要素,'
             'ケース,ラベル,平均Fxy[kN/m],最大|Fxy|[kN/m],検定比,判定']
    for wl in res['walls']:
        for r in wl['cases']:
            lines.append(
                '%s,%s,%s,%s,%.3f,%g,%.3f,%s,%d,%s,%.3f,%.3f,%.3f,%s'
                % (wl['name'], wl['loc'], wl['range'], wl['zrange'],
                   wl['L'], wl['beta'], wl['qa'],
                   ' '.join(str(e) for e in wl['eles']),
                   r['case'], r['label'], r['mean'], r['amax'],
                   r['ratio'], 'OK' if r['ok'] else 'NG'))
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, 'w', encoding='utf-8-sig', newline='\r\n') as f:
        f.write('\n'.join(lines) + '\n')
    return csv_path
