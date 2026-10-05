# -*- coding: utf-8 -*-
"""RC規準2010による定着(17条)・付着(16条)・柱梁接合部(15条3項)の検定.

準拠 (式番号はすべて日本建築学会「鉄筋コンクリート構造計算規準・同解説
2010」による):

定着 (17条):
    la ≥ lab                                  (17.1)
    lab = α・S・σt・db / (10・fb)               (17.2)
      fb: 表16.1「その他の鉄筋」欄 (普通コン: Fc/40+0.9、軽量は0.8倍)
      σt: 仕口面の鉄筋応力度。原則は短期許容応力度 ft
      α : 横補強筋で拘束されたコア内定着 1.0 / それ以外 1.25
      S : 表17.1 (耐震部材: 直線定着1.25 / 標準フック・機械式0.7)
    構造規定 17条1.(5):
      1) 直線定着長さは原則300mm以上
      2) 折曲げ定着の投影定着長さは原則 8db かつ 150mm 以上
      3) 梁主筋の柱への折曲げ定着の投影定着長さは仕口部材全せいの
         0.75倍以上を基本 (ただし書きあり)
    通し配筋 (17条1.(4)):
      db/D ≤ 3.6・(1.5+0.1Fc)/ft               (17.3)
    併記: 平23国交告432号
      l ≥ k・σ・d/(F/4+9)  k=1.57 (軽量1.96)
      σ: 短期応力度 (短期許容応力度未満の場合は許容応力度)

付着 (16条):
    長期 (使用性):  τa1 = QL/(Σψ・j) ≤ Lfa      (16.1)
    短期 (損傷制御): τa1 = (QL+QE)/(Σψ・j) ≤ sfa  (16.3)
    安全性 (大地震): τy = σy・db/(4(ld−d)) ≤ K・fb  (16.5)
      K = 0.3(C+W)/db + 0.4 ≤ 2.5              (16.6)
      W = 80・Ast/(s・N) ≤ 2.5db                (16.7)
      C = min(鉄筋間のあき, 3×最小かぶり厚さ) ≤ 5db
      fb: 表16.1 (上端筋 0.8×(Fc/40+0.9) / その他 Fc/40+0.9)
      付着長さ ld (通し筋): 両端曲げ降伏 (L+d)/2、それ以外 L (16条1.(3)2))
      許容付着応力度 fa: 表6.3 (長期 上端 min(Fc/15, 0.9+2Fc/75)、
        その他 min(Fc/10, 1.35+Fc/25)。短期は長期の1.5倍)

柱梁接合部 (15条3項):
    QAj = κA・(fs−0.5)・bj・D                    (15.10)
      κA = 10(十字形) / 7(T形) / 5(ト形) / 3(L形)
      bj = bb + ba1 + ba2、bai = min(bi/2, D/4)
      fs: コンクリートの短期許容せん断応力度 (表6.1)
    QDj = Σ(My/j)・(1−ξ)                        (15.11)
      ξ = j / (Hc・(1−D/Lb))                    (15.13)
      My: 梁の降伏曲げ (My = 0.9・at・σy・d と仮定。σy=鉄筋規格降伏点)
      Hc: 上下柱の平均高さ (最上階は柱高の1/2)、Lb: 左右梁の平均長さ
    接合部帯筋の細則 15条3.(4): D10以上 / 帯筋比0.2%以上 / 間隔150mm以下

モデル解釈の仮定 (検討書にも明記する):
  ・柱断面 (SB: H×B) の向きは β角で判定 (β≈0: HがX方向 / β≈90: HがY方向)。
    MIDASの鉛直材は局所z軸が全体X方向 (β=0) となる規約による。
  ・接合部形式は節点まわりの柱・梁の接続から自動判定 (設計者が確認する)。
  ・応力ファイルの符号は既存タブと同じ (Q=Fz, M=My)。
"""

import math

import numpy as np

from mgtkit.mgt import (mgtopen_beam, mgtopen_node, mgtopen_material,
                        mgtopen_RCbeam, mgtopen_RCcolumn, mgtopen_plate,
                        _lines, _scan_section, _tlinenum, _field,
                        _str2double)
from mgtkit.section import mgtopen_section
from mgtkit.draw_model import column_beam_judge_one
from mgtkit.draw_stress import read_beam_stress
from mgtkit.rc_check import (Area_steelbar, ALST_steelbar_KJ, _bar_table)

#: 接合部形式 → (κA, 表示名)
FORMS = {'cross': (10.0, '十字形'), 'tee': (7.0, 'T形'),
         'knee': (5.0, 'ト形'), 'ell': (3.0, 'L形')}

#: 折曲げ定着の投影定着長さの基本値 (17条1.(5)3): 仕口部材全せいの0.75倍)
PROJ_RATIO = 0.75

#: 告示432号の係数 k (普通コンクリート)
K432 = 1.57


# ---------------------------------------------------------------------------
# 許容応力度 (RC規準2010 6条・16条表16.1)
# ---------------------------------------------------------------------------

def fs_short(fc):
    """コンクリートの短期許容せん断応力度 (表6.1: 長期の1.5倍)."""
    return 1.5 * min(fc / 30.0, 0.49 + fc / 100.0)


def fa_bond(fc, top, short):
    """許容付着応力度 (表6.3)。top=True で上端筋。短期は長期の1.5倍."""
    if top:
        v = min(fc / 15.0, 0.9 + 2.0 * fc / 75.0)
    else:
        v = min(fc / 10.0, 1.35 + fc / 25.0)
    return v * (1.5 if short else 1.0)


def fb_161(fc, top=False):
    """付着割裂の基準となる強度 fb (表16.1、普通コンクリート)."""
    v = fc / 40.0 + 0.9
    return 0.8 * v if top else v


def sd_of(di):
    """鉄筋径から材種を自動決定する (rc_check.py と同じ規約)."""
    if di > 28:
        return 390
    if di > 18:
        return 345
    return 295


def ft_short(di, sd):
    """鉄筋の短期許容引張応力度 (表6.2)."""
    return float(ALST_steelbar_KJ([di, sd])[1, 0])


_PERI = None


def perimeter(di):
    """異形鉄筋1本の公称周長 [mm] (bar_table.json の col3 1本値)."""
    global _PERI
    if _PERI is None:
        import json
        import os
        import mgtkit.rc_check as _rc
        path = os.path.join(os.path.dirname(_rc.__file__),
                            'data', 'bar_table.json')
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        _PERI = {int(k): float(v[0])
                 for k, v in zip(d['diameters'], d['col3'])}
    return _PERI[int(di)]


# ---------------------------------------------------------------------------
# 荷重ケースの組合せ (断面算定・jointchk と同じ L/H/S/M 指定)
# ---------------------------------------------------------------------------

def build_cases(case_types):
    """case_types: [{'no','type','name'}] → 検定ケース list.

    戻り値: [{'name','term'('long'/'short'),'combo':[(ケース番号,係数),..]}]
    """
    L = [(c['no'], c.get('name') or 'C%g' % c['no'])
         for c in case_types if c['type'] == 'L']
    H = [(c['no'], c.get('name') or 'C%g' % c['no'])
         for c in case_types if c['type'] == 'H']
    S = [(c['no'], c.get('name') or 'C%g' % c['no'])
         for c in case_types if c['type'] in ('S', 'M')]
    if not L:
        raise ValueError('長期(L)の荷重ケースが指定されていません。')
    cases = []
    for lno, lname in L:
        cases.append({'name': lname, 'term': 'long', 'combo': [(lno, 1.0)]})
    for lno, lname in L:
        for hno, hname in H:
            cases.append({'name': lname + '+' + hname, 'term': 'short',
                          'combo': [(lno, 1.0), (hno, 1.0)]})
            cases.append({'name': lname + '-' + hname, 'term': 'short',
                          'combo': [(lno, 1.0), (hno, -1.0)]})
    for sno, sname in S:
        cases.append({'name': sname, 'term': 'short', 'combo': [(sno, 1.0)]})
    return cases


def _forces_by_case(beam_stress, eles):
    """{ケース番号: {要素番号: (Qi, Mi, Qj, Mj)}} を返す (kN, kNm).

    Q=|Fz| は i端・j端行の値、M=My は i端・j端行の値 (符号つき)。
    """
    forces = {}
    eles = set(int(e) for e in eles)
    for cno in np.unique(np.asarray(beam_stress[:, 1], dtype=float)):
        block = beam_stress[beam_stress[:, 1] == cno]
        d = {}
        for ele in eles:
            rows = block[block[:, 0] == ele]
            if rows.shape[0] == 0:
                continue
            d[ele] = (float(rows[0, 4]), float(rows[0, 6]),
                      float(rows[-1, 4]), float(rows[-1, 6]))
        forces[float(cno)] = d
    return forces


def _combo_forces(forces, combo, ele):
    """組合せ後の (Qi, Mi, Qj, Mj)。ケース・要素が無ければ None."""
    out = [0.0, 0.0, 0.0, 0.0]
    for cno, fac in combo:
        d = forces.get(float(cno))
        if d is None or ele not in d:
            return None
        f = d[ele]
        for k in range(4):
            out[k] += fac * f[k]
    return out


# ---------------------------------------------------------------------------
# モデルの読み取りと接合部のトポロジー
# ---------------------------------------------------------------------------

def _rebar_rows(rcbeams, sec_no):
    """*REBAR-BEAM の該当断面の 3x17 行列 (無ければ None)."""
    for row in rcbeams:
        if int(row[0]) == int(sec_no):
            return np.asarray(row[1], dtype=float)
    return None


def _beam_layers(mat_row, top):
    """配筋行 (17列) から (本数list, 径, 段数) を返す。top=True で上端."""
    o = 0 if top else 7
    n1, n2 = float(mat_row[1 + o]), float(mat_row[2 + o])
    di = float(mat_row[4 + o])
    layers = [n1] + ([n2] if n2 > 0 else [])
    return layers, di, len(layers)


def _sec_dims(sections, sec_no):
    """SB(中実角)断面の (H, B) [mm]。SB以外は None."""
    sb = np.atleast_2d(np.asarray(sections[2], dtype=float)) \
        if sections[2] is not None and len(sections[2]) else None
    if sb is None:
        return None
    hit = sb[sb[:, 0] == sec_no]
    if hit.shape[0] == 0:
        return None
    return float(hit[0, 1]) * 1000.0, float(hit[0, 2]) * 1000.0


def _col_dims_xy(sections, sec_no, beta):
    """柱断面のX方向せい・Y方向せい (Dx, Dy) [mm]。

    MIDASの鉛直材は β=0 で断面H (局所z) が全体X方向を向く規約とし、
    β≈90° では H が Y方向。斜めβは近い方に丸めて注記対象とする。
    """
    dims = _sec_dims(sections, sec_no)
    if dims is None:
        return None
    H, B = dims
    b = abs(float(beta)) % 180.0
    if 45.0 <= b < 135.0:
        return B, H          # H が Y方向
    return H, B              # H が X方向


def read_model(mgt_path, notes=None, form_secs=None):
    """mgt を読み、検定対象 (RC梁・柱梁接合部) を組み立てる.

    form_secs: 接合部の形式判定 (十字/T/ト/L) に算入する梁の断面番号 list。
      None のときは *REBAR-BEAM に配筋がある断面のみ (従来どおり)。
      配筋の無い水平RC梁 (SB断面) も beams には含まれる (rebar=None)。

    戻り値 dict:
      beams: {要素番号: {'sec','name','b','D','ni','nj','len',
                         'rebar'(3x17 or None)}}
      joints: [{'node','dir','form','col_sec','Dc','bc','beta',
                'col_above','col_below','hc_above','hc_below',
                'plus': 要素番号 or None, 'minus': 要素番号 or None}]
      Fc: {材料番号: Fc値}, ele_mat: {要素番号: 材料番号}
      rc_cols: mgtopen_RCcolumn の dict {断面番号: row}
    """
    notes = notes if notes is not None else []
    node = np.atleast_2d(mgtopen_node(mgt_path))
    beam = np.atleast_2d(mgtopen_beam(mgt_path))
    element = beam[:, :5]
    sections, section_no, section_name = mgtopen_section(mgt_path)
    material = np.atleast_2d(mgtopen_material(mgt_path))
    rcbeams = mgtopen_RCbeam(mgt_path)
    rccol = mgtopen_RCcolumn(mgt_path)

    if not len(rcbeams):
        raise ValueError('*REBAR-BEAM (梁配筋) が mgt にありません。'
                         'MIDASで配筋データを書き出してください。')

    node_xyz = {int(r[0]): (float(r[1]), float(r[2]), float(r[3]))
                for r in node}
    sec_name = {int(n): section_name[i]
                for i, n in enumerate(np.asarray(section_no).ravel())}
    mat_fc = {}
    for r in material:
        if int(r[1]) == 3:                      # RC
            mat_fc[int(r[0])] = float(r[2])

    rc_cols = {int(r[0]): np.asarray(r, dtype=float)
               for r in np.atleast_2d(rccol)} if len(rccol) else {}

    beams = {}
    columns = {}                                # 要素番号 → 諸元
    for i, r in enumerate(beam):
        ele, mat, sec, ni, nj = (int(r[0]), int(r[1]), int(r[2]),
                                 int(r[3]), int(r[4]))
        beta = float(r[5]) if beam.shape[1] > 5 else 0.0
        if ni not in node_xyz or nj not in node_xyz:
            continue
        kind = column_beam_judge_one(i, element, node)
        xi, yi, zi = node_xyz[ni]
        xj, yj, zj = node_xyz[nj]
        length = math.dist((xi, yi, zi), (xj, yj, zj)) * 1000.0  # mm
        if kind == 2.0:                          # 柱
            lo, hi = (ni, nj) if zi < zj else (nj, ni)
            columns[ele] = {'sec': sec, 'mat': mat, 'beta': beta,
                            'n_lo': lo, 'n_hi': hi, 'h': length}
        elif kind == 1.0 and mat in mat_fc:      # 水平RC梁
            rb = _rebar_rows(rcbeams, sec)
            dims = _sec_dims(sections, sec)
            if dims is None:                     # SB以外は対象外
                continue
            dx, dy = xj - xi, yj - yi
            axis = 'X' if abs(dx) >= abs(dy) else 'Y'
            beams[ele] = {'sec': sec, 'name': sec_name.get(sec, str(sec)),
                          'mat': mat, 'D': dims[0], 'b': dims[1],
                          'ni': ni, 'nj': nj, 'len': length,
                          'axis': axis, 'rebar': rb}

    # 節点 → 上下柱・四方の梁
    col_top = {}                                 # 節点 → 直下柱の要素番号
    col_bot = {}                                 # 節点 → 直上柱の要素番号
    for ele, c in columns.items():
        col_top[c['n_hi']] = ele
        col_bot[c['n_lo']] = ele

    # 形式判定に算入する断面: 指定が無ければ配筋あり断面のみ
    if form_secs is not None:
        form_set = set(int(s) for s in form_secs)
    else:
        form_set = set(bm['sec'] for bm in beams.values()
                       if bm['rebar'] is not None)

    joints = []
    for n in sorted(set(col_top) | set(col_bot)):
        below = col_top.get(n)
        above = col_bot.get(n)
        if below is None:                        # 柱脚 (基礎) は対象外
            continue
        for axis in ('X', 'Y'):
            plus = minus = None
            for ele, bm in beams.items():
                if bm['axis'] != axis or n not in (bm['ni'], bm['nj']) \
                        or bm['sec'] not in form_set:
                    continue
                xi = node_xyz[bm['ni']]
                xj = node_xyz[bm['nj']]
                far = xj if bm['ni'] == n else xi
                here = node_xyz[n]
                d = (far[0] - here[0]) if axis == 'X' else (far[1] - here[1])
                if d >= 0:
                    if plus is not None:
                        notes.append('節点%d %s方向: 同方向に複数の梁。要素%d'
                                     'を採用し%dを無視しました。'
                                     % (n, axis, plus, ele))
                        continue
                    plus = ele
                else:
                    if minus is not None:
                        notes.append('節点%d %s方向: 同方向に複数の梁。要素%d'
                                     'を採用し%dを無視しました。'
                                     % (n, axis, minus, ele))
                        continue
                    minus = ele
            if plus is None and minus is None:
                continue
            c = columns[below]
            dims = _col_dims_xy(sections, c['sec'], c['beta'])
            if dims is None:
                notes.append('節点%d: 柱断面 %s がSB(中実角)でないため'
                             '接合部検定をスキップしました。'
                             % (n, sec_name.get(c['sec'], c['sec'])))
                continue
            dc = dims[0] if axis == 'X' else dims[1]
            bc = dims[1] if axis == 'X' else dims[0]
            both = plus is not None and minus is not None
            form = ('cross' if both else 'knee') if above is not None \
                else ('tee' if both else 'ell')
            joints.append({
                'node': n, 'dir': axis, 'form': form,
                'col_ele': below, 'col_sec': c['sec'],
                'col_name': sec_name.get(c['sec'], str(c['sec'])),
                'col_mat': c['mat'], 'beta': c['beta'],
                'Dc': dc, 'bc': bc,
                'hc_below': c['h'],
                'hc_above': columns[above]['h'] if above is not None else None,
                'plus': plus, 'minus': minus})
    return {'beams': beams, 'joints': joints, 'Fc': mat_fc,
            'rc_cols': rc_cols, 'sec_name': sec_name,
            'node_xyz': node_xyz, 'columns': columns,
            'sections': sections}


# ---------------------------------------------------------------------------
# 個別の検定
# ---------------------------------------------------------------------------

def _dt_eff(cover, hoop_di, layers, di):
    """引張縁から鉄筋群重心までの距離 dt [mm] (2段は重心平均)."""
    d1 = cover + hoop_di + di / 2.0
    if len(layers) < 2 or layers[1] <= 0:
        return d1
    gap = max(25.0, 1.5 * di)                    # 段間あき (14条(4)準用)
    d2 = d1 + di + gap
    n1, n2 = layers[0], layers[1]
    return (d1 * n1 + d2 * n2) / (n1 + n2)


def _clear_spacing(b, cover, hoop_di, n1, di):
    """1段目鉄筋のあき [mm] (等間隔とみなす)."""
    if n1 <= 1:
        return 1e9
    return (b - 2.0 * (cover + hoop_di) - n1 * di) / (n1 - 1.0)


def check_anchor(joint, bm, side, fc, params):
    """定着の検定 (ト形/L形の外端側)。上端・下端筋の2行を返す."""
    p = params
    dc = joint['Dc']
    la = dc - p['anchor_offset']
    rows = []
    mat = bm['rebar'][0 if side == 'i' else 2]   # 端部の配筋行
    for top in (True, False):
        layers, di, ndan = _beam_layers(mat, top)
        if di <= 0 or not layers or layers[0] <= 0:
            continue
        sd = sd_of(di)
        ft = ft_short(di, sd)
        fb = fb_161(fc, top=False)               # 17条は「その他の鉄筋」欄
        sigma = ft                               # 原則: 短期許容応力度
        lab = p['alpha'] * p['S'] * sigma * di / (10.0 * fb)
        # 2段配筋は内側段筋の投影定着長さが短くなるため、それぞれ検定する
        # (17条解説: 多段配筋でも fb に0.6を乗じる必要はない)。短縮量は
        # 既定で 主筋径+段あき (max(25,1.5db))、UIから実況値に変更できる
        if ndan >= 2:
            off2 = float(p.get('anchor_offset2', -1.0))
            if off2 < 0:
                off2 = di + max(25.0, 1.5 * di)
            la2 = la - off2
        else:
            la2 = None
        l432 = K432 * max(sigma, ft) * di / (fc / 4.0 + 9.0)
        proj = PROJ_RATIO * dc
        rows.append({
            'pos': '上端' if top else '下端', 'di': int(di), 'sd': sd,
            'n': sum(layers), 'sigma': sigma, 'fb': fb,
            'lab': lab, 'la2': la2, 'l432': l432, 'la': la, 'proj': proj,
            'ok_rc': la >= lab and (la2 is None or la2 >= lab),
            'ok_432': la >= l432 and (la2 is None or la2 >= l432),
            'ok_proj': la >= proj,
            'ok_min': la >= max(8.0 * di, 150.0)})
    return rows


def check_through(joint, bm, fc):
    """通し配筋の径の検定 (17.3式)。十字形・T形で適用."""
    rows = []
    mat = bm['rebar'][0]
    for top in (True, False):
        layers, di, _ = _beam_layers(mat, top)
        if di <= 0:
            continue
        sd = sd_of(di)
        ft = ft_short(di, sd)
        lim = 3.6 * (1.5 + 0.1 * fc) / ft * joint['Dc']
        rows.append({'pos': '上端' if top else '下端', 'di': int(di),
                     'sd': sd, 'limit': lim, 'ok': di <= lim})
    return rows


def check_bond(bm, fc, q_long, q_short, ld_len, params):
    """付着の検定 (16.1/16.3/16.5)。上端・下端筋の2行を返す."""
    p = params
    D, b = bm['D'], bm['b']
    cover, hoop_di = p['beam_cover'], float(bm['rebar'][0][14])
    hoop_pitch = float(bm['rebar'][0][15])
    hoop_n = float(bm['rebar'][0][16])
    rows = []
    for top in (True, False):
        mat = bm['rebar'][0]                     # i端行 (端部配筋)
        layers, di, ndan = _beam_layers(mat, top)
        if di <= 0 or not layers or layers[0] <= 0:
            continue
        n_all = sum(layers)
        psi = perimeter(di) * n_all
        dt = _dt_eff(cover, hoop_di, layers, di)
        d_eff = D - dt
        j = 7.0 / 8.0 * d_eff
        tau_l = q_long * 1000.0 / (psi * j)
        tau_s = q_short * 1000.0 / (psi * j)
        fa_l = fa_bond(fc, top, short=False)
        fa_s = fa_bond(fc, top, short=True)
        # (16.5) 安全性 (省略指定時は None を返す)
        if p.get('do_bond_safety', True):
            sd = sd_of(di)
            sigma_y = float(sd)
            ld = ld_len if p['ld_mode'] == 'L' else (ld_len + d_eff) / 2.0
            denom = 4.0 * max(ld - d_eff, 1.0)
            tau_y = sigma_y * di / denom
            c_val = min(_clear_spacing(b, cover, hoop_di, layers[0], di),
                        3.0 * cover)
            c_val = min(c_val, 5.0 * di)
            ast = hoop_n * Area_steelbar(hoop_di, 1) if hoop_di > 0 \
                else 0.0
            w = min(80.0 * ast / (hoop_pitch * layers[0]), 2.5 * di) \
                if hoop_pitch > 0 else 0.0
            kk = min(0.3 * (c_val + w) / di + 0.4, 2.5)
            fb = fb_161(fc, top=top)
            ok_y = tau_y <= kk * fb
            kfb = kk * fb
        else:
            ld = tau_y = kk = fb = kfb = None
            ok_y = None
        rows.append({
            'pos': '上端' if top else '下端', 'di': int(di), 'n': n_all,
            'ndan': ndan, 'psi': psi, 'd': d_eff, 'j': j,
            'tau_l': tau_l, 'fa_l': fa_l, 'ok_l': tau_l <= fa_l,
            'tau_s': tau_s, 'fa_s': fa_s, 'ok_s': tau_s <= fa_s,
            'ld': ld, 'tau_y': tau_y, 'K': kk, 'fb': fb,
            'kfb': kfb, 'ok_y': ok_y})
    return rows


def _my_beam(bm, fc, top, cover):
    """梁の降伏曲げ My = 0.9・at・σy・d [kNm].

    配筋の無い梁 (形式判定にのみ算入) は My=0 とし、j は d≒D−60mm で
    近似する (ξ・j平均の算定用)。
    """
    if bm['rebar'] is None:
        return 0.0, 7.0 / 8.0 * (bm['D'] - 60.0)
    mat = bm['rebar'][0]
    layers, di, _ = _beam_layers(mat, top)
    if di <= 0 or not layers:
        return 0.0, 0.0
    at = Area_steelbar(di, 1) * sum(layers)
    sd = sd_of(di)
    hoop_di = float(mat[14])
    dt = _dt_eff(cover, hoop_di, layers, di)
    d_eff = bm['D'] - dt
    my = 0.9 * at * float(sd) * d_eff / 1.0e6    # kNm
    return my, 7.0 / 8.0 * d_eff


def check_joint(joint, beams, fc, params):
    """柱梁接合部の検定 (15.10〜15.13)."""
    p = params
    dc, bc = joint['Dc'], joint['bc']
    kappa, form_name = FORMS[joint['form']]
    fs = fs_short(fc)
    bms = [beams[e] for e in (joint['plus'], joint['minus'])
           if e is not None]
    bb = max(bm['b'] for bm in bms)
    bi2 = max((bc - bb) / 2.0, 0.0)
    bj = min(bb + 2.0 * min(bi2 / 2.0, dc / 4.0), bc)
    qaj = kappa * (fs - 0.5) * bj * dc / 1000.0  # kN

    # QDj = Σ(My/j)(1−ξ): 一方上端引張・他方下端引張の大きい方
    my_j = []
    for bm in bms:
        my_t, j_t = _my_beam(bm, fc, True, p['beam_cover'])
        my_b, j_b = _my_beam(bm, fc, False, p['beam_cover'])
        my_j.append((my_t * 1000.0 / j_t if j_t > 0 else 0.0,
                     my_b * 1000.0 / j_b if j_b > 0 else 0.0,
                     (j_t + j_b) / 2.0))
    if len(my_j) == 2:
        sum_myj = max(my_j[0][0] + my_j[1][1], my_j[0][1] + my_j[1][0])
    else:
        sum_myj = max(my_j[0][0], my_j[0][1])
    j_avg = sum(m[2] for m in my_j) / len(my_j)

    # ξ = j/(Hc(1−D/Lb))
    if joint['hc_above'] is not None:
        hc = (joint['hc_below'] + joint['hc_above']) / 2.0
    else:
        hc = joint['hc_below'] / 2.0
    lb = sum(beams[e]['len'] for e in (joint['plus'], joint['minus'])
             if e is not None) / len(bms)
    xi = j_avg / (hc * (1.0 - dc / lb)) if lb > dc else 1.0
    xi = min(max(xi, 0.0), 1.0)
    qdj = sum_myj * (1.0 - xi)

    return {'form': form_name, 'kappa': kappa, 'fs': fs,
            'bb': bb, 'bj': bj, 'Dc': dc, 'bc': bc,
            'QAj': qaj, 'sum_myj': sum_myj, 'j': j_avg,
            'Hc': hc, 'Lb': lb, 'xi': xi, 'QDj': qdj,
            'ratio': qdj / qaj if qaj > 0 else float('inf'),
            'ok': qdj <= qaj}


def joint_hoop_check(joint, rc_cols, bc, dc, factor=1.5):
    """接合部内帯筋の細則 (15条3.(4))。柱配筋が無ければ None.

    factor: 接合部内の帯筋間隔 = 柱の帯筋間隔 (MIDAS入力) × factor。
    既定1.5は細則iii)「隣接する柱の帯筋間隔の1.5倍以下」の上限に対応。
    """
    row = rc_cols.get(int(joint['col_sec']))
    if row is None:
        return None
    hoop_di = float(row[6])
    pitch_col = float(row[7])
    n_leg = max(float(row[8]), float(row[9]))
    if hoop_di <= 0 or pitch_col <= 0:
        return None
    pitch = pitch_col * float(factor)
    aw = Area_steelbar(hoop_di, 1) * n_leg
    pw = aw / (bc * pitch)
    return {'hoop_di': int(hoop_di), 'pitch': pitch,
            'pitch_col': pitch_col, 'n_leg': n_leg,
            'pw': pw * 100.0,
            'ok': (hoop_di >= 10 and pw >= 0.002 and pitch <= 150.0
                   and pitch <= 1.5 * pitch_col)}


# ---------------------------------------------------------------------------
# 耐震壁周辺柱の断面 (RC規準2010 19条解説 p.318-320)
#   断面積 ≥ s・t'/2、最小径 ≥ √(s・t'/3) かつ 2t'
#   s : 壁部材の高さ (部材長さ) と壁長さ (部材幅) の短いほう
#   t': 設計用せん断力 QD に対して必要な壁板の最小壁厚 (解19.58式)。
#       せん断補強筋比 ps' が上限値0.012となるときの壁厚として
#         t' = wr・QD / (0.012・le・ft)   (le=壁長さ)
#       で算定する (wr: 壁板の負担率 Qw/(Qw+ΣQc)。既定1.0=全負担)。
#   耐震壁は「断面名が接頭辞 (既定 EW) で始まるRC鉛直要素」として拾う。
# ---------------------------------------------------------------------------

#: 枠柱・壁端の一致とみなす水平距離 [mm]
_WALL_END_TOL = 300.0

#: せん断補強筋比の上限値 (解19.58: ps' = 0.012)
PS_DASH = 0.012


#: 枠柱として意味のある最小径 [mm] (これ未満はダミー断面とみなす)
_COL_MIN_DIM = 50.0


def _col_sec_props(sections, sec_no):
    """柱断面の (d1, d2, A, dmin)。SB(中実角)とSR(中実丸)に対応.

    SB: d1=H, d2=B, A=H・B。SR: d1=d2=D, A=π/4・D^2。他は None。
    """
    dims = _sec_dims(sections, sec_no)
    if dims is not None:
        h, b = dims
        return h, b, h * b, min(h, b)
    sr = np.atleast_2d(np.asarray(sections[3], dtype=float)) \
        if sections[3] is not None and len(sections[3]) else None
    if sr is not None:
        hit = sr[sr[:, 0] == sec_no]
        if hit.shape[0]:
            d = float(hit[0, 1]) * 1000.0
            return d, d, math.pi / 4.0 * d * d, d
    return None


def wall_requirements(s, tp):
    """必要断面積 [mm2] と必要最小径 [mm] を返す."""
    a_req = s * tp / 2.0
    d_req = max(math.sqrt(s * tp / 3.0), 2.0 * tp)
    return a_req, d_req


def collect_wall_members(model, prefix='EW', notes=None):
    """断面名が prefix で始まるRC鉛直要素を耐震壁部材として拾う.

    戻り値: [{'ele','sec','name','lw'(壁長),'t'(モデル壁厚),'h'(部材長),
              's','axis','strong_fz','cx','cy','z0','z1','cols'}]
    cols は壁端 (中心±壁長/2) に近接する鉛直RC部材 (枠柱) の要素番号。
    """
    notes = notes if notes is not None else []
    xyz = model['node_xyz']
    pref = str(prefix or 'EW').upper()
    walls = []
    for ele, c in sorted(model['columns'].items()):
        name = str(model['sec_name'].get(c['sec'], ''))
        if not name.upper().startswith(pref):
            continue
        if c['mat'] not in model['Fc']:
            continue
        dims = _sec_dims(model['sections'], c['sec'])
        if dims is None:
            notes.append('耐震壁 %s (要素%d): 断面がSB (中実角) でないため'
                         '対象外としました。' % (name, ele))
            continue
        H, B = dims
        lw, t = max(H, B), min(H, B)
        dxy = _col_dims_xy(model['sections'], c['sec'], c['beta'])
        axis = 'X' if dxy[0] >= dxy[1] else 'Y'
        lo, hi = xyz[c['n_lo']], xyz[c['n_hi']]
        walls.append({
            'ele': ele, 'sec': c['sec'], 'name': name,
            'lw': lw, 't': t, 'h': c['h'], 's': min(c['h'], lw),
            'axis': axis, 'strong_fz': H >= B,
            'cx': lo[0] * 1000.0, 'cy': lo[1] * 1000.0,
            'z0': min(lo[2], hi[2]) * 1000.0,
            'z1': max(lo[2], hi[2]) * 1000.0})

    # 枠柱: 壁端 (中心±壁長/2) に近接する鉛直RC部材 (壁部材自身は除く)
    for w in walls:
        if w['axis'] == 'X':
            ends = [(w['cx'] - w['lw'] / 2, w['cy']),
                    (w['cx'] + w['lw'] / 2, w['cy'])]
        else:
            ends = [(w['cx'], w['cy'] - w['lw'] / 2),
                    (w['cx'], w['cy'] + w['lw'] / 2)]
        cols = [[], []]
        for ele, c in model['columns'].items():
            if ele == w['ele']:
                continue
            name = str(model['sec_name'].get(c['sec'], ''))
            if name.upper().startswith(pref):
                continue
            if c['mat'] not in model['Fc']:
                continue
            props = _col_sec_props(model['sections'], c['sec'])
            if props is None or props[3] < _COL_MIN_DIM:
                continue                         # ダミー断面等は枠柱にしない
            lo, hi = xyz[c['n_lo']], xyz[c['n_hi']]
            x, y = lo[0] * 1000.0, lo[1] * 1000.0
            cz0 = min(lo[2], hi[2]) * 1000.0
            cz1 = max(lo[2], hi[2]) * 1000.0
            if min(cz1, w['z1']) - max(cz0, w['z0']) < 300.0:
                continue
            for k, (ex, ey) in enumerate(ends):
                if math.hypot(x - ex, y - ey) <= _WALL_END_TOL:
                    cols[k].append(ele)
        w['cols'] = {'minus': cols[0], 'plus': cols[1]}
    return walls


def _wall_forces(beam_stress, eles):
    """{ケース番号: {要素番号: (Fy_i, Fz_i, Fy_j, Fz_j)}} [kN]."""
    forces = {}
    eles = set(int(e) for e in eles)
    for cno in np.unique(np.asarray(beam_stress[:, 1], dtype=float)):
        block = beam_stress[beam_stress[:, 1] == cno]
        d = {}
        for ele in eles:
            rows = block[block[:, 0] == ele]
            if rows.shape[0] == 0:
                continue
            d[ele] = (float(rows[0, 3]), float(rows[0, 4]),
                      float(rows[-1, 3]), float(rows[-1, 4]))
        forces[float(cno)] = d
    return forces


def _combo_forces_wall(wforces, combo, ele, strong_fz):
    """組合せ後の強軸方向せん断 (i端, j端) [kN]。無ければ None."""
    qi = qj = 0.0
    for cno, fac in combo:
        d = wforces.get(float(cno))
        if d is None or ele not in d:
            return None
        f = d[ele]
        if strong_fz:
            qi += fac * f[1]
            qj += fac * f[3]
        else:
            qi += fac * f[0]
            qj += fac * f[2]
    return qi, qj


def eval_wall_members(walls, model, cases, wforces, params,
                      wall_cols=None, notes=None):
    """壁部材ごとに QD → t' → 必要値を算定し、枠柱を判定する.

    wall_cols: {壁要素番号(str/int): {'minus': [要素番号,..],
                'plus': [...]}} — 枠柱の手動指定 (自動判定の上書き)。
    """
    notes = notes if notes is not None else []
    ft = float(params['wall_ft'])
    wr = float(params['wall_wr'])
    rows = []
    for w in walls:
        # 枠柱の手動上書き (断面符号で指定。'auto'=自動 / 'none'=なし /
        # 断面番号=その断面の柱として判定)
        manual = False
        manual_sec = {'minus': None, 'plus': None}
        ov = None
        if wall_cols:
            ov = wall_cols.get(str(w['ele']), wall_cols.get(w['ele']))
        if ov is not None:
            new_cols = dict(w['cols'])
            changed = []
            for side in ('minus', 'plus'):
                v = ov.get(side)
                if v in (None, '', 'auto'):
                    continue
                manual = True
                label = '始端' if side == 'minus' else '終端'
                if v == 'none':
                    new_cols[side] = []
                    changed.append('%s=なし' % label)
                else:
                    try:
                        sec = int(float(v))
                    except (TypeError, ValueError):
                        notes.append('耐震壁 %s: 枠柱の指定 %r が不正な'
                                     'ため自動判定を用いました。'
                                     % (w['name'], v))
                        manual_sec[side] = None
                        continue
                    manual_sec[side] = sec
                    changed.append('%s=%s' % (
                        label, model['sec_name'].get(sec, str(sec))))
            if changed:
                notes.append('耐震壁 %s (要素%d): 枠柱を手動指定しました'
                             ' (%s)。' % (w['name'], w['ele'],
                                          ', '.join(changed)))
            w = dict(w, cols=new_cols)
        # ② 強軸方向せん断力の最大 (短期ケースの組合せ)
        qd, qd_case = 0.0, ''
        found = False
        for c in cases:
            f = _combo_forces_wall(wforces, c['combo'], w['ele'],
                                   w['strong_fz'])
            if f is None:
                continue
            found = True
            if c['term'] != 'short':
                continue
            q = max(abs(f[0]), abs(f[1]))
            if q > qd:
                qd, qd_case = q, c['name']
        if not found:
            notes.append("耐震壁 %s (要素%d): 応力ファイルに当該要素が"
                         "ないため、t' はモデル壁厚を用いました。"
                         % (w['name'], w['ele']))
            tp, qd = w['t'], None
        elif qd <= 0:
            notes.append("耐震壁 %s (要素%d): 短期ケースのせん断力が0の"
                         "ため、t' はモデル壁厚を用いました。"
                         % (w['name'], w['ele']))
            tp, qd = w['t'], None
        else:
            # ③ t' = wr・QD/(0.012・le・ft)、le=壁長さ
            tp = wr * qd * 1000.0 / (PS_DASH * w['lw'] * ft)
            if tp > w['t']:
                notes.append("耐震壁 %s (要素%d): 必要壁厚 t'=%.0fmm が"
                             "モデル壁厚 %.0fmm を上回ります (壁板自体の"
                             "せん断も要確認)。"
                             % (w['name'], w['ele'], tp, w['t']))
        # ④ s = min(部材高さ, 壁長さ)
        a_req, d_req = wall_requirements(w['s'], tp)
        ok_all = True
        col_rows = []
        for side in ('minus', 'plus'):
            # 断面符号での手動指定: その断面のB×Hで判定する
            if manual_sec[side] is not None:
                sec = manual_sec[side]
                props = _col_sec_props(model['sections'], sec)
                if props is None:
                    notes.append('耐震壁 %s: 指定された枠柱断面 %s が'
                                 'SB (中実角)/SR (中実丸) でないため'
                                 '判定できません。'
                                 % (w['name'],
                                    model['sec_name'].get(sec, sec)))
                    ok_all = False
                    col_rows.append({'side': side, 'name': '(判定不可)',
                                     'B': None, 'H': None, 'A': None,
                                     'dmin': None, 'ok': False})
                    continue
                hh, bb, a, dmin = props
                ok = a >= a_req and dmin >= d_req
                ok_all = ok_all and ok
                col_rows.append({
                    'side': side, 'ele': None,
                    'name': model['sec_name'].get(sec, str(sec)),
                    'B': bb, 'H': hh, 'A': a, 'dmin': dmin, 'ok': ok})
                continue
            eles_side = w['cols'][side]
            if not eles_side:
                ok_all = False
                col_rows.append({'side': side, 'name': '(枠柱なし)',
                                 'B': None, 'H': None, 'A': None,
                                 'dmin': None, 'ok': False})
                continue
            for ele in eles_side:
                c = model['columns'][ele]
                props = _col_sec_props(model['sections'], c['sec'])
                if props is None:
                    notes.append('耐震壁 %s: 枠柱要素%d の断面がSB/SR'
                                 '以外のため判定できません。'
                                 % (w['name'], ele))
                    continue
                dx, dy, a, dmin = props
                ok = a >= a_req and dmin >= d_req
                ok_all = ok_all and ok
                col_rows.append({
                    'side': side, 'ele': ele,
                    'name': model['sec_name'].get(c['sec'],
                                                  str(c['sec'])),
                    'B': dx, 'H': dy, 'A': a, 'dmin': dmin, 'ok': ok})
        rows.append({
            'ele': w['ele'], 'name': w['name'], 'axis': w['axis'],
            'lw': w['lw'], 't': w['t'], 'h': w['h'], 's': w['s'],
            'z0': w['z0'], 'z1': w['z1'],
            'qd': qd, 'qd_case': qd_case, 'tp': tp,
            'a_req': a_req, 'd_req': d_req,
            'cols': col_rows, 'ok': ok_all, 'manual': manual})
    return rows


# ---------------------------------------------------------------------------
# 壁の開口補強筋 (RC規準2010 19条5項)
#   (19.14) 開口隅角部の付加斜張力:
#       Ad・ft + (Av・ft + Ah・ft)/√2 ≥ (h0+l0)/(2√2・l)・QD
#   (19.15) 開口左右の付加曲げ:
#       (l−l0p)(Ad・ft/√2 + Av0・ft) + t(l−l0p)²/(4(nh+1))・psv・ft
#           ≥ h0/2・QD
#   (19.16) 開口上下の付加曲げ:
#       (h−h0p)(Ad・ft/√2 + Ah0・ft) + t(h−h0p)²/(4nv)・psh・ft
#           ≥ l0/2・h/l・QD
#       (単層壁・ピロティ壁の最下層では第2項の nv を nv+1 に置換)
#   開口周囲 = 開口から500mm以内かつ中間線以内 (図19.5)。Av・Ah は
#   その範囲の縦筋・横筋の合計 (柱・梁主筋も含めてよいが本ツールでは
#   安全側に壁筋+開口補強筋のみを算入する)。
# ---------------------------------------------------------------------------

def check_opening(w, qd, op, wall_ft, notes=None):
    """開口補強筋の検定 (1壁×1開口グループ)。行 dict を返す.

    w : collect_wall_members の壁 dict (lw, h, t, name, ele)
    qd: 設計用せん断力 QD [kN] (None なら検定不可)
    op: {'l0','h0','nh','nv','single','w_di','w_pitch','w_double',
         'd_n','d_di','v_n','v_di','h_n','h_di'}
    """
    notes = notes if notes is not None else []
    l, h, t = w['lw'], w['h'], w['t']
    l0, h0 = float(op['l0']), float(op['h0'])
    nh = max(1, int(op.get('nh') or 1))
    nv = max(1, int(op.get('nv') or 1))
    l0p = l0 * nh
    h0p = h0 * nv
    if l0p >= l or h0p >= h:
        notes.append('開口補強 %s: 開口寸法が壁寸法以上のため検定でき'
                     'ません。' % w['name'])
        return None
    # 壁筋 (縦横同仕様とみなす)
    dw, sw = float(op['w_di']), float(op['w_pitch'])
    aw = Area_steelbar(dw, 1) * (2.0 if op.get('w_double') else 1.0)
    ps = aw / (t * sw)                     # psv = psh
    ftw = float(wall_ft)
    # 開口補強筋 (径から材種自動: sd_of)
    def _bar(nkey, dkey):
        n, di = float(op.get(nkey) or 0), float(op.get(dkey) or 0)
        if n <= 0 or di <= 0:
            return 0.0, 0.0
        return n * Area_steelbar(di, 1), ft_short(di, sd_of(di))
    ad, ftd = _bar('d_n', 'd_di')
    av0, ftv = _bar('v_n', 'v_di')
    ah0, fth = _bar('h_n', 'h_di')
    # 開口周囲の縦筋・横筋の合計 (壁筋: 開口両側/上下 各500mm以内)
    n_zone = 2.0 * (500.0 / sw)
    av_wall = n_zone * aw
    ah_wall = n_zone * aw
    if qd is None:
        return dict(name=w['name'], ele=w['ele'], ok=None)
    q = qd * 1000.0                        # N
    rt2 = math.sqrt(2.0)
    # (19.14) [N]
    lhs14 = ad * ftd + (av0 * ftv + av_wall * ftw
                        + ah0 * fth + ah_wall * ftw) / rt2
    rhs14 = (h0 + l0) / (2.0 * rt2 * l) * q
    # (19.15) [N・mm]
    lhs15 = ((l - l0p) * (ad * ftd / rt2 + av0 * ftv)
             + t * (l - l0p) ** 2 / (4.0 * (nh + 1)) * ps * ftw)
    rhs15 = h0 / 2.0 * q
    # (19.16) [N・mm] (単層壁・ピロティ最下層は第2項 nv→nv+1)
    nv_eff = nv + 1 if op.get('single') else nv
    lhs16 = ((h - h0p) * (ad * ftd / rt2 + ah0 * fth)
             + t * (h - h0p) ** 2 / (4.0 * nv_eff) * ps * ftw)
    rhs16 = l0 / 2.0 * h / l * q
    return {
        'name': w['name'], 'ele': w['ele'], 'l': l, 'h': h, 't': t,
        'l0': l0, 'h0': h0, 'nh': nh, 'nv': nv, 'single': bool(
            op.get('single')), 'qd': qd, 'ps': ps * 100.0,
        'ad': ad, 'av0': av0, 'ah0': ah0,
        'd_txt': '%d-D%d' % (op.get('d_n') or 0, op.get('d_di') or 0),
        'v_txt': '%d-D%d' % (op.get('v_n') or 0, op.get('v_di') or 0),
        'h_txt': '%d-D%d' % (op.get('h_n') or 0, op.get('h_di') or 0),
        'lhs14': lhs14 / 1000.0, 'rhs14': rhs14 / 1000.0,
        'ok14': lhs14 >= rhs14,
        'lhs15': lhs15 / 1.0e6, 'rhs15': rhs15 / 1.0e6,
        'ok15': lhs15 >= rhs15,
        'lhs16': lhs16 / 1.0e6, 'rhs16': rhs16 / 1.0e6,
        'ok16': lhs16 >= rhs16,
        'ok': lhs14 >= rhs14 and lhs15 >= rhs15 and lhs16 >= rhs16}


# ---------------------------------------------------------------------------
# 入口 (read / run)
# ---------------------------------------------------------------------------

def read_targets(mgt_path, wall_prefix='EW', notes=None):
    """対象読込。UI 表示用のサマリを返す."""
    notes = notes if notes is not None else []
    model = read_model(mgt_path, notes=notes)
    secs = {}
    for ele, bm in model['beams'].items():
        s = secs.setdefault(bm['sec'], {
            'sec': bm['sec'], 'name': bm['name'], 'b': bm['b'],
            'D': bm['D'], 'n_ele': 0,
            'has_rebar': bm['rebar'] is not None})
        s['n_ele'] += 1
    joints = {}
    for j in model['joints']:
        joints.setdefault(j['form'], 0)
        joints[j['form']] += 1
    fcs = sorted(set(model['Fc'].values()))
    walls = collect_wall_members(model, prefix=wall_prefix, notes=notes)
    wall_list = []
    for w in walls:
        def _side(side):
            return [{'ele': e,
                     'name': model['sec_name'].get(
                         model['columns'][e]['sec'], str(e))}
                    for e in w['cols'][side]]
        wall_list.append({
            'ele': w['ele'], 'name': w['name'], 'axis': w['axis'],
            'lw': w['lw'], 't': w['t'], 'h': w['h'], 's': w['s'],
            'z0': w['z0'], 'z1': w['z1'],
            'cols_minus': _side('minus'), 'cols_plus': _side('plus')})
    # RC柱の断面リスト (枠柱のプルダウン用)。壁断面 (接頭辞) と
    # ダミー断面 (最小径50mm未満) は除く。SB(中実角)・SR(中実丸)対応。
    pref = str(wall_prefix or 'EW').upper()
    col_secs = {}
    for ele, c in model['columns'].items():
        if c['mat'] not in model['Fc']:
            continue
        name = str(model['sec_name'].get(c['sec'], c['sec']))
        if name.upper().startswith(pref):
            continue
        props = _col_sec_props(model['sections'], c['sec'])
        if props is None or props[3] < _COL_MIN_DIM:
            continue
        col_secs[c['sec']] = {'sec': c['sec'], 'name': name,
                              'H': props[0], 'B': props[1]}
    return {'sections': sorted(secs.values(), key=lambda s: s['sec']),
            'n_joints': [{'form': FORMS[k][1], 'n': v}
                         for k, v in sorted(joints.items())],
            'fc_list': fcs, 'walls': wall_list,
            'col_secs': sorted(col_secs.values(),
                               key=lambda s: s['name'])}


DEFAULT_PARAMS = {
    'beam_cover': 40.0,        # 梁の主筋かぶり (設計かぶり) [mm]
    'col_cover': 40.0,         # 柱かぶり [mm]
    'anchor_offset': 85.0,     # 柱せい−投影定着長さ (かぶり+帯筋+柱主筋等)
    'anchor_offset2': -1.0,    # 2段目筋の投影長さ控除 [mm]。負値=自動
                               # (主筋径+段あき max(25,1.5db))
    'alpha': 1.0,              # 17.2式 α (コア内定着1.0 / 以外1.25)
    'S': 0.7,                  # 表17.1 (耐震部材・標準フック0.7 / 直線1.25)
    'anchor_method': 'rc',     # 定着の判定方法 'rc'=RC規準17.2式 /
                               # 'k432'=告示432号 / 'proj'=0.75D細則
    'ld_mode': 'L',            # 付着長さ ld ('L' or 'Ld2'=(L+d)/2)
    'do_anchor': True, 'do_through': True, 'do_bond': True,
    'do_bond_safety': True,    # 16.5式 (付着割裂の安全性)。耐震壁負担
    'do_joint': True,          # 建物等では省略可 (16条解説 p.212)
    'joint_hoop_factor': 1.5,  # 接合部内帯筋間隔 = 柱帯筋間隔×この倍率
    'do_wall': True,           # 耐震壁周辺柱の断面 (19条解説 p.318-320)
    'wall_prefix': 'EW',       # 耐震壁とみなす断面名の接頭辞
    'wall_ft': 295.0,          # 壁せん断補強筋の短期許容引張応力度 ft
    'wall_wr': 1.0,            # 壁板の負担率 Qw/(Qw+ΣQc) (1.0=全負担)
}


def run_check(mgt_path, beam_stress_path, case_types, params=None,
              form_secs=None, check_secs=None, wall_cols=None,
              wall_open=None, notes=None):
    """検定の実行。結果 dict を返す.

    form_secs : 接合部の形式判定に算入する梁の断面番号 list (None=配筋あり)
    check_secs: 定着・通し配筋・付着の検定対象の断面番号 list (None=配筋あり)
    wall_cols : 耐震壁の枠柱の手動指定 (eval_wall_members 参照)
    wall_open : {壁要素番号: 開口入力 dict} — 開口補強筋の検定
                (check_opening 参照)。指定のある壁だけ検定する。
    """
    notes = notes if notes is not None else []
    p = dict(DEFAULT_PARAMS)
    if params:
        for k, v in params.items():
            if v is not None and k in p:
                p[k] = type(p[k])(v) if not isinstance(p[k], bool) \
                    else bool(v)
    if p['anchor_method'] not in ('rc', 'k432', 'proj'):
        raise ValueError('定着の判定方法の指定が不正です: %r'
                         % p['anchor_method'])
    model = read_model(mgt_path, notes=notes, form_secs=form_secs)
    beams, joints = model['beams'], model['joints']
    fc_of = lambda mat: model['Fc'].get(mat, 0.0)

    rebar_secs = set(bm['sec'] for bm in beams.values()
                     if bm['rebar'] is not None)
    check_set = set(int(s) for s in check_secs) \
        if check_secs is not None else set(rebar_secs)
    no_rebar_checked = check_set - rebar_secs
    if no_rebar_checked:
        names = sorted(set(bm['name'] for bm in beams.values()
                           if bm['sec'] in no_rebar_checked))
        notes.append('配筋データが無いため検定できない断面を検定対象から'
                     '外しました: %s' % ', '.join(names))
        check_set -= no_rebar_checked

    bs = read_beam_stress(beam_stress_path)
    cases = build_cases(case_types)
    forces = _forces_by_case(bs, beams.keys())

    # 要素ごとの長期/短期の最大 |Q|
    q_env = {}
    for ele in beams:
        ql = qs = 0.0
        for c in cases:
            f = _combo_forces(forces, c['combo'], ele)
            if f is None:
                continue
            q = max(abs(f[0]), abs(f[2]))
            if c['term'] == 'long':
                ql = max(ql, q)
            else:
                qs = max(qs, q)
        q_env[ele] = (ql, qs)

    # 柱せいから梁の内法長さを概算 (両端の接合部の柱せい/2 を控除)
    dc_at = {}
    for j in joints:
        for side, ele in (('plus', j['plus']), ('minus', j['minus'])):
            if ele is None:
                continue
            bm = beams[ele]
            end = 'i' if bm['ni'] == j['node'] else 'j'
            dc_at[(ele, end)] = j['Dc']

    def clear_len(ele):
        bm = beams[ele]
        di_ = dc_at.get((ele, 'i'), 0.0)
        dj_ = dc_at.get((ele, 'j'), 0.0)
        return max(bm['len'] - di_ / 2.0 - dj_ / 2.0, bm['len'] * 0.5)

    # ---- 定着 (ト形・L形の外端) と 通し配筋 (十字・T形) ----
    anchor_rows, through_rows = [], []
    for j in joints:
        fc = fc_of(j['col_mat'])
        if fc <= 0:
            continue
        if j['form'] in ('knee', 'ell'):
            if not p['do_anchor']:
                continue
            ele = j['plus'] if j['plus'] is not None else j['minus']
            bm = beams[ele]
            if bm['sec'] not in check_set:
                if bm['rebar'] is None:
                    notes.append('節点%d %s方向: 梁 %s は配筋データが無い'
                                 'ため定着未検定です。'
                                 % (j['node'], j['dir'], bm['name']))
                continue
            side = 'i' if bm['ni'] == j['node'] else 'j'
            for r in check_anchor(j, bm, side, fc, p):
                r.update({'node': j['node'], 'dir': j['dir'],
                          'form': FORMS[j['form']][1],
                          'col': j['col_name'], 'Dc': j['Dc'],
                          'beam': bm['name'], 'fc': fc})
                r['ok'] = {'rc': r['ok_rc'], 'k432': r['ok_432'],
                           'proj': r['ok_proj']}[p['anchor_method']]
                anchor_rows.append(r)
        else:
            if not p['do_through']:
                continue
            ele = j['plus'] if j['plus'] is not None else j['minus']
            bm = beams[ele]
            if bm['sec'] not in check_set:
                continue
            for r in check_through(j, bm, fc):
                r.update({'node': j['node'], 'dir': j['dir'],
                          'form': FORMS[j['form']][1],
                          'col': j['col_name'], 'Dc': j['Dc'],
                          'beam': bm['name'], 'fc': fc})
                through_rows.append(r)

    # ---- 付着 (対象梁全数、断面ごとに最大Qで代表) ----
    bond_rows = []
    if p['do_bond']:
        by_sec = {}
        for ele, bm in beams.items():
            key = bm['sec']
            if key not in check_set:
                continue
            ql, qs = q_env.get(ele, (0.0, 0.0))
            cur = by_sec.get(key)
            if cur is None or qs > cur['qs']:
                by_sec[key] = {'ele': ele, 'ql': ql, 'qs': qs}
        for sec, pick in sorted(by_sec.items()):
            bm = beams[pick['ele']]
            fc = fc_of(bm['mat'])
            if fc <= 0:
                continue
            ld_len = clear_len(pick['ele'])
            for r in check_bond(bm, fc, pick['ql'], pick['qs'],
                                ld_len, p):
                r.update({'sec': sec, 'beam': bm['name'], 'ele': pick['ele'],
                          'b': bm['b'], 'D': bm['D'], 'fc': fc,
                          'ql': pick['ql'], 'qs': pick['qs'],
                          'ldlen': ld_len})
                bond_rows.append(r)

    # ---- 柱梁接合部 ----
    joint_rows = []
    if p['do_joint']:
        for j in joints:
            fc = fc_of(j['col_mat'])
            if fc <= 0:
                continue
            r = check_joint(j, beams, fc, p)
            no_rb = [beams[e]['name'] for e in (j['plus'], j['minus'])
                     if e is not None and beams[e]['rebar'] is None]
            if no_rb:
                notes.append('節点%d %s方向: 配筋の無い梁 %s は My=0 と'
                             'して接合部せん断を算定しました (形式判定'
                             'にのみ算入)。'
                             % (j['node'], j['dir'], ', '.join(no_rb)))
            hoop = joint_hoop_check(j, model['rc_cols'], j['bc'], j['Dc'],
                                    factor=p['joint_hoop_factor'])
            r.update({'node': j['node'], 'dir': j['dir'],
                      'col': j['col_name'], 'fc': fc,
                      'beams': '/'.join(beams[e]['name']
                                        for e in (j['plus'], j['minus'])
                                        if e is not None),
                      'hoop': hoop})
            joint_rows.append(r)

    # ---- 耐震壁周辺柱の断面 (19条) と 開口補強筋 (19条5項) ----
    wall_rows, open_rows = [], []
    if p['do_wall'] or wall_open:
        walls = collect_wall_members(model, prefix=p['wall_prefix'],
                                     notes=notes)
        if walls:
            wforces = _wall_forces(bs, [w['ele'] for w in walls])
            if p['do_wall']:
                wall_rows = eval_wall_members(walls, model, cases,
                                              wforces, p,
                                              wall_cols=wall_cols,
                                              notes=notes)
            if wall_open:
                qd_of = {r['ele']: r['qd']
                         for r in (wall_rows or eval_wall_members(
                             walls, model, cases, wforces, p,
                             notes=[]))}
                for w in walls:
                    op = wall_open.get(str(w['ele']),
                                       wall_open.get(w['ele']))
                    if not op or not float(op.get('l0') or 0):
                        continue
                    r = check_opening(w, qd_of.get(w['ele']), op,
                                      p['wall_ft'], notes=notes)
                    if r is not None:
                        if r.get('ok') is None:
                            notes.append('開口補強 %s: QDが取得できない'
                                         'ため未検定です。' % w['name'])
                        else:
                            open_rows.append(r)

    summary = {
        'anchor': {'n': len(anchor_rows),
                   'ng': sum(1 for r in anchor_rows if not r['ok'])},
        'through': {'n': len(through_rows),
                    'ng': sum(1 for r in through_rows if not r['ok'])},
        'bond': {'n': len(bond_rows),
                 'ng': sum(1 for r in bond_rows
                           if not (r['ok_l'] and r['ok_s']
                                   and r['ok_y'] is not False))},
        'joint': {'n': len(joint_rows),
                  'ng': sum(1 for r in joint_rows if not r['ok'])},
        'wall': {'n': len(wall_rows),
                 'ng': sum(1 for r in wall_rows if not r['ok'])},
        'open': {'n': len(open_rows),
                 'ng': sum(1 for r in open_rows if not r['ok'])},
    }
    # 断面の選択状態 (検討書の共通条件に明記する)
    form_set = set(int(s) for s in form_secs) if form_secs is not None \
        else set(rebar_secs)
    sec_names = {}
    for bm in beams.values():
        sec_names[bm['sec']] = bm['name']
    sec_select = {
        'form': sorted(sec_names[s] for s in form_set if s in sec_names),
        'check': sorted(sec_names[s] for s in check_set
                        if s in sec_names),
        'form_off': sorted(sec_names[s] for s in sec_names
                           if s not in form_set)}

    return {'params': p, 'cases': [{'name': c['name'], 'term': c['term']}
                                   for c in cases],
            'anchor': anchor_rows, 'through': through_rows,
            'bond': bond_rows, 'joints': joint_rows,
            'walls': wall_rows, 'openings': open_rows,
            'sec_select': sec_select,
            'summary': summary}
