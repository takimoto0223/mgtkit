# -*- coding: utf-8 -*-
"""梁接合部検定の中身 — 対象部材の抽出・接合種別の判定・金物耐力による検定.

対象部材の抽出 (read_targets):
    *FRAME-RLS の端部解放フラグ (6桁 'FxFyFzMxMyMz'、1=解放) に
    '1' を含む端部をピン接合とみなし、その端を持つ梁要素 (BEAM) を
    断面 (符号) ごとにまとめて返す。

梁の継手の除外:
    ピン端の節点を共有する剛接合の部材の中に、ピン側の部材と同一方向
    (部材軸のなす角が _SPLICE_TOL_DEG 以内) のものがある場合、その
    ピン端は「梁の継手」(部材の途中の接合) とみなして接合部検討の
    対象外とする。除外したピン端は注記で報告する。

接合種別 (柱-梁 / 梁-梁) の判定:
    ピン端の節点を共有する他の梁要素 (BEAM) のうち、その節点側の端部の
    曲げ (My/Mz = フラグ末尾2桁) が解放されていないものを剛接合として
    集め、その中に柱 (column_beam_judge_one が 2.0 を返す鉛直材) が
    あれば「柱-梁」、柱が無く梁 (斜め材を含む) があれば「梁-梁」、
    剛接合の要素が1つも無ければ「判定不可」とする。
    トラス要素・壁 (板) 要素は判定に含めない。自動判定の結果は
    設計者が確認する前提 (検討書にもその旨を明記する)。

金物の割当 (run_check の fittings):
    符号ごとに「金物リストから選択 (樹種つき)」または「手入力」。
    リスト選択時は、各ピン端の接合種別に応じた耐力を自動で使い分ける:
      短期せん断/長期せん断/短期引張 = カタログの基準耐力
      長期引張 = 短期基準引張 × 1.1/2 (小数1位切捨て。カタログの
                 長期基準せん断と同じ比率。カタログに長期引張の記載が
                 ないため)
    接合種別が判定不可の端は、柱-梁/梁-梁の小さい方の耐力で検定する
    (安全側)。手入力時は入力した1組の耐力を全種別の端に用いる。

検定 (run_check):
    ピン端の軸力 N=Fx とせん断力 Q=Fz を金物の許容耐力で検定する。
    このコードベースの符号規約 (w_check / s_check と同一) では
    N > 0 が引張。引張のみ N/Ta で検定し、圧縮はめり込み・接触で
    伝達されるものとして金物検定の対象外 (参考値として返す)。
    check_compression=True のときは圧縮 (N < 0) も |N|/Ca で検定する。
    圧縮耐力 Ca はカタログに記載がないため常に手入力 (めり込み・支圧等の
    検討に基づく値を設計者が入力する前提。長期・短期の「-」相互換算と
    中短期の短期×1.6/2 は他の耐力と同じ扱い)。
    せん断は |Q|/Qa。長期ケースは長期耐力、組合せ・短期ケースは
    短期耐力を用いる。応力があるのに耐力未入力の端は「未検定」。

荷重ケースは断面算定タブと同じ種別指定を使う:
    'L' = 長期 / 'H' = 水平のみ (各Lと ±H を組合せて短期ケースを生成) /
    'S' = 短期 (長期と組合せ済みの応力をそのまま短期として検定)
"""

import json
import math
import os

import numpy as np

from mgtkit.mgt import (mgtopen_beam, mgtopen_framerls, mgtopen_node,
                        mgtopen_group)
from mgtkit.section import mgtopen_section
from mgtkit.draw_stress import read_beam_stress
from mgtkit.draw_model import column_beam_judge_one

#: 接合種別の表示名
JT_CB = '柱-梁'
JT_BB = '梁-梁'
JT_NA = '判定不可'

#: 長期引張耐力の換算係数 (カタログの長期基準せん断 = 短期×1.1/2 と同じ)
TA_L_FACTOR = 1.1 / 2.0

#: 中短期の耐力の換算係数 (木質系: 短期×1.6/2)
MID_FACTOR = 1.6 / 2.0

#: 検定の期別 (キー, 表示名)。中短期は 'M' ケース指定時のみ現れる
TERMS = (('long', '長期'), ('short', '短期'), ('mid', '中短期'))
TERM_LABEL = dict(TERMS)

#: 逆せん断判定の基準となる長期せん断力の下限 [kN]。これ未満は
#: 向きの判定に使わない (符号が数値ノイズで決まるのを防ぐ)
QREF_EPS = 0.01


def load_fittings_db():
    """金物リスト (data/joint_fittings.json) を読む."""
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'data', 'joint_fittings.json')
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _floor1(x):
    """小数1位で切捨て (カタログの長期値の丸めと同じ)."""
    return math.floor(float(x) * 10.0 + 1e-9) / 10.0


# ---------------------------------------------------------------------------
# 対象部材の抽出と接合種別の判定
# ---------------------------------------------------------------------------

def _sec_name_map(mgt_path):
    """断面番号 → (断面名, 梁せい[mm]) を返す (名前は draw_ratio.py と同じ).

    梁せいは中実角 (SB) 断面のみ取得できる (列 [secno, H, B]、H は m)。
    それ以外の断面種は None (金物の対応梁せい照合の対象外)。
    """
    secs, section_no, section_name = mgtopen_section(mgt_path)
    names = {}
    for k, n in enumerate(np.atleast_1d(
            np.asarray(section_no, dtype=float)).ravel()):
        if k < len(section_name):
            names[int(n)] = str(section_name[k]).strip()
    depths = {}
    sb = secs[2] if len(secs) > 2 else None
    if sb is not None and np.asarray(sb).size:
        for row in np.atleast_2d(sb):
            if row.shape[0] >= 3:
                depths[int(row[0])] = float(row[1]) * 1000.0
    return names, depths


def _bend_released(rls, ele, end):
    """要素 ele の end ('i'/'j') 側で曲げ (My/Mz = フラグ末尾2桁) が
    解放されているか。ねじり Mx や軸・せん断のみの解放は剛接合扱い."""
    if ele not in rls:
        return False
    flag = rls[ele][0 if end == 'i' else 1]
    return '1' in flag[-2:]


#: 梁の継手とみなす部材軸の平行判定の許容角度 [度]
_SPLICE_TOL_DEG = 2.0
_SPLICE_COS = math.cos(math.radians(_SPLICE_TOL_DEG))


def _elem_dir(k, beam, node_xyz):
    """要素 (行index k) の部材軸の単位ベクトル。長さ0なら None."""
    ni = node_xyz.get(int(beam[k, 3]))
    nj = node_xyz.get(int(beam[k, 4]))
    if ni is None or nj is None:
        return None
    v = (nj[0] - ni[0], nj[1] - ni[1], nj[2] - ni[2])
    ln = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    if ln <= 0:
        return None
    return (v[0] / ln, v[1] / ln, v[2] / ln)


def _classify_end(node_no, self_k, beam, node, node_xyz, rls,
                  judge_cache, dir_cache):
    """ピン端 (節点 node_no) の (接合種別, 継手か) を判定する.

    節点を共有する他の梁要素のうち剛接合 (その節点側の曲げが解放なし) の
    ものを集める。その中にピン側の部材と同一方向のものがあれば
    「梁の継手」(is_splice=True)。種別は柱があれば柱-梁、梁のみなら
    梁-梁、剛接合が無ければ判定不可。
    """
    if self_k not in dir_cache:
        dir_cache[self_k] = _elem_dir(self_k, beam, node_xyz)
    d0 = dir_cache[self_k]
    has_col = False
    has_beam = False
    is_splice = False
    for k in range(beam.shape[0]):
        ele = int(beam[k, 0])
        if k == self_k:
            continue
        if int(beam[k, 3]) == node_no:
            end = 'i'
        elif int(beam[k, 4]) == node_no:
            end = 'j'
        else:
            continue
        if _bend_released(rls, ele, end):
            continue  # その節点側もピン → 剛接合要素ではない
        if k not in dir_cache:
            dir_cache[k] = _elem_dir(k, beam, node_xyz)
        d1 = dir_cache[k]
        if d0 is not None and d1 is not None:
            dot = abs(d0[0] * d1[0] + d0[1] * d1[1] + d0[2] * d1[2])
            if dot >= _SPLICE_COS:
                is_splice = True
        if k not in judge_cache:
            judge_cache[k] = column_beam_judge_one(k, beam, node)
        if judge_cache[k] == 2.0:
            has_col = True
        else:
            has_beam = True
    if has_col:
        jt = JT_CB
    elif has_beam:
        jt = JT_BB
    else:
        jt = JT_NA
    return jt, is_splice


def read_targets(mgt_path, notes=None):
    """ピン端をもつ梁要素の一覧と、断面 (符号) ごとの集計を返す.

    戻り値: (members, sections, splices)
      members : list[dict]  {'ele', 'sec', 'sec_name', 'node_i', 'node_j',
                             'flag_i', 'flag_j',
                             'ends': [{'end': 'i'/'j', 'node': 節点番号,
                                       'jtype': 柱-梁/梁-梁/判定不可}]}
      sections: list[dict]  {'sec', 'name', 'depth_mm' (SB断面のみ、他None),
                             'n_members', 'n_ends',
                             'types': {接合種別: 端数}} (断面番号の昇順)
      splices : 梁の継手として対象外にしたピン端のlist
                [{'ele', 'end', 'node', 'sec', 'sec_name'}]
    """
    beam = mgtopen_beam(mgt_path)
    rls = mgtopen_framerls(mgt_path)
    node = mgtopen_node(mgt_path)
    names, depths = _sec_name_map(mgt_path)
    node_xyz = {}
    if node.size:
        for r in np.atleast_2d(node):
            node_xyz[int(r[0])] = (float(r[1]), float(r[2]), float(r[3]))

    members = []
    splices = []  # 梁の継手として対象外にしたピン端
    judge_cache = {}
    dir_cache = {}
    if beam.size:
        beam = np.atleast_2d(beam)
        for k in range(beam.shape[0]):
            row = beam[k]
            ele = int(row[0])
            if ele not in rls:
                continue
            flag_i, flag_j = rls[ele]
            sec = int(row[2])
            ends = []
            for end, flag, nno in (('i', flag_i, int(row[3])),
                                   ('j', flag_j, int(row[4]))):
                if '1' not in flag:
                    continue
                jt, is_splice = _classify_end(nno, k, beam, node, node_xyz,
                                              rls, judge_cache, dir_cache)
                if is_splice:
                    splices.append({'ele': ele, 'end': end, 'node': nno,
                                    'sec': sec,
                                    'sec_name': names.get(sec,
                                                          '断面%d' % sec)})
                    continue
                ends.append({'end': end, 'node': nno, 'jtype': jt})
            if not ends:
                continue
            members.append({'ele': ele, 'sec': sec,
                            'sec_name': names.get(sec, '断面%d' % sec),
                            'node_i': int(row[3]), 'node_j': int(row[4]),
                            'flag_i': flag_i, 'flag_j': flag_j,
                            'ends': ends})

    if splices and notes is not None:
        disp = ['%d(%s)' % (sp['ele'], sp['end']) for sp in splices[:30]]
        if len(splices) > 30:
            disp.append('他%d箇所' % (len(splices) - 30))
        notes.append('部材の継手 (同一方向に剛接合の部材が連続するピン端。'
                     '梁・柱とも) を検定対象外にしました: %d箇所 [%s]'
                     % (len(splices), ', '.join(disp)))
    na_eles = sorted({m['ele'] for m in members
                      if any(e['jtype'] == JT_NA for e in m['ends'])})
    if na_eles and notes is not None:
        notes.append('接合種別を判定できないピン端があります (節点を共有する'
                     '剛接合の梁・柱が見つかりません): 要素 %s' % na_eles)

    agg = {}
    for m in members:
        a = agg.setdefault(m['sec'], {'sec': m['sec'], 'name': m['sec_name'],
                                      'depth_mm': depths.get(m['sec']),
                                      'n_members': 0, 'n_ends': 0,
                                      'types': {}})
        a['n_members'] += 1
        a['n_ends'] += len(m['ends'])
        for e in m['ends']:
            a['types'][e['jtype']] = a['types'].get(e['jtype'], 0) + 1
    sections = [agg[k] for k in sorted(agg)]
    return members, sections, splices


def _group_ele_map(mgt_path):
    """mgt のグループ名 → 所属要素番号 set (*GROUP。無ければ空dict)."""
    axis_name, axis_elenum, _nodes = mgtopen_group(mgt_path)
    out = {}
    for name, arr in zip(axis_name, axis_elenum):
        a = np.atleast_1d(np.asarray(arr, dtype=float)).ravel()
        out[str(name)] = {int(v) for v in a if v > 0}
    return out


def read_groups(mgt_path, members=None, splices=None):
    """グループ一覧を返す (除外/グループ耐力入力の指定UI用).

    members / splices を与えた場合は、各要素に所属グループ名 'groups' を
    書き込む (UI がグループ単位の金物入力欄や警告を組むのに使う)。

    戻り値: [{'name': グループ名, 'n_eles': 所属要素数,
              'n_targets': うちピン端をもつ検定対象部材の数}]
    """
    gmap = _group_ele_map(mgt_path)
    eles = {m['ele'] for m in (members or [])}
    for m in (members or []):
        m['groups'] = [g for g, s in gmap.items() if m['ele'] in s]
    for sp in (splices or []):
        sp['groups'] = [g for g, s in gmap.items() if sp['ele'] in s]
    out = []
    for name, gset in gmap.items():
        out.append({'name': name, 'n_eles': len(gset),
                    'n_targets': len(gset & eles)})
    return out


# ---------------------------------------------------------------------------
# 検定ケースの構成 (断面算定タブと同じ L/H/S 種別)
# ---------------------------------------------------------------------------

def build_check_cases(case_types, file_cases):
    """ケース種別指定から検定ケースの一覧を作る.

    case_types: [{'no': ケース番号, 'type': 'L'/'H'/'S'/'M', 'name': ケース名}]
      'M' = 中短期 (組合せ済み)。金物耐力を短期の1.6/2倍で検定する
      (断面算定タブと違い、jointchk はケース番号の制約なし)。
    file_cases: 応力ファイルに実在するケース番号の list

    戻り値: list[dict] {'name', 'term' ('long'/'short'/'mid'),
                        'combo': [(ケース番号, 係数), ...]}
    """
    typed = {}
    for c in case_types:
        typed[float(c['no'])] = (str(c['type']).strip().upper(),
                                 str(c.get('name') or '').strip())
    missing = [c for c in file_cases if float(c) not in typed]
    if missing:
        raise ValueError('ケース種別が未指定の荷重ケースがあります: %s '
                         '(「対象読込」でケース一覧を読み込み直してください)'
                         % ['%g' % c for c in missing])

    L, H, S, M = [], [], [], []
    for cno in file_cases:
        ctype, cname = typed[float(cno)]
        if ctype == 'L':
            L.append((float(cno), cname or 'G+P'))
        elif ctype == 'H':
            H.append((float(cno), cname or 'CASE%g' % cno))
        elif ctype == 'S':
            S.append((float(cno), cname or 'G+P+CASE%g' % cno))
        elif ctype == 'M':
            M.append((float(cno), cname or 'G+P+CASE%g' % cno))
        else:
            raise ValueError("ケース%gの種別 '%s' が不正です "
                             "('L'=長期/'H'=水平のみ/'S'=短期(組合せ済み)/"
                             "'M'=中短期(組合せ済み))" % (cno, ctype))
    if H and not L:
        raise ValueError("水平('H')ケースには組合せる長期('L')ケースが必要です")
    if H and (S or M):
        raise ValueError("水平荷重('H')と短期/中短期荷重('S'/'M')は混在"
                         'できません (断面算定と同一の制約)')

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
    for mno, mname in M:
        cases.append({'name': mname, 'term': 'mid', 'combo': [(mno, 1.0)]})

    # ケース名は検定ケースの識別に使うため一意化する (同名の長期ケースが
    # 複数ある場合などに、末尾のケース番号を付けて区別する)
    seen = set()
    for c in cases:
        if c['name'] in seen:
            base = '%s(C%g)' % (c['name'], c['combo'][-1][0])
            name = base
            k = 2
            while name in seen:
                name = '%s-%d' % (base, k)
                k += 1
            c['name'] = name
        seen.add(c['name'])
    return cases


def _end_forces_by_case(beam_stress, eles):
    """要素ごとの i端/j端の (N, Q) をケース別に引けるようにする.

    beam_stress: read_beam_stress の戻り値 (3行/要素: i端・中央・j端)
    戻り値: (forces, file_cases)
      forces: {ケース番号: {要素番号: (Ni, Qi, Nj, Qj)}}  [kN]
      file_cases: ケース番号の list (ファイル内の登場順)
    """
    forces = {}
    order = []
    eles = set(int(e) for e in eles)
    for cno in np.asarray(beam_stress[:, 1], dtype=float):
        if not order or order[-1] != cno:
            if cno not in order:
                order.append(float(cno))
    for cno in order:
        block = beam_stress[beam_stress[:, 1] == cno]
        d = {}
        for ele in eles:
            rows = block[block[:, 0] == ele]
            if rows.shape[0] == 0:
                continue
            d[ele] = (float(rows[0, 2]), float(rows[0, 4]),
                      float(rows[-1, 2]), float(rows[-1, 4]))
        forces[cno] = d
    return forces, order


def _num_or_none(v):
    """空欄/None は None、それ以外は float (0以下はエラー)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError('金物耐力の値が数値として読めません: %r '
                         '(数値、空欄、または「-」を入力してください)' % v)
    if x <= 0:
        raise ValueError('金物耐力は正の値を入力してください: %g' % x)
    return x


def _cap_or_auto(v):
    """手入力の耐力欄を読む。(値, 自動計算フラグ) を返す.

    「-」(全角・長音符も可) は「相手方の期の値から許容応力度の比率
    (1.1/2.0) で自動計算する」指定。
    """
    s = '' if v is None else str(v).strip()
    if s in ('-', '−', 'ー', '－'):  # 半角/全角マイナス・長音符
        return None, True
    return _num_or_none(s), False


# ---------------------------------------------------------------------------
# 金物耐力の解決 (符号ごとの指定 → 接合種別ごとの耐力)
# ---------------------------------------------------------------------------

def _norm_unit_key(k):
    """金物割当単位のキーを正規化する.

    's|<断面番号>' = 符号ごと / 'g|<グループ名>' = グループごと。
    接頭辞なし (従来形式の断面番号) は 's|' 扱い。
    """
    ks = str(k)
    if ks.startswith('g|'):
        return ks
    if ks.startswith('s|'):
        return 's|%d' % int(float(ks[2:]))
    return 's|%d' % int(float(ks))


def _resolve_fittings(fittings, need, labels, notes, check_comp=False):
    """割当単位 (符号またはグループ) ごとの金物指定から耐力を作る.

    fittings: {単位キー ('s|断面番号' / 'g|グループ名' / 断面番号):
               {'fitting': リスト金物名 (手入力なら空), 'species': 樹種,
                'name': 手入力時の金物名,
                'ta_l','ta_s','qa_l','qa_s','qr_s': 手入力時の耐力,
                'ca_l','ca_s': 圧縮耐力 (check_comp時のみ参照。カタログに
                               記載がないためリスト選択時も手入力)}}
    need  : ピン端に現れる (単位キー, 接合種別) の list
    labels: 単位キー → 表示名 (符号名 / グループ名)

    戻り値: {(単位キー, 接合種別): {'name','species','ta_l','ta_s',
             'qa_l','qa_s','qr_s','ca_l','ca_s','ta_l_auto'}}
    """

    def _comp_caps(v):
        """圧縮耐力 (常に手入力) を読む。(ca_l, ca_s, 自動計算したか)."""
        if not check_comp:
            return None, None, False
        ca_l, l_auto = _cap_or_auto(v.get('ca_l'))
        ca_s, s_auto = _cap_or_auto(v.get('ca_s'))
        if l_auto and ca_s is not None:
            ca_l = _floor1(ca_s * TA_L_FACTOR)
        if s_auto and ca_l is not None:
            ca_s = _floor1(ca_l / TA_L_FACTOR)
        auto = ((l_auto and ca_l is not None)
                or (s_auto and ca_s is not None))
        return ca_l, ca_s, auto
    fin = {}
    for k, v in dict(fittings or {}).items():
        fin[_norm_unit_key(k)] = v
    no_fit = sorted({u for u, _ in need if u not in fin})
    if no_fit:
        raise ValueError('金物が未指定の欄があります: %s'
                         % ', '.join(labels.get(u, u) for u in no_fit))

    db = None
    fit = {}
    na_units = []
    for ukey, jt in need:
        v = fin[ukey]
        label = labels.get(ukey, ukey)
        fname = str(v.get('fitting') or '').strip()
        if fname:
            if db is None:
                db = load_fittings_db()
            sp = str(v.get('species') or '').strip()
            fdef = next((f for f in db['fittings'] if f['name'] == fname),
                        None)
            if fdef is None:
                raise ValueError('金物リストに %s がありません '
                                 '(%s)' % (fname, label))
            spv = fdef['values'].get(sp)
            if not spv:
                raise ValueError('金物 %s に樹種 %s の耐力データが'
                                 'ありません (%s)' % (fname, sp, label))
            if jt in (JT_CB, JT_BB):
                vals = spv.get(jt) or {}
            else:
                # 判定不可 → 柱-梁/梁-梁の小さい方 (安全側)。
                # 片方が「—」(None) なら耐力を決められないので None
                def _min(key):
                    a = (spv.get(JT_CB) or {}).get(key)
                    b = (spv.get(JT_BB) or {}).get(key)
                    if a is None or b is None:
                        return None
                    return min(a, b)
                vals = {k: _min(k) for k in ('ta_s', 'qa_l', 'qa_s', 'qr_s')}
                if ukey not in na_units:
                    na_units.append(ukey)
            ta_s = vals.get('ta_s')
            ca_l, ca_s, ca_auto = _comp_caps(v)
            fit[(ukey, jt)] = {
                'name': fname, 'species': sp,
                'ta_l': _floor1(ta_s * TA_L_FACTOR) if ta_s else None,
                'ta_s': ta_s,
                'qa_l': vals.get('qa_l'), 'qa_s': vals.get('qa_s'),
                'qr_s': vals.get('qr_s'),
                'ca_l': ca_l, 'ca_s': ca_s,
                'ta_l_auto': bool(ta_s),
                # 圧縮の「-」指定も手入力扱いの自動計算 (検討書の注記用)
                'cap_auto': ca_auto}
        else:
            # 手入力。「-」は相手方の期から 1.1/2.0 の比率で自動計算
            # (長期 = 短期×1.1/2、短期 = 長期÷(1.1/2)。いずれも小数1位切捨て。
            #  相手方も未入力/「-」なら None = 未検定)
            ta_l, ta_l_auto = _cap_or_auto(v.get('ta_l'))
            ta_s, ta_s_auto = _cap_or_auto(v.get('ta_s'))
            qa_l, qa_l_auto = _cap_or_auto(v.get('qa_l'))
            qa_s, qa_s_auto = _cap_or_auto(v.get('qa_s'))
            qr_s, _qr_auto = _cap_or_auto(v.get('qr_s'))  # 逆せん断に相手方は
            #                                              無いので「-」=未入力
            if ta_l_auto and ta_s is not None:
                ta_l = _floor1(ta_s * TA_L_FACTOR)
            if ta_s_auto and ta_l is not None:
                ta_s = _floor1(ta_l / TA_L_FACTOR)
            if qa_l_auto and qa_s is not None:
                qa_l = _floor1(qa_s * TA_L_FACTOR)
            if qa_s_auto and qa_l is not None:
                qa_s = _floor1(qa_l / TA_L_FACTOR)
            ca_l, ca_s, ca_auto = _comp_caps(v)
            fit[(ukey, jt)] = {
                'name': str(v.get('name') or '').strip(),
                'species': '',
                'ta_l': ta_l, 'ta_s': ta_s,
                'qa_l': qa_l, 'qa_s': qa_s, 'qr_s': qr_s,
                'ca_l': ca_l, 'ca_s': ca_s,
                'ta_l_auto': bool(ta_l_auto and ta_l is not None),
                # 「-」指定で自動計算した耐力があるか (検討書の注記用)
                'cap_auto': bool(
                    (ta_l_auto and ta_l is not None)
                    or (ta_s_auto and ta_s is not None)
                    or (qa_l_auto and qa_l is not None)
                    or (qa_s_auto and qa_s is not None)
                    or ca_auto)}
    for ukey in na_units:
        notes.append('%s: 接合種別が判定できないピン端は、柱-梁/梁-梁の'
                     '小さい方の耐力で検定しました (安全側)'
                     % labels.get(ukey, ukey))
    return fit


# ---------------------------------------------------------------------------
# 検定
# ---------------------------------------------------------------------------

def run_check(mgt_path, beam_stress_path, sec_select, fittings, case_types,
              exclude_groups=None, fit_groups=None, unit_labels=None,
              check_compression=False, hide_jtype=False, notes=None):
    """梁接合部の検定を行う.

    sec_select     : 検討対象の断面番号 list
    fittings       : 割当単位 (符号またはグループ) ごとの金物指定
                     (_resolve_fittings 参照)
    case_types     : [{'no', 'type', 'name'}]  断面算定タブと同じ種別指定
    exclude_groups : 検定対象外にする mgt グループ名の list (所属要素を
                     対象から除外し、注記に残す)
    fit_groups     : 金物をグループ単位で割り当てる mgt グループ名の list。
                     所属する対象部材はそのグループを検定単位として
                     耐力入力・集計する (複数グループに属する要素は
                     指定順の先頭を採用)。属さない部材は従来どおり符号ごと
    unit_labels    : 検定単位の表示名の上書き {単位キー: 表示名}。
                     グループ行の「グループ名(特記)」などを検討書・集計の
                     表示に使う (未指定の単位は既定のグループ名/符号名)
    check_compression : True なら圧縮 (N < 0) も入力された圧縮耐力
                     (fittings の 'ca_l'/'ca_s') で検定する。False (既定)
                     なら従来どおり圧縮は検定対象外
    hide_jtype     : True なら接合種別 (柱-梁/梁-梁/判定不可) を表示しない。
                     検定は従来どおり種別ごとの耐力で行い (計算値は不変)、
                     表示用の集計だけを符号/グループ単位に統合する:
                     case_rows は種別をまたいだ検定比最大の行に集約し、
                     'summary_disp' (総括表示用の統合版) を追加で返す
                     ('summary' は金物表用に種別別のまま)

    戻り値 dict:
      'cases'        : [{'name', 'term'}]
      'case_types_in': 設計者のケース種別指定の記録
      'members'      : 検討対象部材 (read_targets のうち選択断面のもの)
      'rows'         : 端部ごと×長期/短期の検定行 (画面の詳細表用)
                       [{'sec','sec_name','ele','end','node','jtype','term',
                         'n','n_case','rt','q','q_case','rs','ng','nc',
                         'tension','cn','cn_case','rc','compression'}]
                       (cn=最小軸力 [圧縮が負]、rc=圧縮検定比。
                        check_compression=False のとき rc は常に None)
      'case_rows'    : (符号, 接合種別)×検定ケースごとの最大検定行
                       (検討書の検定表用)
                       [{'sec','sec_name','jtype','case','term',
                         'n','n_at','rt','q','q_at','rs','ng','nc',
                         'tension','cn','cn_at','rc','compression'}]
      'summary'      : (符号, 接合種別) ごとの集計 (耐力・最大検定比)
      'splice_summary': 検定対象外にした梁の継手の軸力の参考値 (符号ごと。
                       画面表示のみで検討書には載せない)
                       [{'sec','name','n_ends','n_missing',
                         'long'/'short': {'n','case','ele','end'} or None}]
      'notes'        : 注記
    """
    if notes is None:
        notes = []

    members_all, _sections, splices = read_targets(mgt_path, notes=notes)
    sec_select = [int(s) for s in sec_select]
    members = [m for m in members_all if m['sec'] in sec_select]
    if not members:
        raise ValueError('選択した断面にピン接合端をもつ梁要素がありません。'
                         '「対象読込」で断面を選び直してください')
    sec_label = {m['sec']: m['sec_name'] for m in members_all}

    # 検定対象外グループの所属要素を除外
    if exclude_groups:
        gmap = _group_ele_map(mgt_path)
        excl = set()
        used = []
        unknown = []
        for g in exclude_groups:
            g = str(g)
            if g in gmap:
                excl |= gmap[g]
                used.append(g)
            else:
                unknown.append(g)
        if unknown:
            notes.append('mgtに存在しないグループの除外指定は無視しました: '
                         '%s' % ', '.join(unknown))
        before = len(members)
        members = [m for m in members if m['ele'] not in excl]
        if used:
            notes.append('検定対象外グループ: %s (対象から %d 本を除外)'
                         % (', '.join(used), before - len(members)))
        if not members:
            raise ValueError('グループ除外の結果、検定対象の部材が'
                             'なくなりました。除外指定を見直してください')

    # 金物の割当単位: グループ指定があればグループ、それ以外は符号
    fit_groups = [str(g) for g in (fit_groups or [])]
    gmap = _group_ele_map(mgt_path) if fit_groups else {}
    unknown_fg = [g for g in fit_groups if g not in gmap]
    if unknown_fg:
        notes.append('mgtに存在しないグループの耐力入力指定は無視しました: '
                     '%s' % ', '.join(unknown_fg))
        fit_groups = [g for g in fit_groups if g in gmap]
    labels = {}
    overlap = 0
    for m in members:
        ukey = 's|%d' % m['sec']
        label = m['sec_name']
        hit = [g for g in fit_groups if m['ele'] in gmap[g]]
        if hit:
            ukey = 'g|%s' % hit[0]
            label = hit[0]
            if len(hit) > 1:
                overlap += 1
        m['unit'] = ukey
        labels[ukey] = label
    if overlap:
        notes.append('複数の耐力入力グループに属する部材が %d 本あります '
                     '(指定順の先頭のグループの金物で検定)' % overlap)
    if fit_groups:
        used_fg = sorted({m['unit'][2:] for m in members
                          if m['unit'].startswith('g|')})
        if used_fg:
            notes.append('金物をグループ単位で割り当て: %s'
                         % ', '.join(used_fg))
    # 表示名の上書き (グループ名(特記) など)
    for k, disp in dict(unit_labels or {}).items():
        try:
            k = _norm_unit_key(k)
        except ValueError:
            continue
        disp = str(disp).strip()
        if k in labels and disp:
            labels[k] = disp

    # ピン端に現れる (割当単位, 接合種別) と金物耐力の解決。
    # 並びは 符号 (断面ID昇順) → グループ (指定順 = mgt登録順) に揃える
    need = []
    for m in members:
        for e in m['ends']:
            key = (m['unit'], e['jtype'])
            if key not in need:
                need.append(key)
    forder = {'g|%s' % g: i for i, g in enumerate(fit_groups)}
    jorder = {JT_CB: 0, JT_BB: 1, JT_NA: 2}
    need.sort(key=lambda kj: (
        (0, int(kj[0][2:]), '') if kj[0].startswith('s|')
        else (1, forder.get(kj[0], 10 ** 6), kj[0]),
        jorder.get(kj[1], 9)))
    fit = _resolve_fittings(fittings, need, labels, notes,
                            check_comp=check_compression)

    beam_stress = read_beam_stress(beam_stress_path)
    if beam_stress.size == 0:
        raise ValueError('beam_stressファイルが空です: %s' % beam_stress_path)
    forces, file_cases = _end_forces_by_case(
        beam_stress,
        [m['ele'] for m in members] + [sp['ele'] for sp in splices])
    cases = build_check_cases(case_types, file_cases)
    # 設計者の種別指定を検討書に残す (応力ファイルのケース順)
    tmap = {float(c['no']): c for c in case_types}
    case_types_in = [{'no': float(cno),
                      'type': str(tmap[float(cno)]['type']).strip().upper(),
                      'name': str(tmap[float(cno)].get('name') or '').strip()}
                     for cno in file_cases if float(cno) in tmap]

    # 端部×ケースごとの応力 (組合せ込み)
    skipped = set()
    end_vals = []  # {'sec','sec_name','jtype','ele','end','node',
    #                 'case','term','n','q'}
    for m in members:
        ele = m['ele']
        percase = []
        ok = True
        for c in cases:
            ni = qi = nj = qj = 0.0
            for cno, coef in c['combo']:
                f = forces.get(cno, {}).get(ele)
                if f is None:
                    ok = False
                    break
                ni += coef * f[0]
                qi += coef * f[1]
                nj += coef * f[2]
                qj += coef * f[3]
            if not ok:
                break
            percase.append((c, ni, qi, nj, qj))
        if not ok:
            skipped.add(ele)
            continue
        for e in m['ends']:
            # 逆せん断判定の基準: そのケースに含まれる長期ケースのせん断の
            # 向きを正とみなす (組合せ済み'S'ケースは最初の長期ケース)。
            # 基準がほぼ0 (または長期ケースが無い) 端は向きを判定できない
            # ため rev=None とし、通常・逆の両方の耐力で検定する (安全側)
            long_q = {}
            for c, ni, qi, nj, qj in percase:
                if c['term'] == 'long':
                    long_q[c['combo'][0][0]] = qi if e['end'] == 'i' else qj
            for c, ni, qi, nj, qj in percase:
                n, q = (ni, qi) if e['end'] == 'i' else (nj, qj)
                if c['term'] == 'long':
                    rev = False
                else:  # 短期・中短期とも逆せん断の向き判定を行う
                    qref = None
                    for cno, _coef in c['combo']:
                        if cno in long_q:
                            qref = long_q[cno]
                            break
                    if qref is None and long_q:
                        qref = next(iter(long_q.values()))
                    if qref is None or abs(qref) < QREF_EPS:
                        rev = None
                    elif q == 0:
                        rev = False
                    else:
                        rev = (q > 0) != (qref > 0)
                end_vals.append({'sec': m['sec'], 'sec_name': m['sec_name'],
                                 'unit': m['unit'],
                                 'jtype': e['jtype'], 'ele': ele,
                                 'end': e['end'], 'node': e['node'],
                                 'case': c['name'], 'term': c['term'],
                                 'n': n, 'q': q, 'rev': rev})
    if skipped:
        notes.append('応力ファイルに無い要素 %s は検定できませんでした '
                     '(mgtと応力ファイルの整合を確認してください)'
                     % sorted(skipped))
    if not end_vals:
        raise ValueError('検定できる端部がありません '
                         '(mgtと応力ファイルの要素番号が一致しているか確認'
                         'してください)')

    def _term_caps(f, term):
        """期別の (引張, せん断, 逆せん断, 圧縮) 耐力。中短期は短期×1.6/2."""
        if term == 'long':
            return f['ta_l'], f['qa_l'], None, f.get('ca_l')
        if term == 'short':
            return f['ta_s'], f['qa_s'], f['qr_s'], f.get('ca_s')
        def _m(v):
            return _floor1(v * MID_FACTOR) if v else None
        return (_m(f['ta_s']), _m(f['qa_s']), _m(f['qr_s']),
                _m(f.get('ca_s')))

    def _tension_ratio(f, term, n):
        """(rt, tension, nc) を返す引張判定."""
        ta = _term_caps(f, term)[0]
        tension = n > 0
        rt = (n / ta) if (tension and ta) else None
        return rt, tension, (tension and ta is None)

    def _comp_ratio(f, term, n):
        """(rc, compression, nc) を返す圧縮判定 (check_compression時のみ)."""
        if not check_compression:
            return None, False, False
        ca = _term_caps(f, term)[3]
        comp = n < 0
        rc = (-n / ca) if (comp and ca) else None
        return rc, comp, (comp and ca is None)

    def _shear_pick(f, term, sel):
        """せん断の決定値を選ぶ。(rs, 決定エントリ, 逆表示, nc) を返す.

        通常せん断は qa_l/qa_s、逆せん断 (rev=True のケース) は qr_s で
        検定し、検定比が最大のエントリを採用する。向きを判定できない
        エントリ (rev=None) は通常・逆の両方の耐力で検定し大きい方を
        採る (安全側)。該当する耐力が未入力のエントリがあれば nc=True
        (比較できた中での最大は返す)。
        """
        best = None
        best_rev = False
        best_rs = None
        nc = False
        _ta, qa, qr, _ca = _term_caps(f, term)
        for v in sel:
            if v['rev'] is True:
                cand = ((True, qr),)
            elif v['rev'] is None:
                cand = ((False, qa), (True, qr))
            else:
                cand = ((False, qa),)
            for revflag, cap in cand:
                if cap is None:
                    nc = True
                    continue
                rs = abs(v['q']) / cap
                if best_rs is None or rs > best_rs:
                    best_rs = rs
                    best = v
                    best_rev = revflag
        if best is None:
            best = max(sel, key=lambda v: abs(v['q']))
            best_rev = best['rev'] is True
        return best_rs, best, best_rev, nc

    # 端部ごと×長期/短期の検定行 (画面の詳細表用):
    # 引張は N 最大、せん断は検定比最大 (正/逆で耐力が違うため)
    rows = []
    ends_seen = []
    for v in end_vals:
        key = (v['ele'], v['end'])
        if key not in ends_seen:
            ends_seen.append(key)
    for ele, end in ends_seen:
        for term, _tl in TERMS:
            sel = [v for v in end_vals
                   if v['ele'] == ele and v['end'] == end
                   and v['term'] == term]
            if not sel:
                continue
            vn = max(sel, key=lambda v: v['n'])
            vc = min(sel, key=lambda v: v['n'])  # 圧縮側 (最小軸力)
            f = fit[(vn['unit'], vn['jtype'])]
            rt, tension, nc_t = _tension_ratio(f, term, vn['n'])
            rc_, comp, nc_c = _comp_ratio(f, term, vc['n'])
            rs, vq, q_rev, nc_s = _shear_pick(f, term, sel)
            ng = (rt is not None and rt > 1.0) or \
                 (rc_ is not None and rc_ > 1.0) or \
                 (rs is not None and rs > 1.0)
            rows.append({'sec': vn['sec'], 'sec_name': vn['sec_name'],
                         'unit': vn['unit'],
                         'ele': ele, 'end': end, 'node': vn['node'],
                         'jtype': vn['jtype'], 'term': term,
                         'n': vn['n'], 'n_case': vn['case'],
                         'tension': tension, 'rt': rt,
                         'cn': vc['n'], 'cn_case': vc['case'],
                         'compression': comp, 'rc': rc_,
                         'q': abs(vq['q']), 'q_case': vq['case'],
                         'q_rev': q_rev,
                         'rs': rs, 'ng': ng, 'nc': nc_t or nc_c or nc_s})

    # (割当単位, 接合種別)×検定ケースごとの最大検定行 (検討書の検定表用)
    case_rows = []
    for u, jt in need:
        f = fit[(u, jt)]
        for c in cases:
            sel = [v for v in end_vals
                   if v['unit'] == u and v['jtype'] == jt
                   and v['case'] == c['name']]
            if not sel:
                continue
            vn = max(sel, key=lambda v: v['n'])
            vc = min(sel, key=lambda v: v['n'])  # 圧縮側 (最小軸力)
            rt, tension, nc_t = _tension_ratio(f, c['term'], vn['n'])
            rc_, comp, nc_c = _comp_ratio(f, c['term'], vc['n'])
            rs, vq, q_rev, nc_s = _shear_pick(f, c['term'], sel)
            ng = (rt is not None and rt > 1.0) or \
                 (rc_ is not None and rc_ > 1.0) or \
                 (rs is not None and rs > 1.0)
            case_rows.append({'sec': u, 'sec_name': labels.get(u, u),
                              'jtype': jt, 'case': c['name'],
                              'term': c['term'],
                              'n': vn['n'],
                              'n_at': '%d(%s)' % (vn['ele'], vn['end']),
                              'tension': tension, 'rt': rt,
                              'cn': vc['n'],
                              'cn_at': '%d(%s)' % (vc['ele'], vc['end']),
                              'compression': comp, 'rc': rc_,
                              'q': abs(vq['q']),
                              'q_at': '%d(%s)' % (vq['ele'], vq['end']),
                              'q_rev': q_rev,
                              'rs': rs, 'ng': ng,
                              'nc': nc_t or nc_c or nc_s})

    # 逆せん断判定に関する注記 (仮定の明示。ユーザー要望による)
    if any(v['rev'] is True for v in end_vals):
        notes.append('逆せん断の判定は「そのケースに含まれる長期ケース '
                     '(組合せ済みの短期ケースでは最初の長期ケース) の'
                     'せん断の向きを正とし、短期で向きが反転したものを'
                     '逆せん断とみなす」仮定によります。実状と合わない'
                     '場合があるため、向きが重要な箇所は個別に確認して'
                     'ください')
    if any(v['rev'] is None for v in end_vals):
        notes.append('長期時のせん断がほぼ0 (%.2fkN未満)、または長期ケースが'
                     '無く、せん断の向きを判定できないピン端があります。'
                     'この端の短期せん断は、通常・逆せん断の両方の耐力で'
                     '検定し大きい方の検定比を採用しました (安全側)'
                     % QREF_EPS)

    # 耐力の未入力で「未検定」になった欄の注記 (実際に応力が生じた検定のみ。
    # 引張耐力の未入力でも、全端が圧縮なら省略は正常なので注記しない)
    for key in need:
        f = fit[key]
        kv = [v for v in end_vals
              if v['unit'] == key[0] and v['jtype'] == key[1]]
        miss = []
        for term, label in TERMS:
            tv = [v for v in kv if v['term'] == term]
            if not tv:
                continue
            ta, qa, qr, ca = _term_caps(f, term)
            if ta is None and any(v['n'] > 0 for v in tv):
                miss.append(label + '引張')
            if (check_compression and ca is None
                    and any(v['n'] < 0 for v in tv)):
                miss.append(label + '圧縮')
            if qa is None and any(v['rev'] is not True for v in tv):
                miss.append(label + 'せん断')
            if (term != 'long' and qr is None
                    and any(v['rev'] is not False for v in tv)):
                miss.append(label + '逆せん断')
        if miss:
            notes.append('%s (%s): %s耐力が未入力のため未検定の端部が'
                         'あります (判定欄に「未検定」と表示)'
                         % (labels.get(key[0], key[0]), key[1],
                            '・'.join(miss)))

    summary = []
    for u, jt in need:
        srows = [r for r in rows if r['unit'] == u and r['jtype'] == jt]
        if not srows:
            continue
        f = fit[(u, jt)]
        rts = [r for r in srows if r['rt'] is not None]
        rcs = [r for r in srows if r['rc'] is not None]
        rss = [r for r in srows if r['rs'] is not None]
        max_rt = max(rts, key=lambda r: r['rt']) if rts else None
        max_rc = max(rcs, key=lambda r: r['rc']) if rcs else None
        max_rs = max(rss, key=lambda r: r['rs']) if rss else None
        ng = (max_rt is not None and max_rt['rt'] > 1.0) or \
             (max_rc is not None and max_rc['rc'] > 1.0) or \
             (max_rs is not None and max_rs['rs'] > 1.0)
        nc = any(r['nc'] for r in srows)
        summary.append({
            'sec': u, 'name': labels.get(u, u), 'jtype': jt, 'nc': nc,
            'fit': f['name'], 'species': f['species'],
            'ta_l': f['ta_l'], 'ta_s': f['ta_s'],
            'qa_l': f['qa_l'], 'qa_s': f['qa_s'], 'qr_s': f['qr_s'],
            'ca_l': f.get('ca_l'), 'ca_s': f.get('ca_s'),
            'ta_l_auto': f['ta_l_auto'],
            'cap_auto': f.get('cap_auto', False),
            'max_rt': (max_rt['rt'] if max_rt else None),
            'max_rt_at': ({'ele': max_rt['ele'], 'end': max_rt['end'],
                           'case': max_rt['n_case'], 'n': max_rt['n'],
                           'term': max_rt['term']} if max_rt else None),
            'max_rc': (max_rc['rc'] if max_rc else None),
            'max_rc_at': ({'ele': max_rc['ele'], 'end': max_rc['end'],
                           'case': max_rc['cn_case'], 'n': max_rc['cn'],
                           'term': max_rc['term']} if max_rc else None),
            'max_rs': (max_rs['rs'] if max_rs else None),
            'max_rs_at': ({'ele': max_rs['ele'], 'end': max_rs['end'],
                           'case': max_rs['q_case'], 'q': max_rs['q'],
                           'rev': max_rs['q_rev'],
                           'term': max_rs['term']} if max_rs else None),
            'ng': ng})

    # 接合種別の非表示: 検定値はそのまま、表示用の集計を符号/グループ単位に
    # 統合する (種別ごとに計算済みの検定比の最大を採るだけなので安全)
    summary_disp = None
    if hide_jtype:
        def _pick_r(g, ratio_key, val_key, prefer_max=True):
            """種別行の中から表示に使う行を選ぶ (検定比最大。全てNoneなら
            応力の大きい方 = 引張は最大N、圧縮は最小N)."""
            with_r = [r for r in g if r[ratio_key] is not None]
            if with_r:
                return max(with_r, key=lambda r: r[ratio_key])
            return (max if prefer_max else min)(g, key=lambda r: r[val_key])

        merged = []
        order = []
        groups = {}
        for r in case_rows:
            k = (r['sec'], r['case'])
            if k not in groups:
                groups[k] = []
                order.append(k)
            groups[k].append(r)
        for k in order:
            g = groups[k]
            rt_r = _pick_r(g, 'rt', 'n', True)
            rc_r = _pick_r(g, 'rc', 'cn', False)
            rs_r = _pick_r(g, 'rs', 'q', True)
            merged.append({
                'sec': g[0]['sec'], 'sec_name': g[0]['sec_name'],
                'jtype': '', 'case': g[0]['case'], 'term': g[0]['term'],
                'n': rt_r['n'], 'n_at': rt_r['n_at'],
                'tension': rt_r['tension'], 'rt': rt_r['rt'],
                'cn': rc_r['cn'], 'cn_at': rc_r['cn_at'],
                'compression': rc_r['compression'], 'rc': rc_r['rc'],
                'q': rs_r['q'], 'q_at': rs_r['q_at'],
                'q_rev': rs_r['q_rev'], 'rs': rs_r['rs'],
                'ng': any(r['ng'] for r in g),
                'nc': any(r['nc'] for r in g)})
        case_rows = merged

        def _pick_s(g, key):
            with_r = [s for s in g if s[key] is not None]
            return max(with_r, key=lambda s: s[key]) if with_r else None

        summary_disp = []
        sorder = []
        sgroups = {}
        for s in summary:
            if s['sec'] not in sgroups:
                sgroups[s['sec']] = []
                sorder.append(s['sec'])
            sgroups[s['sec']].append(s)
        for u in sorder:
            g = sgroups[u]
            ent = {'sec': u, 'name': g[0]['name'], 'jtype': '',
                   'fit': g[0]['fit'], 'species': g[0]['species'],
                   'ng': any(s['ng'] for s in g),
                   'nc': any(s['nc'] for s in g)}
            for key in ('max_rt', 'max_rc', 'max_rs'):
                best = _pick_s(g, key)
                ent[key] = best[key] if best else None
                ent[key + '_at'] = best[key + '_at'] if best else None
            summary_disp.append(ent)

    # 継手 (検定対象外) の軸力の参考値: 符号ごとに期別の最大軸力 (引張正)。
    # 継手の設計用の目安として画面にのみ表示する (検討書には載せない)
    splice_summary = []
    by_sec = {}
    for sp in splices:
        by_sec.setdefault((sp['sec'], sp['sec_name']), []).append(sp)
    for (s, name), sps in sorted(by_sec.items()):
        entry = {'sec': s, 'name': name, 'n_ends': len(sps), 'n_missing': 0,
                 'long': None, 'short': None, 'mid': None}
        missing = set()
        for term, _tl in TERMS:
            best = None
            for sp in sps:
                for c in cases:
                    if c['term'] != term:
                        continue
                    n = 0.0
                    ok = True
                    for cno, coef in c['combo']:
                        f = forces.get(cno, {}).get(sp['ele'])
                        if f is None:
                            ok = False
                            break
                        n += coef * (f[0] if sp['end'] == 'i' else f[2])
                    if not ok:
                        missing.add((sp['ele'], sp['end']))
                        continue
                    if best is None or n > best['n']:
                        best = {'n': n, 'case': c['name'],
                                'ele': sp['ele'], 'end': sp['end']}
            entry[term] = best
        entry['n_missing'] = len(missing)
        splice_summary.append(entry)

    if check_compression:
        notes.append('圧縮の検定は設計者が入力した圧縮耐力 (めり込み・支圧等'
                     'の検討に基づく値) によります。カタログに圧縮耐力の記載'
                     'はないため、リスト金物選択時も圧縮耐力は手入力です')

    return {'cases': [{'name': c['name'], 'term': c['term']} for c in cases],
            'check_compression': bool(check_compression),
            'hide_jtype': bool(hide_jtype),
            'case_types_in': case_types_in,
            'members': members, 'rows': rows, 'case_rows': case_rows,
            'summary': summary, 'summary_disp': summary_disp,
            'splice_summary': splice_summary,
            'notes': notes}
