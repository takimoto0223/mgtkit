# -*- coding: utf-8 -*-
"""鉄骨造露出柱脚の検定.

準拠:
  国土交通省国土技術政策総合研究所ほか「2025年版 建築物の構造関係技術基準
  解説書」付録1-2.6 柱脚の設計の考え方 (2) 露出型柱脚 (本文 p.668-677)
  および、その実装である SS7 解説書 計算編 6.8.1 露出型 (6-40〜6-49)、
  3.6.4 (回転剛性)、10.6.1〜10.6.3 (Nu・Mu・My・Qu)。
  式番号は技術基準解説書 (付1.2-xx) と SS7 (6.xxx / 10.xx) を併記する。

(1) 回転剛性 (付1.2-20) (SS7 3.32)
    KBS = E・nt・Ab・(dt+dc)² / (2・lb)
      E = 2.05×10⁵ N/mm²、Ab: 軸部断面積、dt: 柱図心〜引張側AB群図心、
      dc: 柱図心〜圧縮側柱フランジ外縁、lb: ABの有効長さ
      計算方向にABが無い (引張側本数0) ときは nt=全本数・dt=0 (SS7)

(2) 許容応力度検定 (フロー① / SS7 6.8.1(2)(3)(4))
    BP下面のコンクリート σc ≦ fc、AB引張 σt ≦ ft (せん断との組合せ)、
    せん断 Q ≦ 0.4(T+N) (摩擦) — 満たさなければABがせん断を負担。
    中立軸と σc・T はSS7のケース1〜5 (6.122〜6.137) による。

(3) 保有耐力接合等 (フロー②〜⑤、ルート1-2・2・3)
    地震時応力を γ 倍した N*=NL+γNE, M*=ML+γME, Q*=QL+γQE で
    伸び能力あり: ③ Mu > α・Mpc (付1.2-21) かつ Qu > Q* (付1.2-22)
                  Mu のみ不満足なら ④ Mu > M* (保有耐力接合ではない)
    伸び能力なし: ⑤ σc < Fc (付1.2-23)、Tb < Pb (付1.2-24)、
                  Qy = max(摩擦, AB) > Q*
    Mu (付1.2-31〜33)、Qu (付1.2-34〜41)、My (付1.2-46)。

(4) 基礎コンクリートの破壊防止 (フロー⑥)
    (a) 縁辺の剥落 Cy/(2・B2・X) < Fc (付1.2-25,26)
    (b) 割裂       Cy/B0 < Fc/3      (付1.2-27)
    (c) 端部のせん断による剥落 (1本) e > 0.54√(bσy/cσt)・d (付1.2-29)

符号: 応力ファイル (beam_stress) の Fx は引張が正 (このコードベースの規約)。
本モジュール内では柱脚の慣例にならい N は**圧縮を正**とする (N = −Fx)。

方向: 柱断面のせい H 方向 (局所z、強軸曲げ My・せん断 Fz) と
幅 B 方向 (局所y、弱軸曲げ Mz・せん断 Fy) を別々に一軸で検定する
(SS7 と同じく直交方向の曲げは考慮しない)。ベースプレートの寸法・
アンカーボルトの配置も柱断面の H/B 方向で入力する。
"""

import math

import numpy as np

from mgtkit.mgt import (mgtopen_node, mgtopen_beam, mgtopen_material,
                        mgtopen_group, _lines)
from mgtkit.section import mgtopen_section
from mgtkit.draw_stress import read_beam_stress

#: アンカーボルトのヤング係数 [N/mm²] (SS7 3.6.4)
E_AB = 2.05e5

#: アンカーボルトの種類 → (F値 [N/mm²], 伸び能力の既定, 表示名)
AB_TYPES = {
    'ABR400': (235.0, True, 'ABR400 (JIS B 1220 転造ねじ)'),
    'ABR490': (325.0, True, 'ABR490 (JIS B 1220 転造ねじ)'),
    'ABM400': (235.0, True, 'ABM400 (JIS B 1220 切削ねじ)'),
    'ABM490': (325.0, True, 'ABM490 (JIS B 1220 切削ねじ)'),
    'SS400': (235.0, False, 'SS400 (伸び能力なし)'),
    'SNR400B': (235.0, False, 'SNR400B (伸び能力なし)'),
    'SNR490B': (325.0, False, 'SNR490B (伸び能力なし)'),
}

#: 呼び径 → 並目ねじの有効断面積 [mm²] (JIS B 1082)。既定値の出どころ。
#: ABR の軸部 (転造前の素材径) は製品により異なるため、既定では
#: 軸部断面積もこの値とする (Mu・Tu・KBS を小さめに評価)。
AB_STRESS_AREA = {16: 157.0, 20: 245.0, 22: 303.0, 24: 353.0, 27: 459.0,
                  30: 561.0, 33: 694.0, 36: 817.0, 39: 976.0, 42: 1120.0,
                  45: 1310.0, 48: 1470.0}


def ab_default_areas(ab_type, d):
    """(軸部断面積 Ab, ねじ部有効断面積 Abe) の既定値 [mm²]."""
    d = int(round(float(d)))
    abe = AB_STRESS_AREA.get(d, math.pi * d * d / 4.0 * 0.75)
    if str(ab_type).startswith('ABR'):
        return abe, abe
    return math.pi * d * d / 4.0, abe


# ---------------------------------------------------------------------------
# 仕様 (グループごと) の既定値と正規化
# ---------------------------------------------------------------------------

#: 仕様の項目 (CSV 列順)。(キー, 見出し, 型)
SPEC_FIELDS = [
    ('group', 'グループ', str),
    ('bp_D', 'BPせい(H方向)mm', float),
    ('bp_B', 'BP幅(B方向)mm', float),
    ('bp_t', 'BP厚mm', float),
    ('bp_F', 'BP F値', float),
    ('ab_type', 'AB種類', str),
    ('ab_d', 'AB呼び径', float),
    ('ab_Ab', 'AB軸部断面積mm2', float),
    ('ab_Abe', 'ABねじ部有効断面積mm2', float),
    ('ab_F', 'AB F値', float),
    ('ductile', '伸び能力(1/0)', int),
    ('n_all', 'AB全本数', int),
    ('nt_H', '引張側本数(H方向)', int),
    ('dtl_H', 'BP縁〜AB芯(H方向)mm', float),
    ('nt_B', '引張側本数(B方向)', int),
    ('dtl_B', 'BP縁〜AB芯(B方向)mm', float),
    ('hole', 'AB孔径mm', float),
    ('lb', 'AB有効長さlb mm', float),
    ('la', 'AB定着長さmm', float),
    ('Fc', '基礎Fc', float),
    ('n_ratio', 'ヤング係数比n', float),
    ('fnd_D', '基礎柱形せい(H方向)mm', float),
    ('fnd_B', '基礎柱形幅(B方向)mm', float),
    ('fnd_h', '柱形立上り高さmm', float),
    ('anc_type', '定着(each=個別金物/plate=連結金物/hook=フック)', str),
    ('anc_Dp', '定着金物寸法Dp mm', float),
]

#: 空欄を許す仕様項目 (空欄=その検討を省略)
OPTIONAL = ('fnd_D', 'fnd_B', 'hole', 'la', 'fnd_h', 'anc_Dp', 'n_ratio')


def n_ratio_of(fc):
    """コンクリートに対する鋼材のヤング係数比 n (RC規準の値).

    Fc≦27: 15、≦36: 13、≦48: 11、≦60: 9、それ以上: 7
    """
    for lim, n in ((27.0, 15.0), (36.0, 13.0), (48.0, 11.0), (60.0, 9.0)):
        if fc <= lim:
            return n
    return 7.0


def default_spec(group, col=None):
    """柱断面から見当をつけた既定の仕様 (設計者が必ず修正する前提)."""
    Hc = col['H'] if col else 300.0
    Bc = col['B'] if col else 300.0
    d = 24
    ab, abe = ab_default_areas('ABR400', d)
    bpD = 50.0 * math.ceil((Hc + 200.0) / 50.0)
    bpB = 50.0 * math.ceil((Bc + 200.0) / 50.0)
    return {'group': group, 'bp_D': bpD, 'bp_B': bpB, 'bp_t': 32.0,
            'bp_F': 325.0, 'ab_type': 'ABR400', 'ab_d': float(d),
            'ab_Ab': ab, 'ab_Abe': abe, 'ab_F': 235.0, 'ductile': 1,
            'n_all': 4, 'nt_H': 2, 'dtl_H': 50.0, 'nt_B': 2, 'dtl_B': 50.0,
            'hole': d + 5.0, 'lb': 20.0 * d + 32.0 + 40.0, 'la': 20.0 * d,
            'Fc': 24.0,
            'n_ratio': n_ratio_of(24.0),
            'fnd_D': bpD + 200.0, 'fnd_B': bpB + 200.0,
            'fnd_h': None, 'anc_type': 'each', 'anc_Dp': 3.0 * d}


def normalize_spec(sp):
    """UI/CSV から来た仕様を型変換する。欠けた項目は ValueError."""
    out = {}
    for key, label, typ in SPEC_FIELDS:
        v = sp.get(key)
        if v is None or (isinstance(v, str) and v.strip() == ''):
            if key in OPTIONAL:
                out[key] = None          # 任意項目 (空欄=検討しない)
                continue
            if key == 'anc_type':
                out[key] = 'each'        # 旧CSV (列なし) は個別定着とみなす
                continue
            raise ValueError('柱脚仕様 %s の「%s」が未入力です。'
                             % (sp.get('group', '?'), label))
        try:
            out[key] = typ(float(v)) if typ is int else typ(v)
        except (TypeError, ValueError):
            raise ValueError('柱脚仕様 %s の「%s」が数値ではありません: %s'
                             % (sp.get('group', '?'), label, v))
    g = out['group']
    if out['n_ratio'] is None:           # 空欄なら Fc から自動
        out['n_ratio'] = n_ratio_of(out['Fc']) if out['Fc'] > 0 else 15.0
    for key in ('bp_D', 'bp_B', 'bp_t', 'ab_d', 'ab_Ab', 'ab_Abe', 'ab_F',
                'lb', 'Fc', 'n_ratio', 'bp_F'):
        if out[key] <= 0:
            raise ValueError('柱脚仕様 %s の %s は正の値にしてください。'
                             % (g, key))
    if out['n_all'] <= 0:
        raise ValueError('柱脚仕様 %s のAB全本数が0です。' % g)
    for dr in ('H', 'B'):
        if out['nt_' + dr] < 0 or 2 * out['nt_' + dr] > out['n_all']:
            raise ValueError('柱脚仕様 %s の引張側本数(%s方向)は 0〜全本数/2 '
                             'にしてください。' % (g, dr))
        if out['nt_' + dr] > 0:
            dim = out['bp_D'] if dr == 'H' else out['bp_B']
            if not 0 < out['dtl_' + dr] < dim / 2:
                raise ValueError('柱脚仕様 %s のBP縁〜AB芯(%s方向)は 0〜BP'
                                 '寸法/2 の範囲にしてください。' % (g, dr))
    out['ductile'] = 1 if out['ductile'] else 0
    if out['anc_type'] not in ANC_TYPES:
        raise ValueError('柱脚仕様 %s の定着の形式は each (個別の定着金物)・'
                         'plate (列を連結したアンカープレート)・hook (フック) '
                         'のいずれかにしてください: %s'
                         % (g, out['anc_type']))
    if out['anc_type'] == 'hook':
        out['anc_Dp'] = None             # フックに定着金物の寸法は無い
    return out


#: 定着の形式 → 表示名
ANC_TYPES = {'each': '個別の定着金物 (Dp角)',
             'plate': '列を連結したアンカープレート (幅Dp)',
             'hook': 'フック (かぎ状に折曲げ)'}


# ---------------------------------------------------------------------------
# 柱断面の性能 (全塑性モーメント)
# ---------------------------------------------------------------------------

def col_props(col, direction):
    """柱断面の方向別性能 (mm, N)。direction='H' (強軸) / 'B' (弱軸).

    戻り値 {'A','Zp','Aw','dc','kind'}。Aw は Mpc の軸力低減の境界に用いる
    ウェブ (曲げ方向に平行な板) の断面積。角の丸みは無視する (Mp を
    やや大きく評価 = 保有耐力接合の判定では安全側)。
    """
    s = col['shape']
    if s == 'H':
        H, B, tw, tf = col['H'], col['B'], col['tw'], col['tf']
        A = 2 * B * tf + (H - 2 * tf) * tw
        if direction == 'H':
            Zp = B * tf * (H - tf) + tw * (H - 2 * tf) ** 2 / 4.0
            Aw = (H - 2 * tf) * tw
            dc = H / 2.0
        else:
            Zp = 2 * tf * B * B / 4.0 + (H - 2 * tf) * tw * tw / 4.0
            Aw = (H - 2 * tf) * tw
            dc = B / 2.0
        kind = 'H' + direction
    elif s == 'BOX':
        H, B, tw, tf = col['H'], col['B'], col['tw'], col['tf']
        A = 2 * B * tf + 2 * (H - 2 * tf) * tw
        if direction == 'H':
            Zp = B * tf * (H - tf) + 2 * tw * (H - 2 * tf) ** 2 / 4.0
            Aw = 2 * (H - 2 * tf) * tw
            dc = H / 2.0
        else:
            Zp = H * tw * (B - tw) + 2 * tf * (B - 2 * tw) ** 2 / 4.0
            Aw = 2 * (B - 2 * tw) * tf
            dc = B / 2.0
        kind = 'BOX'
    elif s == 'P':
        D, t = col['H'], col['tw']
        A = math.pi * (D - t) * t
        Zp = (D ** 3 - (D - 2 * t) ** 3) / 6.0
        Aw = A
        dc = D / 2.0
        kind = 'P'
    else:
        raise ValueError('柱断面 %s (%s) は露出柱脚の検定に未対応です '
                         '(H形・角形鋼管・円形鋼管のみ)。'
                         % (col['name'], s))
    return {'A': A, 'Zp': Zp, 'Aw': Aw, 'dc': dc, 'kind': kind}


def mpc(col, direction, N):
    """軸力を考慮した柱の全塑性モーメント Mpc [N・mm] と Mp.

    H形強軸・角形鋼管: N/Ny ≦ Aw/2A → Mp、超えると 2A/(2A−Aw)・(1−N/Ny)・Mp
      (技術基準解説書 付録1-2.6 設計例2 の Mpc=2A/(A+2Af)(1−N/Ny)Mp と同形)
    H形弱軸: N/Ny ≦ Aw/A → Mp、超えると {1−((N−Aw・σy)/(Ny−Aw・σy))²}Mp
    円形鋼管: cos(π/2・N/Ny)・Mp
    N は圧縮・引張とも絶対値で扱う。
    """
    p = col_props(col, direction)
    F = col['F']
    Mp = p['Zp'] * F
    Ny = p['A'] * F
    n = min(abs(N) / Ny, 1.0)
    if p['kind'] == 'P':
        return math.cos(math.pi / 2.0 * n) * Mp, Mp
    if p['kind'] == 'HB':
        r = p['Aw'] / p['A']
        if n <= r:
            return Mp, Mp
        aw_y = p['Aw'] * F
        return (1.0 - ((abs(N) - aw_y) / (Ny - aw_y)) ** 2) * Mp, Mp
    r = p['Aw'] / (2.0 * p['A'])
    if n <= r:
        return Mp, Mp
    return 2 * p['A'] / (2 * p['A'] - p['Aw']) * (1.0 - n) * Mp, Mp


def alpha_of(F):
    """保有耐力接合の安全率 α (付表1.2-2 仕口部): 400N級1.3 / 490N級1.2.

    F≦295 (SN400・BCR295・STKR400 等) を400N級とみなす。
    """
    return 1.3 if F <= 295.0 else 1.2


# ---------------------------------------------------------------------------
# 方向ごとの幾何
# ---------------------------------------------------------------------------

def dir_geom(sp, direction):
    """方向別の BP 寸法・AB配置。

    D: 曲げ方向のBP寸法、b: 直交方向のBP寸法、dtl: BP縁〜引張側AB芯、
    nt: 引張側本数 (0=この方向にABが無い: 全本数が中心に集中とみなす)。
    """
    if direction == 'H':
        D, b = sp['bp_D'], sp['bp_B']
    else:
        D, b = sp['bp_B'], sp['bp_D']
    nt = sp['nt_' + direction]
    dtl = sp['dtl_' + direction] if nt > 0 else D / 2.0
    return {'D': D, 'b': b, 'nt': nt, 'dtl': dtl,
            'nt_eff': nt if nt > 0 else sp['n_all']}


def kbs(sp, col, direction):
    """回転剛性 KBS [kN・m/rad] と内訳 (付1.2-20)."""
    g = dir_geom(sp, direction)
    dc = col_props(col, direction)['dc']
    if g['nt'] > 0:
        nt, dt = g['nt'], g['D'] / 2.0 - g['dtl']
    else:
        nt, dt = sp['n_all'], 0.0
    k = E_AB * nt * sp['ab_Ab'] * (dt + dc) ** 2 / (2.0 * sp['lb'])
    return {'K': k / 1e6, 'nt': nt, 'dt': dt, 'dc': dc}


# ---------------------------------------------------------------------------
# 許容応力度: BP下面の σc と AB引張力 T (SS7 6.8.1(2) ケース1〜5)
# ---------------------------------------------------------------------------

def _cubic_root(coef, lo, hi):
    """3次式の実根のうち lo < x ≦ hi のもの (複数なら大きい方)."""
    rts = np.roots(coef)
    cand = [float(r.real) for r in rts
            if abs(r.imag) <= 1e-7 * max(1.0, abs(r.real))
            and lo < r.real <= hi * (1 + 1e-9)]
    if not cand:
        return None
    return max(cand)


def base_stress(N, M, g, ab, n_all, n_ratio):
    """BP下面のコンクリート最大圧縮応力度 σc [N/mm²] と引張側AB群の
    引張力 T [N]、中立軸 xn [mm]、ケース番号を返す。

    N: 軸力 (圧縮が正) [N]、M: 曲げモーメントの大きさ [N・mm] (≧0)。
    g: dir_geom()、ab: AB 1本の軸部断面積 (中立軸計算用)。
    記号は SS7 6.8.1(2): b=BP幅, D=BPせい, dt=BP縁〜引張側AB群重心,
    at=引張側ABの総断面積 (軸部、引張側0本なら全本数)、ag=全ABの総断面積。
    """
    M = abs(M)
    b, D = g['b'], g['D']
    dt = g['dtl']
    at = g['nt_eff'] * ab
    ag = n_all * ab
    n = n_ratio
    two_side = g['nt'] > 0        # 引張側0本ならケース4は無視
    out = {'case': 0, 'sc': 0.0, 'T': 0.0, 'xn': None}
    if abs(N) < 1e-6:
        if M < 1e-6:
            return out
        xn = (n * at / b) * (-1.0 + math.sqrt(1.0 + 2.0 * b * (D - dt)
                                             / (n * at)))        # (6.130)
        out.update(case=0, xn=xn,
                   sc=2 * M / (b * xn * (D - dt - xn / 3.0)),    # (6.131)
                   T=M / (D - dt - xn / 3.0))                    # (6.132)
        return out
    e = M / N
    if N > 0:
        if e <= D / 6.0:                                          # ケース1
            out.update(case=1, sc=N / (b * D) * (1 + 6 * e / D))  # (6.122)
            return out
        if e <= D / 6.0 + dt / 3.0:                               # ケース2
            out.update(case=2, sc=2 * N / (3 * b * (D / 2.0 - e)),
                       xn=3 * (D / 2.0 - e))                      # (6.124-126)
            return out
    else:
        # ケース5: 圧縮なし (|e| ≦ 2(D/2−dt)²/D)
        if abs(e) <= 2 * (D / 2.0 - dt) ** 2 / D + 1e-9:
            T = (M / (D - 2 * dt) if D - 2 * dt > 0 else 0.0) \
                - N * at / ag                                     # (6.137)
            out.update(case=5, sc=0.0, T=T)
            return out
    # ケース3: 一部圧縮・引張側ABのみ引張 (6.127)
    K = 6 * n * at / b
    c = e + D / 2.0 - dt
    xn = _cubic_root([1.0, 3 * (e - D / 2.0), K * c, -K * c * (D - dt)],
                     0.0, D - dt)
    if xn is not None and (N > 0 or xn >= dt or not two_side):
        sc = 2 * N * c / (b * xn * (D - dt - xn / 3.0))           # (6.128)
        T = N * (e - D / 2.0 + xn / 3.0) / (D - dt - xn / 3.0)    # (6.129)
        out.update(case=3, sc=sc, T=T, xn=xn)
        return out
    # ケース4: 一部圧縮・両側のABが引張 (6.133-6.135)
    K4 = 12 * n * at / b
    xn = _cubic_root([1.0, 3 * (e - D / 2.0), K4 * e,
                      -K4 * (e * D / 2.0 + (D / 2.0 - dt) ** 2)], 0.0, dt)
    if xn is None:
        raise ValueError('柱脚の中立軸が求まりません (N=%.1fkN, M=%.1fkNm)'
                         % (N / 1e3, M / 1e6))
    sc = 2 * N * e / (b * xn * (D / 2.0 - xn / 3.0)
                      + 2 * n * at * (D / 2.0 - dt) * (D - 2 * dt) / xn)
    T = n * at * sc * (D - dt - xn) / xn
    out.update(case=4, sc=sc, T=T, xn=xn)
    return out


def bolt_allow(T, Q, N, g, sp, term, method):
    """ABの許容応力度検定 (SS7 6.8.1(3)(4))。term='long'/'short'.

    せん断は摩擦 QA=0.4(T+N) (6.139) で負担できればABはせん断を負担しない。
    負担できない場合は τ=Q/ag (全本数・ねじ部) と σt=T/at (引張側・ねじ部)
    を 鋼構造設計規準2005 (fts=min(ft, 1.4ft−1.6τ)) または
    許容応力度設計規準2019 (fts=√(ft²−3τ²)) で検定する。
    """
    F = sp['ab_F']
    ft = F / 1.5 if term == 'long' else F
    fs = ft / math.sqrt(3.0)
    a_te = g['nt_eff'] * sp['ab_Abe']
    a_ge = sp['n_all'] * sp['ab_Abe']
    st = max(T, 0.0) / a_te
    qa = 0.4 * max(T + N, 0.0)
    r = {'ft': ft, 'fs': fs, 'st': st, 'QA': qa, 'tau': 0.0,
         'fts': ft, 'fric': Q <= qa + 1e-6}
    if not r['fric']:
        tau = Q / a_ge
        if method == '2019':
            fts = math.sqrt(max(ft * ft - 3 * tau * tau, 0.0))
        else:
            fts = min(ft, 1.4 * ft - 1.6 * tau)
        r.update(tau=tau, fts=max(fts, 0.0))
    r['r_t'] = st / r['fts'] if r['fts'] > 0 else (0.0 if st == 0 else 99.9)
    r['r_s'] = r['tau'] / fs
    return r


# ---------------------------------------------------------------------------
# 終局耐力 (付1.2-31〜41 / SS7 10.31〜10.43)
# ---------------------------------------------------------------------------

def ultimate(N, g, sp, col, direction):
    """Nu, Tu, Mu, My, Qu 等 [N, N・mm]。N は圧縮が正."""
    Fc = sp['Fc']
    F = sp['ab_F']
    Nu = 0.85 * g['b'] * g['D'] * Fc
    if g['nt'] > 0:
        nt = g['nt']
        dt = g['D'] / 2.0 - g['dtl']
    else:
        nt = sp['n_all'] / 2.0
        dt = 0.0
    Tu = nt * sp['ab_Ab'] * F
    D = g['D']
    if Nu >= N > Nu - Tu:
        Mu = (Nu - N) * dt                                        # (付1.2-31)
        rng = 1
    elif Nu - Tu >= N > -Tu:
        Mu = Tu * dt + (N + Tu) * D / 2.0 * (1 - (N + Tu) / Nu)   # (付1.2-32)
        rng = 2
    elif -Tu >= N > -2 * Tu:
        Mu = (N + 2 * Tu) * dt                                    # (付1.2-33)
        rng = 3
    else:
        Mu = 0.0
        rng = 0
    Su = nt * sp['ab_Ab'] * F / math.sqrt(3.0)                    # (付1.2-41)
    if rng == 1:
        Qfu = 0.5 * Nu
        Qbu = Su * (1 + math.sqrt(max(0.0, 1 - ((Nu - N) / Tu) ** 2)))
    elif rng == 2:
        Qfu = 0.5 * (N + Tu)
        Qbu = Su
    elif rng == 3:
        Qfu = 0.0
        Qbu = Su * math.sqrt(max(0.0, 1 - (-N / Tu - 1) ** 2))
    else:
        Qfu = Qbu = 0.0
    dc = col_props(col, direction)['dc']
    # 伸び能力なしの降伏曲げ耐力 (付1.2-46)。ねじ部で決まる
    My = nt * sp['ab_Abe'] * F * (dt + dc) + N * dc
    return {'Nu': Nu, 'Tu': Tu, 'Mu': max(Mu, 0.0), 'rng': rng,
            'Qfu': Qfu, 'Qbu': Qbu, 'Qu': max(Qfu, Qbu), 'Su': Su,
            'My': max(My, 0.0), 'dt': dt, 'dc': dc, 'nt': nt}


def bqa_ult(Tb, g, sp, method):
    """伸び能力なしのABのせん断耐力 bQA (SS7 6.115〜6.118、ft0=F)."""
    F = sp['ab_F']
    fs = F / math.sqrt(3.0)
    a_ge = sp['n_all'] * sp['ab_Abe']
    a_te = g['nt_eff'] * sp['ab_Abe']
    st = Tb / a_te
    if method == '2019':
        return math.sqrt(max(0.0, (F * F - max(st, 0.0) ** 2) / 3.0)) * a_ge
    if Tb <= 0:
        return fs * a_ge
    if Tb <= a_te * F:
        return min(fs, (1.4 * F - st) / 1.6) * a_ge
    return 0.25 * F * a_ge


# ---------------------------------------------------------------------------
# モデルの読込 (柱脚の抽出)
# ---------------------------------------------------------------------------

def _section_table(mgt_path, notes):
    """{断面番号: {'shape','H','B','tw','tf','name'}} (mm)。"""
    from mgtkit.capfig.draw import _section_names
    names = _section_names(_lines(mgt_path))
    try:
        sections, _no, _nm = mgtopen_section(mgt_path)
    except Exception as e:                        # noqa: BLE001
        notes.append('断面の読込で例外が出たため寸法が取れない断面が'
                     'あります: %s' % e)
        return {}
    tab = {}

    def _rows(i):
        s = sections[i] if i < len(sections) else None
        if s is None or not np.size(s):
            return []
        return np.atleast_2d(np.asarray(s, dtype=float))

    for r in _rows(1):          # H: [no, H, B, tw, tf1, B2, tf2, r]
        tab[int(r[0])] = {'shape': 'H', 'H': r[1] * 1e3, 'B': r[2] * 1e3,
                          'tw': r[3] * 1e3, 'tf': r[4] * 1e3}
    for r in _rows(7):          # BOX: [no, H, B, tw, tf, 0]
        tab[int(r[0])] = {'shape': 'BOX', 'H': r[1] * 1e3, 'B': r[2] * 1e3,
                          'tw': r[3] * 1e3, 'tf': r[4] * 1e3}
    for r in _rows(4):          # P: [no, D, t]
        tab[int(r[0])] = {'shape': 'P', 'H': r[1] * 1e3, 'B': r[1] * 1e3,
                          'tw': r[2] * 1e3, 'tf': r[2] * 1e3}
    for no, d in tab.items():
        d['name'] = names.get(no, str(no))
    return tab, names


def _steel_F(mat_row):
    """材料行 → 鋼材のF値 [N/mm²]。鉄骨でなければ None."""
    kind, v = int(mat_row[1]), float(mat_row[2])
    if kind == 1:                 # SN系: 引張強さの呼び
        return {400: 235.0, 490: 325.0, 520: 355.0}.get(int(v), None)
    if kind == 2:                 # BCR/BCP/STKR: F値そのもの
        return v
    return None


def read_model(mgt_path, notes=None):
    """mgt を読み、柱脚 (鉄骨の鉛直材の最下端要素) を抽出する."""
    notes = notes if notes is not None else []
    node = mgtopen_node(mgt_path)
    pos = {int(r[0]): (float(r[1]), float(r[2]), float(r[3]))
           for r in np.atleast_2d(node)}
    beam = np.atleast_2d(mgtopen_beam(mgt_path))
    mat = np.atleast_2d(mgtopen_material(mgt_path))
    matF = {}
    for r in mat:
        if r.size >= 3:
            matF[int(r[0])] = _steel_F(r)
    res = _section_table(mgt_path, notes)
    sec_tab, sec_names = res if isinstance(res, tuple) else ({}, {})

    # 鉛直材 (水平成分が鉛直成分の tan10° 以下) を集める
    vert = {}
    for r in beam:
        if r.size < 5:
            continue
        ele, m, s, ni, nj = (int(r[0]), int(r[1]), int(r[2]),
                             int(r[3]), int(r[4]))
        if ni not in pos or nj not in pos:
            continue
        pi, pj = pos[ni], pos[nj]
        dz = pj[2] - pi[2]
        dh = math.hypot(pj[0] - pi[0], pj[1] - pi[1])
        if abs(dz) < 1e-9 or dh > math.tan(math.radians(10)) * abs(dz):
            continue
        low, up = (ni, nj) if dz > 0 else (nj, ni)
        vert[ele] = {'ele': ele, 'mat': m, 'sec': s, 'ni': ni, 'nj': nj,
                     'low': low, 'up': up, 'i_is_low': dz > 0,
                     'beta': float(r[5]) if r.size > 5 else 0.0,
                     'z': pos[low][2], 'xy': pos[low][:2]}
    # 下に「鉄骨の」鉛直材が続く要素は柱の途中 (分割要素) とみなす。
    # 下が RC 柱などコンクリートの鉛直材なら、その上端に載る鉄骨柱の
    # 下端は露出柱脚として扱う (RC 柱頭に鉄骨柱を載せる架構)
    uppers = {v['up'] for v in vert.values()
              if matF.get(v['mat']) is not None}
    bases = {}
    for ele, v in vert.items():
        if v['low'] in uppers:          # 下に鉄骨の鉛直材が続く → 柱脚でない
            continue
        F = matF.get(v['mat'])
        if F is None:                   # 鉄骨でない (RC柱など)
            continue
        c = dict(v)
        c['F'] = F
        sd = sec_tab.get(v['sec'])
        if sd is None:
            c.update(shape='?', H=0.0, B=0.0, tw=0.0, tf=0.0,
                     name=sec_names.get(v['sec'], str(v['sec'])))
        else:
            c.update(sd)
        bases[ele] = c

    g_name, g_elem, g_node = mgtopen_group(mgt_path)
    groups = []
    for i, nm in enumerate(g_name):
        els = {int(x) for x in np.atleast_1d(
            np.asarray(g_elem[i], dtype=float)).ravel()}
        nds = {int(x) for x in np.atleast_1d(
            np.asarray(g_node[i], dtype=float)).ravel()}
        hit = sorted(e for e, c in bases.items()
                     if e in els or (not els and c['low'] in nds))
        if hit:
            groups.append({'name': str(nm).strip(), 'eles': hit})
    return {'bases': bases, 'groups': groups}


def read_targets(mgt_path, notes=None):
    """対象読込: 柱脚を含むグループの一覧 (UI用)."""
    notes = notes if notes is not None else []
    m = read_model(mgt_path, notes)
    out = []
    for g in m['groups']:
        cols = [m['bases'][e] for e in g['eles']]
        secs = {}
        for c in cols:
            secs.setdefault(c['name'], c)
        rep = max(cols, key=lambda c: c['H'] * c['B'])
        out.append({'name': g['name'], 'n': len(cols), 'eles': g['eles'],
                    'secs': [{'name': k, 'shape': c['shape'],
                              'H': c['H'], 'B': c['B'], 'F': c['F']}
                             for k, c in secs.items()],
                    'default': default_spec(g['name'], rep)})
    if not out:
        notes.append('柱脚 (鉄骨の鉛直材で、下に鉄骨の鉛直材が続かない要素) を'
                     '含むグループが見つかりませんでした。')
    unk = sorted({c['name'] for c in m['bases'].values()
                  if c['shape'] not in ('H', 'BOX', 'P')})
    if unk:
        notes.append('H形・角形鋼管・円形鋼管以外の柱断面 (%s) は検定の'
                     '対象外です。' % ', '.join(unk))
    return {'groups': out, 'n_bases': len(m['bases'])}


# ---------------------------------------------------------------------------
# 荷重ケース
# ---------------------------------------------------------------------------

def build_cases(case_types):
    """検定ケース list。

    各要素: {'name','term','combo':[(no,係数)], 'seis': (長期combo, 地震combo)
    または None}。seis は γ 倍の検討に用いる NL/NE の分解:
      L±H … 長期 L と ±H
      S   … 先頭の長期ケース L0 と (S − L0) (組合せ済みの短期ケースを
            長期+地震とみなして分解する)
      M   … 中短期 (積雪等): 地震を含まないので γ 倍の対象外
    """
    L = [(c['no'], c.get('name') or 'C%g' % c['no'])
         for c in case_types if c['type'] == 'L']
    H = [(c['no'], c.get('name') or 'C%g' % c['no'])
         for c in case_types if c['type'] == 'H']
    S = [(c['no'], c.get('name') or 'C%g' % c['no'])
         for c in case_types if c['type'] == 'S']
    Mc = [(c['no'], c.get('name') or 'C%g' % c['no'])
          for c in case_types if c['type'] == 'M']
    if not L:
        raise ValueError('長期(L)の荷重ケースが指定されていません。')
    cases = []
    for lno, lname in L:
        cases.append({'name': lname, 'term': 'long',
                      'combo': [(lno, 1.0)], 'seis': None})
    for lno, lname in L:
        for hno, hname in H:
            for sg, sym in ((1.0, '+'), (-1.0, '-')):
                cases.append({'name': lname + sym + hname, 'term': 'short',
                              'combo': [(lno, 1.0), (hno, sg)],
                              'seis': ([(lno, 1.0)], [(hno, sg)])})
    l0 = L[0][0]
    for sno, sname in S:
        cases.append({'name': sname, 'term': 'short',
                      'combo': [(sno, 1.0)],
                      'seis': ([(l0, 1.0)], [(sno, 1.0), (l0, -1.0)])})
    for mno, mname in Mc:
        cases.append({'name': mname, 'term': 'short',
                      'combo': [(mno, 1.0)], 'seis': None})
    return cases


def _end_forces(bs, bases):
    """{ケース: {要素: (N, QH, MH, QB, MB)}} [N, N・mm]。柱脚側の端の値.

    N=−Fx (圧縮正)、H方向: Q=Fz・M=My、B方向: Q=Fy・M=Mz。
    柱脚側 = 下端節点側 (i端が下なら要素の先頭行、そうでなければ末尾行)。
    """
    out = {}
    eles = set(bases)
    for cno in np.unique(np.asarray(bs[:, 1], dtype=float)):
        block = bs[bs[:, 1] == cno]
        d = {}
        for ele in eles:
            rows = block[block[:, 0] == ele]
            if rows.shape[0] == 0:
                continue
            r = rows[0] if bases[ele]['i_is_low'] else rows[-1]
            d[ele] = (-r[2] * 1e3, r[4] * 1e3, r[6] * 1e6,
                      r[3] * 1e3, r[7] * 1e6)
        out[float(cno)] = d
    return out


def _combo(forces, combo, ele, fac=1.0):
    out = np.zeros(5)
    for cno, f in combo:
        d = forces.get(float(cno))
        if d is None or ele not in d:
            return None
        out += fac * f * np.asarray(d[ele])
    return out


# ---------------------------------------------------------------------------
# 検定本体
# ---------------------------------------------------------------------------

ROUTES = {'1-1': 'ルート1-1', '1-2': 'ルート1-2', '2': 'ルート2',
          '3': 'ルート3'}


def _bp_bending(st, g, col, sp, direction, term):
    """ベースプレートの曲げ (SS7 6.8.5、リブなし・片持ち板として).

    圧縮側: L1 = 柱外縁〜BP縁、w = σc
      e ≦ D/6 (全面圧縮) → Mb = 0.5wL1²
      L1 ≦ xn → Mb = {0.5 − L1/(6xn)}wL1²
      L1 > xn → Mb = (w・xn/2)(L1 − xn/3)
    引張側 (1辺支持): Mb = P・L/(2L+d)、P = T/nt、L = AB芯〜柱面
    σb = Mb/(t²/6) ≦ fb' = F/1.3 (長期) / 1.5F/1.3 (短期)
    """
    dcol = col_props(col, direction)['dc'] * 2.0
    L1 = (g['D'] - dcol) / 2.0
    w = st['sc']
    if L1 <= 0 or w <= 0:
        mc = 0.0
    elif st['case'] == 1 or st['xn'] is None:
        mc = 0.5 * w * L1 * L1
    elif L1 <= st['xn']:
        mc = (0.5 - L1 / (6 * st['xn'])) * w * L1 * L1
    else:
        mc = w * st['xn'] / 2.0 * (L1 - st['xn'] / 3.0)
    mt = 0.0
    Lt = g['D'] / 2.0 - g['dtl'] - dcol / 2.0
    if g['nt'] > 0 and st['T'] > 0 and Lt > 0:
        P = st['T'] / g['nt']
        mt = P * Lt / (2 * Lt + sp['ab_d'])
    t = sp['bp_t']
    fb = sp['bp_F'] / 1.3 * (1.0 if term == 'long' else 1.5)
    sb = max(mc, mt) / (t * t / 6.0)
    return {'Mc': mc, 'Mt': mt, 'sb': sb, 'fb': fb, 'r': sb / fb,
            'L1': L1, 'Lt': Lt,
            't_req': math.sqrt(6 * max(mc, mt) / fb)}


def _bp_target(col, direction):
    """BP曲げを検定する方向か (SS7: 角形鋼管の両方向・H形の強軸のみ)."""
    if col['shape'] == 'BOX':
        return True
    return col['shape'] == 'H' and direction == 'H'


def check_column(c, sp, cases, forces, params):
    """柱脚1本・2方向の検定結果 list を返す."""
    route = params['route']
    gamma = params['gamma']
    method = params['bolt_method']
    rows = []
    for dr in ('H', 'B'):
        g = dir_geom(sp, dr)
        k = kbs(sp, c, dr)
        qi = 1 if dr == 'H' else 3
        mi = 2 if dr == 'H' else 4
        # ---- 許容応力度 (全ケース) ----
        allow = None
        # ルート1-1 のコーン状破壊用: ABの引張の有無と、摩擦で負担できない
        # せん断力 (ABがせん断を負担するケース) の最大
        al_cone = {'T_any': False, 'shear_by_bolt': False, 'QD': 0.0,
                   'QD_case': None}
        for cs in cases:
            f = _combo(forces, cs['combo'], c['ele'])
            if f is None:
                continue
            N, Q, M = f[0], abs(f[qi]), abs(f[mi])
            st = base_stress(N, M, g, sp['ab_Ab'], sp['n_all'],
                             sp['n_ratio'])
            fc = sp['Fc'] / 3.0 * (1.0 if cs['term'] == 'long' else 2.0)
            bt = bolt_allow(st['T'], Q, N, g, sp, cs['term'], method)
            rec = {'case': cs['name'], 'term': cs['term'],
                   'N': N, 'M': M, 'Q': Q, 'st': st, 'fc': fc,
                   'r_c': st['sc'] / fc, 'bolt': bt}
            rec['r'] = max(rec['r_c'], bt['r_t'], bt['r_s'])
            if params.get('do_bp') and _bp_target(c, dr):
                rec['bp'] = _bp_bending(st, g, c, sp, dr, cs['term'])
                rec['r'] = max(rec['r'], rec['bp']['r'])
            if allow is None or rec['r'] > allow['r']:
                allow = rec
            al_cone['T_any'] = al_cone['T_any'] or st['T'] > 1e-6
            if not bt['fric']:
                al_cone['shear_by_bolt'] = True
                if Q > al_cone['QD']:
                    al_cone.update(QD=Q, QD_case=cs['name'])
        # ---- 保有耐力接合 / γ倍応力の検討 (ルート1-1以外) ----
        ult = None
        fnd = None
        if route != '1-1':
            for cs in cases:
                if cs['seis'] is None:
                    continue
                fl = _combo(forces, cs['seis'][0], c['ele'])
                fe = _combo(forces, cs['seis'][1], c['ele'])
                if fl is None or fe is None:
                    continue
                Ns = fl[0] + gamma * fe[0]
                Ms = abs(fl[mi] + gamma * fe[mi])
                Qs = abs(fl[qi] + gamma * fe[qi])
                u = ultimate(Ns, g, sp, c, dr)
                mpc_v, mp_v = mpc(c, dr, Ns)
                al = alpha_of(c['F'])
                rec = {'case': cs['name'], 'N': Ns, 'M': Ms, 'Q': Qs,
                       'u': u, 'Mpc': mpc_v, 'Mp': mp_v, 'alpha': al}
                if sp['ductile']:
                    rec['hoyu'] = u['Mu'] >= al * mpc_v       # (付1.2-21)
                    rec['ok_q'] = u['Qu'] >= Qs               # (付1.2-22)
                    rec['ok_m'] = u['Mu'] >= Ms               # フロー④
                    # 保有耐力接合なら曲げは検定済み (比0)。でなければ M*/Mu
                    if rec['hoyu']:
                        rec['r_m'] = 0.0
                    else:
                        rec['r_m'] = Ms / u['Mu'] if u['Mu'] > 0 else 99.9
                    rec['r_q'] = Qs / u['Qu'] if u['Qu'] > 0 else 99.9
                    rec['ok'] = rec['ok_q'] and (rec['hoyu'] or
                                                 rec['ok_m'])
                    rec['r'] = max(rec['r_q'], rec['r_m'])
                    if route == '3':
                        # ルート3: ⑧ 判定のみ (Ds 割増しの要否)。
                        # 保有耐力接合でなくてもNGとはしない
                        rec['ok'] = rec['ok_q']
                        rec['r'] = rec['r_q']
                else:
                    st = base_stress(Ns, Ms, g, sp['ab_Ab'], sp['n_all'],
                                     sp['n_ratio'])
                    Pb = g['nt_eff'] * sp['ab_Abe'] * sp['ab_F']
                    qf = 0.4 * max(st['T'] + Ns, 0.0)
                    qb = bqa_ult(st['T'], g, sp, method)
                    Qy = max(qf, qb)
                    rec.update(st=st, Pb=Pb, Qfa=qf, bQA=qb, Qy=Qy)
                    rec['ok_c'] = st['sc'] < sp['Fc']          # (付1.2-23)
                    rec['ok_t'] = st['T'] < Pb                 # (付1.2-24)
                    rec['ok_q'] = Qy >= Qs
                    rec['r'] = max(st['sc'] / sp['Fc'], st['T'] / Pb,
                                   Qs / Qy if Qy > 0 else 99.9)
                    # ルート3 (⑪): 柱脚My > α・Mpc の判定 (Ds の扱い)
                    myv = min(u['My'], u['Mu'])
                    rec['My_eff'] = myv
                    rec['hoyu'] = myv >= al * mpc_v            # (付1.2-44)
                    rec['ok'] = rec['ok_c'] and rec['ok_t'] and rec['ok_q']
                if ult is None or rec['r'] > ult['r']:
                    ult = rec
                # ---- 基礎コンクリートの破壊防止 (フロー⑥) ----
                cy = g['nt_eff'] * sp['ab_Ab'] * sp['ab_F'] + max(Ns, 0.0)
                sbb = (u['Qbu'] >= u['Qfu'] if sp['ductile']
                       else rec['bQA'] >= rec['Qfa'])
                tb = (rec['st'] if 'st' in rec else
                      base_stress(Ns, Ms, g, sp['ab_Ab'], sp['n_all'],
                                  sp['n_ratio']))['T']
                if fnd is None:
                    fnd = {'case': cs['name'], 'cy': cy, 'N': Ns,
                           'shear_by_bolt': False, 'T_any': False,
                           'QD': 0.0, 'QD_case': None}
                elif cy > fnd['cy']:
                    fnd.update(case=cs['name'], cy=cy, N=Ns)
                fnd['shear_by_bolt'] = fnd['shear_by_bolt'] or sbb
                fnd['T_any'] = fnd['T_any'] or tb > 1e-6
                if Qs > fnd['QD']:
                    fnd.update(QD=Qs, QD_case=cs['name'])
            if fnd is not None:
                fnd.update(_foundation(fnd['cy'], g, sp, dr))
                fnd.update(_cones(fnd, sp, dr))
            else:
                # 水平・短期ケースが無く γ倍の検討ができない場合も、定着の
                # コーン状破壊は許容応力度の検討ケースで確認しておく
                fnd = dict(al_cone, cone_only=True)
                fnd.update(_cones(fnd, sp, dr))
        else:
            # ルート1-1: フロー6 (基礎コンクリートの破壊防止) は対象外だが、
            # 定着のコーン状破壊は告示1456号ハのただし書きの根拠になるため
            # 許容応力度の検討ケースで確認する
            fnd = dict(al_cone, cone_only=True)
            fnd.update(_cones(fnd, sp, dr))
        rows.append({'ele': c['ele'], 'sec': c['name'], 'dir': dr,
                     'shape': c['shape'], 'F': c['F'], 'kbs': k,
                     'allow': allow, 'ult': ult, 'fnd': fnd})
    return rows


def _foundation(cy, g, sp, direction):
    """基礎コンクリート立上り部の破壊防止 (付1.2-25〜29)."""
    Fc = sp['Fc']
    B0 = sp['bp_B'] * sp['bp_D']
    out = {'c2': cy / B0, 'c2a': Fc / 3.0}
    out['ok_b'] = out['c2'] < out['c2a']
    fD = sp['fnd_D'] if direction == 'H' else sp['fnd_B']
    fB = sp['fnd_B'] if direction == 'H' else sp['fnd_D']
    if fD and fB and fD > g['D']:
        X = (fD - g['D']) / 2.0
        out.update(X=X, c1=cy / (2.0 * fB * X), ok_a=cy / (2.0 * fB * X) < Fc)
        # 端部のせん断による剥落 (1本): AB芯〜柱形縁 e > 0.54√(σy/cσt)・d
        if g['nt'] > 0:
            e_have = X + g['dtl']
            cst = 0.31 * math.sqrt(Fc)
            e_req = 0.54 * math.sqrt(sp['ab_F'] / cst) * sp['ab_d']
            out.update(e_have=e_have, e_req=e_req, ok_e=e_have > e_req)
    return out


# ---------------------------------------------------------------------------
# コーン状破壊 (技術基準 (付1.2-30)・付図1.2-35/37、SS7 6.8.1 フロー6)
# ---------------------------------------------------------------------------

#: コーン状破壊の低減係数 φ1 (短期)
PHI1 = 0.6


def bolt_positions(sp):
    """BP中心を原点とするABの平面座標 [(x, y)] [mm]。x=H方向、y=B方向.

    外周配置を仮定する: H方向の両端列 (x=±(D/2−dtl_H)) に nt_H 本ずつ、
    B方向の両端列 (y=±(B/2−dtl_B)) に nt_B 本ずつ、それぞれ等間隔
    (隅のボルトは両方の列に共有)。引張側本数0の方向は中心線上。
    """
    D, B = sp['bp_D'], sp['bp_B']
    ntH, ntB = sp['nt_H'], sp['nt_B']
    xr = D / 2.0 - sp['dtl_H'] if ntH > 0 else 0.0
    yr = B / 2.0 - sp['dtl_B'] if ntB > 0 else 0.0
    pts = set()

    def _line(n, half):
        if n <= 1:
            return [0.0]
        return [-half + 2 * half * i / (n - 1) for i in range(n)]
    if ntH > 0:
        span = yr if ntB > 0 else B / 2.0 - sp['dtl_H']
        for y in _line(ntH, span):
            pts.add((round(xr, 6), round(y, 6)))
            pts.add((round(-xr, 6), round(y, 6)))
    if ntB > 0:
        span = xr if ntH > 0 else D / 2.0 - sp['dtl_B']
        for x in _line(ntB, span):
            pts.add((round(x, 6), round(yr, 6)))
            pts.add((round(x, 6), round(-yr, 6)))
    if not pts:
        pts.add((0.0, 0.0))
    return sorted(pts)


def _dir_xy(pts, direction):
    """方向 H: (x, y) のまま / B: 曲げ方向を x に取り直す."""
    return pts if direction == 'H' else [(y, x) for x, y in pts]


def _grid(x0, x1, y0, y1, n=700):
    step = max(max(x1 - x0, y1 - y0) / n, 0.5)
    xs = np.arange(x0, x1, step) + step / 2.0
    ys = np.arange(y0, y1, step) + step / 2.0
    X, Y = np.meshgrid(xs, ys)
    return X, Y, step * step


def cone_tension(sp, direction):
    """引張側AB群の定着部のコーン状破壊 (付1.2-30・付図1.2-35).

    有効水平投影面積 Ac = 定着金物 (頭部) から水平距離 la 以内の範囲を
    基礎柱形の上面 (BPと同心の fnd_D×fnd_B) で切り取り、定着金物の面積を
    除いたもの (45°のコーン。技術基準 設計例1 の Ac=(1)+(2)+2×(3)−(4) と
    同じ考え方)。定着金物は each: ボルトごとの Dp角、plate: 引張側の列を
    連結した幅 Dp のアンカープレート (列の両端から Dp/2 延長)。
    柱形の外 (基礎梁上面) へのコーンの広がりは無視する (安全側)。
    Tp = 0.31・φ1・√Fc・Ac
    """
    fD = sp['fnd_D'] if direction == 'H' else sp['fnd_B']
    fB = sp['fnd_B'] if direction == 'H' else sp['fnd_D']
    la, Dp = sp.get('la'), sp.get('anc_Dp')
    g = dir_geom(sp, direction)
    if sp['anc_type'] == 'hook':
        Dp = 0.0                 # 定着金物なし: ボルト芯から半径 la の円
    if not (fD and fB and la and Dp is not None) or g['nt'] <= 0:
        return None
    if sp['anc_type'] != 'hook' and not Dp:
        return None
    pts = _dir_xy(bolt_positions(sp), direction)
    xt = max(p[0] for p in pts)
    row = sorted(p[1] for p in pts if abs(p[0] - xt) < 1e-6)
    h = Dp / 2.0
    if sp['anc_type'] == 'plate':
        rects = [(xt - h, xt + h, row[0] - h, row[-1] + h)]
    else:
        rects = [(xt - h, xt + h, y - h, y + h) for y in row]
    X, Y, da = _grid(-fD / 2.0, fD / 2.0, -fB / 2.0, fB / 2.0)
    inside = np.zeros(X.shape, bool)
    head = np.zeros(X.shape, bool)
    for x0, x1, y0, y1 in rects:
        dx = np.maximum(np.maximum(x0 - X, X - x1), 0.0)
        dy = np.maximum(np.maximum(y0 - Y, Y - y1), 0.0)
        inside |= dx * dx + dy * dy <= la * la
        head |= (dx == 0) & (dy == 0)
    Ac = float((inside & ~head).sum()) * da
    Tp = 0.31 * PHI1 * math.sqrt(sp['Fc']) * Ac
    Tu = g['nt'] * sp['ab_Ab'] * sp['ab_F']
    return {'Ac': Ac, 'Tp': Tp, 'Tu': Tu, 'n_row': len(row)}


def cone_shear(sp, direction):
    """柱形側面へ向かうせん断によるコーン状破壊 (付1.2-30・付図1.2-37).

    せん断方向の縁 (柱形側面) に最も近いAB列について、各ボルトから
    側面までの距離 c を半径とする半円 (側面上、柱形上面から下向き) の
    和集合を有効投影面積 Acv とする。側面の幅 (直交方向の柱形寸法) と
    立上り高さ (入力時) で切り取る。Qc = 0.31・φ1・√Fc・Acv
    """
    fD = sp['fnd_D'] if direction == 'H' else sp['fnd_B']
    fB = sp['fnd_B'] if direction == 'H' else sp['fnd_D']
    if not (fD and fB):
        return None
    pts = _dir_xy(bolt_positions(sp), direction)
    xe = max(p[0] for p in pts)
    row = sorted(p[1] for p in pts if abs(p[0] - xe) < 1e-6)
    c = fD / 2.0 - xe
    if c <= 0:
        return None
    depth = min(c, sp['fnd_h']) if sp.get('fnd_h') else c
    X, Z, da = _grid(-fB / 2.0, fB / 2.0, 0.0, depth)
    m = np.zeros(X.shape, bool)
    for y in row:
        m |= (X - y) ** 2 + Z ** 2 <= c * c
    Acv = float(m.sum()) * da
    Qc = 0.31 * PHI1 * math.sqrt(sp['Fc']) * Acv
    return {'Acv': Acv, 'Qc': Qc, 'c': c, 'n_row': len(row)}


def _cones(fnd, sp, direction):
    """フロー6のコーン状破壊の判定。省略条件は SS7 6.8.1 による.

    定着: γ倍応力のどのケースでもABに引張が生じなければ Tu=0 (省略)。
    列状せん断: ABがせん断力を負担しない (Qfu≧Qbu、伸び能力なしは
    摩擦 ≧ bQA) 場合は省略。QD は γ倍したせん断力の最大。
    """
    out = {}
    ct = cone_tension(sp, direction)
    if ct is not None:
        ct['skip'] = not fnd['T_any']
        ct['ok'] = True if ct['skip'] else ct['Tu'] < ct['Tp']
        out['cone_t'] = ct
    elif dir_geom(sp, direction)['nt'] <= 0:
        out['cone_t_na'] = 'no_row'      # この方向に引張側の列が無い
    else:
        out['cone_t_na'] = 'no_input'    # 柱形寸法・定着長などが未入力
    cs = cone_shear(sp, direction)
    if cs is not None:
        cs['QD'] = fnd['QD']
        cs['skip'] = not fnd['shear_by_bolt']
        cs['ok'] = True if cs['skip'] else cs['Qc'] > cs['QD']
        out['cone_s'] = cs
    return out


#: 告示1456号 第1第一号ヘ の縁端距離表 (d上限, せん断縁等, 圧延縁等) [mm]
EDGE_TABLE = [(10, 18, 16), (12, 22, 18), (16, 28, 22), (20, 34, 26),
              (22, 38, 28), (24, 44, 32), (27, 49, 36), (30, 54, 40)]


def edge_min(d, sheared=False):
    """告示1456号の最小縁端距離 [mm] (30超は 9d/5・4d/3)."""
    for dmax, s, r in EDGE_TABLE:
        if d <= dmax:
            return float(s if sheared else r)
    return 9.0 * d / 5.0 if sheared else 4.0 * d / 3.0


def spec_checks(sp, col, sheared=False, cone_ok=False):
    """平12建告第1456号 第1第一号 (露出形式柱脚) の仕様規定.

    ハ 定着長さ ≧ 20d (許容応力度計算を行っても適用除外にならない。
       ただし書き: 付着力を考慮して抜け出し及びコンクリートの破壊が
       生じないことを確かめた場合は適用しない。cone_ok=True は、定着部の
       コーン状破壊の検討を全柱脚・両方向で満たしたことを表し、このとき
       ハはただし書きにより適用除外とする (定着金物・フックとも。
       ユーザーの設計判断、2026-09-25)。cone_ok が文字列のときは
       除外できない理由として適用欄に添える)
    ニ AB全断面積 ≧ 柱最下端の断面積 × 0.2
    ホ BP厚さ ≧ 1.3d
    ヘ AB孔径 ≦ d+5mm、縁端距離 ≧ 表の値
    (ニ・ホ・ヘ (とイ) は令82条一〜三号の計算を行えば適用除外)
    """
    d = sp['ab_d']
    A = col_props(col, 'H')['A'] if col['shape'] in ('H', 'BOX', 'P') \
        else None
    out = []
    if sp.get('la'):
        out.append({'item': 'ハ 定着長さ', 'req': '≧20d=%.0f' % (20 * d),
                    'have': '%.0f' % sp['la'], 'ok': sp['la'] >= 20 * d,
                    'exempt': cone_ok is True,
                    'basis': 'ただし書き' if cone_ok is True else '',
                    'why': cone_ok if isinstance(cone_ok, str) else ''})
    if A:
        ratio = sp['n_all'] * sp['ab_Ab'] / A
        out.append({'item': 'ニ AB断面積/柱断面積',
                    'req': '≧0.20', 'have': '%.2f' % ratio,
                    'ok': ratio >= 0.2, 'exempt': True})
    out.append({'item': 'ホ BP厚さ', 'req': '≧1.3d=%.1f' % (1.3 * d),
                'have': '%.0f' % sp['bp_t'], 'ok': sp['bp_t'] >= 1.3 * d,
                'exempt': True})
    if sp.get('hole'):
        out.append({'item': 'ヘ AB孔径', 'req': '≦d+5=%.0f' % (d + 5),
                    'have': '%.0f' % sp['hole'], 'ok': sp['hole'] <= d + 5,
                    'exempt': True})
    edges = []
    for dr in ('H', 'B'):
        dim = sp['bp_D'] if dr == 'H' else sp['bp_B']
        edges.append(sp['dtl_' + dr] if sp['nt_' + dr] > 0 else dim / 2.0)
    em = edge_min(d, sheared)
    out.append({'item': 'ヘ 縁端距離', 'req': '≧%.0f' % em,
                'have': '%.0f' % min(edges), 'ok': min(edges) >= em,
                'exempt': True})
    for it in out:
        it.setdefault('basis', '令82条' if it['exempt'] else '')
        # 判定欄: 適用除外の項目は規定を満たさなくても NG とは書かない
        it['mark'] = 'OK' if it['ok'] else ('-' if it['exempt'] else 'NG')
    return out


def _cone_ok_groups(rows):
    """グループごとの定着部のコーン状破壊の状態.

    True = 全柱脚・両方向で満たした (告示ハのただし書きの根拠)、
    文字列 = 除外できない理由 (未検討・NG)。
    """
    # 引張側の列が無い方向 (引張側本数0) はコーン状破壊の対象外として
    # 数えない。グループ内で1つも検討していなければ未検討とする
    res = {}
    checked = set()
    for r in rows:
        f = r.get('fnd') or {}
        ct = f.get('cone_t')
        if ct:
            checked.add(r['group'])
            st = True if ct['ok'] else 'コーン状破壊がNG'
        elif f.get('cone_t_na') == 'no_row':
            st = True
        else:
            st = 'コーン状破壊が未検討 (柱形寸法・定着長の入力が必要)'
        prev = res.get(r['group'], True)
        res[r['group']] = st if prev is True else prev
    for g in res:
        if res[g] is True and g not in checked:
            res[g] = 'コーン状破壊が未検討 (引張側の列が無い)'
    return res


def run_check(mgt_path, beam_stress_path, case_types, specs, params=None,
              notes=None):
    """全柱脚を検定する.

    specs: [{group, bp_D, ...}] (グループごとの仕様)。要素が複数の
    グループに属する場合は specs の並び順で先のグループを採用する。
    """
    notes = notes if notes is not None else []
    params = dict(params or {})
    params.setdefault('route', '2')
    params.setdefault('gamma', 1.67 if params['route'] == '1-2' else 2.0)
    params.setdefault('bolt_method', '2005')
    params.setdefault('do_bp', True)
    params['gamma'] = float(params['gamma'])
    if params['route'] not in ROUTES:
        raise ValueError('計算ルートの指定が不正です: %s' % params['route'])
    m = read_model(mgt_path, notes)
    specs = [normalize_spec(s) for s in (specs or [])]
    if not specs:
        raise ValueError('柱脚仕様が1つもありません。①で検討するグループを'
                         '選び、②で仕様を入力してください。')
    gmap = {g['name']: g for g in m['groups']}
    assign = {}
    for sp in specs:
        g = gmap.get(sp['group'])
        if g is None:
            notes.append('グループ %s に柱脚がないため仕様を無視しました。'
                         % sp['group'])
            continue
        for e in g['eles']:
            if e in assign:
                notes.append('要素 %d はグループ %s と %s の両方に属します。'
                             '%s の仕様で検定しました。'
                             % (e, assign[e]['group'], sp['group'],
                                assign[e]['group']))
                continue
            assign[e] = sp
    bs = read_beam_stress(beam_stress_path)
    if bs.size == 0:
        raise ValueError('beam_stressファイルが空です。')
    forces = _end_forces(bs, m['bases'])
    cases = build_cases(case_types)
    if params['route'] != '1-1' and not any(c['seis'] for c in cases):
        notes.append('水平(H)・短期(S)の荷重ケースが無いため、γ倍応力による'
                     '検討 (保有耐力接合等) は行っていません。')
    if any(c['type'] == 'S' for c in case_types) and params['route'] != '1-1':
        notes.append('短期(S)ケースは「S − 先頭の長期ケース」を地震時応力 '
                     'NE・ME・QE とみなして γ 倍しています。')
    rows = []
    skipped = []
    flipped = []
    for e in sorted(assign):
        c = m['bases'][e]
        if c['shape'] not in ('H', 'BOX', 'P'):
            skipped.append('%d(%s)' % (e, c['name']))
            continue
        if not c['i_is_low']:
            flipped.append(e)
        sp = assign[e]
        for r in check_column(c, sp, cases, forces, params):
            r['group'] = sp['group']
            rows.append(r)
    if skipped:
        notes.append('未対応断面のため検定しなかった柱脚: %s'
                     % ', '.join(skipped))
    if flipped:
        notes.append('i端が上端の柱要素 (%s) は j端 (下端) の応力を'
                     '用いました。' % ', '.join(str(e) for e in flipped[:20]))
    missing = sorted({r['ele'] for r in rows if r['allow'] is None})
    if missing:
        notes.append('応力ファイルに応力が無い柱脚要素: %s'
                     % ', '.join(str(e) for e in missing[:20]))
    # 告示1456号の仕様規定 (グループごと。ニは断面積が最大の柱で判定)
    kokuji = []
    cone_ok = _cone_ok_groups(rows)
    for sp in specs:
        cols = [m['bases'][e] for e, s in assign.items()
                if s is sp and m['bases'][e]['shape'] in ('H', 'BOX', 'P')]
        if not cols:
            continue
        rep = max(cols, key=lambda c: col_props(c, 'H')['A'])
        kokuji.append({'group': sp['group'], 'col': rep['name'],
                       'items': spec_checks(
                           sp, rep, bool(params.get('sheared_edge')),
                           cone_ok=cone_ok.get(
                               sp['group'], 'コーン状破壊が未検討'))})
    return {'rows': rows, 'specs': specs, 'params': params,
            'kokuji': kokuji,
            'cases': [{'name': c['name'], 'term': c['term']}
                      for c in cases],
            'summary': summarize(rows, params)}


def _ok_allow(r):
    return r['allow'] is None or r['allow']['r'] <= 1.0 + 1e-9


def summarize(rows, params):
    """グループ別の最大検定比とNG数."""
    out = {}
    for r in rows:
        s = out.setdefault(r['group'], {'group': r['group'], 'n': 0,
                                        'r_allow': 0.0, 'ng_allow': 0,
                                        'r_ult': 0.0, 'ng_ult': 0,
                                        'n_hoyu_ng': 0, 'ng_fnd': 0,
                                        'kbs_H': [], 'kbs_B': []})
        s['n'] += 1
        s['kbs_' + r['dir']].append(r['kbs']['K'])
        if r['allow']:
            s['r_allow'] = max(s['r_allow'], r['allow']['r'])
            s['ng_allow'] += 0 if _ok_allow(r) else 1
        if r['ult']:
            s['r_ult'] = max(s['r_ult'], r['ult']['r'])
            s['ng_ult'] += 0 if r['ult']['ok'] else 1
            if not r['ult'].get('hoyu', True):
                s['n_hoyu_ng'] += 1
        f = r['fnd']
        if f and not all(f.get(k, True) for k in ('ok_a', 'ok_b')):
            s['ng_fnd'] += 1
        if f and f.get('ok_e') is False and f.get('shear_by_bolt'):
            s['ng_fnd'] += 1
        for k in ('cone_t', 'cone_s'):
            if f and f.get(k) and not f[k]['ok']:
                s['ng_fnd'] += 1
    res = []
    for s in out.values():
        s['n'] //= 2
        for k in ('kbs_H', 'kbs_B'):
            v = s.pop(k)
            s[k] = (min(v), max(v)) if v else None
        res.append(s)
    return res
