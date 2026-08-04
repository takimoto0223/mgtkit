# -*- coding: utf-8 -*-
"""構造図DXF (新規版).

MATLAB移植 (export_dxf.py) とは独立した新規実装。ezdxf で正規のDXF
(R2010) を生成するため、AutoCAD/Rhino/Jw_cad でそのまま開ける。

方針 (2026-07-13 ユーザー合意、箱根民泊の実施図を配置の手本に更新):
  - 軸組図 (通りグループ別の鉛直構面) と 伏図 (レベル別)
  - **各部材は閉じた四角形 (矩形ポリライン) で描く**
  - 取り合いは実施図の流儀:
      * 通し柱 (節点をまたいで連続する柱) は連続、取り付く梁は柱面まで後退
      * 管柱 (節点で終わる柱) は横架材せいの半分だけ後退 (梁勝ち)
      * 小梁は大梁 (せいの大きい方) の面まで後退
  - ピン接合 (*FRAME-RLS の曲げ解放) は相手の面からさらに紙面上一定距離
    (既定1.5mm) を縮尺換算して離す。剛接合は面まで
  - レイヤは材質分類ごとに部材/断面で分ける
    (部材: S-RC伏_20 / S-木_20 / S-鉄骨_20,
     断面: S-RC軸_30 / S-木断面_30 / S-鉄骨断面_30, テキスト: S-Red10)
  - 分割部材 (*UNIT) は通しの一本として描画
  - 丸鋼等の引張ブレースのみ単線破線、それ以外の斜材 (登り梁等) も矩形
  - ジオメトリは実寸mm (1:1)。縮尺は注記サイズ・ピン離隔・用紙当てはめに
    使用し、自動提案 (標準系列) + 手動指定。ブラウザに用紙プレビュー表示
"""
import math
import os

import numpy as np

from .util import find_index
from .mgt import (mgtopen_node, mgtopen_element,
                  mgtopen_group, mgtopen_unit_2015, mgtopen_framerls,
                  space_erace)
from .section import mgtopen_section
from .ratio_pipeline import sectionget

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

LAYER_DEF = {   # 部材 (矩形・線)
    'STEEL': ('S-鉄骨_20', 4),      # シアン
    'RC': ('S-RC伏_20', 2),         # 黄
    'WOOD': ('S-木_20', 30),        # オレンジ (ACI 30)
    'OTHER': ('S-その他_20', 8),    # グレー
}
LAYER_SEC = {   # 断面 (ブロックINSERT)
    'STEEL': ('S-鉄骨断面_30', 3),      # 緑
    'RC': ('S-RC軸_30', 6),             # マゼンタ
    'WOOD': ('S-木断面_30', 161),       # 薄青 (ACI 161)
    'OTHER': ('S-その他断面_30', 8),    # グレー
}
LAYER_TEXT = ('S-Red10', 1)     # 赤
LAYER_TITLE = ('S-Red10', 1)

SCALE_SERIES = [20, 25, 30, 40, 50, 60, 75, 100, 150, 200, 250, 300,
                400, 500, 600, 750, 1000]

PAPER_MM = {'A1': (841.0, 594.0), 'A2': (594.0, 420.0),
            'A3': (420.0, 297.0), 'A4': (297.0, 210.0)}
PAPER_MARGIN_MM = 15.0

Z_TOL = 1e-3   # 同一レベル判定 (m)
import re as _re_mod
_NAME_DIM_RE = _re_mod.compile(r'(\d+)\s*[xX×]\s*(\d+)')
THIN_BRACE_B = 0.03  # これ以下の幅の斜材は単線表現 (m)


# ---------------------------------------------------------------------------
# モデル読込
# ---------------------------------------------------------------------------

def material_class(mat_type):
    t = int(mat_type)
    if t in (1, 2):
        return 'STEEL'
    if t in (3, 4, 5, 6, 7, 8):
        return 'RC'
    if t == 10:
        return 'WOOD'
    return 'OTHER'


def _material_classes_lenient(mgt_path):
    """*MATERIAL を寛容にパースして {材料番号: 材質分類} を返す.

    構造図はレイヤ分け用途のみのため、検定用パーサ (mgtopen_material) の
    厳密な名前規約 (Fc24等) 違反で停止しないよう独自に分類する。
    STEEL→STEEL / CONC・SRC→RC / USER→名前にW-,W_,CLTがあればWOOD、
    それ以外はOTHER。
    """
    import io as _io
    out = {}
    try:
        with _io.open(mgt_path, encoding='cp932', errors='replace') as f:
            lines = f.read().splitlines()
    except OSError:
        return out
    ins = False
    for ln in lines:
        s = ln.strip()
        if s.startswith('*MATERIAL'):
            ins = True
            continue
        if ins and s.startswith('*'):
            break
        if not ins or not s or s.startswith(';'):
            continue
        parts = [t.strip() for t in ln.split(',')]
        if len(parts) < 3:
            continue
        try:
            no = int(float(parts[0]))
        except ValueError:
            continue
        mtype = parts[1].upper()
        name = parts[2].upper()
        if mtype == 'STEEL':
            out[no] = 'STEEL'
        elif mtype in ('CONC', 'SRC'):
            out[no] = 'RC'
        elif mtype == 'USER':
            if 'W-' in name or 'W_' in name or 'CLT' in name:
                out[no] = 'WOOD'
            else:
                out[no] = 'OTHER'
        else:
            out[no] = 'OTHER'
    return out


def member_bd(sec_row):
    """sectionget の戻り値 → (幅b, せいd, 取得成功, 円形か) [m]."""
    sec_row = np.asarray(sec_row, dtype=float).ravel()
    if sec_row.size < 4:
        return 0.1, 0.1, False, False
    code = int(sec_row[sec_row.size - 2])
    dims = sec_row[1:sec_row.size - 2]

    def _get(i):
        return float(dims[i]) if i < dims.size and dims[i] > 0 else 0.0

    if code in (3, 4, 13, 16):          # 中実丸 / 鋼管 / CPO / CFT円
        v = _get(0)
        return (v or 0.1), (v or 0.1), v > 0, True
    if code in (1, 2, 5, 6, 7, 8, 9, 10, 11, 12, 14, 15, 17):
        d = _get(0)
        b = _get(1)
        if d > 0 and b > 0:
            return b, d, True, False
        v = d or b
        return (v or 0.1), (v or 0.1), v > 0, False
    return 0.1, 0.1, False, False


class StructModel(object):
    pass


def load_struct_model(mgt_path, limit_sec_no=9000.0):
    """構造図用のモデル読込 + 部材 (member) の組立て."""
    M = StructModel()
    M.mgt_path = mgt_path
    M.node = mgtopen_node(mgt_path)
    M.element = mgtopen_element(mgt_path)
    (M.sections, M.section_no, M.section_name) = mgtopen_section(mgt_path)
    (M.axis_name, M.axis_element, M.axis_node) = mgtopen_group(mgt_path)
    M.releases = mgtopen_framerls(mgt_path)
    M.unit_element = mgtopen_unit_2015(mgt_path, M.element, M.node)
    M.limit_sec_no = float(limit_sec_no)

    element = M.element
    sec_names = {}
    for no, nm in zip(np.atleast_1d(M.section_no), list(M.section_name)):
        sec_names[int(no)] = space_erace(str(nm))
    M.sec_names = sec_names

    mat_map = _material_classes_lenient(mgt_path)

    def _ele_row(ele_no):
        i = int(np.atleast_1d(find_index(element[:, 0], float(ele_no)))[0])
        return element[i, :] if i != -1 else None

    def _pin_flag(flag):
        return len(flag) >= 6 and ('1' in (flag[4], flag[5]))

    def _end_release(ele_no, node_no):
        rls = M.releases.get(int(ele_no))
        if not rls:
            return False
        row = _ele_row(ele_no)
        if row is None:
            return False
        if int(row[3]) == int(node_no):
            return _pin_flag(rls[0])
        if int(row[4]) == int(node_no):
            return _pin_flag(rls[1])
        return False

    members = []
    used = set()
    bad_dims = set()
    for item in M.unit_element:
        eles = np.atleast_1d(np.asarray(item[1], dtype=float)).ravel()
        nodes = np.atleast_1d(np.asarray(item[2], dtype=float)).ravel()
        if eles.size == 0 or nodes.size < 2:
            continue
        row0 = _ele_row(eles[0])
        if row0 is None:
            continue
        if float(row0[2]) >= M.limit_sec_no:
            for e in eles:
                used.add(int(e))
            continue
        m = _make_member(M, [int(e) for e in eles], int(nodes[0]),
                         int(nodes[-1]), row0, mat_map, _end_release,
                         bad_dims, is_unit=True,
                         mid_nodes=[int(n) for n in nodes[1:-1]])
        if m:
            members.append(m)
        for e in eles:
            used.add(int(e))

    for r in range(element.shape[0]):
        ele_no = int(element[r, 0])
        if ele_no in used:
            continue
        if float(element[r, 2]) >= M.limit_sec_no:
            continue
        m = _make_member(M, [ele_no], int(element[r, 3]),
                         int(element[r, 4]), element[r, :], mat_map,
                         _end_release, bad_dims)
        if m:
            members.append(m)

    if bad_dims:
        print('注意: 断面寸法を取得できない断面があるため 100mm 角で'
              '描画します: 断面番号 '
              + ', '.join(str(s) for s in sorted(bad_dims)[:10]))

    # 断面名の寸法 (幅x せい) と登録値の食い違い検出 (mgt側指摘)
    _swapped = []
    _seen_sec = set()
    for m in members:
        if m['sec_no'] in _seen_sec or m['is_round']:
            continue
        _seen_sec.add(m['sec_no'])
        mm = _NAME_DIM_RE.search(m['sec_name'])
        if not mm:
            continue
        a, bdim = float(mm.group(1)), float(mm.group(2))
        wb, wd = round(m['b'] * 1000), round(m['d'] * 1000)
        if abs(a - bdim) < 0.5:
            continue
        if abs(a - wb) < 0.5 and abs(bdim - wd) < 0.5:
            continue  # 名前(幅xせい)と登録(B,H)が一致
        if abs(a - wd) < 0.5 and abs(bdim - wb) < 0.5:
            _swapped.append('%s (登録: 幅%d×せい%d)'
                            % (m['sec_name'], wb, wd))
    if _swapped:
        print('mgt記述の注意: 断面名の寸法 (幅x せい) と登録値 (B=幅, H=せい)'
              ' が逆になっている疑いがあります。mgtの断面登録 (H/B) を'
              '確認してください (描画は登録値に従うため向きが90度違って'
              '見えます): ' + ', '.join(_swapped[:8]))

    members = _merge_collinear(members)
    M.members = members

    # 節点 → 部材 (端点・中間節点とも) の索引
    node_members = {}
    for i, m in enumerate(members):
        for n in [m['n1'], m['n2']] + m.get('mid_nodes', []):
            node_members.setdefault(int(n), []).append(i)
    M.node_members = node_members
    # 節点番号 → 座標
    M.node_xyz = {}
    for r in range(M.node.shape[0]):
        M.node_xyz[int(M.node[r, 0])] = M.node[r, 1:4].astype(float)
    return M


def _merge_collinear(members):
    """同一断面・同一直線上で端点を共有する梁/斜材を通しの一本に連結する.

    実施図では登り梁・桁などは通し部材で描かれるため、*UNIT登録が無くても
    モデルの中間節点で分割されただけの部材は連結する。条件:
      - 両方とも beam/brace (柱は管柱/通し柱の別をモデルどおり尊重)
      - 断面番号・β角が同じで方向が平行 (交差角<0.5度)
      - 共有端にピン (曲げ解放) が無い
      - その節点で同種のつながり候補が一意 (分岐なし)
    """
    def _dir(m):
        v = m['p2'] - m['p1']
        L = float(np.linalg.norm(v))
        return v / L if L > 1e-9 else v

    changed = True
    while changed:
        changed = False
        # 端点 → (member index, which_end)
        ends = {}
        for i, m in enumerate(members):
            ends.setdefault(int(m['n1']), []).append((i, 1))
            ends.setdefault(int(m['n2']), []).append((i, 2))
        for node_no, lst in ends.items():
            # 分岐節点 (小梁取付き等) でも、同断面・同一直線・剛接続の
            # ペアが一意に決まれば連結する
            pair = None
            n_cand = 0
            for a_i in range(len(lst)):
                for b_i in range(a_i + 1, len(lst)):
                    (i, ei), (j, ej) = lst[a_i], lst[b_i]
                    if i == j:
                        continue
                    a, b = members[i], members[j]
                    if a is None or b is None:
                        continue
                    if a['sec_no'] != b['sec_no']:
                        continue
                    if a['kind'] != b['kind']:
                        continue
                    if abs(a['beta'] - b['beta']) > 1e-6:
                        continue
                    if (a['pin1'] if ei == 1 else a['pin2']):
                        continue
                    if (b['pin1'] if ej == 1 else b['pin2']):
                        continue
                    da, db = _dir(a), _dir(b)
                    cr = float(np.linalg.norm(np.cross(da, db)))
                    if cr > math.sin(math.radians(0.5)):
                        continue
                    n_cand += 1
                    pair = ((i, ei), (j, ej))
            if pair is None or n_cand > 1:
                continue  # 候補なし、または曖昧 (十字交差等) は連結しない
            (i, ei), (j, ej) = pair
            a, b = members[i], members[j]
            # a の外端 → b の外端 で作り直す
            if ei == 1:
                a_out, a_out_pin, a_out_p = a['n2'], a['pin2'], a['p2']
            else:
                a_out, a_out_pin, a_out_p = a['n1'], a['pin1'], a['p1']
            if ej == 1:
                b_out, b_out_pin, b_out_p = b['n2'], b['pin2'], b['p2']
            else:
                b_out, b_out_pin, b_out_p = b['n1'], b['pin1'], b['p1']
            merged = dict(a)
            merged['eles'] = list(a['eles']) + list(b['eles'])
            merged['n1'], merged['p1'], merged['pin1'] = \
                int(a_out), a_out_p, a_out_pin
            merged['n2'], merged['p2'], merged['pin2'] = \
                int(b_out), b_out_p, b_out_pin
            merged['mid_nodes'] = (list(a.get('mid_nodes', []))
                                   + list(b.get('mid_nodes', []))
                                   + [int(node_no)])
            merged['is_unit'] = True
            members[i] = merged
            members[j] = None
            changed = True
        if changed:
            members = [m for m in members if m is not None]
    return [m for m in members if m is not None]


def _sec_code(sec_name):
    """断面名 → 図面に書く符号.

    '_' より前を採用 ('WG1_■-120x270' → 'WG1')。'_' が無い場合は
    先頭の英数字の並び ('WC3■-120x270' → 'WC3')。
    """
    s = str(sec_name or '').strip()
    if not s:
        return ''
    if '_' in s:
        return s.split('_')[0]
    import re
    m = re.match(r'^[0-9A-Za-z]+', s)
    return m.group(0) if m else s


def _make_member(M, eles, n1, n2, ele_row, mat_map, end_release, bad_dims,
                 is_unit=False, mid_nodes=None):
    node = M.node
    i1 = int(np.atleast_1d(find_index(node[:, 0], float(n1)))[0])
    i2 = int(np.atleast_1d(find_index(node[:, 0], float(n2)))[0])
    if i1 == -1 or i2 == -1:
        return None
    p1 = node[i1, 1:4].astype(float)
    p2 = node[i2, 1:4].astype(float)
    dxy = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    dz = abs(p2[2] - p1[2])
    if dxy > dz:
        kind = 'beam' if dz < dxy / 100.0 else 'brace'
    elif dxy < dz / 100.0:
        kind = 'column'
    else:
        kind = 'brace'
    sec_no = int(ele_row[2])
    sec_row = sectionget(eles[0], M.element, M.sections, M.limit_sec_no)
    b, d, ok, is_round = member_bd(sec_row)
    if not ok:
        bad_dims.add(sec_no)
    sec_name = M.sec_names.get(sec_no, str(sec_no))
    return {
        'eles': eles, 'n1': n1, 'n2': n2, 'p1': p1, 'p2': p2,
        'sec_no': sec_no, 'sec_name': sec_name,
        'code': _sec_code(sec_name) or str(sec_no),
        'mclass': mat_map.get(int(ele_row[1]), 'OTHER'),
        'b': b, 'd': d, 'beta': float(ele_row[5]),
        'kind': kind, 'is_unit': is_unit, 'is_round': is_round,
        'mid_nodes': mid_nodes or [],
        'pin1': end_release(eles[0], n1),
        'pin2': end_release(eles[-1], n2),
    }


# ---------------------------------------------------------------------------
# 取り合い (実施図の配置ルール)
# ---------------------------------------------------------------------------

def _col_width_along(m_col, theta_rad):
    """柱 m_col の、水平方向 theta に沿った見付け寸法/2 [m].

    MIDASの鉛直材はβ=0で幅B(=b)がY方向・せいH(=d)がX方向を向く
    (期待図の実測で確定 2026-07-13)。
    """
    if m_col['is_round']:
        return m_col['b'] / 2.0
    rel = theta_rad - math.radians(m_col['beta'])
    return (abs(math.sin(rel)) * m_col['b']
            + abs(math.cos(rel)) * m_col['d']) / 2.0


def _beam_depth_view(m):
    """梁/斜材の立面での見付せい/2 [m] (β=90の寝梁は幅bが見付)."""
    rad = math.radians(m['beta'])
    return (abs(math.cos(rad)) * m['d'] + abs(math.sin(rad)) * m['b']) / 2.0


def _beam_width_plan(m):
    """梁/斜材の伏図での見付幅/2 [m]."""
    rad = math.radians(m['beta'])
    return (abs(math.cos(rad)) * m['b'] + abs(math.sin(rad)) * m['d']) / 2.0


def _column_through(M, m_col, z):
    """柱 m_col が高さ z を貫通するか (通し柱としてその節点を跨ぐか)."""
    z1 = min(m_col['p1'][2], m_col['p2'][2])
    z2 = max(m_col['p1'][2], m_col['p2'][2])
    return z1 < z - Z_TOL and z2 > z + Z_TOL


def beam_end_setback(M, mi, node_no, theta_rad, view):
    """梁 (水平材) の端の後退量 [m] を決める.

    view='elev'|'plan'。ルール:
      柱がその節点にあれば柱面まで (通し柱でなくてもよい: 木造は柱勝ち面)
      柱が無く、せいの大きい梁 (大梁) がその節点を通る/終わるなら大梁面まで
    """
    me = M.members[mi]
    z = M.node_xyz[int(node_no)][2]
    best = 0.0
    for j in M.node_members.get(int(node_no), []):
        if j == mi:
            continue
        m2 = M.members[j]
        if m2['kind'] == 'column':
            # 柱端がこのレベルで終わる場合も柱面までは逃げる
            best = max(best, _col_width_along(m2, theta_rad))
        elif m2['kind'] in ('beam', 'brace') and view == 'plan':
            # 平行材 (継手・連結相手) は突合せのまま
            v1 = (me['p2'] - me['p1'])[:2]
            v2 = (m2['p2'] - m2['p1'])[:2]
            L1 = math.hypot(v1[0], v1[1])
            L2 = math.hypot(v2[0], v2[1])
            if L1 < 1e-9 or L2 < 1e-9:
                continue
            if abs(float(v1[0] * v2[0] + v1[1] * v2[1]) / (L1 * L2)) \
                    > math.cos(math.radians(2.0)):
                continue
            # 貫通材 (この節点が相手の中間節点) は常に相手勝ち。
            # それ以外は大梁 (せい大) の面まで。同せいは先勝ち (index小)
            through = int(node_no) in m2.get('mid_nodes', [])
            if (through or m2['d'] > me['d'] + 1e-9
                    or (abs(m2['d'] - me['d']) < 1e-9 and j < mi)):
                best = max(best, _beam_width_plan(m2))
        elif m2['kind'] in ('beam', 'brace') and view == 'elev':
            if abs(m2['p1'][2] - z) < Z_TOL and abs(m2['p2'][2] - z) < Z_TOL:
                continue  # 同レベル同士の継手は突合せ
    return best


def column_end_setback(M, mi, node_no):
    """柱の端 (柱頭・柱脚) の鉛直方向の後退量 [m] と支持材の有無.

    節点=横架材天端の規約:
      柱頭: 取り付く横架材の下端まで逃げる (梁=d、登り梁=d/cosθ)
      柱脚: 横架材の上に載る (後退0。ピン離隔判定用に支持材有無を返す)
    戻り値: (setback, has_support)
    """
    me = M.members[mi]
    z = float(M.node_xyz[int(node_no)][2])
    z_other = float(me['p2'][2] if int(node_no) == int(me['n1'])
                    else me['p1'][2])
    is_top = z >= z_other
    best = 0.0
    has = False
    for j in M.node_members.get(int(node_no), []):
        if j == mi:
            continue
        m2 = M.members[j]
        if m2['kind'] == 'beam':
            has = True
            best = max(best, m2['d'])
        elif m2['kind'] == 'brace':
            v = m2['p2'] - m2['p1']
            L = float(np.linalg.norm(v))
            if L < 1e-9:
                continue
            has = True
            cos_slope = math.hypot(v[0], v[1]) / L
            best = max(best, m2['d'] / max(cos_slope, 0.2))
    if not is_top:
        return 0.0, has
    return best, has


# ---------------------------------------------------------------------------
# 図形計算 (共通)
# ---------------------------------------------------------------------------

FRAME_TOL = 0.005  # 同一通り judgment (m)


def _group_arrays(M, gname):
    """グループ名 → (節点番号list, 要素番号set)."""
    gi = M.axis_name.index(gname)
    nodes = [int(n) for n in np.atleast_1d(
        np.asarray(M.axis_node[gi], dtype=float)).ravel()
        if int(n) in M.node_xyz]
    eles = set(int(e) for e in np.atleast_1d(
        np.asarray(M.axis_element[gi], dtype=float)).ravel() if e > 0)
    return nodes, eles


def _member_in_group(m, eles):
    return any(int(e) in eles for e in m.get('eles', []))


def struct_groups(M):
    """mgtのグループを軸組図/伏図に分類する.

    節点が平面上でほぼ一直線に並ぶ → kind='axis' (軸組図)
    面的に広がる → kind='plan' (伏図: フロアグループ)
    戻り値: list of dict {name, kind, n_col, n_beam} (mgtの記載順)
    """
    out = []
    for gname in list(getattr(M, 'axis_name', []) or []):
        try:
            nodes, eles = _group_arrays(M, gname)
        except (ValueError, TypeError):
            continue
        pts = [M.node_xyz[n][:2] for n in nodes]
        if len(pts) < 2 and eles:
            for m in M.members:
                if _member_in_group(m, eles):
                    pts.append(m['p1'][:2])
                    pts.append(m['p2'][:2])
        if len(pts) < 2:
            continue
        xy = np.asarray(pts, dtype=float)
        q = xy - xy.mean(axis=0)
        w = np.linalg.eigvalsh(q.T @ q)
        rms_perp = math.sqrt(max(float(w[0]), 0.0) / len(pts))
        spread = math.sqrt(max(float(w[-1]), 0.0) / len(pts))
        if rms_perp < 0.05 and spread > 0.05:
            kind = 'axis'
        elif rms_perp >= 0.05:
            kind = 'plan'
        else:
            continue  # 平面上で1点に集まる (壁1枚など) は対象外
        n_col = 0
        n_beam = 0
        for m in M.members:
            if _member_in_group(m, eles):
                if m['kind'] == 'column':
                    n_col += 1
                else:
                    n_beam += 1
        out.append({'name': gname, 'kind': kind, 'n_col': n_col,
                    'n_beam': n_beam})
    return out


def auto_frames(M):
    """軸組図の通り一覧.

    mgtのグループ定義 (節点が一直線のもの) があればグループ名を使う。
    無い場合のみ柱位置から X=一定 / Y=一定 の通りを自動検出して
    X01.. / Y01.. と命名する。
    """
    frames = [{'key': 'G:' + g['name'], 'label': g['name'], 'axis': 'G',
               'value': 0.0, 'n_col': g['n_col']}
              for g in struct_groups(M) if g['kind'] == 'axis']
    if frames:
        return frames
    from collections import Counter
    xs = Counter()
    ys = Counter()
    for m in M.members:
        if m['kind'] != 'column':
            continue
        for p in (m['p1'], m['p2']):
            xs[round(float(p[0]) / FRAME_TOL) * FRAME_TOL] += 1
            ys[round(float(p[1]) / FRAME_TOL) * FRAME_TOL] += 1
    frames = []
    for i, v in enumerate(sorted(xs.keys())):
        frames.append({'key': 'X=%.3f' % v,
                       'label': 'X%02d (X=%.3f)' % (i + 1, v),
                       'axis': 'X', 'value': float(v),
                       'n_col': xs[v] // 2})
    for i, v in enumerate(sorted(ys.keys())):
        frames.append({'key': 'Y=%.3f' % v,
                       'label': 'Y%02d (Y=%.3f)' % (i + 1, v),
                       'axis': 'Y', 'value': float(v),
                       'n_col': ys[v] // 2})
    return frames


def frame_label(M, key):
    """キー → 表示名 (自動命名があればそれ)."""
    for f in auto_frames(M):
        if f['key'] == key:
            return f['label']
    if key.startswith('G:'):
        return key[2:]
    return key


def _frame_geometry(M, key):
    """軸組図キー → (origin(2,), u(2,), 対象節点set, 表示名).

    'X=<v>' : X=v の面 (s=Y方向) / 'Y=<v>' : Y=v の面 (s=X方向)
    'G:<名>': グループのNODE_LISTから最小二乗 (従来方式)
    """
    if key.startswith('X=') or key.startswith('Y='):
        axis = key[0]
        val = float(key[2:])
        ci = 0 if axis == 'X' else 1
        node_set = set()
        for n, p in M.node_xyz.items():
            if abs(float(p[ci]) - val) <= FRAME_TOL:
                node_set.add(int(n))
        if not node_set:
            raise ValueError('通り %s に節点がありません' % key)
        if axis == 'X':
            origin = np.array([val, 0.0])
            u = np.array([0.0, 1.0])
        else:
            origin = np.array([0.0, val])
            u = np.array([1.0, 0.0])
        return origin, u, node_set, frame_label(M, key)
    gname = key[2:] if key.startswith('G:') else key
    center, u, node_set = axis_line_fit(M, gname)
    return center, u, node_set, gname


def axis_line_fit(M, gname):
    gi = M.axis_name.index(gname)
    node_nos = np.atleast_1d(
        np.asarray(M.axis_node[gi], dtype=float)).ravel()
    idx = np.atleast_1d(find_index(M.node[:, 0], node_nos))
    idx = idx[idx >= 0].astype(int)
    xy = M.node[idx, 1:3].astype(float)
    if xy.shape[0] < 2:
        raise ValueError('構面 %s の節点が不足しています (NODE_LIST を'
                         '確認してください)' % gname)
    center = xy.mean(axis=0)
    q = xy - center
    cov = q.T @ q
    w, v = np.linalg.eigh(cov)
    u = v[:, int(np.argmax(w))]
    if abs(u[0]) >= abs(u[1]):
        if u[0] < 0:
            u = -u
    else:
        if u[1] < 0:
            u = -u
    return center, u, set(int(n) for n in node_nos)


def _member_quad(q1, q2, half_w, cut1, cut2):
    """中心線 q1→q2 (mm) を両端 cut 短縮した矩形4隅にする.

    戻り値: (a1, a2, u, n, corners) or None
      corners = [a1+n, a2+n, a2-n, a1-n] (n=half_w幅の法線)
    """
    q1 = np.asarray(q1, dtype=float)
    q2 = np.asarray(q2, dtype=float)
    v = q2 - q1
    L = float(np.hypot(v[0], v[1]))
    if L < 1e-9:
        return None
    u = v / L
    c1 = min(max(cut1, 0.0), 0.45 * L)
    c2 = min(max(cut2, 0.0), 0.45 * L)
    a1 = q1 + u * c1
    a2 = q2 - u * c2
    n = np.array([-u[1], u[0]]) * half_w
    corners = [a1 + n, a2 + n, a2 - n, a1 - n]
    return a1, a2, u, n, corners


def _line_intersect(p1, d1, p2, d2):
    """点p+方向d の2直線の交点 (平行ならNone)."""
    det = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(det) < 1e-12:
        return None
    t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / det
    return np.asarray(p1, dtype=float) + np.asarray(d1, dtype=float) * t


def auto_scale(width_mm, height_mm, paper='A3'):
    pw, ph = PAPER_MM.get(paper, PAPER_MM['A3'])
    aw = pw - 2 * PAPER_MARGIN_MM
    ah = ph - 2 * PAPER_MARGIN_MM - 10.0
    need = max(width_mm / max(aw, 1.0), height_mm / max(ah, 1.0))
    for s in SCALE_SERIES:
        if s >= need:
            return s
    return SCALE_SERIES[-1]


def _is_thin_brace(m):
    return m['kind'] == 'brace' and (m['is_round'] or m['b'] <= THIN_BRACE_B)


class _FigBuilder(object):
    """軸組図/伏図共通の図形組み立て (矩形・マイター・直交断面・符号)."""

    def __init__(self, M, n_scale, gap_m, text_h_mm):
        self.M = M
        self.n = n_scale
        self.gap = gap_m          # ピン離し [m]
        self.th = text_h_mm       # 文字高さ [mm 実寸]
        self.rects = {}
        self.lines = {}
        self.inserts = []         # (code, b_mm, d_mm, is_round, (x,y), rot, mclass)
        self.texts = []           # ((x,y), txt, ang, mclass)
        self._ins_seen = set()

    def add_rect(self, mclass, corners):
        self.rects.setdefault(mclass, []).append(
            [(float(c[0]), float(c[1])) for c in corners])

    def add_line(self, mclass, p1, p2):
        self.lines.setdefault(mclass, []).append(
            ((float(p1[0]), float(p1[1])), (float(p2[0]), float(p2[1]))))

    def add_insert(self, m, pos, rot=0.0):
        key = (m['code'], round(float(pos[0])), round(float(pos[1])))
        if key in self._ins_seen:
            return
        self._ins_seen.add(key)
        self.inserts.append((m['code'], m['b'] * 1000, m['d'] * 1000,
                             m['is_round'], (float(pos[0]), float(pos[1])),
                             float(rot), m['mclass']))

    def add_beam_text(self, m, a1, a2, n_up):
        mid = (np.asarray(a1) + np.asarray(a2)) / 2.0
        ang = math.degrees(math.atan2(a2[1] - a1[1], a2[0] - a1[0]))
        if ang > 90 or ang <= -90:
            ang += 180
        nn = np.asarray(n_up, dtype=float)
        L = float(np.hypot(nn[0], nn[1]))
        off = (nn / L * (L + 0.15 * self.th)) if L > 1e-9 \
            else np.array([0.0, 0.15 * self.th])
        pos = mid + off
        self.texts.append(((float(pos[0]), float(pos[1])), m['code'], ang,
                           m['mclass']))

    def add_column_text(self, m, x_left, z_mid):
        self.texts.append(((float(x_left - 0.3 * self.th), float(z_mid)),
                           m['code'], 90.0, m['mclass']))


def _upward(n):
    """法線ベクトルを上向き (y正、水平ならx正) に揃える."""
    n = np.asarray(n, dtype=float)
    if n[1] < -1e-9 or (abs(n[1]) <= 1e-9 and n[0] < 0):
        return -n
    return n


# ---------------------------------------------------------------------------
# 軸組図
# ---------------------------------------------------------------------------

def build_elevation(M, key, pin_paper_mm=1.5, scale=None, paper='A3',
                    text_paper_mm=2.5):
    """軸組図の2D図形を組み立てる. 座標系: (構面内s, Z) [mm].

    key: 'X=<座標>' / 'Y=<座標>' (自動検出通り) または 'G:<グループ名>'
    """
    center, u, node_set, gname = _frame_geometry(M, str(key))
    theta = math.atan2(u[1], u[0])

    def s_of(p):
        return float((p[0] - center[0]) * u[0] + (p[1] - center[1]) * u[1])

    mem_idx = [i for i, m in enumerate(M.members)
               if m['n1'] in node_set and m['n2'] in node_set]
    if not mem_idx:
        raise ValueError('通り %s に描画できる部材がありません' % gname)
    mem_set = set(mem_idx)

    pts = []
    for i in mem_idx:
        m = M.members[i]
        pts.append((s_of(m['p1']) * 1000, m['p1'][2] * 1000))
        pts.append((s_of(m['p2']) * 1000, m['p2'][2] * 1000))
    arr = np.asarray(pts)
    w = float(arr[:, 0].max() - arr[:, 0].min())
    h = float(arr[:, 1].max() - arr[:, 1].min())
    n_scale = int(scale) if scale else auto_scale(w, h, paper)
    F = _FigBuilder(M, n_scale, pin_paper_mm * n_scale / 1000.0,
                    text_paper_mm * n_scale)

    def q_of(node_no):
        p = M.node_xyz[int(node_no)]
        return np.array([s_of(p) * 1000, p[2] * 1000])

    # ---- マイター判定 (梁同士の突き合わせ: 相手の面が無い端) ----
    def _col_at(node_no):
        for j in M.node_members.get(int(node_no), []):
            if M.members[j]['kind'] == 'column' and j in mem_set:
                return True
        return False

    miter = {}   # (member_idx, end1or2) -> partner (mj, endk)
    end_map = {}
    for i in mem_idx:
        m = M.members[i]
        if m['kind'] == 'column' or _is_thin_brace(m):
            continue
        end_map.setdefault(int(m['n1']), []).append((i, 1))
        end_map.setdefault(int(m['n2']), []).append((i, 2))
    for node_no, lst in end_map.items():
        if len(lst) != 2 or _col_at(node_no):
            continue
        (i, ei), (j, ej) = lst
        a, b = M.members[i], M.members[j]
        va = a['p2'] - a['p1']
        vb = b['p2'] - b['p1']
        na = float(np.linalg.norm(va))
        nb = float(np.linalg.norm(vb))
        if na < 1e-9 or nb < 1e-9:
            continue
        cosang = abs(float(np.dot(va, vb)) / (na * nb))
        if cosang > math.cos(math.radians(2.0)):
            continue  # ほぼ平行 (連結対象外の継手) はマイターしない
        miter[(i, ei)] = (j, ej)
        miter[(j, ej)] = (i, ei)

    quad = {}
    for i in mem_idx:
        m = M.members[i]
        q1 = q_of(m['n1'])
        q2 = q_of(m['n2'])
        if m['kind'] == 'column':
            half = _col_width_along(m, theta) * 1000
            sb1, sup1 = column_end_setback(M, i, m['n1'])
            sb2, sup2 = column_end_setback(M, i, m['n2'])
            cut1 = (sb1 + (F.gap if (m['pin1'] and sup1) else 0.0)) * 1000
            cut2 = (sb2 + (F.gap if (m['pin2'] and sup2) else 0.0)) * 1000
        else:
            half = _beam_depth_view(m) * 1000
            sb1 = beam_end_setback(M, i, m['n1'], theta, 'elev')
            sb2 = beam_end_setback(M, i, m['n2'], theta, 'elev')
            cut1 = (sb1 + (self_gap(F, m['pin1'], sb1))) * 1000
            cut2 = (sb2 + (self_gap(F, m['pin2'], sb2))) * 1000
            if (i, 1) in miter:
                cut1 = 0.0
            if (i, 2) in miter:
                cut2 = 0.0
        if _is_thin_brace(m):
            r = _member_quad(q1, q2, 0.0, cut1, cut2)
            if r:
                F.add_line(m['mclass'], r[0], r[1])
                F.add_beam_text(m, r[0], r[1], np.array([0.0, 0.0]))
            continue
        if m['kind'] != 'column':
            # 節点=天端: 中心線を見付の半分だけ下へずらす
            v = q2 - q1
            L = float(np.hypot(v[0], v[1]))
            if L > 1e-9:
                nup = _upward(np.array([-v[1], v[0]]) / L)
                q1 = q1 - nup * half
                q2 = q2 - nup * half
        r = _member_quad(q1, q2, half, cut1, cut2)
        if r is None:
            continue
        quad[i] = r

    # ---- マイター頂点の差し替え ----
    done_joint = set()
    for (i, ei), (j, ej) in miter.items():
        jk = tuple(sorted([(i, ei), (j, ej)]))
        if jk in done_joint or i not in quad or j not in quad:
            continue
        done_joint.add(jk)
        _apply_miter(quad, i, ei, j, ej)

    for i, r in quad.items():
        m = M.members[i]
        a1, a2, uu, nn, corners = r
        F.add_rect(m['mclass'], corners)
        if m['kind'] == 'column':
            x_left = min(c[0] for c in corners)
            F.add_column_text(m, x_left, (a1[1] + a2[1]) / 2.0)
        else:
            F.add_beam_text(m, a1, a2, _upward(nn))

    # ---- 直交部材の断面 (天端合わせ) ----
    #  同一節点に両側から直交材が来る場合は、構面法線の正方向(手前)側の
    #  部材のみ描く (向こう側の梁は描かない: ユーザー指摘 2026-07-13)
    n_perp = np.array([-u[1], u[0]])  # 構面法線 (平面内)

    def _side_of(m2, node_no):
        pm = (m2['p1'][:2] + m2['p2'][:2]) / 2.0
        p0 = M.node_xyz[int(node_no)][:2]
        return float((pm[0] - p0[0]) * n_perp[0]
                     + (pm[1] - p0[1]) * n_perp[1])

    for node_no in node_set:
        cands = []
        for j in M.node_members.get(int(node_no), []):
            if j in mem_set:
                continue
            m2 = M.members[j]
            if m2['kind'] == 'column' or _is_thin_brace(m2):
                continue
            cands.append((j, _side_of(m2, node_no)))
        if not cands:
            continue
        front = [j for j, s in cands if s > 1e-6]
        pick = front if front else [j for j, _s in cands]
        pz = M.node_xyz[int(node_no)][2]
        for j in pick:
            m2 = M.members[j]
            pos = (s_of(M.node_xyz[int(node_no)]) * 1000, pz * 1000)
            F.add_insert(m2, pos, m2['beta'])

    return _finish_fig(F, arr, '軸組図 %s  S=1/%d (%s)'
                       % (gname, n_scale, paper), n_scale, w, h)


def self_gap(F, pinned, setback):
    """ピン離し量 [m]: 相手の面がある (setback>0) 場合のみ離す."""
    return F.gap if (pinned and setback > 1e-9) else 0.0


def _apply_miter(quad, i, ei, j, ej):
    """梁同士の突き合わせ端をマイター (留め) にする."""
    a1i, a2i, ui, ni, ci = quad[i]
    a1j, a2j, uj, nj, cj = quad[j]
    pi = a1i if ei == 1 else a2i
    pj = a1j if ej == 1 else a2j
    u_in_i = ui if ei == 1 else -ui       # 接合部→部材内側
    u_in_j = uj if ej == 1 else -uj
    wsum = u_in_i + u_in_j
    L = float(np.hypot(wsum[0], wsum[1]))
    if L < 1e-9:
        return
    outer_dir = -wsum / L
    ni_o = ni if float(np.dot(ni, outer_dir)) > 0 else -ni
    nj_o = nj if float(np.dot(nj, outer_dir)) > 0 else -nj
    p_out = _line_intersect(pi + ni_o, u_in_i, pj + nj_o, u_in_j)
    p_in = _line_intersect(pi - ni_o, u_in_i, pj - nj_o, u_in_j)
    if p_out is None or p_in is None:
        return
    lim = 6.0 * max(float(np.hypot(ni[0], ni[1])),
                    float(np.hypot(nj[0], nj[1])), 1.0)
    if (float(np.hypot(*(p_out - pi))) > lim
            or float(np.hypot(*(p_in - pi))) > lim):
        return

    def _replace(entry, e, p_o, p_i, n_o):
        a1, a2, uu, nn, corners = entry
        plus_is_outer = float(np.dot(nn, n_o)) > 0
        if e == 1:
            corners[0] = p_o if plus_is_outer else p_i
            corners[3] = p_i if plus_is_outer else p_o
        else:
            corners[1] = p_o if plus_is_outer else p_i
            corners[2] = p_i if plus_is_outer else p_o

    _replace(quad[i], ei, p_out, p_in, ni_o)
    _replace(quad[j], ej, p_out, p_in, nj_o)


def _finish_fig(F, arr, title, n_scale, w, h):
    xs = list(arr[:, 0])
    ys = list(arr[:, 1])
    for (_c, b, d, _r, (x, y), _rot, _mc) in F.inserts:
        xs += [x - b / 2, x + b / 2]
        ys += [y - d, y]
    return {'rects': F.rects, 'lines': F.lines, 'inserts': F.inserts,
            'texts': F.texts, 'extent': (w, h), 'scale': n_scale,
            'title': title,
            'bounds': (min(xs), min(ys), max(xs), max(ys))}


# ---------------------------------------------------------------------------
# 伏図
# ---------------------------------------------------------------------------

def plan_levels(M):
    zs = []
    for m in M.members:
        if m['kind'] == 'beam':
            zs.append(round(float(m['p1'][2]), 3))
    return sorted(set(zs))


def plan_keys(M):
    """伏図の候補一覧.

    mgtのフロアグループ (面的に広がるグループ) があればそれを使う。
    無い場合のみ梁レベル (Z) を列挙する。
    戻り値: list of dict {key, label}
    """
    plans = [{'key': 'G:' + g['name'], 'label': g['name']}
             for g in struct_groups(M)
             if g['kind'] == 'plan' and g['n_beam'] > 0]
    if plans:
        return plans
    return [{'key': '%.3f' % z, 'label': '%+.3fm' % z}
            for z in plan_levels(M)]


def build_plan(M, level, pin_paper_mm=1.5, scale=None, paper='A3',
               text_paper_mm=2.5):
    """伏図の2D図形を組み立てる. 座標系: (X, Y) [mm].

    level: 'G:<グループ名>' (グループのELEM_LISTの部材のみ作図)
           または梁天端レベル [m] (数値)
    """
    if isinstance(level, str) and level.startswith('G:'):
        gname = level[2:]
        _nodes, eles = _group_arrays(M, gname)
        beam_idx = [i for i, m in enumerate(M.members)
                    if m['kind'] in ('beam', 'brace')
                    and _member_in_group(m, eles)]
        col_idx = [i for i, m in enumerate(M.members)
                   if m['kind'] == 'column' and _member_in_group(m, eles)]
        title = '伏図 %s' % gname
        if not beam_idx and not col_idx:
            raise ValueError('グループ %s に描画できる部材がありません'
                             ' (ELEM_LIST を確認してください)' % gname)
    else:
        level = float(level)
        beam_idx = [i for i, m in enumerate(M.members)
                    if m['kind'] in ('beam', 'brace')
                    and abs(m['p1'][2] - level) < Z_TOL
                    and abs(m['p2'][2] - level) < Z_TOL]
        col_idx = [i for i, m in enumerate(M.members)
                   if m['kind'] == 'column'
                   and (abs(max(m['p1'][2], m['p2'][2]) - level) < Z_TOL
                        or _column_through(M, m, level))]
        title = '伏図 レベル%+.3fm' % level
        if not beam_idx and not col_idx:
            raise ValueError('レベル %.3fm に描画できる部材がありません'
                             % level)

    pts = []
    for i in beam_idx:
        m = M.members[i]
        pts.append(m['p1'][:2] * 1000)
        pts.append(m['p2'][:2] * 1000)
    for i in col_idx:
        m = M.members[i]
        top = m['p1'] if m['p1'][2] > m['p2'][2] else m['p2']
        pts.append(top[:2] * 1000)
    arr = np.asarray(pts)
    w = float(arr[:, 0].max() - arr[:, 0].min())
    h = float(arr[:, 1].max() - arr[:, 1].min())
    n_scale = int(scale) if scale else auto_scale(w, h, paper)
    F = _FigBuilder(M, n_scale, pin_paper_mm * n_scale / 1000.0,
                    text_paper_mm * n_scale)
    beam_set = set(beam_idx)

    # マイター (柱の無いコーナー)
    def _col_at(node_no):
        for j in M.node_members.get(int(node_no), []):
            if M.members[j]['kind'] == 'column':
                return True
        return False

    miter = {}
    end_map = {}
    for i in beam_idx:
        m = M.members[i]
        if _is_thin_brace(m):
            continue
        end_map.setdefault(int(m['n1']), []).append((i, 1))
        end_map.setdefault(int(m['n2']), []).append((i, 2))
    for node_no, lst in end_map.items():
        if len(lst) != 2 or _col_at(node_no):
            continue
        (i, ei), (j, ej) = lst
        a, b = M.members[i], M.members[j]
        va = (a['p2'] - a['p1'])[:2]
        vb = (b['p2'] - b['p1'])[:2]
        na = float(np.hypot(va[0], va[1]))
        nb = float(np.hypot(vb[0], vb[1]))
        if na < 1e-9 or nb < 1e-9:
            continue
        if abs(float(np.dot(va, vb)) / (na * nb)) \
                > math.cos(math.radians(2.0)):
            continue
        miter[(i, ei)] = (j, ej)
        miter[(j, ej)] = (i, ei)

    quad = {}
    for i in beam_idx:
        m = M.members[i]
        q1 = m['p1'][:2] * 1000
        q2 = m['p2'][:2] * 1000
        theta = math.atan2(q2[1] - q1[1], q2[0] - q1[0])
        sb1 = beam_end_setback(M, i, m['n1'], theta, 'plan')
        sb2 = beam_end_setback(M, i, m['n2'], theta, 'plan')
        cut1 = (sb1 + self_gap(F, m['pin1'], sb1)) * 1000
        cut2 = (sb2 + self_gap(F, m['pin2'], sb2)) * 1000
        if (i, 1) in miter:
            cut1 = 0.0
        if (i, 2) in miter:
            cut2 = 0.0
        if _is_thin_brace(m):
            r = _member_quad(q1, q2, 0.0, cut1, cut2)
            if r:
                F.add_line(m['mclass'], r[0], r[1])
                F.add_beam_text(m, r[0], r[1], np.array([0.0, 0.0]))
            continue
        r = _member_quad(q1, q2, _beam_width_plan(m) * 1000, cut1, cut2)
        if r is None:
            continue
        quad[i] = r

    done_joint = set()
    for (i, ei), (j, ej) in miter.items():
        jk = tuple(sorted([(i, ei), (j, ej)]))
        if jk in done_joint or i not in quad or j not in quad:
            continue
        done_joint.add(jk)
        _apply_miter(quad, i, ei, j, ej)

    for i, r in quad.items():
        m = M.members[i]
        a1, a2, uu, nn, corners = r
        F.add_rect(m['mclass'], corners)
        F.add_beam_text(m, a1, a2, _upward(nn))

    # 柱断面 (断面ブロック、β回転、基点=天端中央→中心合わせ補正)
    for i in col_idx:
        m = M.members[i]
        top = (m['p1'] if m['p1'][2] > m['p2'][2] else m['p2'])[:2] * 1000
        rot = m['beta'] + 90.0  # β=0で幅BがY方向 (規約反転 2026-07-13)
        rad = math.radians(rot)
        off = np.array([-math.sin(rad), math.cos(rad)]) * (m['d'] * 1000 / 2)
        F.add_insert(m, (top[0] + off[0], top[1] + off[1]), rot)
        rb = math.radians(m['beta'])
        half_y = (abs(math.cos(rb)) * m['b']
                  + abs(math.sin(rb)) * m['d']) * 1000 / 2
        F.texts.append(((float(top[0]),
                         float(top[1] + half_y + 0.15 * F.th)),
                        m['code'], 0.0, m['mclass']))

    return _finish_fig(F, arr, '%s  S=1/%d (%s)'
                       % (title, n_scale, paper), n_scale, w, h)


# ---------------------------------------------------------------------------
# DXF書き出し (ezdxf)
# ---------------------------------------------------------------------------

def _new_doc():
    import ezdxf
    doc = ezdxf.new('R2010', setup=True)
    for name, color in (list(LAYER_DEF.values()) + list(LAYER_SEC.values())
                        + [LAYER_TEXT, LAYER_TITLE]):
        if name not in doc.layers:
            doc.layers.add(name, color=color)
    return doc


def _block_name(code):
    import re
    s = re.sub(r'[^0-9A-Za-z_\-]', '_', str(code))
    return 'sec_' + (s or 'X')


def _ensure_sec_block(doc, code, b_mm, d_mm, is_round):
    """断面ブロック (基点=天端中央、矩形/円+対角×) を定義して名前を返す.

    同じ符号で寸法違いの断面がある場合 (例: 柱WC3と梁WC3) は
    寸法つきの別名ブロックに分ける。
    """
    reg = getattr(doc, '_mgtkit_sec_reg', None)
    if reg is None:
        reg = {}
        doc._mgtkit_sec_reg = reg
    key = (str(code), round(float(b_mm), 1), round(float(d_mm), 1),
           bool(is_round))
    if key in reg:
        return reg[key]
    name = _block_name(code)
    if name in doc.blocks:
        name = '%s_%dx%d' % (_block_name(code), round(b_mm), round(d_mm))
        n2 = name
        c = 2
        while n2 in doc.blocks:
            n2 = '%s_%d' % (name, c)
            c += 1
        name = n2
    reg[key] = name
    blk = doc.blocks.new(name)
    attr = {'layer': '0'}
    if is_round:
        r = d_mm / 2.0
        blk.add_circle((0.0, -r), r, dxfattribs=attr)
        k = r / math.sqrt(2.0)
        blk.add_line((-k, -r + k), (k, -r - k), dxfattribs=attr)
        blk.add_line((k, -r + k), (-k, -r - k), dxfattribs=attr)
    else:
        hb = b_mm / 2.0
        blk.add_lwpolyline([(-hb, 0.0), (hb, 0.0), (hb, -d_mm),
                            (-hb, -d_mm)], close=True, dxfattribs=attr)
        blk.add_line((hb, 0.0), (-hb, -d_mm), dxfattribs=attr)
        blk.add_line((hb, -d_mm), (-hb, 0.0), dxfattribs=attr)
    return name


def _fig_to_msp(fig, msp, text_paper_mm=2.5, title_paper_mm=5.0,
                origin=(0.0, 0.0)):
    from ezdxf.enums import TextEntityAlignment
    doc = msp.doc
    ox, oy = origin
    n = fig['scale']
    th = text_paper_mm * n
    for mclass, rect_list in fig.get('rects', {}).items():
        layer = LAYER_DEF[mclass][0]
        for corners in rect_list:
            msp.add_lwpolyline(
                [(x + ox, y + oy) for (x, y) in corners], close=True,
                dxfattribs={'layer': layer})
    for mclass, seg_list in fig.get('lines', {}).items():
        layer = LAYER_DEF[mclass][0]
        for p1, p2 in seg_list:
            msp.add_line((p1[0] + ox, p1[1] + oy),
                         (p2[0] + ox, p2[1] + oy),
                         dxfattribs={'layer': layer, 'linetype': 'DASHED'})
    for (code, b, d, is_round, (x, y), rot, mclass) in fig.get('inserts', []):
        name = _ensure_sec_block(doc, code, b, d, is_round)
        msp.add_blockref(name, (x + ox, y + oy), dxfattribs={
            'layer': LAYER_SEC[mclass][0], 'rotation': rot})
    for (pos, txt, ang, mclass) in fig['texts']:
        t = msp.add_text(txt, dxfattribs={
            'layer': LAYER_TEXT[0], 'height': th, 'rotation': ang})
        t.set_placement((pos[0] + ox, pos[1] + oy),
                        align=TextEntityAlignment.BOTTOM_CENTER)
    x0, y0, x1, y1 = fig['bounds']
    tt = msp.add_text(fig['title'], dxfattribs={
        'layer': LAYER_TITLE[0], 'height': title_paper_mm * n})
    tt.set_placement((x0 + ox, y1 + oy + title_paper_mm * n * 1.5),
                     align=TextEntityAlignment.BOTTOM_LEFT)


def export_struct_dxf(mgt_path, out_dir, axes=None, levels=None,
                      paper='A3', scale=None, pin_paper_mm=1.5,
                      text_paper_mm=2.5, limit_sec_no=9000.0,
                      one_file=True):
    """構造図DXFの一括生成."""
    os.makedirs(out_dir, exist_ok=True)
    M = load_struct_model(mgt_path, limit_sec_no=limit_sec_no)
    figs = []
    for g in (axes or []):
        try:
            figs.append(('axis', g,
                         build_elevation(M, g, pin_paper_mm, scale, paper,
                                         text_paper_mm)))
        except ValueError as e:
            print('注意: %s' % e)
    for lv in (levels or []):
        try:
            figs.append(('plan', lv,
                         build_plan(M, lv, pin_paper_mm, scale,
                                    paper, text_paper_mm)))
        except ValueError as e:
            print('注意: %s' % e)
    if not figs:
        raise ValueError('描画できる図がありません (通り・レベルの選択を'
                         '確認してください)')

    base = os.path.splitext(os.path.basename(mgt_path))[0]
    made = []
    info = []
    if one_file:
        doc = _new_doc()
        msp = doc.modelspace()
        x_cursor = 0.0
        for (_kind, _key, fig) in figs:
            x0, y0, x1, y1 = fig['bounds']
            origin = (x_cursor - x0, -y0)
            _fig_to_msp(fig, msp, text_paper_mm, origin=origin)
            x_cursor += (x1 - x0) + 0.15 * max(x1 - x0, 1000.0) \
                + 20.0 * fig['scale']
            info.append({'title': fig['title'], 'scale': fig['scale']})
        out = os.path.join(out_dir, base + '_構造図.dxf')
        doc.saveas(out)
        made.append(out)
    else:
        for (kind, key, fig) in figs:
            doc = _new_doc()
            _fig_to_msp(fig, doc.modelspace(), text_paper_mm)
            if kind == 'axis':
                label = '軸組_%s' % frame_label(M, str(key)).split(' ')[0]
            elif str(key).startswith('G:'):
                label = '伏図_%s' % str(key)[2:]
            else:
                label = '伏図_%+.3f' % float(key)
            label = str(label).replace('/', '_').replace('\\', '_')
            out = os.path.join(out_dir, '%s_%s.dxf' % (base, label))
            doc.saveas(out)
            made.append(out)
            info.append({'title': fig['title'], 'scale': fig['scale']})
    return made, info


# ---------------------------------------------------------------------------
# 用紙プレビュー (PNG)
# ---------------------------------------------------------------------------

def preview_png(mgt_path, out_path, kind, key, paper='A3', scale=None,
                pin_paper_mm=1.5, limit_sec_no=9000.0):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from .draw_model import _setup_japanese_font
    _setup_japanese_font()

    M = load_struct_model(mgt_path, limit_sec_no=limit_sec_no)
    if kind == 'axis':
        fig_d = build_elevation(M, key, pin_paper_mm, scale, paper)
    else:
        fig_d = build_plan(M, key, pin_paper_mm, scale, paper)
    n = fig_d['scale']
    pw, ph = PAPER_MM.get(paper, PAPER_MM['A3'])
    fw, fh = pw * n, ph * n
    x0, y0, x1, y1 = fig_d['bounds']
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0

    fig, ax = plt.subplots(figsize=(9, 9 * ph / pw))
    ax.add_patch(plt.Rectangle((cx - fw / 2, cy - fh / 2), fw, fh,
                               fill=False, ec='#888', lw=1.2))
    m_mm = PAPER_MARGIN_MM * n
    ax.add_patch(plt.Rectangle((cx - fw / 2 + m_mm, cy - fh / 2 + m_mm),
                               fw - 2 * m_mm, fh - 2 * m_mm, fill=False,
                               ec='#ccc', lw=0.8, ls='--'))
    colors = {'STEEL': '#00b5b8', 'RC': '#c8b400', 'WOOD': '#ff7f24',
              'OTHER': '#7f7f7f'}
    for mclass, rect_list in fig_d.get('rects', {}).items():
        for corners in rect_list:
            xs = [p[0] for p in corners] + [corners[0][0]]
            ys = [p[1] for p in corners] + [corners[0][1]]
            ax.plot(xs, ys, color=colors[mclass], lw=0.7)
    for mclass, seg_list in fig_d.get('lines', {}).items():
        for p1, p2 in seg_list:
            ax.plot([p1[0], p2[0]], [p1[1], p2[1]],
                    color=colors[mclass], lw=0.7, ls='--')
    for (code, b, d, is_round, (x, y), rot, mclass) in \
            fig_d.get('inserts', []):
        col = colors[mclass]
        rad = math.radians(rot)
        c, s = math.cos(rad), math.sin(rad)

        def _tp(px, py):
            return (x + px * c - py * s, y + px * s + py * c)
        if is_round:
            r = d / 2.0
            ax.add_patch(plt.Circle(_tp(0, -r), r, fill=False, ec=col,
                                    lw=0.6))
        else:
            hb = b / 2.0
            corners = [_tp(-hb, 0), _tp(hb, 0), _tp(hb, -d), _tp(-hb, -d)]
            xs = [p[0] for p in corners] + [corners[0][0]]
            ys = [p[1] for p in corners] + [corners[0][1]]
            ax.plot(xs, ys, color=col, lw=0.6)
            ax.plot([corners[1][0], corners[3][0]],
                    [corners[1][1], corners[3][1]], color=col, lw=0.5)
            ax.plot([corners[0][0], corners[2][0]],
                    [corners[0][1], corners[2][1]], color=col, lw=0.5)
    for (pos, txt, ang, mclass) in fig_d.get('texts', []):
        ax.text(pos[0], pos[1], txt, fontsize=6, color='#444',
                ha='center', va='bottom', rotation=ang,
                rotation_mode='anchor')
    ax.set_title('%s' % fig_d['title'], fontsize=11)
    ax.set_aspect('equal')
    ax.set_xticks([])
    ax.set_yticks([])
    pad = 0.05 * max(fw, fh)
    ax.set_xlim(cx - fw / 2 - pad, cx + fw / 2 + pad)
    ax.set_ylim(cy - fh / 2 - pad, cy + fh / 2 + pad)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return out_path, n
