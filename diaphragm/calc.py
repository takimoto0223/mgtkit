# -*- coding: utf-8 -*-
"""木造水平構面 (床・屋根面) の面内せん断検定.

木合板耐力壁の検定 (plywood.py) と同じ規約で、モデル上の板要素の厚みが
床倍率として入力されている前提 (例: THICKNESS 0.00269m → 床倍率2.69)。
許容せん断耐力を

    qa = qa_base (=1.96 kN/m) × 床倍率

とし、板要素ごとに節点 (3または4点) の面内せん断力 Fxy [kN/m] の
平均をとって検定する:

    要素の検定比 = |要素の節点平均Fxy| / qa

床倍率ごとに全要素の最大検定比で判定する (節点値の平均をとる考え方は
木合板耐力壁 plywood.py と同じ。壁は壁1枚単位、水平構面は要素単位)。

対象は板要素のうち鉛直壁でないもの (要素面の法線の鉛直成分 |nz| が
NZ_MIN 以上 = 水平から60°以内の勾配面。勾配屋根の構面も含む)。
鉛直壁は木合板壁の検定 (断面検定タブ) の対象なのでここでは除外する。

新規実装 (MATLAB原典なし)。
"""

import math
import os

import numpy as np

from mgtkit.mgt import mgtopen_node, mgtopen_plate, mgtopen_thickness
from mgtkit.plywood import _load_stress
from mgtkit.ratio_pipeline import default_case_types

#: 法線の鉛直成分の下限 (これ未満は鉛直壁とみなし対象外)。
#: 0.5 = 水平から60°までの勾配面を水平構面として扱う
NZ_MIN = 0.5

#: 同一構面レベルとみなす要素重心Zの間隔 [m] (図の描き分け用)
Z_GAP = 1.2


def _normal_z(pts):
    """板要素の面法線の鉛直成分 |nz| を返す (退化要素は None)."""
    P = np.asarray(pts, dtype=float)
    c = P.mean(axis=0)
    q = P - c
    w, v = np.linalg.eigh(q.T @ q)
    if float(w[1]) <= 1e-12:          # 節点が一直線上 (退化)
        return None
    return abs(float(v[2, 0]))        # 最小固有値の固有ベクトル = 面法線


def _collect(mgt_path, stress_path):
    """mgtと応力ファイルを読み、水平構面の板要素を集める.

    戻り値: (eles, data, cases)
      eles: [{'ele','beta','nodes','pts','zc'}] (法線 |nz|>=NZ_MIN のみ)
      data: plate_stress の ndarray [要素, ケース, 節点, Fxy]
      cases: 応力ファイル中のケース番号 (昇順)
    """
    node = mgtopen_node(mgt_path)
    plate = mgtopen_plate(mgt_path)
    thick = mgtopen_thickness(mgt_path)
    if plate.size == 0:
        raise ValueError('mgtに板要素 (*ELEMENT の PLATE) がありません')
    node_xyz = {int(r[0]): np.asarray(r[1:4], dtype=float) for r in node}
    thick_map = {int(r[0]): float(r[1]) for r in np.atleast_2d(thick)} \
        if thick.size else {}

    data = _load_stress(stress_path)
    cases = sorted(set(int(v) for v in data[:, 1]))
    stressed = sorted(set(int(v) for v in data[:, 0]))

    plates = {}
    for r in np.atleast_2d(plate):
        nds = [int(x) for x in r[3:7] if int(x) > 0]
        plates[int(r[0])] = {'tid': int(r[2]), 'nodes': nds}

    missing = [e for e in stressed if e not in plates]
    if missing:
        print('注意: plate_stress の要素 %s はmgtの板要素にありません '
              '(無視します)' % missing[:10])

    eles = []
    n_wall = 0
    for e in stressed:
        if e not in plates:
            continue
        pl = plates[e]
        try:
            pts = [node_xyz[n] for n in pl['nodes']]
        except KeyError:
            print('注意: 要素 %d の節点がmgtにありません (無視します)' % e)
            continue
        nz = _normal_z(pts)
        if nz is None:
            print('注意: 要素 %d は節点が一直線上のため無視します' % e)
            continue
        if nz < NZ_MIN:
            n_wall += 1                # 鉛直壁 (木合板壁検定の対象)
            continue
        beta = thick_map.get(pl['tid'])
        if beta is None:
            print('注意: 要素 %d の厚みID %d が *THICKNESS にありません'
                  % (e, pl['tid']))
            continue
        eles.append({'ele': e, 'beta': beta, 'nodes': pl['nodes'],
                     'pts': pts,
                     'zc': float(np.mean([p[2] for p in pts]))})
    if n_wall:
        print('注意: 鉛直壁の板要素 %d 件は水平構面の対象外としました '
              '(木合板壁の検定は断面検定タブで行ってください)' % n_wall)
    if not eles:
        raise ValueError('検定対象の水平構面の板要素がありません '
                         '(鉛直壁以外の板要素が応力ファイルに必要です)')

    big = sorted(set(x['beta'] for x in eles if x['beta'] > 10))
    if big:
        print('mgt記述の注意: 厚み(=床倍率)が %s の板要素があります。'
              '「モデルの厚み=床倍率」の規約と異なる可能性があります'
              ' (そのまま倍率として検定します)'
              % ', '.join('%g' % b for b in big))
    return eles, data, cases


def read_targets(mgt_path, stress_path):
    """対象読込: 床倍率グループの一覧と荷重ケースの既定種別を返す.

    戻り値 dict:
      groups: [{'beta', 'n_eles', 'zmin', 'zmax'}] (倍率昇順)
      cases:  [{'no', 'type', 'name'}] (default_case_types による既定)
    """
    eles, _data, cases = _collect(mgt_path, stress_path)
    groups = []
    for beta in sorted(set(round(x['beta'], 4) for x in eles)):
        mem = [x for x in eles if round(x['beta'], 4) == beta]
        zs = [p[2] for x in mem for p in x['pts']]
        groups.append({'beta': beta, 'n_eles': len(mem),
                       'zmin': float(min(zs)), 'zmax': float(max(zs))})
    return {'groups': groups,
            'cases': default_case_types(np.asarray(cases, dtype=float))}


def run_check(mgt_path, stress_path, case_sel, qa_base=1.96, betas=None):
    """水平構面の検定 (要素ごとの節点平均方式).

    case_sel: 検討対象ケース [{'no': 番号, 'name': 表示名, 'type': 種別}]
    (選択分のみ)。
    betas: 検討対象とする床倍率のリスト (None = 全部。画面のチェックで
    外した床倍率は検定・検討書から除く)。
    戻り値 dict:
      cases: [{'case', 'label', 'type'}] (応力ファイルに存在した選択のみ)
      groups: [{'beta','qa','n_eles','zmin','zmax',
                'cases': [{'case','label','mean','ele','ratio','ok'}],
                'worst_ratio','worst_case','worst_label',
                'clusters': [{'zmin','zmax','polys','evals','emax'}]}]
      qa_base
    要素の検定比 = |要素の節点平均Fxy| ÷ qa。cases の各行はその床倍率・
    ケースで検定比が最大となる要素 (決定要素) の値。
    図データ (clusters) は検討書用で、JSON応答へは含めないこと。
      polys: [{'ele','xy': [[x,y],...]}]
      evals: {case: {ele: |要素の節点平均Fxy|}} (塗り色用)
      emax:  {case: {'ele','val','x','y'}} (決定要素の注記用。x,y=重心)
    """
    eles, data, cases_in = _collect(mgt_path, stress_path)
    sel = []
    seen = set()
    for c in (case_sel or []):
        no = int(float(c.get('no')))
        if no in seen:
            continue
        seen.add(no)
        if no not in cases_in:
            print('注意: 選択したケース %d は応力ファイルにありません '
                  '(無視します)' % no)
            continue
        sel.append({'case': no, 'label': str(c.get('name') or '').strip()
                    or ('ケース %d' % no),
                    'type': str(c.get('type') or '').strip()})
    if not sel:
        raise ValueError('検討対象の荷重ケースが選択されていません '
                         '(応力ファイル中のケース: %s)' % cases_in)

    # 床倍率 (板厚) の選択: チェックを外した倍率は対象外にする
    if betas is not None:
        bset = set(round(float(b), 4) for b in betas)
        skip = sorted(set(round(x['beta'], 4) for x in eles) - bset)
        eles = [x for x in eles if round(x['beta'], 4) in bset]
        if skip:
            print('注意: 床倍率 %s は選択が外れているため検討対象外と'
                  'しました' % ', '.join('%g' % b for b in skip))
        if not eles:
            raise ValueError('検討対象の床倍率が選択されていません')

    # (要素, ケース) → [(節点, Fxy), ...]
    vals = {}
    for row in data:
        vals.setdefault((int(row[0]), int(row[1])), []).append(
            (int(row[2]), float(row[3])))

    # 要素・ケースごとの節点平均Fxy (検定値。図の塗り分けにも使う)
    emean = {}
    for x in eles:
        for c in sel:
            vv = vals.get((x['ele'], c['case']), [])
            if vv:
                emean[(x['ele'], c['case'])] = \
                    float(np.mean([f for _n, f in vv]))

    groups = []
    for beta in sorted(set(round(x['beta'], 4) for x in eles)):
        mem = [x for x in eles if round(x['beta'], 4) == beta]
        qa = qa_base * beta
        zs = [p[2] for x in mem for p in x['pts']]

        # ケースごとの決定要素 (|要素平均Fxy| が最大の要素) と検定比
        crows = []
        worst = None
        for c in sel:
            best = None          # (|平均|, 符号つき平均, 要素)
            for x in mem:
                m = emean.get((x['ele'], c['case']))
                if m is None:
                    continue
                if best is None or abs(m) > best[0]:
                    best = (abs(m), m, x['ele'])
            if best is None:
                print('注意: 床倍率 %g のケース %d に応力データが'
                      'ありません' % (beta, c['case']))
                continue
            ratio = best[0] / qa if qa > 0 else float('inf')
            crows.append({'case': c['case'], 'label': c['label'],
                          'mean': best[1], 'ele': best[2],
                          'ratio': ratio, 'ok': ratio <= 1.0})
            if worst is None or ratio > worst['ratio']:
                worst = crows[-1]

        # 図用: 重心Zでレベルに分けて平面図データを組む
        clusters = []
        for x in sorted(mem, key=lambda t: t['zc']):
            if clusters and x['zc'] - clusters[-1][-1]['zc'] <= Z_GAP:
                clusters[-1].append(x)
            else:
                clusters.append([x])
        cl_out = []
        for cl in clusters:
            czs = [p[2] for x in cl for p in x['pts']]
            polys = [{'ele': x['ele'],
                      'xy': [[float(p[0]), float(p[1])] for p in x['pts']]}
                     for x in cl]
            evals = {}
            emax = {}
            for c in sel:
                ev = {}
                mx = None        # (|平均|, 符号つき平均, 要素データ)
                for x in cl:
                    m = emean.get((x['ele'], c['case']))
                    if m is None:
                        continue
                    ev[x['ele']] = abs(m)
                    if mx is None or abs(m) > mx[0]:
                        mx = (abs(m), m, x)
                if ev:
                    evals[c['case']] = ev
                if mx is not None:
                    x = mx[2]
                    emax[c['case']] = {
                        'ele': x['ele'], 'val': mx[1],
                        'x': float(np.mean([p[0] for p in x['pts']])),
                        'y': float(np.mean([p[1] for p in x['pts']]))}
            cl_out.append({'zmin': float(min(czs)), 'zmax': float(max(czs)),
                           'polys': polys, 'evals': evals, 'emax': emax})

        groups.append({'beta': beta, 'qa': qa, 'n_eles': len(mem),
                       'eles': sorted(x['ele'] for x in mem),
                       'zmin': float(min(zs)), 'zmax': float(max(zs)),
                       'cases': crows,
                       'worst_ratio': worst['ratio'] if worst else 0.0,
                       'worst_case': worst['case'] if worst else None,
                       'worst_label': worst['label'] if worst else '',
                       'clusters': cl_out})
    return {'cases': sel, 'groups': groups, 'qa_base': qa_base}


def diaphragm_csv(res, csv_path):
    """検定結果をCSV (UTF-8 BOM) に書き出す."""
    lines = ['床倍率,対象要素数,Z範囲,許容qa[kN/m],ケース,ラベル,'
             '要素平均Fxy[kN/m](決定要素),決定要素,検定比,判定']
    for g in res['groups']:
        for r in g['cases']:
            lines.append('%g,%d,%.3f〜%.3f,%.3f,%d,%s,%.3f,%d,%.3f,%s'
                         % (g['beta'], g['n_eles'], g['zmin'], g['zmax'],
                            g['qa'], r['case'], r['label'], r['mean'],
                            r['ele'], r['ratio'],
                            'OK' if r['ok'] else 'NG'))
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, 'w', encoding='utf-8-sig', newline='\r\n') as f:
        f.write('\n'.join(lines) + '\n')
    return csv_path
