# -*- coding: utf-8 -*-
"""必要壁量の算定と検定 (地震力・風圧力) と CSV 出力.

地震力 (層重量から):
    αi = Σ(i階以上の層重量) / Σ(全層重量)
    T  = h × (0.02 + 0.01α)   (令88条。h = 建築物の高さ[m]、α = 架構係数。
                               木造だけなら α=1 で T=0.03h)
    Rt = 1                              (T <  Tc)
         1 - 0.2(T/Tc - 1)^2            (Tc <= T < 2Tc)
         1.6Tc / T                      (2Tc <= T)
    Ai = 1 + (1/√αi - αi) * 2T/(1+3T)
    Ci = I * Z * Rt * Ai * C0        (I = 重要度係数。既定 1.0)
    Qi = Ci * Σ(i階以上の層重量)                     [kN]
    必要壁量 = Qi / 1.96[kN/m]                        [m]
      (壁倍率1・長さ1m の耐力壁の許容せん断耐力が 1.96kN/m なので、
       Σ(壁長×壁倍率) と同じ土俵で比べられる)

風圧力:
    必要壁量 = 見付面積[m2] × 係数[cm/m2] / 100      [m]
      係数は原則 50 (特定行政庁が指定する強風区域は 50〜75)。
      見付面積は「その階の床面から 1.35m 上より上の部分」の投影面積。
      重要度係数 I は地震力にのみ掛ける (風圧力には掛けない)。

判定:
    検定比 = 必要壁量 / 存在壁量 (地震・風の大きい方で判定)。1.0 以下で OK。

4分割法 (令46条 / 平12建告1352号) — quarter_check():
    ある方向の耐力壁について、平面をその方向と直交する軸で4分割し、
    両端の1/4 (側端部分) ごとに
        壁量充足率 = 側端部分の存在壁量 / 側端部分の必要壁量
        壁率比     = 小さい方の充足率 / 大きい方の充足率
    を求める。両側の充足率がともに1.0を超えるか、壁率比が0.5以上でOK。

    X方向の壁は Y軸で4分割 (X方向の力に対する壁のY方向の偏りを見る)、
    Y方向の壁は X軸で4分割する。壁は平面での中心位置で側端部分に振り分ける。

    側端部分の必要壁量は「その階の地震力による必要壁量 ÷ 4」とした。
    奥行きが一定の平面なら側端部分は床面積の1/4なのでこれと一致する。
    L形など側端部分の床面積が1/4でない平面では充足率の絶対値がずれるが、
    判定の主軸である **壁率比は両側で同じ値で割るため影響を受けない**。
"""

import math
import os

#: 見付面積の対象から外す高さ (床面から) [m]
WIND_BASE_H = 1.35


#: Tc [s] -> 地盤種別の呼び名 (令88条)
SOIL_NAME = {0.4: '第一種地盤', 0.6: '第二種地盤', 0.8: '第三種地盤'}


def _soil_text(tc):
    """地盤種別を「0.6 (第二種地盤)」の形で書く."""
    name = SOIL_NAME.get(round(float(tc), 3))
    return ('%g (%s)' % (tc, name)) if name else '%g' % tc


def rt_value(T, Tc):
    """振動特性係数 Rt."""
    if T <= 0 or Tc <= 0:
        return 1.0
    if T < Tc:
        return 1.0
    if T < 2 * Tc:
        return 1.0 - 0.2 * (T / Tc - 1.0) ** 2
    return 1.6 * Tc / T


def ai_value(alpha, T):
    """Ai 分布 (αi は i階が支える重量の比)."""
    if alpha <= 0:
        return 1.0
    return 1.0 + (1.0 / math.sqrt(alpha) - alpha) * 2.0 * T / (1.0 + 3.0 * T)


def wind_area_estimate(res):
    """見付面積の参考値 (モデルの外形からの矩形近似) を階ごとに返す.

    X方向の風 → 受ける面の幅は Y方向スパン、高さは (最高部 - (床+1.35m))。
    切妻・寄棟などの屋根形は考慮していないので、あくまで入力の目安。
    """
    m = res['model']
    wx = max(m['y1'] - m['y0'], 0.0)      # X方向の風を受ける面の幅
    wy = max(m['x1'] - m['x0'], 0.0)      # Y方向の風を受ける面の幅
    out = {}
    for st in res['stories']:
        h = max(m['z_top'] - (st['z0'] + WIND_BASE_H), 0.0)
        out[st['no']] = {'x': wx * h, 'y': wy * h, 'h': h}
    return out


def run_check(res, story_inputs, z=1.0, tc=0.6, c0=0.2, qa=1.96,
              wind_k=50.0, height=None, alpha=1.0, imp=1.0, notes=None):
    """存在壁量 (res) と入力 (story_inputs) から必要壁量・検定比を出す.

    story_inputs: [{'no': 階番号, 'w': 層重量[kN],
                    'af_x': X方向の風の見付面積[m2], 'af_y': 同Y方向}, ...]

    戻り値 dict:
      rows   階ごと (上から順に並べたもの) の算定結果
      params 使用した係数 (T, Rt など)
      ng     検定比 > 1.0 の件数
    """
    notes = notes if notes is not None else []
    stories = res['stories']                       # 下から
    inp = {int(s.get('no')): s for s in (story_inputs or [])}

    ws = []
    for st in stories:
        s = inp.get(st['no'], {})
        try:
            ws.append(max(float(s.get('w') or 0.0), 0.0))
        except (TypeError, ValueError):
            ws.append(0.0)
    w_total = sum(ws)
    if w_total <= 0:
        raise ValueError('層重量が入力されていません。'
                         '階ごとの層重量[kN]を入力してください。')
    for st, w in zip(stories, ws):
        if w <= 0:
            notes.append('%s の層重量が 0 です。地震力の必要壁量は 0 として'
                         '計算しています。' % st['name'])

    # 一次固有周期は 建築物の高さ h と架構係数 α から求める (令88条)
    #   T = h × (0.02 + 0.01α)      α = 木造・鉄骨造の階の高さの合計 / h
    h_model = max(res['model']['h_bldg'], 0.0)
    h_in = float(height) if height else h_model
    a_frame = float(alpha)          # 架構係数 (下の αi と混同しないよう別名)
    T = h_in * (0.02 + 0.01 * a_frame)
    if T <= 0:
        h_in = 3.0
        T = h_in * (0.02 + 0.01 * a_frame)
        notes.append('建築物の高さが取得できなかったため h = 3.0 m と'
                     '仮定しました。「建築物の高さ h」欄で指定してください。')
    Rt = rt_value(T, tc)

    rows = []
    n = len(stories)
    for i in range(n - 1, -1, -1):                 # 上の階から表に並べる
        st = stories[i]
        wsum = sum(ws[i:])
        alpha_i = wsum / w_total if w_total > 0 else 0.0
        Ai = ai_value(alpha_i, T)
        Ci = imp * z * Rt * Ai * c0
        Qi = Ci * wsum
        req_eq = Qi / qa if qa > 0 else float('inf')

        s = inp.get(st['no'], {})
        row = {'no': st['no'], 'name': st['name'], 'z0': st['z0'],
               'z1': st['z1'], 'h': st['h'], 'nwall': st['nwall'],
               'levels': st.get('levels', [st['z0']]),
               'h_disp': st.get('h_disp', '%.3f' % st['h']),
               'w': ws[i], 'wsum': wsum, 'alpha': alpha_i, 'ai': Ai,
               'ci': Ci, 'qi': Qi, 'req_eq': req_eq}
        for d, key in (('X', 'af_x'), ('Y', 'af_y')):
            try:
                af = max(float(s.get(key) or 0.0), 0.0)
            except (TypeError, ValueError):
                af = 0.0
            exist = res['exist'].get(st['no'], {}).get(d, 0.0)
            req_w = af * wind_k / 100.0
            req = max(req_eq, req_w)
            ratio_eq = (req_eq / exist) if exist > 0 else None
            ratio_w = (req_w / exist) if exist > 0 else None
            ratio = (req / exist) if exist > 0 else None
            row[d.lower()] = {
                'exist': exist, 'exist_q': exist * qa,
                'af': af, 'req_eq': req_eq, 'req_w': req_w, 'req': req,
                'ratio_eq': ratio_eq, 'ratio_w': ratio_w, 'ratio': ratio,
                'gov': ('風圧力' if req_w > req_eq else '地震力'),
                'ok': (ratio is not None and ratio <= 1.0),
                'margin': exist - req}
            if exist <= 0:
                notes.append('%s の %s方向に耐力壁がありません '
                             '(存在壁量 0 のため NG)。' % (st['name'], d))
        rows.append(row)

    ng = sum(1 for r in rows for d in ('x', 'y') if not r[d]['ok'])
    ng4 = quarter_check(res, rows, notes)
    params = {'z': z, 'tc': tc, 'c0': c0, 'qa': qa, 'wind_k': wind_k,
              'imp': imp, 'T': T, 'Rt': Rt, 'h_bldg': h_in,
              'h_model': h_model, 'alpha': a_frame,
              'w_total': w_total, 'wind_base_h': WIND_BASE_H,
              'side_frac': SIDE_FRAC}
    return {'rows': rows, 'params': params, 'ng': ng, 'ng4': ng4}


# ---------------------------------------------------------------------------
# 4分割法 (令46条 / 平12建告1352号)
# ---------------------------------------------------------------------------

#: 側端部分の割合と座標の許容は mgt_walls 側の定義を使う
#: (壁ごとの振り分け assign_quarter と同じ値でなければならないため)
from mgtkit.wallqty.mgt_walls import SIDE_FRAC, SIDE_TOL   # noqa: E402


def quarter_check(res, rows, notes=None):
    """4分割法。rows (run_check の階ごとの結果) に 'q4' を書き込む.

    row[d]['q4'] = {
      'axis'      分割した軸 ('Y' / 'X')
      'lo','hi'   その階の平面の範囲 (分割軸方向)
      'b1','b2'   側端部分の境界座標
      'side'      [{'range','exist','ratio'}, ...]  (下側/左側 が先)
      'mid'       中央部分の存在壁量 (参考。判定には使わない)
      'req_side'  側端部分の必要壁量
      'wr'        壁率比 (None = 判定できない)
      'ok'        判定
      'why'       判定の理由
    }
    """
    notes = notes if notes is not None else []
    st_by_no = {st['no']: st for st in res['stories']}
    for r in rows:
        st = st_by_no.get(r['no'])
        walls = [w for w in res['walls'] if w['story'] == r['no']]
        for d in ('x', 'y'):
            D = d.upper()
            # X方向の壁は Y軸で、Y方向の壁は X軸で4分割する
            axis = 'Y' if D == 'X' else 'X'
            bb = st.get('bbox') if st else None
            if not bb:
                r[d]['q4'] = None
                continue
            lo, hi = (bb[2], bb[3]) if axis == 'Y' else (bb[0], bb[1])
            span = hi - lo
            q = span * SIDE_FRAC
            b1, b2 = lo + q, hi - q
            # 振り分けは mgt_walls.assign_quarter が壁ごとに済ませている
            sums = [0.0, 0.0]
            mid = 0.0
            for w in walls:
                q = (w.get('q4') or {}).get(D)
                if not q:
                    continue
                sums[0] += q[0] * w['beta']
                sums[1] += q[1] * w['beta']
                mid += q[2] * w['beta']
            req_side = r['req_eq'] * SIDE_FRAC
            rat = [(s / req_side if req_side > 0 else None) for s in sums]
            if max(sums) > 0:
                wr = min(sums) / max(sums)
            else:
                wr = None
            if all(x is not None and x > 1.0 for x in rat):
                ok, why = True, '両側端の充足率がともに1.0超'
            elif wr is not None and wr >= 0.5:
                ok, why = True, '壁率比 %.3f ≧ 0.5' % wr
            elif wr is None:
                ok, why = False, '両側端に耐力壁がない'
            else:
                ok, why = False, '壁率比 %.3f < 0.5' % wr
            r[d]['q4'] = {
                'axis': axis, 'lo': lo, 'hi': hi, 'b1': b1, 'b2': b2,
                'req_side': req_side, 'mid': mid, 'wr': wr,
                'ok': ok, 'why': why,
                'side': [{'range': '%s %.3f〜%.3f' % (axis, lo, b1),
                          'exist': sums[0], 'ratio': rat[0]},
                         {'range': '%s %.3f〜%.3f' % (axis, b2, hi),
                          'exist': sums[1], 'ratio': rat[1]}]}
            if span <= 0:
                notes.append('%s の平面の %s 方向の幅が 0 のため、4分割法の'
                             '判定はできません。' % (r['name'], axis))
    ng = sum(1 for r in rows for d in ('x', 'y')
             if r[d].get('q4') and not r[d]['q4']['ok'])
    return ng


# ---------------------------------------------------------------------------
# CSV 出力
# ---------------------------------------------------------------------------

def _c(s):
    """CSVの1セルにするため、値中のカンマ・改行を空白に置き換える."""
    return str(s).replace(',', ' ').replace('\n', ' ')


def _write(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8-sig', newline='\r\n') as f:
        f.write('\n'.join(lines) + '\n')
    return path


def export_summary_csv(res, chk, out_path):
    """階ごとの壁量検定表を CSV (UTF-8 BOM) に書き出す."""
    p = chk['params']
    lines = ['木造壁量計算 — 階別検定表']
    lines.append('I=%g (地震力のみ), Z=%g, 地盤種別Tc=%s, C0=%g, '
                 '建築物の高さh=%.3f m, 架構係数α=%g, '
                 'T=h(0.02+0.01α)=%.4f s, Rt=%.4f, '
                 '壁倍率1あたりの許容耐力=%g kN/m, 風圧力係数=%g cm/m2, '
                 '全重量=%.1f kN'
                 % (p.get('imp', 1.0), p['z'], _soil_text(p['tc']), p['c0'],
                    p['h_bldg'], p.get('alpha', 1.0), p['T'],
                    p['Rt'], p['qa'], p['wind_k'], p['w_total']))
    lines.append('')
    lines.append('階,床レベル[m],階高[m],層重量Wi[kN],ΣWi[kN],αi,Ai,Ci,'
                 'Qi[kN],必要壁量(地震)[m],方向,存在壁量ΣLβ[m],'
                 '許容耐力[kN],見付面積[m2],必要壁量(風)[m],'
                 '必要壁量(採用)[m],支配,検定比,判定,余裕[m]')
    for r in chk['rows']:
        for d in ('x', 'y'):
            v = r[d]
            lines.append(
                '%s,%s,%s,%.2f,%.2f,%.4f,%.4f,%.4f,%.2f,%.3f,'
                '%s,%.3f,%.2f,%.2f,%.3f,%.3f,%s,%s,%s,%.3f'
                % (_c(r['name']),
                   ' '.join('%.3f' % z for z in r['levels']),
                   _c(r['h_disp']), r['w'], r['wsum'],
                   r['alpha'], r['ai'], r['ci'], r['qi'], r['req_eq'],
                   d.upper(), v['exist'], v['exist_q'], v['af'],
                   v['req_w'], v['req'], v['gov'],
                   ('%.3f' % v['ratio']) if v['ratio'] is not None else '-',
                   'OK' if v['ok'] else 'NG', v['margin']))
    return _write(out_path, lines)


def export_quarter_csv(res, chk, out_path):
    """4分割法の結果を CSV (UTF-8 BOM) に書き出す."""
    lines = ['木造壁量計算 — 4分割法 (令46条 / 平12建告1352号)']
    lines.append('側端部分の必要壁量 = その階の地震力による必要壁量 × 1/4。'
                 '両側端の充足率がともに1.0超、または壁率比0.5以上でOK')
    lines.append('')
    lines.append('階,方向,分割軸,側端1 範囲,側端1 存在壁量[m],側端1 充足率,'
                 '側端2 範囲,側端2 存在壁量[m],側端2 充足率,'
                 '中央部分 存在壁量[m],側端の必要壁量[m],壁率比,判定,理由')
    for r in chk['rows']:
        for d in ('x', 'y'):
            q = r[d].get('q4')
            if not q:
                continue
            s1, s2 = q['side']
            lines.append(
                '%s,%s,%s,%s,%.3f,%s,%s,%.3f,%s,%.3f,%.3f,%s,%s,%s'
                % (_c(r['name']), d.upper(), q['axis'],
                   _c(s1['range']), s1['exist'],
                   ('%.3f' % s1['ratio']) if s1['ratio'] is not None else '-',
                   _c(s2['range']), s2['exist'],
                   ('%.3f' % s2['ratio']) if s2['ratio'] is not None else '-',
                   q['mid'], q['req_side'],
                   ('%.3f' % q['wr']) if q['wr'] is not None else '-',
                   'OK' if q['ok'] else 'NG', _c(q['why'])))
    return _write(out_path, lines)


def _q4_text(wl):
    """4分割法の振り分けを 1セルの文字列にする (画面の表示と同じ内容)."""
    out = []
    for d in ('X', 'Y'):
        q = (wl.get('q4') or {}).get(d)
        if not q or sum(q) <= 0.0005:
            continue
        p = []
        for lab, v in (('側端1', q[0]), ('側端2', q[1]), ('中央', q[2])):
            if v > 0.0005:
                p.append('%s %.2fm' % (lab, v))
        out.append('%s: %s' % (d, ' + '.join(p)))
    return ' / '.join(out)


def export_walls_csv(res, out_path):
    """読み取った耐力壁の一覧を CSV (UTF-8 BOM) に書き出す."""
    lines = ['木造壁量計算 — 耐力壁一覧 '
             '(材料名に「%s」を含む板要素)' % res['keyword']]
    lines.append('')
    lines.append('壁符号,階,方向,位置,範囲,高さ範囲,床レベル[m],'
                 'その位置の階高[m],床からの浮き[m],'
                 '壁長L[m],X方向L[m],Y方向L[m],壁倍率β,壁倍率β(モデル値),'
                 'X方向Lβ[m],Y方向Lβ[m],壁高H[m],階高/壁長,勾配[度],'
                 '算入,除外理由,4分割法の振り分け,厚み名,材料名,要素番号')
    for wl in res['walls']:
        lines.append(
            '%s,%s,%s,%s,%s,%s,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%g,%g,'
            '%.3f,%.3f,%.3f,%s,%.1f,%s,%s,%s,%s,%s,%s'
            % (_c(wl['story_name'] + '-' + wl['name']), _c(wl['story_name']),
               _c(wl['dir']),
               _c(wl['loc']), _c(wl['range']), _c(wl['zrange']),
               wl['base'], wl['h_story'], wl['gap'],
               wl['L'], wl['lx'], wl['ly'], wl['beta'], wl['beta_raw'],
               wl['lbx'], wl['lby'], wl['H'],
               ('%.2f' % wl['aspect']) if math.isfinite(wl['aspect'])
               else '-',
               wl['slope'],
               'する' if wl['used'] else 'しない', _c(wl['why']),
               _c(_q4_text(wl)),
               _c(wl['thick_name']), _c(wl['mat_name']),
               ' '.join(str(e) for e in wl['eles'])))
    return _write(out_path, lines)
