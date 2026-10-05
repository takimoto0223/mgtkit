# -*- coding: utf-8 -*-
"""RCスラブの断面検定 (許容曲げモーメント・許容せん断力・構造規定).

事務所の Excel (X:\\HSC_share\\XLS\\RC_slab.xls) の次のシートを移植した。
    ダブル配筋 (長期) : 「ダブル配筋スラブ許容M-配力筋方向」
    ダブル配筋 (短期) : 「ダブル配筋スラブ許容M_短期」 (ft と fs を短期に)
    シングル配筋      : 「シングル配筋スラブ許容M」
Fc から決まる係数は Excel の値ではなく RC規準2010 の本文から取る
(ユーザー指示 2026-09-28)。
    Ec  : 表5.1  3.35e4 × (γ/24)^2 × (Fc/60)^(1/3)、γ = 表7.1 − 1.0
    γ   : 表7.1  鉄筋コンクリートの単位体積重量
    n   : 表12.1 ヤング係数比 (Fc≦27:15, ≦36:13, ≦48:11, ≦60:9)
    fc  : 表6.1  長期 Fc/3、短期は長期の2倍
    fs  : 表6.1  長期 min(Fc/30, 0.49+Fc/100) (軽量は0.9倍)、短期は1.5倍
    ft  : 表6.2  SD295A/B 195、SD345・390・490 215 (D29以上195)、短期は規格値
    fa  : 表6.3  上端 min(Fc/15, 0.9+2Fc/75)、その他 min(Fc/10, 1.35+Fc/25)
    Ma  : 13条4. (13.1)式 M = at・ft・j、j = 7/8・d
    Qa  : 15条 (18条4.) Qa = fs・b・j (b = 1m)
    Mc  : 0.56√Fc・Z (Z = t²/6、1m 幅) — Excel と同じ参考値
    構造規定: 18条1. 最小厚さ80mm (軽量100mm)、18条5. 表18.2 配筋間隔・
              D10以上・鉄筋比0.2%以上

有効せいの取り方は Excel どおり (主筋方向/配力筋方向を画面で選ぶ)。
    ダブル: 配力筋方向 (長辺、内側の段) は d = t − (かぶり + 1.5×最外径)、
            主筋方向 (短辺、外側の段) は d = t − (かぶり + 0.5×最外径)、
            正曲げ=下端筋、負曲げ=上端筋、せん断は下端側の j
    シングル: 正曲げは d = 上かぶり + 0.5×最外径 (上から)、
              負曲げは d = t − (上かぶり + 1.5×最外径) (下から) の安全側
"""

import math
import re

#: 異形鉄筋 JIS G 3112 の公称断面積 (mm²) と最外径 (mm)
BAR_AREA = {10: 71.33, 13: 126.7, 16: 198.6, 19: 286.5, 22: 387.1,
            25: 506.7, 29: 642.4, 32: 794.2}
BAR_OUTER = {10: 11, 13: 14, 16: 18, 19: 21, 22: 25, 25: 28, 29: 33, 32: 36}
SD_GRADES = ('SD295', 'SD345', 'SD390', 'SD490')
C_TYPES = ('普通', '軽量1種', '軽量2種')
E_S = 2.05e5            # 表5.1


def gamma_rc(fc, ctype='普通'):
    """表7.1 鉄筋コンクリートの単位体積重量 (kN/m³)."""
    if ctype == '軽量1種':
        return 20.0 if fc <= 27 else 22.0
    if ctype == '軽量2種':
        return 18.0
    if fc <= 36:
        return 24.0
    if fc <= 48:
        return 24.5
    return 25.0


def e_concrete(fc, gamma):
    """表5.1 (γ はコンクリートの単位体積重量 = 表7.1 − 1.0)."""
    return 3.35e4 * (gamma / 24.0) ** 2 * (fc / 60.0) ** (1.0 / 3.0)


def n_ratio(fc):
    """表12.1."""
    if fc <= 27:
        return 15
    if fc <= 36:
        return 13
    if fc <= 48:
        return 11
    return 9


def ft_long(sd, di):
    """表6.2 長期 引張 (N/mm²)."""
    if sd.startswith('SD295'):
        return 195.0
    return 195.0 if di >= 29 else 215.0


def ft_short(sd):
    """表6.2 短期 引張 = 規格降伏点."""
    return float(re.sub(r'\D', '', sd)[:3] or 295)


def parse_bar(spec):
    """'D16@200' / 'D13D16@100' (交互) → {'dias':[13,16], 'pitch':100,
    'at': mm²/m, 'dmin':13, 'dmax':16}."""
    s = str(spec).upper().replace(' ', '').replace('＠', '@')
    m = re.fullmatch(r'((?:D\d+)+)@(\d+(?:\.\d+)?)', s)
    if not m:
        raise ValueError('配筋の書き方が読めません: %s (例 D16@200, '
                         'D13D16@100)' % spec)
    dias = [int(x) for x in re.findall(r'D(\d+)', m.group(1))]
    for d in dias:
        if d not in BAR_AREA:
            raise ValueError('鉄筋径 D%d は未対応です (D10〜D32)。' % d)
    pitch = float(m.group(2))
    # 交互配筋は各径が pitch × 本数 の間隔で並ぶ
    at = sum(BAR_AREA[d] for d in dias) / len(dias) * 1000.0 / pitch
    return {'spec': s, 'dias': dias, 'pitch': pitch, 'at': at,
            'dmin': min(dias), 'dmax': max(dias)}


def materials(fc, ctype='普通', sd='SD295', dmax=16):
    g = gamma_rc(fc, ctype)
    ec = e_concrete(fc, g - 1.0)
    light = ctype != '普通'
    fs_l = min(fc / 30.0, 0.49 + fc / 100.0) * (0.9 if light else 1.0)
    fa_up = min(fc / 15.0, 0.9 + 2.0 * fc / 75.0)
    fa_ot = min(fc / 10.0, 1.35 + fc / 25.0)
    return {'Fc': fc, 'ctype': ctype, 'gamma': g, 'Ec': ec, 'n': n_ratio(fc),
            'fc_l': fc / 3.0, 'fc_s': fc / 3.0 * 2.0,
            'fs_l': fs_l, 'fs_s': fs_l * 1.5,
            'sd': sd, 'Es': E_S, 'ft_l': ft_long(sd, dmax),
            'ft_s': ft_short(sd),
            'fa_up_l': fa_up, 'fa_up_s': fa_up * 1.5,
            'fa_l': fa_ot, 'fa_s': fa_ot * 1.5}


def check(spec, m_pos, m_neg, q, short=False):
    """1断面・1荷重期間の検定.

    spec: {'Fc','ctype','sd','t','single','cover_up','cover_dn',
           'bar_up','bar_dn','dir'}  (bar_up=上端筋=負曲げ, bar_dn=下端筋=正曲げ、
           dir='main' 主筋方向 (短辺・外側の段) / 'dist' 配力筋方向 (長辺・内側))
    m_pos (≥0), m_neg (≤0): kN·m/m、q: kN/m (絶対値で使う)
    """
    fc = float(spec['Fc'])
    t = float(spec['t'])
    single = bool(spec.get('single'))
    up = parse_bar(spec['bar_up'])
    dn = parse_bar(spec['bar_up'] if single else spec['bar_dn'])
    mat = materials(fc, spec.get('ctype') or '普通', spec.get('sd') or 'SD295',
                    max(up['dmax'], dn['dmax']))
    c_up = float(spec['cover_up'])
    c_dn = float(spec.get('cover_dn') or c_up)
    o_up, o_dn = BAR_OUTER[up['dmax']], BAR_OUTER[dn['dmax']]
    geo = {'dt_up_sh': c_up + o_up / 2.0, 'dt_up_ln': c_up + o_up * 1.5}
    main = spec.get('dir') == 'main'   # 主筋方向 (短辺・外側の段)
    sfx = 'sh' if main else 'ln'
    if single:
        geo['j_up_sh'] = 0.875 * geo['dt_up_sh']
        geo['j_up_ln'] = 0.875 * (t - geo['dt_up_ln'])
        geo['j_up_neg_sh'] = 0.875 * (t - geo['dt_up_sh'])
        # 正曲げは上面から鉄筋まで (安全側に上の段)、負曲げは下面から
        d_pos, j_pos = geo['dt_up_sh'], geo['j_up_sh']
        if main:
            d_neg, j_neg = t - geo['dt_up_sh'], geo['j_up_neg_sh']
        else:
            d_neg, j_neg = t - geo['dt_up_ln'], geo['j_up_ln']
        j_q = j_neg
    else:
        geo['dt_dn_sh'] = c_dn + o_dn / 2.0
        geo['dt_dn_ln'] = c_dn + o_dn * 1.5
        for k in ('up', 'dn'):
            geo['j_%s_sh' % k] = 0.875 * (t - geo['dt_%s_sh' % k])
            geo['j_%s_ln' % k] = 0.875 * (t - geo['dt_%s_ln' % k])
        d_pos, j_pos = t - geo['dt_dn_' + sfx], geo['j_dn_' + sfx]
        d_neg, j_neg = t - geo['dt_up_' + sfx], geo['j_up_' + sfx]
        j_q = geo['j_dn_' + sfx]
    ft = mat['ft_s'] if short else mat['ft_l']
    fs = mat['fs_s'] if short else mat['fs_l']
    mc = 0.56 * math.sqrt(fc) * 1000.0 * t ** 2 / 6.0 / 1e6

    def _bend(m, d, j, bar):
        need = abs(m) * 1e6 / ft / j
        return {'M': m, 'd': d, 'j': j, 'at_need': need, 'bar': bar,
                'ratio': need / bar['at'], 'ok': need <= bar['at'] + 1e-9,
                'Ma': bar['at'] * ft * j / 1e6,
                'mc_ratio': abs(m) / mc, 'mc_ok': abs(m) <= mc}

    pos = _bend(m_pos, d_pos, j_pos, dn)
    neg = _bend(m_neg, d_neg, j_neg, up)
    qa = fs * 1000.0 * j_q / 1000.0
    shear = {'Q': abs(q), 'fsj': qa, 'j': j_q, 'ratio': abs(q) / qa,
             'ok': abs(q) <= qa + 1e-9}

    light = mat['ctype'] != '普通'
    tmin = 100.0 if light else 80.0
    pmax = max(up['pitch'], dn['pitch'])
    lim_ln = 250.0 if light else 300.0
    at_min = min(up['at'], dn['at'])
    rules = [
        {'name': '最小スラブ厚', 'val': '%.0fmm' % t,
         'lim': '%.0fmm以上' % tmin, 'ok': t >= tmin},
        {'name': '引張鉄筋D10以上', 'val': 'D%d' % min(up['dmin'], dn['dmin']),
         'lim': 'D10以上', 'ok': min(up['dmin'], dn['dmin']) >= 10},
        {'name': '鉄筋間隔 (短辺)', 'val': '%.0fmm' % pmax,
         'lim': '200mm以下', 'ok': pmax <= 200},
        {'name': '鉄筋間隔 (長辺)', 'val': '%.0fmm' % pmax,
         'lim': '%.0fmm以下%s' % (lim_ln, '' if light else
                                  ' かつ 3t=%.0fmm以下' % (3 * t)),
         'ok': pmax <= lim_ln and (light or pmax <= 3 * t)},
        {'name': 'スラブ鉄筋比', 'val': '%.2f%%' % (at_min / 1000 / t * 100),
         'lim': '0.2%以上', 'ok': at_min / 1000.0 / t >= 0.002},
    ]
    ok = pos['ok'] and neg['ok'] and shear['ok'] and all(r['ok'] for r in rules)
    return {'spec': dict(spec), 'short': short, 'main': main, 'mat': mat, 'geo': geo,
            'ft': ft, 'fs': fs, 'Mc': mc, 'pos': pos, 'neg': neg,
            'shear': shear, 'rules': rules, 'ok': ok,
            'bar_up': up, 'bar_dn': dn, 'single': single}
