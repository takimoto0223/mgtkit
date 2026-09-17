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

from .util import find_index, read_mgt_text
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
LAYER_GRID = ('S-Gray09芯線', 8)  # 通り芯 (一点鎖線・グレー)。丸記号と通り名は LAYER_TEXT
LAYER_WALL = ('S-Orange40', 30)   # 木造柱伏図の立上り壁 (struct_cad と同じ)
DASHED_SUFFIX = '破線'            # 木造柱伏図の梁: 部材レイヤ名 + '破線' (例 S-木_20破線)
BEAM_DASHED_PAPER_MM = (3.0, -1.5)  # 破線 (線, 空き) 紙面mm

# 木造の伏図2枚 (struct_cad の mode=wood と同じ規則)
#   最上階以外: 梁伏図 (梁=実線+梁符号 / 柱=断面のみ) と 柱伏図 (梁=破線・符号なし /
#               柱=符号つき / その床から立ち上がる壁) の2枚
#   最上階   : 伏図1枚 (梁符号・柱符号とも)
#   柱は「その床から上へ伸びる柱」。下の柱は、ピンで分かれるとき・上に柱が無いときだけ×印
WOOD_TOP_TOL = 0.05       # 最上階判定の許容差 [m]
WALL_INCLINE_DEG = 60.0   # これ以上傾いた面要素を壁とみなす
WALL_BASE_TOL = 0.3       # 壁の下端と床レベルの許容差 [m]
WALL_HATCH_PAPER_MM = 2.5  # 木造軸組図の壁ハッチ (ANSI31) の線間隔 (紙面mm)
ANSI31_SPACING = 3.175     # ezdxf の ANSI31 の線間隔 (尺度1のとき)

# 通り芯 (寸法はすべて紙面mm。縮尺を掛けて実寸にする)
GRID_OVERHANG_PAPER_MM = 5.0    # 図の外形からの線の出
GRID_BUBBLE_R_PAPER_MM = 4.0    # 丸記号の半径
GRID_TEXT_PAPER_MM = 3.5        # 通り名の文字高さ
GRID_DASHDOT_PAPER_MM = (8.0, -1.0, 0.0, -1.0)  # 一点鎖線 (線, 空き, 点, 空き)
# 軸組図のレベル線 (通り芯と同じレイヤ・線種。▽記号とレベル名はまだ描かない)
LEVEL_FLAT_TOL = 0.3      # 節点Zの広がりがこれ未満のフロアを「水平な床」とみなす [m]

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
    out = {}
    try:
        lines = read_mgt_text(mgt_path).split('\n')
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
            # 連結すると部材 i の端点と向きが変わり、ends (端点→部材・端) が
            # 古くなる。古い ends のまま続けると端を取り違えて区間が欠けるので、
            # 1回連結するごとに ends を作り直す (2026-09-17 修正)
            break
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


# ---------------------------------------------------------------------------
# 通り芯
# ---------------------------------------------------------------------------

def grid_lines(M, keys):
    """通り芯にするキー → 平面上の直線のリスト.

    keys は auto_frames のキー ('G:<グループ名>' / 'X=<v>' / 'Y=<v>')。
    戻り値: list of dict {key, label, p (2,) [m], u (2,) 単位ベクトル}
    同じ直線になるもの (グループ名違いの重複など) は先に出たものだけ残す。
    """
    out = []
    for key in (keys or []):
        key = str(key)
        try:
            p, u, _nodes, _name = _frame_geometry(M, key)
        except (ValueError, IndexError):
            _note_once(M, '注意: 通り芯 %s の位置を決められないため描きません' % key)
            continue
        p = np.asarray(p, dtype=float)
        u = np.asarray(u, dtype=float)
        u = u / float(np.hypot(u[0], u[1]))
        dup = False
        for g in out:
            parallel = abs(float(u[0] * g['u'][1] - u[1] * g['u'][0])) < 1e-3
            dist = abs(float((p[0] - g['p'][0]) * g['u'][1]
                             - (p[1] - g['p'][1]) * g['u'][0]))
            if parallel and dist < FRAME_TOL:
                dup = True
                _note_once(M, '注意: 通り芯 %s は %s と同じ位置のため省略します'
                           % (frame_label(M, key).split(' ')[0], g['label']))
                break
        if dup:
            continue
        out.append({'key': key, 'label': frame_label(M, key).split(' ')[0],
                    'p': p, 'u': u})
    return out


def _note_once(M, msg):
    """図ごとに同じ注意が並ばないよう、モデル単位で 1 回だけ出す."""
    seen = getattr(M, '_notes_seen', None)
    if seen is None:
        seen = M._notes_seen = set()
    if msg not in seen:
        seen.add(msg)
        print(msg)


def _grid_extra_paper_mm(grids):
    """auto_scale 用: 通り芯で図の外に増える紙面寸法 (両側の線の出 + 丸記号)."""
    if not grids:
        return 0.0
    return 2 * GRID_OVERHANG_PAPER_MM + 2 * GRID_BUBBLE_R_PAPER_MM


GRID_HIT_TOL_MM = 100.0  # 図の範囲からこの距離 [mm 実寸] までに通る通り芯を描く


def model_plan_bbox_mm(M):
    """建物全体の平面範囲 (x0, y0, x1, y1) [mm]. 全部材 (ダミー断面は部材化されない) の端点から.

    伏図の通り芯は、部分的なフロアでもこの範囲で描く (全階で通り芯の長さ・位置を揃える)。
    """
    if not hasattr(M, '_plan_bbox_mm'):
        xs, ys = [], []
        for m in M.members:
            for pnt in (m['p1'], m['p2']):
                xs.append(float(pnt[0]) * 1000.0)
                ys.append(float(pnt[1]) * 1000.0)
        M._plan_bbox_mm = ((min(xs), min(ys), max(xs), max(ys)) if xs else None)
    return M._plan_bbox_mm


def _grids_for_plan(lines, bbox, n_scale):
    """伏図 (X, Y [mm]) の通り芯. 丸記号は縦の通りなら下端、横の通りなら左端.

    bbox: 部材の外形 (x0, y0, x1, y1)。図と交わらない通り (部分的な床の外の通り) は描かない。
    """
    if not lines:
        return []
    x0, y0, x1, y1 = bbox
    ext = GRID_OVERHANG_PAPER_MM * n_scale
    r = GRID_BUBBLE_R_PAPER_MM * n_scale
    out = []
    for g in lines:
        p = g['p'] * 1000.0
        u = g['u']
        # 直線 p + t*u のうち図の範囲 (GRID_HIT_TOL_MM 広げた矩形) を通る区間
        tlo, thi = -np.inf, np.inf
        hit = True
        for k, (lo, hi) in enumerate(((x0 - GRID_HIT_TOL_MM, x1 + GRID_HIT_TOL_MM),
                                      (y0 - GRID_HIT_TOL_MM, y1 + GRID_HIT_TOL_MM))):
            if abs(u[k]) < 1e-12:
                if not (lo <= p[k] <= hi):
                    hit = False
                continue
            ta, tb = sorted(((lo - p[k]) / u[k], (hi - p[k]) / u[k]))
            tlo, thi = max(tlo, ta), min(thi, tb)
        if not hit or tlo > thi:
            continue  # 図と交わらない通り
        e1 = p + u * (float(tlo) + GRID_HIT_TOL_MM - ext)
        e2 = p + u * (float(thi) - GRID_HIT_TOL_MM + ext)
        if abs(u[1]) >= abs(u[0]):
            start, other = (e1, e2) if e1[1] <= e2[1] else (e2, e1)
        else:
            start, other = (e1, e2) if e1[0] <= e2[0] else (e2, e1)
        d = start - other
        d = d / float(np.hypot(d[0], d[1]))
        c = start + d * r
        out.append({'label': g['label'],
                    'p1': (float(e1[0]), float(e1[1])),
                    'p2': (float(e2[0]), float(e2[1])),
                    'bubble': (float(c[0]), float(c[1])), 'r': r})
    return out


def _levels_for_elevation(levels, bbox, n_scale):
    """軸組図 (s, Z [mm]) のレベル線. 構面の高さの範囲にあるものだけ、図の左右へ線の出を付けて描く."""
    if not levels:
        return []
    s_min, z_min, s_max, z_max = bbox
    ext = GRID_OVERHANG_PAPER_MM * n_scale
    out = []
    for label, z in levels:
        if z < z_min - GRID_HIT_TOL_MM or z > z_max + GRID_HIT_TOL_MM:
            continue
        out.append({'label': label, 'p1': (float(s_min - ext), float(z)),
                    'p2': (float(s_max + ext), float(z))})
    return out


def _grids_for_elevation(lines, center, u, bbox, n_scale):
    """軸組図 (s, Z [mm]) の通り芯. この構面と交わる通りだけを縦線で描き、丸記号は下端.

    bbox: 部材の外形 (s0, z0, s1, z1)。節点=梁天端なので、下端は節点ではなく外形で決める。
    """
    if not lines:
        return []
    s_min, z_min, s_max, z_max = bbox
    ext = GRID_OVERHANG_PAPER_MM * n_scale
    r = GRID_BUBBLE_R_PAPER_MM * n_scale
    tol = GRID_HIT_TOL_MM
    out = []
    seen = []
    for g in lines:
        x = _line_intersect(center, u, g['p'], g['u'])
        if x is None:
            continue  # 構面と平行 (この構面自身など)
        s = float((x[0] - center[0]) * u[0] + (x[1] - center[1]) * u[1]) * 1000.0
        if s < s_min - tol or s > s_max + tol:
            continue
        if any(abs(s - v) < 1.0 for v in seen):
            continue
        seen.append(s)
        zb = z_min - ext
        out.append({'label': g['label'], 'p1': (s, float(zb)),
                    'p2': (s, float(z_max + ext)),
                    'bubble': (s, float(zb - r)), 'r': r})
    return out


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


def auto_scale(width_mm, height_mm, paper='A3', extra_paper_mm=0.0):
    """用紙に収まる縮尺. extra_paper_mm: 図の外に付く通り芯などの紙面寸法 (縦横とも)."""
    pw, ph = PAPER_MM.get(paper, PAPER_MM['A3'])
    aw = pw - 2 * PAPER_MARGIN_MM - extra_paper_mm
    ah = ph - 2 * PAPER_MARGIN_MM - 10.0 - extra_paper_mm
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
        self.grids = []           # {label, p1, p2, bubble, r} 通り芯
        self.rects_dashed = {}    # 木造柱伏図の破線の梁 {mclass: [corners]}
        self.inserts_down = []    # 木造伏図の下柱 (×印のみ) inserts と同じ形
        self.walls = []           # 木造の壁 [corners] (柱伏図=立上り壁 / 軸組図=壁パネル)
        self.wall_hatch = False   # True なら walls に斜線ハッチを付ける (軸組図)
        self.levels = []          # 軸組図のレベル線 {label, p1, p2}
        self.level_spec = []      # (表示名, Z[mm]) → _finish_fig で levels にする
        self.grid_spec = ([], None)  # (通り芯の直線, 図の種類) → _finish_fig で grids にする
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
                    text_paper_mm=2.5, grids=None, levels=None, wood=False):
    """軸組図の2D図形を組み立てる. 座標系: (構面内s, Z) [mm].

    key: 'X=<座標>' / 'Y=<座標>' (自動検出通り) または 'G:<グループ名>'
    grids: 通り芯として描く通りのキー (auto_frames のキー)。この構面と交わるものだけ描く
    levels: レベル線にするフロアのキー (plan_keys のキー)。構面の高さの範囲にあるものだけ描く
    wood: True なら壁パネル (外形・斜線ハッチ・板厚符号) も描く
    """
    grid_ls = grid_lines(M, grids)
    level_ls = level_lines(M, levels)
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
    extra = _grid_extra_paper_mm(grid_ls)
    if level_ls:
        extra = max(extra, 2 * GRID_OVERHANG_PAPER_MM)
    n_scale = int(scale) if scale else auto_scale(w, h, paper, extra)
    F = _FigBuilder(M, n_scale, pin_paper_mm * n_scale / 1000.0,
                    text_paper_mm * n_scale)
    F.grid_spec = (grid_ls, ('elev', center, u))
    F.level_spec = level_ls

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

    # ---- 木造: 壁パネル (外形 + 斜線ハッチ + 板厚符号、パネル1枚ごと) ----
    if wood:
        for poly, (cx, cy), name in _wood_elevation_walls(M, node_set, s_of):
            F.walls.append(poly)
            F.wall_hatch = True
            if name:
                F.texts.append(((float(cx), float(cy)), name, 0.0, 'WOOD'))

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
    for (_c, b, d, _r, (x, y), _rot, _mc) in F.inserts + F.inserts_down:
        xs += [x - b / 2, x + b / 2]
        ys += [y - d, y]
    for corners in F.walls:
        xs += [c[0] for c in corners]
        ys += [c[1] for c in corners]
    grid_ls, kind = F.grid_spec
    if grid_ls or F.level_spec:
        # 通り芯の位置決めだけは部材の外形 (矩形・線) も含めた範囲で行う。
        # 図の bounds (配置・見出し位置) は通り芯なしのときと変えない。
        gx, gy = list(xs), list(ys)
        for rect_list in list(F.rects.values()) + list(F.rects_dashed.values()):
            for corners in rect_list:
                gx += [c[0] for c in corners]
                gy += [c[1] for c in corners]
        for seg_list in F.lines.values():
            for p1, p2 in seg_list:
                gx += [p1[0], p2[0]]
                gy += [p1[1], p2[1]]
        bbox = (min(gx), min(gy), max(gx), max(gy))
        if kind[0] == 'elev':
            F.grids = _grids_for_elevation(grid_ls, kind[1], kind[2], bbox, n_scale)
            F.levels = _levels_for_elevation(F.level_spec, bbox, n_scale)
        else:
            gb = model_plan_bbox_mm(F.M)
            if gb:
                bbox = (min(bbox[0], gb[0]), min(bbox[1], gb[1]),
                        max(bbox[2], gb[2]), max(bbox[3], gb[3]))
            F.grids = _grids_for_plan(grid_ls, bbox, n_scale)
    for lv in F.levels:
        xs += [lv['p1'][0], lv['p2'][0]]
        ys += [lv['p1'][1], lv['p2'][1]]
    for g in F.grids:
        (bx, by), r = g['bubble'], g['r']
        xs += [g['p1'][0], g['p2'][0], bx - r, bx + r]
        ys += [g['p1'][1], g['p2'][1], by - r, by + r]
    return {'rects': F.rects, 'lines': F.lines, 'inserts': F.inserts,
            'texts': F.texts, 'grids': F.grids,
            'rects_dashed': F.rects_dashed, 'inserts_down': F.inserts_down,
            'walls': F.walls, 'wall_hatch': F.wall_hatch, 'levels': F.levels,
            'extent': (w, h), 'scale': n_scale,
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


def _group_floor_nodes(M, gname):
    """床グループの節点. NODE_LIST が空なら ELEM_LIST の梁の節点で代える."""
    nodes, eles = _group_arrays(M, gname)
    if nodes:
        return list(nodes)
    out = set()
    for m in M.members:
        if m['kind'] != 'column' and _member_in_group(m, eles):
            out.update([int(m['n1']), int(m['n2'])] + list(m.get('mid_nodes', [])))
    return [n for n in out if n in M.node_xyz]


def plan_key_z(M, key):
    """伏図キー → 床レベル Z [m]. グループは節点Zの平均 (struct_cad と同じ)."""
    key = str(key)
    if key.startswith('G:'):
        nodes = _group_floor_nodes(M, key[2:])
        zs = [float(M.node_xyz[n][2]) for n in nodes]
        if not zs:
            raise ValueError('グループ %s に節点がありません' % key[2:])
        return sum(zs) / len(zs)
    return float(key)


def plan_key_is_flat(M, key):
    """伏図キーが水平な床か (勾配屋根などはレベル線の既定から外す)."""
    key = str(key)
    if not key.startswith('G:'):
        return True
    zs = [float(M.node_xyz[n][2]) for n in _group_floor_nodes(M, key[2:])]
    return bool(zs) and (max(zs) - min(zs)) < LEVEL_FLAT_TOL


def level_lines(M, keys):
    """レベル線にするフロアのキー → list of (表示名, Z [mm]). 同じ高さは先の方だけ."""
    out = []
    for key in (keys or []):
        key = str(key)
        try:
            z = plan_key_z(M, key) * 1000.0
        except (ValueError, IndexError):
            _note_once(M, '注意: レベル %s の高さを決められないため描きません' % key)
            continue
        label = key[2:] if key.startswith('G:') else '%+.3f' % float(key)
        dup = next((lb for lb, zz in out if abs(zz - z) < 1.0), None)
        if dup is not None:
            _note_once(M, '注意: レベル %s は %s と同じ高さのため省略します'
                       % (label, dup))
            continue
        out.append((label, z))
    return out


def wood_top_z(M):
    """木造の最上階レベル: 伏図候補 (選択に関係なくモデル全体) の最大Z."""
    zs = []
    for p in plan_keys(M):
        try:
            zs.append(plan_key_z(M, p['key']))
        except (ValueError, IndexError):
            continue
    return max(zs) if zs else None


def wood_plan_sheets(M, key, top_z=None):
    """木造での伏図の枚数: 最上階は ['full']、それ以外は ['beam', 'column']."""
    if top_z is None:
        top_z = wood_top_z(M)
    z = plan_key_z(M, key)
    if top_z is None or z >= top_z - WOOD_TOP_TOL:
        return ['full']
    return ['beam', 'column']


def _wood_plan_columns(M, floor_nodes):
    """床節点に置く柱 (struct_cad _columns_at_node と同じ規則).

    戻り値: list of (member index, 節点番号, down, 符号を付けるか)
      - 節点を貫通する通し柱 → その柱1本 (上柱扱い)
      - 上柱と下柱が別部材で、どちらかがその節点でピン → 上柱+下柱
      - 剛で分かれているだけ → 上柱のみ / 片側しか無ければその側
      down=True は下柱 (×印のみ)。符号は上柱 (通し柱) にだけ付ける。
    """
    out = []
    for node in sorted(set(int(n) for n in floor_nodes)):
        if node not in M.node_xyz:
            continue
        nz = float(M.node_xyz[node][2])
        up, down, through = [], [], None
        for j in M.node_members.get(node, []):
            m = M.members[j]
            if m['kind'] != 'column':
                continue
            if node in (int(m['n1']), int(m['n2'])):
                far = m['p2'] if int(m['n1']) == node else m['p1']
                pinned = m['pin1'] if int(m['n1']) == node else m['pin2']
                if far[2] > nz + Z_TOL:
                    up.append((j, pinned))
                elif far[2] < nz - Z_TOL:
                    down.append((j, pinned))
            elif _column_through(M, m, nz):
                through = j
        if through is not None:
            out.append((through, node, False, True))
            continue
        pin_split = bool(up) and bool(down) and any(
            pinned for _j, pinned in up + down)
        if up:
            out.append((up[0][0], node, False, True))
        if down and (pin_split or not up):
            out.append((down[0][0], node, True, False))
    return out


def _thickness_table(mgt_path):
    """*THICKNESS を寛容に読む → {厚さID: (板厚[m], 名称)}.

    VALUE型: iTHK, TYPE, [NAME,] bSAME, THIK-IN, ... 。名称の有無どちらも読む
    (struct_cad read/thickness.py と同じ規則)。
    """
    out = {}
    ins = False
    for ln in read_mgt_text(mgt_path).split('\n'):
        st = ln.strip()
        if st.startswith('*'):
            ins = st.startswith('*THICKNESS')
            continue
        if not ins or not st or st.startswith(';'):
            continue
        f = [x.strip() for x in ln.split(',')]
        if len(f) < 2 or _is_number(f[1]):
            continue  # 続き行
        try:
            tid = int(float(f[0]))
        except ValueError:
            continue
        val, name = 0.0, ''
        for t in f[1:]:
            if _is_number(t):
                val = float(t)
                break
            if not name and t.upper() not in ('VALUE', 'STIFFENED', 'USER',
                                              'YES', 'NO', 'DB'):
                name = t
        out[tid] = (val, name)
    return out


def _is_number(t):
    try:
        float(t)
        return True
    except ValueError:
        return False


def _surface_elements(mgt_path):
    """*ELEMENT の PLATE / WALL 行 → list of (要素番号, 厚さID, [節点...])."""
    out = []
    ins = False
    for ln in read_mgt_text(mgt_path).split('\n'):
        st = ln.strip()
        if st.startswith('*'):
            ins = st.startswith('*ELEMENT')
            continue
        if not ins or not st or st.startswith(';'):
            continue
        f = [x.strip() for x in ln.split(',')]
        if len(f) < 7 or f[1].upper() not in ('PLATE', 'WALL'):
            continue
        try:
            nodes = [int(float(v)) for v in f[4:8] if v and float(v) > 0]
            out.append((int(float(f[0])), int(float(f[3])), nodes))
        except ValueError:
            continue
    return out


def _ensure_surfaces(M):
    """面要素 (PLATE/WALL) と板厚表をモデルに読み込んでおく (1回だけ)."""
    if not hasattr(M, '_surfaces'):
        M._surfaces = _surface_elements(M.mgt_path)
        M._thickness = _thickness_table(M.mgt_path)


def _wood_elevation_walls(M, node_set, s_of):
    """軸組図に描く壁パネル → list of (投影ポリゴン[(s,z) mm], 重心, 板厚名称).

    壁 = 節点がすべてこの構面の節点に含まれる PLATE/WALL 要素 (部材の選び方と同じ)。
    パネル 1 枚ごと (struct_cad axis_elevation と同じ)。
    """
    _ensure_surfaces(M)
    out = []
    for _ele, tid, nodes in M._surfaces:
        if len(nodes) < 3 or any(n not in node_set or n not in M.node_xyz
                                 for n in nodes):
            continue
        poly = [(s_of(M.node_xyz[n]) * 1000.0, float(M.node_xyz[n][2]) * 1000.0)
                for n in nodes]
        area2 = sum(poly[i][0] * poly[(i + 1) % len(poly)][1]
                    - poly[(i + 1) % len(poly)][0] * poly[i][1]
                    for i in range(len(poly)))
        if abs(area2) < 1.0:
            continue  # 構面に対して真横を向いた面 (投影が線になる)
        cx = sum(p[0] for p in poly) / len(poly)
        cy = sum(p[1] for p in poly) / len(poly)
        _t, name = M._thickness.get(tid, (0.0, ''))
        out.append((poly, (cx, cy), name))
    return out


def _plane_inclination_deg(pts):
    """節点群にフィットした面の水平面からの傾き [deg] (0=水平, 90=鉛直)."""
    pts = np.asarray(pts, dtype=float)
    if len(pts) < 3:
        return 90.0 if np.ptp(pts[:, 2]) > 1e-6 else 0.0
    q = pts - pts.mean(axis=0)
    w, v = np.linalg.eigh(q.T @ q)
    if w[1] <= 1e-9 * max(w[2], 1e-12):
        return 90.0 if np.ptp(pts[:, 2]) > 1e-6 else 0.0
    nz = abs(float(v[2, 0]))
    return math.degrees(math.acos(min(1.0, nz)))


def _wood_rising_walls(M, fz, floor_xy_box):
    """床レベル fz から立ち上がる壁 → list of (corners[mm], 中点[mm], 角度, 名称).

    壁 = 60度以上傾いた PLATE/WALL 要素で、下端が fz にあり、平面位置が
    その床の範囲内のもの。矩形は struct_cad _wall_rect と同じ:
    外面を下の梁の外側の縁 (床の重心から遠い側) に合わせ、板厚だけ内側へ広げる。
    """
    _ensure_surfaces(M)
    x0, y0, x1, y1 = floor_xy_box
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    out = []
    for _ele, tid, nodes in M._surfaces:
        pts = [M.node_xyz[n] for n in nodes if n in M.node_xyz]
        if len(pts) < 3:
            continue
        if _plane_inclination_deg(pts) < WALL_INCLINE_DEG:
            continue
        zmin = min(float(p[2]) for p in pts)
        if abs(zmin - fz) > WALL_BASE_TOL:
            continue
        mx = sum(float(p[0]) for p in pts) / len(pts)
        my = sum(float(p[1]) for p in pts) / len(pts)
        if not (x0 - 0.1 <= mx <= x1 + 0.1 and y0 - 0.1 <= my <= y1 + 0.1):
            continue
        t, name = M._thickness.get(tid, (0.0, ''))
        if t <= 0.0:
            continue
        # 平面上の線分 = 最も離れた2点
        uniq = list(dict.fromkeys((round(float(p[0]), 4), round(float(p[1]), 4))
                                  for p in pts))
        if len(uniq) < 2:
            continue
        best, bd = None, -1.0
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                d = (uniq[i][0] - uniq[j][0]) ** 2 + (uniq[i][1] - uniq[j][1]) ** 2
                if d > bd:
                    bd, best = d, (uniq[i], uniq[j])
        (ax, ay), (bx, by) = best
        L = math.hypot(bx - ax, by - ay)
        if L < 1e-9:
            continue
        ux, uy = (bx - ax) / L, (by - ay) / L
        nx, ny = -uy, ux
        if nx * ((ax + bx) / 2 - cx) + ny * ((ay + by) / 2 - cy) < 0:
            nx, ny = -nx, -ny   # 外向き (床の重心から遠い側)
        # 下端2節点を結ぶ梁の平面見付け幅 (無ければ板厚)
        bottom = set(n for n in nodes if n in M.node_xyz
                     and abs(float(M.node_xyz[n][2]) - zmin) < 1e-6)
        support = t
        if len(bottom) >= 2:
            for j in M.node_members.get(next(iter(bottom)), []):
                m = M.members[j]
                if m['kind'] == 'column':
                    continue
                mn = set([int(m['n1']), int(m['n2'])] + list(m.get('mid_nodes', [])))
                if bottom <= mn:
                    support = _beam_width_plan(m) * 2.0
                    break
        outer = support / 2.0
        inner = outer - t
        corners = [((ax + nx * outer) * 1000, (ay + ny * outer) * 1000),
                   ((bx + nx * outer) * 1000, (by + ny * outer) * 1000),
                   ((bx + nx * inner) * 1000, (by + ny * inner) * 1000),
                   ((ax + nx * inner) * 1000, (ay + ny * inner) * 1000)]
        mid = (((ax + bx) / 2 + nx * outer) * 1000, ((ay + by) / 2 + ny * outer) * 1000)
        ang = math.degrees(math.atan2(uy, ux))
        out.append((corners, mid, ang, (nx, ny), name))
    return out


def build_plan(M, level, pin_paper_mm=1.5, scale=None, paper='A3',
               text_paper_mm=2.5, grids=None, wood_sheet=None, top_z=None):
    """伏図の2D図形を組み立てる. 座標系: (X, Y) [mm].

    level: 'G:<グループ名>' (グループのELEM_LISTの部材のみ作図)
           または梁天端レベル [m] (数値)
    grids: 通り芯として描く通りのキー (auto_frames のキー)
    wood_sheet: None=木造以外 (従来どおり) / 木造では 'full' (最上階) /
                'beam' (梁伏図) / 'column' (柱伏図)
    """
    grid_ls = grid_lines(M, grids)
    wood = wood_sheet is not None
    level_key = level
    if isinstance(level, str) and level.startswith('G:'):
        gname = level[2:]
        grp_nodes, eles = _group_arrays(M, gname)
        beam_idx = [i for i, m in enumerate(M.members)
                    if m['kind'] in ('beam', 'brace')
                    and _member_in_group(m, eles)]
        col_idx = [i for i, m in enumerate(M.members)
                   if m['kind'] == 'column' and _member_in_group(m, eles)]
        title = '伏図 %s' % gname
        floor_nodes = _group_floor_nodes(M, gname)
        if not beam_idx and not col_idx and not wood:
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
        floor_nodes = set()
        for i in beam_idx:
            floor_nodes.update([int(M.members[i]['n1']), int(M.members[i]['n2'])]
                               + list(M.members[i].get('mid_nodes', [])))
        for n, p in M.node_xyz.items():
            if abs(float(p[2]) - level) < Z_TOL and M.node_members.get(int(n)):
                floor_nodes.add(int(n))
        if not beam_idx and not col_idx and not wood:
            raise ValueError('レベル %.3fm に描画できる部材がありません'
                             % level)

    if wood:
        # 木造: 柱は「その床から上へ伸びる柱」(struct_cad 方式) で選び直す
        wood_cols = _wood_plan_columns(M, floor_nodes)
        if wood_sheet == 'beam':
            title = title.replace('伏図', '梁伏図', 1)
        elif wood_sheet == 'column':
            title = title.replace('伏図', '柱伏図', 1)
        if not beam_idx and not wood_cols:
            raise ValueError('%s に描画できる部材がありません' % title)

    pts = []
    for i in beam_idx:
        m = M.members[i]
        pts.append(m['p1'][:2] * 1000)
        pts.append(m['p2'][:2] * 1000)
    if wood:
        for _j, node, _down, _lab in wood_cols:
            pts.append(M.node_xyz[node][:2] * 1000)
    else:
        for i in col_idx:
            m = M.members[i]
            top = m['p1'] if m['p1'][2] > m['p2'][2] else m['p2']
            pts.append(top[:2] * 1000)
    arr = np.asarray(pts)
    w = float(arr[:, 0].max() - arr[:, 0].min())
    h = float(arr[:, 1].max() - arr[:, 1].min())
    sw, sh = w, h
    gb = model_plan_bbox_mm(M) if grid_ls else None
    if gb:
        # 通り芯は建物全体の範囲で描くので、縮尺もその範囲が収まるように選ぶ
        sw = max(float(arr[:, 0].max()), gb[2]) - min(float(arr[:, 0].min()), gb[0])
        sh = max(float(arr[:, 1].max()), gb[3]) - min(float(arr[:, 1].min()), gb[1])
    n_scale = int(scale) if scale else auto_scale(
        sw, sh, paper, _grid_extra_paper_mm(grid_ls))
    F = _FigBuilder(M, n_scale, pin_paper_mm * n_scale / 1000.0,
                    text_paper_mm * n_scale)
    F.grid_spec = (grid_ls, ('plan',))
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
                if wood_sheet != 'column':
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
        if wood_sheet == 'column':
            F.rects_dashed.setdefault(m['mclass'], []).append(
                [(float(c[0]), float(c[1])) for c in corners])
            continue  # 柱伏図の梁は破線・符号なし
        F.add_rect(m['mclass'], corners)
        F.add_beam_text(m, a1, a2, _upward(nn))

    # 柱断面 (断面ブロック、β回転、基点=天端中央→中心合わせ補正)
    def _put_column(m, xy, down=False, label=True):
        rot = m['beta'] + 90.0  # β=0で幅BがY方向 (規約反転 2026-07-13)
        rad = math.radians(rot)
        off = np.array([-math.sin(rad), math.cos(rad)]) * (m['d'] * 1000 / 2)
        pos = (xy[0] + off[0], xy[1] + off[1])
        if down:
            key = (m['code'], round(float(pos[0])), round(float(pos[1])), 'down')
            if key not in F._ins_seen:
                F._ins_seen.add(key)
                F.inserts_down.append((m['code'], m['b'] * 1000, m['d'] * 1000,
                                       m['is_round'], (float(pos[0]), float(pos[1])),
                                       float(rot), m['mclass']))
        else:
            F.add_insert(m, pos, rot)
        if label:
            rb = math.radians(m['beta'])
            half_y = (abs(math.cos(rb)) * m['b']
                      + abs(math.sin(rb)) * m['d']) * 1000 / 2
            F.texts.append(((float(xy[0]),
                             float(xy[1] + half_y + 0.15 * F.th)),
                            m['code'], 0.0, m['mclass']))

    if wood:
        for j, node, down, lab in wood_cols:
            _put_column(M.members[j], M.node_xyz[node][:2] * 1000, down=down,
                        label=lab and wood_sheet != 'beam')
    else:
        for i in col_idx:
            m = M.members[i]
            top = (m['p1'] if m['p1'][2] > m['p2'][2] else m['p2'])[:2] * 1000
            _put_column(m, top)

    # 木造の柱伏図: その床から立ち上がる壁 (板厚の矩形 + 板厚符号)
    if wood_sheet == 'column':
        fz = plan_key_z(M, level_key)
        xs = [float(M.node_xyz[n][0]) for n in floor_nodes if n in M.node_xyz]
        ys = [float(M.node_xyz[n][1]) for n in floor_nodes if n in M.node_xyz]
        if xs:
            for corners, mid, ang, (nx, ny), name in _wood_rising_walls(
                    M, fz, (min(xs), min(ys), max(xs), max(ys))):
                F.walls.append(corners)
                if not name:
                    continue
                a = ang
                if a > 90 or a <= -90:
                    a += 180
                up = (-math.sin(math.radians(a)), math.cos(math.radians(a)))
                pos = (mid[0] + nx * 0.15 * F.th, mid[1] + ny * 0.15 * F.th)
                if nx * up[0] + ny * up[1] < 0:   # 外向きが文字の下向き → 文字高さ分さらに外へ
                    pos = (pos[0] + nx * F.th, pos[1] + ny * F.th)
                F.texts.append(((float(pos[0]), float(pos[1])), name, a, 'WOOD'))

    return _finish_fig(F, arr, '%s  S=1/%d (%s)'
                       % (title, n_scale, paper), n_scale, w, h)


# ---------------------------------------------------------------------------
# DXF書き出し (ezdxf)
# ---------------------------------------------------------------------------

def _new_doc():
    import ezdxf
    doc = ezdxf.new('R2010', setup=True)
    for name, color in (list(LAYER_DEF.values()) + list(LAYER_SEC.values())
                        + [LAYER_TEXT, LAYER_TITLE, LAYER_GRID]):
        if name not in doc.layers:
            doc.layers.add(name, color=color)
    return doc


def _scaled_linetype(doc, prefix, paper_pattern, n_scale):
    """紙面寸法のパターンに縮尺を焼き込んだ線種を定義して名前を返す.

    線種尺度 (LTSCALE/CELTSCALE) の扱いは CAD ごとに違うため、
    図の縮尺ごとに別の線種を定義する。
    """
    name = '%s_S%d' % (prefix, int(n_scale))
    if name not in doc.linetypes:
        pat = [float(v) * n_scale for v in paper_pattern]
        total = sum(abs(v) for v in pat)
        doc.linetypes.add(name, pattern=[total] + pat,
                          description='%s 1/%d' % (prefix, int(n_scale)))
    return name


def _grid_linetype(doc, n_scale):
    """縮尺ごとの一点鎖線 (通り芯)."""
    return _scaled_linetype(doc, 'GRID_DASHDOT', GRID_DASHDOT_PAPER_MM, n_scale)


def _ensure_layer(doc, name, color):
    if name not in doc.layers:
        doc.layers.add(name, color=color)


def _block_name(code):
    import re
    s = re.sub(r'[^0-9A-Za-z_\-]', '_', str(code))
    return 'sec_' + (s or 'X')


def _ensure_sec_block(doc, code, b_mm, d_mm, is_round, down=False):
    """断面ブロック (基点=天端中央、矩形/円+対角×) を定義して名前を返す.

    同じ符号で寸法違いの断面がある場合 (例: 柱WC3と梁WC3) は
    寸法つきの別名ブロックに分ける。
    """
    reg = getattr(doc, '_mgtkit_sec_reg', None)
    if reg is None:
        reg = {}
        doc._mgtkit_sec_reg = reg
    key = (str(code), round(float(b_mm), 1), round(float(d_mm), 1),
           bool(is_round), bool(down))
    if key in reg:
        return reg[key]
    name = _block_name(code) + ('_down' if down else '')
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
    if down:
        # 下柱: 外形なしの×印のみ (struct_cad の <符号>_section2 と同じ表現)
        hb = (d_mm if is_round else b_mm) / 2.0
        blk.add_line((hb, 0.0), (-hb, -d_mm), dxfattribs=attr)
        blk.add_line((hb, -d_mm), (-hb, 0.0), dxfattribs=attr)
        return name
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
    if fig.get('prims'):
        from .dxf_list import prims_to_msp
        prims_to_msp(fig['prims'], msp, origin)
    walls = fig.get('walls') or []
    if walls:
        _ensure_layer(doc, LAYER_WALL[0], LAYER_WALL[1])
    for corners in walls:
        pts = [(x + ox, y + oy) for (x, y) in corners]
        if fig.get('wall_hatch'):
            hatch = msp.add_hatch(dxfattribs={'layer': LAYER_WALL[0]})
            hatch.set_pattern_fill('ANSI31', scale=WALL_HATCH_PAPER_MM * n
                                   / ANSI31_SPACING)
            hatch.paths.add_polyline_path(pts, is_closed=True)
        msp.add_lwpolyline(pts, close=True, dxfattribs={'layer': LAYER_WALL[0]})
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
    for (code, b, d, is_round, (x, y), rot, mclass) in fig.get('inserts_down', []):
        name = _ensure_sec_block(doc, code, b, d, is_round, down=True)
        msp.add_blockref(name, (x + ox, y + oy), dxfattribs={
            'layer': LAYER_SEC[mclass][0], 'rotation': rot})
    dashed = fig.get('rects_dashed') or {}
    if dashed:
        lt_d = _scaled_linetype(doc, 'BEAM_DASHED', BEAM_DASHED_PAPER_MM, n)
    for mclass, rect_list in dashed.items():
        layer = LAYER_DEF[mclass][0] + DASHED_SUFFIX
        _ensure_layer(doc, layer, LAYER_DEF[mclass][1])
        for corners in rect_list:
            msp.add_lwpolyline(
                [(x + ox, y + oy) for (x, y) in corners], close=True,
                dxfattribs={'layer': layer, 'linetype': lt_d})

    for (pos, txt, ang, mclass) in fig['texts']:
        t = msp.add_text(txt, dxfattribs={
            'layer': LAYER_TEXT[0], 'height': th, 'rotation': ang})
        t.set_placement((pos[0] + ox, pos[1] + oy),
                        align=TextEntityAlignment.BOTTOM_CENTER)
    grids = fig.get('grids') or []
    levels = fig.get('levels') or []
    if grids or levels:
        lt = _grid_linetype(doc, n)
        gh = GRID_TEXT_PAPER_MM * n
    for lv in levels:
        msp.add_line((lv['p1'][0] + ox, lv['p1'][1] + oy),
                     (lv['p2'][0] + ox, lv['p2'][1] + oy),
                     dxfattribs={'layer': LAYER_GRID[0], 'linetype': lt})
    for g in grids:
        msp.add_line((g['p1'][0] + ox, g['p1'][1] + oy),
                     (g['p2'][0] + ox, g['p2'][1] + oy),
                     dxfattribs={'layer': LAYER_GRID[0], 'linetype': lt})
        bx, by = g['bubble']
        msp.add_circle((bx + ox, by + oy), g['r'],
                       dxfattribs={'layer': LAYER_TEXT[0]})
        t = msp.add_text(g['label'], dxfattribs={
            'layer': LAYER_TEXT[0], 'height': gh})
        t.set_placement((bx + ox, by + oy),
                        align=TextEntityAlignment.MIDDLE_CENTER)
    x0, y0, x1, y1 = fig['bounds']
    tt = msp.add_text(fig['title'], dxfattribs={
        'layer': LAYER_TITLE[0], 'height': title_paper_mm * n})
    tt.set_placement((x0 + ox, y1 + oy + title_paper_mm * n * 1.5),
                     align=TextEntityAlignment.BOTTOM_LEFT)


def export_struct_dxf(mgt_path, out_dir, axes=None, levels=None,
                      paper='A3', scale=None, pin_paper_mm=1.5,
                      text_paper_mm=2.5, limit_sec_no=9000.0,
                      one_file=True, grids=None, wood=False, level_keys=None,
                      list_out=False, list_categories=None):
    """構造図DXFの一括生成.

    grids: 通り芯として描く通りのキー
    wood : True なら木造 (最上階以外の伏図を梁伏図・柱伏図の2枚にする)
    level_keys: 軸組図にレベル線を描くフロアのキー
    list_out: True なら部材リスト図も出す (dxf_list.py)
    list_categories: 部材リストに載せる区分 (S / W / OTHER / RC / RCB)。None なら全部
    """
    os.makedirs(out_dir, exist_ok=True)
    M = load_struct_model(mgt_path, limit_sec_no=limit_sec_no)
    figs = []
    for g in (axes or []):
        try:
            figs.append(('axis', g,
                         build_elevation(M, g, pin_paper_mm, scale, paper,
                                         text_paper_mm, grids, level_keys,
                                         wood=wood)))
        except ValueError as e:
            print('注意: %s' % e)
    top_z = wood_top_z(M) if wood else None
    for lv in (levels or []):
        try:
            sheets = wood_plan_sheets(M, lv, top_z) if wood else [None]
            for sh in sheets:
                figs.append(('plan', (lv, sh),
                             build_plan(M, lv, pin_paper_mm, scale,
                                        paper, text_paper_mm, grids,
                                        wood_sheet=sh, top_z=top_z)))
        except ValueError as e:
            print('注意: %s' % e)
    if list_out:
        from .dxf_list import build_list_figures
        for cat, fig in build_list_figures(M, scale, paper, text_paper_mm,
                                           categories=list_categories):
            figs.append(('list', cat, fig))
    if not figs:
        raise ValueError('描画できる図がありません (通り・レベルの選択や'
                         '部材リストの出力を確認してください)')

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
            elif kind == 'list':
                label = 'リスト_%s' % fig['title'].split('  ')[0].replace(' ', '')
            else:
                key, sheet = key
                if str(key).startswith('G:'):
                    label = '伏図_%s' % str(key)[2:]
                else:
                    label = '伏図_%+.3f' % float(key)
                label += {'beam': '_梁', 'column': '_柱'}.get(sheet, '')
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
                pin_paper_mm=1.5, limit_sec_no=9000.0, grids=None,
                wood=False, sheet=None, level_keys=None, text_paper_mm=2.5,
                page=1, info=None):
    """用紙プレビュー PNG. 部材リスト (kind='list', key=区分) は page 枚目を描き、
    info (dict) に 'pages' (その区分の図の枚数) を入れる."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from .draw_model import _setup_japanese_font
    _setup_japanese_font()

    M = load_struct_model(mgt_path, limit_sec_no=limit_sec_no)
    if kind == 'list':
        from .dxf_list import build_list_figures
        figs_l = build_list_figures(M, scale, paper, text_paper_mm,
                                    categories=[str(key)])
        if not figs_l:
            raise ValueError('部材リストの %s に載せる断面がありません' % key)
        page = min(max(int(page or 1), 1), len(figs_l))
        if info is not None:
            info['pages'] = len(figs_l)
            info['page'] = page
        fig_d = figs_l[page - 1][1]
    elif kind == 'axis':
        fig_d = build_elevation(M, key, pin_paper_mm, scale, paper,
                                text_paper_mm, grids=grids, levels=level_keys,
                                wood=wood)
    else:
        wood_sheet = None
        if wood:
            sheets = wood_plan_sheets(M, key)
            wood_sheet = sheet if sheet in sheets else sheets[0]
        fig_d = build_plan(M, key, pin_paper_mm, scale, paper, text_paper_mm,
                           grids=grids, wood_sheet=wood_sheet)
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
    if fig_d.get('prims'):
        from .dxf_list import prims_to_axes
        # 文字の大きさを実寸に合わせる: 図の横幅 9in に 用紙幅×1.1 が入る
        pt_per_mm = 9 * 72.0 / (fw * 1.1)
        prims_to_axes(fig_d['prims'], ax, pt_per_mm)
    for mclass, rect_list in fig_d.get('rects_dashed', {}).items():
        for corners in rect_list:
            xs = [p[0] for p in corners] + [corners[0][0]]
            ys = [p[1] for p in corners] + [corners[0][1]]
            ax.plot(xs, ys, color=colors[mclass], lw=0.7, ls='--')
    for corners in fig_d.get('walls', []):
        xs = [p[0] for p in corners] + [corners[0][0]]
        ys = [p[1] for p in corners] + [corners[0][1]]
        if fig_d.get('wall_hatch'):
            # 部材と符号が読めるよう、壁は薄い色で背面に描く
            ax.fill(xs, ys, fill=False, hatch='////', ec='#f5c48a', lw=0.4,
                    zorder=0)
        else:
            ax.plot(xs, ys, color='#e07b00', lw=0.9)
    for (code, b, d, is_round, (x, y), rot, mclass) in fig_d.get('inserts_down', []):
        rad = math.radians(rot)
        c, s_ = math.cos(rad), math.sin(rad)
        hb = (d if is_round else b) / 2.0
        for (px1, py1), (px2, py2) in (((hb, 0), (-hb, -d)), ((hb, -d), (-hb, 0))):
            ax.plot([x + px1 * c - py1 * s_, x + px2 * c - py2 * s_],
                    [y + px1 * s_ + py1 * c, y + px2 * s_ + py2 * c],
                    color=colors[mclass], lw=0.5)
    for lv in fig_d.get('levels', []):
        ax.plot([lv['p1'][0], lv['p2'][0]], [lv['p1'][1], lv['p2'][1]],
                color='#999', lw=0.6, ls='-.')
    for g in fig_d.get('grids', []):
        ax.plot([g['p1'][0], g['p2'][0]], [g['p1'][1], g['p2'][1]],
                color='#999', lw=0.6, ls='-.')
        ax.add_patch(plt.Circle(g['bubble'], g['r'], fill=False, ec='#d33',
                                lw=0.6))
        ax.text(g['bubble'][0], g['bubble'][1], g['label'], fontsize=6,
                color='#d33', ha='center', va='center')
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
    # 部材リストは文字が小さいので高い解像度で出す
    fig.savefig(out_path, dpi=180 if kind == 'list' else 110)
    plt.close(fig)
    return out_path, n
