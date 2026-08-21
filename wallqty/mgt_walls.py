# -*- coding: utf-8 -*-
"""mgt から木造の耐力壁 (板要素) を読み、階ごと・方向ごとの存在壁量を出す.

モデル化の規約 (この機能が前提とするルール):
  ・耐力壁は板要素 (*ELEMENT の PLATE) でモデル化する
  ・その板要素に割り当てる **材料の名前に「壁倍率」を含める**
    (この文字列でふるい分けるので、床・屋根・RC壁などの板要素は混ざらない)
  ・板要素の厚みがそのまま壁倍率。モデルの単位系で読み替える
        単位 mm のモデル: 厚み 5   mm → 壁倍率 5.0
        単位 m  のモデル: 厚み 0.005 m → 壁倍率 5.0
    (どちらも「厚みを m に直して ×1000」で壁倍率になる)

壁か床・屋根かは **面の勾配** で判定する。板要素に最小二乗で平面を当て、
その法線 n から勾配 θ = arccos(|nz|) を出す (鉛直な壁 θ=90°、水平な床
θ=0°)。θ が slope_min (既定 60°) 以上なら壁として算入する。勾配のついた
壁も、壁長は **水平投影長さ** で数える (壁量の壁長は平面での長さ)。

存在壁量は 階・方向ごとの Σ(壁長L[m] × 壁倍率β)。
板要素は 階・厚みID・**同一平面** ごとにまとめたうえで、壁の通り方向
(面と水平面の交線の向き) へ投影し、**途切れずに続く区間 (run) ひとつを
1枚の耐力壁**とする。

  ・縦にも横にもメッシュ分割された同じ壁は 1つの run にまとまるので、
    二重に数えることはない
  ・開口などで離れている部分は別の run = 別の耐力壁になり、壁符号も別に
    振られる (平面図でも開口をまたがずに描かれる)
  ・勾配壁は高さごとに平面上の位置がずれるため、「同一直線」ではなく
    「同一平面」でまとめる必要がある

階の区切りは *STORY から作るが、画面から stories_def で上書きできる。
1つの階に複数の床レベルを持たせられるので、スキップフロアにも対応する
(壁は自分の下端以下で最も高いレベルの階に属する)。
"""

import math
import re

import numpy as np

from mgtkit.mgt import (_lines, _scan_section, _tlinenum, _field,
                        mgtopen_node, mgtopen_element, mgtopen_plate,
                        mgtopen_thickness, mgtopen_thickness_name,
                        mgtopen_FL, mgtopen_group)
from mgtkit.util import space_erace

#: 壁倍率1・長さ1m あたりの許容せん断耐力 [kN/m] (= 1.96kN/m ≒ 200kgf/m)
QA_UNIT = 1.96

#: 材料名のふるい分けキーワード (既定)
DEFAULT_KEYWORD = '壁倍率'

#: 壁として算入する勾配の下限 [度] (既定)。90°=鉛直、0°=水平
DEFAULT_SLOPE_MIN = 60.0

#: 耐力壁として算入できる最小の壁長 [m] (600mm 未満は算入しない)
DEFAULT_MIN_LEN = 0.6

#: 耐力壁として算入できる 階高/壁長 の上限 (これを超える細長い壁は算入しない)
DEFAULT_MAX_ASPECT = 5.0

#: 壁倍率の上限。モデルの厚みがこれを超えていたらこの値で頭打ちにする
DEFAULT_BETA_CAP = 7.0

#: 床から立ち上がっているとみなす、下端の浮きの上限 [m]。
#: これより浮いている壁は垂れ壁 (雑壁) とみなして算入しない
DEFAULT_MAX_GAP = 0.30

XY_TOL = 0.005                   # 同一平面とみなす面外ずれ (m)
PLANE_TOL = 0.010                # 板要素が平面に載っているとみなす許容 (m)
ANG_TOL = math.radians(2.0)      # 同一平面の平行判定
DIR_TOL = math.radians(5.0)      # X方向/Y方向の壁とみなす角度
VERT_TOL = 1.0                   # これ以内なら「鉛直」と表示 (度)
AXIS_TOL = 0.05                  # *GROUP を通り芯とみなす座標のばらつき (m)
COL_SLOPE = 0.05                 # 鉛直材とみなす傾き (水平距離/鉛直距離)
COL_MINH = 0.30                  # 鉛直材とみなす最小の高さ (m)
COL_OVERLAP = 0.20               # 柱をその階のものとみなす z の重なり (m)
SIDE_FRAC = 0.25                 # 4分割法の側端部分の割合 (1/4 固定)
SIDE_TOL = 0.01                  # 壁を側端部分に振り分けるときの座標の許容 (m)
STORY_TOL = 0.05                 # 壁下端を階レベルへ寄せる許容 (m)
CLUSTER_TOL = 0.30               # *STORY が無いとき壁下端をまとめる許容 (m)

_UNIT2M = {'M': 1.0, 'CM': 0.01, 'MM': 0.001, 'FT': 0.3048, 'IN': 0.0254}


# ---------------------------------------------------------------------------
# mgt の小さなパーサ (mgt.py に無いもの)
# ---------------------------------------------------------------------------

def model_length_unit(mgt_path):
    """*UNIT の長さ単位を返す -> (単位名, m換算係数). 無ければ ('M', 1.0).

    *UNIT は
        *UNIT    ; Unit System
        ; FORCE, LENGTH, HEAT, TEMPER
         KN, M, KJ, C
    の形。2番目のフィールドが長さ単位。
    """
    lines = _lines(mgt_path)
    for i, tline in enumerate(lines):
        if '*UNIT' not in tline:
            continue
        for tline2 in lines[i + 1:i + 6]:
            s = tline2.strip()
            if not s or s.startswith(';') or s.startswith('*'):
                continue
            toks = [space_erace(t).upper() for t in s.split(',')]
            if len(toks) >= 2 and toks[1] in _UNIT2M:
                return toks[1], _UNIT2M[toks[1]]
            for t in toks:                       # 並びが違うmgtの保険
                if t in _UNIT2M:
                    return t, _UNIT2M[t]
            break
        break
    return 'M', 1.0


def material_names(mgt_path):
    """*MATERIAL の {材料番号: 材料名} を返す.

    mgt.mgtopen_material_name は名前だけを並べて返し番号と対応しないため、
    ここで番号つきの辞書を作る (走査範囲は mgtopen_material と同じ)。
    """
    try:
        lines = _lines(mgt_path)
        start, end = _scan_section(lines, '*MATERIAL', 7)
    except (ValueError, IndexError):
        return {}
    out = {}
    for i in range(max(start, 1), min(end, len(lines)) + 1):
        tline = lines[i - 1]
        tn = _tlinenum(tline)
        if len(tn) < 4:
            continue
        try:
            mid = int(float(space_erace(_field(tline, tn, 1))))
        except ValueError:
            continue
        out[mid] = space_erace(_field(tline, tn, 3))
    return out


def vertical_members(mgt_path, pos):
    """線材のうち鉛直なもの (主に柱) を拾う -> [{'x','y','zmin','zmax'}, ...].

    平面図に建物の平面サイズを示すために使う。壁だけだと外形が分からない
    モデルがあるため、柱の位置を □ でプロットする。
    """
    try:
        ele = mgtopen_element(mgt_path)
    except Exception:                                  # noqa: BLE001
        return []
    if np.size(ele) == 0:
        return []
    ele = np.atleast_2d(ele)
    if ele.shape[1] < 5:
        return []
    seen = {}
    for r in range(ele.shape[0]):
        ni, nj = int(ele[r, 3]), int(ele[r, 4])
        if ni not in pos or nj not in pos:
            continue
        pi, pj = pos[ni], pos[nj]
        dz = abs(float(pi[2]) - float(pj[2]))
        if dz < COL_MINH:
            continue
        if math.hypot(float(pi[0]) - float(pj[0]),
                      float(pi[1]) - float(pj[1])) > COL_SLOPE * dz:
            continue
        x = (float(pi[0]) + float(pj[0])) / 2.0
        y = (float(pi[1]) + float(pj[1])) / 2.0
        z0, z1 = sorted((float(pi[2]), float(pj[2])))
        k = (round(x, 3), round(y, 3), round(z0, 3), round(z1, 3))
        if k in seen:
            continue
        seen[k] = {'x': x, 'y': y, 'zmin': z0, 'zmax': z1}
    return list(seen.values())


def axis_grid(mgt_path, len2m):
    """*GROUP から通り芯を拾う -> {'x': [{'name','v'}...], 'y': [...]}.

    mgtkit の他タブと同じ規約で、グループ名がそのまま通り芯名。
    'x' は X が一定の通り (平面図では縦線)、'y' は Y が一定の通り (横線)。
    節点が2点未満のグループ、XY とも動く/とも動かないグループは通り芯と
    みなさない。通り芯グループが無ければ空を返す (呼び出し側で連番を振る)。
    """
    try:
        names, _elenum, nodnum = mgtopen_group(mgt_path)
        node = mgtopen_node(mgt_path)
    except Exception:                                  # noqa: BLE001
        return {'x': [], 'y': []}
    pos = {int(r[0]): (float(r[1]) * len2m, float(r[2]) * len2m)
           for r in np.atleast_2d(node)}
    gx, gy = [], []
    for nm, nn in zip(names, nodnum):
        pts = []
        for v in np.atleast_1d(nn).ravel():
            # 節点リストが変則なグループ (NaN が混じる等) でも落とさない
            if not math.isfinite(float(v)):
                continue
            p = pos.get(int(v))
            if p is not None:
                pts.append(p)
        if len(pts) < 2:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        sx, sy = max(xs) - min(xs), max(ys) - min(ys)
        if sx <= AXIS_TOL < sy:
            gx.append({'name': nm, 'v': sum(xs) / len(xs)})
        elif sy <= AXIS_TOL < sx:
            gy.append({'name': nm, 'v': sum(ys) / len(ys)})
    gx.sort(key=lambda a: a['v'])
    gy.sort(key=lambda a: a['v'])
    return {'x': gx, 'y': gy}


# ---------------------------------------------------------------------------
# 幾何
# ---------------------------------------------------------------------------

def _element_geo(pts):
    """板要素に平面を当て、勾配と平面での通り方向を返す.

    戻り値 dict または None (平面に載っていない/面積が無い要素):
      slope  面の勾配 [度]  (90=鉛直, 0=水平)
      n      平面の単位法線 (3,)
      c3     3D重心 (3,)
      c      平面(XY)での重心 (2,)
      u      平面での通り方向の単位ベクトル (2,) = 面と水平面の交線の向き。
             完全に水平な面 (交線が定まらない) では None
    """
    P = np.asarray(pts, dtype=float)
    if len(P) < 3:
        return None
    c3 = P.mean(axis=0)
    q = P - c3
    if float(np.abs(q).max()) < 1e-6:
        return None
    w, v = np.linalg.eigh(q.T @ q)
    n = v[:, 0]                                   # 最小固有値の向き = 面外
    if math.sqrt(max(float(w[0]), 0.0) / len(P)) > PLANE_TOL:
        return None                               # ねじれていて平面でない
    nz = min(abs(float(n[2])), 1.0)
    slope = math.degrees(math.acos(nz))           # 90=鉛直, 0=水平
    d = np.array([-float(n[1]), float(n[0])])     # ẑ × n = 面内の水平方向
    nd = math.hypot(d[0], d[1])
    if nd < 1e-9:
        d = None                                  # 完全な水平面 (床・屋根)
    else:
        d /= nd
        if abs(d[0]) >= abs(d[1]):                # 向きの正規化
            d = d if d[0] > 0 else -d
        else:
            d = d if d[1] > 0 else -d
    return {'slope': slope, 'n': n, 'c3': c3, 'c': c3[:2], 'u': d}


def _same_plane(n1, c1, n2, c2):
    """2要素が同じ面に載っているか (法線が平行で、面のオフセットが一致)."""
    if float(np.linalg.norm(np.cross(n1, n2))) > math.sin(ANG_TOL):
        return False
    return abs(float(np.dot(np.asarray(c2) - np.asarray(c1), n1))) < 2 * XY_TOL


def _merge_intervals(intervals):
    """区間リスト [(a,b), ...] の和集合を [(a,b), ...] で返す.

    開口などで離れている区間は繋がない。壁長はこの和集合の長さの合計で、
    平面図もこの区間ごとに線を描く (端から端まで1本で描くと、開口を
    またいで実際より長く見えてしまう)。
    """
    if not intervals:
        return []
    iv = sorted(intervals)
    out = []
    a, b = iv[0]
    for s, e in iv[1:]:
        if s > b + XY_TOL:
            out.append((a, b))
            a, b = s, e
        else:
            b = max(b, e)
    out.append((a, b))
    return out


def _union_length(intervals):
    """区間リスト [(a,b), ...] の和集合の長さ."""
    return sum(b - a for a, b in _merge_intervals(intervals))


def assign_quarter(stories, walls):
    """4分割法の側端部分への振り分けを、壁ごとに持たせる.

    w['q4'] = {'X': [側端1, 側端2, 中央], 'Y': [...]}   単位は壁長 [m]
    X方向の壁は Y軸で、Y方向の壁は X軸で平面を4分割する。通常の X/Y 方向の
    壁は分割軸方向に広がりが無いので丸ごとどこかに入るが、斜め壁は境界を
    またぐことがあるので、分割軸方向の重なり長さの比で按分する。
    """
    bb_by = {st['no']: st.get('bbox') for st in stories}
    for w in walls:
        w['q4'] = {'X': [0.0, 0.0, 0.0], 'Y': [0.0, 0.0, 0.0]}
        bb = bb_by.get(w['story'])
        if not bb:
            continue
        for D in ('X', 'Y'):
            ln = w['lx'] if D == 'X' else w['ly']
            if ln <= 0:
                continue
            if D == 'X':                       # X方向の壁 → Y軸で4分割
                lo, hi = bb[2], bb[3]
                a0, a1 = sorted((w['y1'], w['y2']))
            else:                              # Y方向の壁 → X軸で4分割
                lo, hi = bb[0], bb[1]
                a0, a1 = sorted((w['x1'], w['x2']))
            q = (hi - lo) * SIDE_FRAC
            b1, b2 = lo + q, hi - q
            spread = a1 - a0
            if spread <= SIDE_TOL:             # 分割軸方向に広がりが無い壁
                p = (a0 + a1) / 2.0
                k = (0 if p <= b1 + SIDE_TOL
                     else (1 if p >= b2 - SIDE_TOL else 2))
                w['q4'][D][k] = ln
            else:                              # 斜め壁: 重なり長さで按分
                f1 = max(0.0, min(a1, b1) - max(a0, lo)) / spread
                f2 = max(0.0, min(a1, hi) - max(a0, b2)) / spread
                w['q4'][D] = [ln * f1, ln * f2,
                              ln * max(0.0, 1.0 - f1 - f2)]


def _merge_runs(items):
    """(始点, 終点, 要素) の並びを、隙間で区切った run にまとめる.

    戻り値: [(始点, 終点, [要素, ...]), ...]
    XY_TOL 以内で接している要素は同じ run になる (メッシュ分割)。
    開口などで離れているものは別の run = 別の耐力壁として扱う。
    """
    if not items:
        return []
    items = sorted(items, key=lambda t: t[0])
    runs = []
    a, b, mm = items[0][0], items[0][1], [items[0][2]]
    for s, e, m in items[1:]:
        if s > b + XY_TOL:
            runs.append((a, b, mm))
            a, b, mm = s, e, [m]
        else:
            b = max(b, e)
            mm.append(m)
    runs.append((a, b, mm))
    return runs


# ---------------------------------------------------------------------------
# 階の定義
# ---------------------------------------------------------------------------

def _levels_from_model(mgt_path, len2m, zmins, notes):
    """モデルから階の定義 [{'name', 'levels': [z]}, ...] を作る (下から).

    *STORY があればそのレベルを 1階 = 1レベル として使う。無ければ壁の
    下端をまとめて推定する。どちらも「1階に1レベル」なので、スキップフロアは
    画面の階定義 (stories_def) で複数レベルにまとめてもらう。
    """
    fl = []
    try:
        for row in mgtopen_FL(mgt_path):
            name = re.sub(r'^NAME=', '', str(row[0])).strip()
            z = float(row[1]) * len2m
            if name and math.isfinite(z):
                fl.append((round(z, 4), name))
    except Exception:                                  # noqa: BLE001
        fl = []                                        # *STORY が変則でも続行

    if fl:
        d = {}
        for z, n in fl:
            d.setdefault(z, n)
        lv = [{'name': n, 'levels': [z]} for z, n in sorted(d.items())]
        zmin_all = min(zmins)
        if zmin_all < lv[0]['levels'][0] - STORY_TOL:
            lv.insert(0, {'name': '最下レベル', 'levels': [round(zmin_all, 4)]})
            notes.append('*STORY の最下レベル (%.3fm) より下に壁があるため、'
                         'Z=%.3fm の階を1つ追加しました。'
                         % (lv[1]['levels'][0], zmin_all))
        return lv

    zs = sorted(zmins)
    lv = []
    for z in zs:
        if not lv or z - lv[-1] > CLUSTER_TOL:
            lv.append(z)
    notes.append('mgt に *STORY (階情報) が無いため、壁の下端レベルから階を'
                 '推定しました (%d階建て扱い)。スキップフロアなどで区切りが'
                 '違う場合は「階の定義」で直してください。' % len(lv))
    return [{'name': '%d階' % (i + 1), 'levels': [round(z, 4)]}
            for i, z in enumerate(lv)]


def _levels_from_input(stories_def, notes):
    """画面で入力された階定義を正規化する (下から順に並べ替えて返す)."""
    out = []
    for s in stories_def:
        lv = []
        for v in (s.get('levels') or []):
            if v is None or str(v).strip() == '':
                continue
            try:
                lv.append(round(float(v), 4))
            except (TypeError, ValueError):
                raise ValueError('階「%s」の床レベルに数値でない値 "%s" が'
                                 'あります。' % (s.get('name') or '?', v))
        lv = sorted(set(lv))
        if not lv:
            continue
        name = str(s.get('name') or '').strip() or ('Z=%.3f' % lv[0])
        out.append({'name': name, 'levels': lv})
    if not out:
        raise ValueError('階の定義が空です。階名と床レベルを1行以上'
                         '入力してください。')
    out.sort(key=lambda s: s['levels'][0])

    seen = {}
    for s in out:
        keep = []
        for z in s['levels']:
            if z in seen:
                notes.append('床レベル %.3fm が「%s」と「%s」で重複して'
                             'います。「%s」の方だけを使います。'
                             % (z, seen[z], s['name'], seen[z]))
                continue
            seen[z] = s['name']
            keep.append(z)
        s['levels'] = keep
    out = [s for s in out if s['levels']]
    if not out:
        raise ValueError('階の定義に有効な床レベルがありません。')
    return out


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------

def read_walls(mgt_path, keyword=DEFAULT_KEYWORD, diag_split=False,
               slope_min=DEFAULT_SLOPE_MIN,
               min_len=DEFAULT_MIN_LEN, max_aspect=DEFAULT_MAX_ASPECT,
               beta_cap=DEFAULT_BETA_CAP, max_gap=DEFAULT_MAX_GAP,
               stories_def=None, notes=None):
    """mgt から耐力壁を読み、階・方向ごとの存在壁量を返す.

    引数:
      keyword     材料名のふるい分け文字列 (既定 '壁倍率')
      diag_split  平面的に斜めな壁を成分分解して X/Y に算入するか
      slope_min   壁として算入する勾配の下限 [度] (既定 60)。これ未満の
                  面は床・屋根とみなして対象外にする
      min_len     耐力壁として算入する最小の壁長 [m] (既定 0.6 = 600mm)。
                  0 なら判定しない
      max_aspect  耐力壁として算入する 階高/壁長 の上限 (既定 5.0)。
                  これを超える細長い壁は算入しない。0 なら判定しない
      beta_cap    壁倍率の上限 (既定 7.0)。モデルの厚みがこれを超えていたら
                  この値で頭打ちにする。0 なら頭打ちにしない
      max_gap     床から立ち上がっているとみなす下端の浮きの上限 [m]
                  (既定 0.30)。これより浮いている壁は垂れ壁とみなして
                  算入しない。0 なら判定しない
      stories_def 階の定義 [{'name': '1F', 'levels': [0.0, 0.45]}, ...]。
                  None ならモデル (*STORY) から作る。

    戻り値 dict:
      stories [{'no','name','levels','z0','z1','h','h_disp','nwall',
                'cols' (柱の平面位置 [[x,y],...]), 'bbox' (壁+柱の外形)}]
      walls   壁ごとの諸元 (端点 x1,y1,x2,y2 と勾配 slope を含む)
      exist   {階no: {'X': ΣLβ, 'Y': ΣLβ}}
      model   {'x0','x1','y0','y1','z_base','z_top','h_bldg'}
    """
    notes = notes if notes is not None else []
    slope_min = float(slope_min)
    unit, len2m = model_length_unit(mgt_path)
    if unit != 'M':
        notes.append('モデルの長さ単位は %s です。壁倍率は「厚みを m に直して'
                     '×1000」で読みました (例: 厚み5mm → 壁倍率5.0)。' % unit)

    node = mgtopen_node(mgt_path)
    plate = mgtopen_plate(mgt_path)
    if np.size(plate) == 0:
        raise ValueError('mgt に板要素 (*ELEMENT の PLATE) がありません。'
                         '耐力壁は板要素でモデル化してください。')
    thick = mgtopen_thickness(mgt_path)
    tname = mgtopen_thickness_name(mgt_path)
    mnames = material_names(mgt_path)

    pos = {int(r[0]): (np.asarray(r[1:4], dtype=float) * len2m)
           for r in np.atleast_2d(node)}
    # 厚み(m) × 1000 = 壁倍率。mgtopen_thickness は既に「生の厚み×1000」なので
    # 単位換算係数を掛けるだけでよい (M: ×1.0 / MM: ×0.001)
    beta_map = {int(r[0]): float(r[1]) * len2m
                for r in np.atleast_2d(thick)} if np.size(thick) else {}

    # --- 材料名でふるい分け -------------------------------------------------
    wall_mats = {no: nm for no, nm in mnames.items() if keyword in nm}
    if not wall_mats:
        raise ValueError('材料名に「%s」を含む材料が mgt にありません。'
                         '耐力壁の板要素に「%s5.0」のような名前の材料を'
                         '割り当ててください (現在の材料名: %s)'
                         % (keyword, keyword,
                            ', '.join(sorted(mnames.values())[:12]) or 'なし'))

    eles = []
    flat = []          # 勾配が足りず床・屋根とみなしたもの
    bad = []           # 平面に載っていないもの
    for r in np.atleast_2d(plate):
        ele, mat, tid = int(r[0]), int(r[1]), int(r[2])
        if mat not in wall_mats:
            continue
        nds = [int(x) for x in r[3:7] if int(x) > 0]
        try:
            pts = [pos[n] for n in nds]
        except KeyError:
            notes.append('要素 %d の節点が *NODE にありません (無視しました)。'
                         % ele)
            continue
        geo = _element_geo(pts)
        if geo is None:
            bad.append(ele)
            continue
        if geo['slope'] < slope_min or geo['u'] is None:
            flat.append((ele, geo['slope']))
            continue
        beta = beta_map.get(tid)
        if beta is None:
            notes.append('要素 %d の厚みID %d が *THICKNESS にありません '
                         '(無視しました)。' % (ele, tid))
            continue
        if beta <= 0:
            notes.append('要素 %d の壁倍率が %g のため無視しました。'
                         % (ele, beta))
            continue
        beta_raw = beta                       # モデルの厚みから読んだ生の値
        if beta_cap > 0 and beta > beta_cap:
            beta = beta_cap
        zs = [float(p[2]) for p in pts]
        eles.append({'ele': ele, 'tid': tid, 'beta': beta,
                     'beta_raw': beta_raw, 'mat': mat,
                     'u': geo['u'], 'c': geo['c'], 'n': geo['n'],
                     'c3': geo['c3'], 'slope': geo['slope'],
                     'pts': pts, 'zmin': min(zs), 'zmax': max(zs)})

    if flat:
        notes.append('材料名に「%s」を含むが勾配が %.0f° 未満の板要素が '
                     '%d 件ありました (床・屋根とみなして対象外)。'
                     '要素番号(勾配): %s'
                     % (keyword, slope_min, len(flat),
                        ' '.join('%d(%.0f°)' % (e, s) for e, s in flat[:10])))
    if bad:
        notes.append('平面に載っていない (ねじれた) 板要素が %d 件あり、'
                     '対象外にしました。要素番号: %s'
                     % (len(bad), ' '.join(str(e) for e in bad[:10])))
    if not eles:
        raise ValueError('材料名に「%s」を含み、勾配が %.0f° 以上の板要素が'
                         'ありません。材料の割り当てと「壁とみなす勾配の'
                         '下限」を確認してください。' % (keyword, slope_min))

    capped = sorted(set(x['beta_raw'] for x in eles
                        if x['beta_raw'] > x['beta']))
    if capped:
        notes.append('モデルの厚みから読んだ壁倍率が %s の壁があります。'
                     '上限 %g で頭打ちにして集計しました。'
                     % (', '.join('%g' % b for b in capped), beta_cap))
    big = sorted(set(x['beta_raw'] for x in eles if x['beta_raw'] > 10))
    if big:
        notes.append('モデルの厚みから読んだ壁倍率が %s の板要素があります。'
                     '「厚み=壁倍率」の規約から外れていないか確認して'
                     'ください。' % ', '.join('%g' % b for b in big))

    # --- 階の定義と割当 -----------------------------------------------------
    z_top_wall = max(x['zmax'] for x in eles)
    if stories_def:
        levels = _levels_from_input(stories_def, notes)
        story_src = 'input'
    else:
        levels = _levels_from_model(mgt_path, len2m,
                                    [x['zmin'] for x in eles], notes)
        story_src = 'model'

    lvflat = sorted((z, i) for i, s in enumerate(levels) for z in s['levels'])
    low = []
    for x in eles:
        base, si, found = lvflat[0][0], lvflat[0][1], False
        for z, i in lvflat:
            if x['zmin'] >= z - STORY_TOL:
                base, si, found = z, i, True
        x['base'], x['si'] = base, si
        if not found:
            low.append(x['ele'])
    if low:
        notes.append('最下レベル (%.3fm) より下から始まる壁が %d 件あります。'
                     '「%s」に含めました。要素番号: %s'
                     % (lvflat[0][0], len(low), levels[lvflat[0][1]]['name'],
                        ' '.join(str(e) for e in low[:10])))

    # スキップフロアの段差部分の補正。
    # 複数レベルの階では、下のレベルから立ち上がっていても上端がその階の
    # 最上レベル以下にとどまる壁は、その階の床より下にある「段差の立ち上がり」
    # なので、下の階の壁として扱う (そうしないと下階の壁の頭が切り取られて
    # 上階の短い壁として二重に数えられる)。
    step = []
    for x in eles:
        if x['si'] <= 0:
            continue
        lv = levels[x['si']]['levels']
        if len(lv) < 2 or x['zmax'] > max(lv) + STORY_TOL:
            continue
        x['si'] -= 1
        x['base'] = max(levels[x['si']]['levels'])
        step.append(x['ele'])
    if step:
        notes.append('スキップフロアの段差部分の壁 (その階の最上の床レベルより'
                     '上へ立ち上がらない壁) が %d 件ありました。下の階の壁'
                     'として集計しています (下階の壁の頭が切り取られて上階の'
                     '短い壁として二重に数えられるのを防ぐため)。要素番号: %s'
                     % (len(step), ' '.join(str(e) for e in step[:10])))

    tops = [(levels[i + 1]['levels'][0] if i + 1 < len(levels)
             else max(z_top_wall, levels[i]['levels'][-1]))
            for i in range(len(levels))]
    # 壁の無い最上レベル (屋根レベルなど) は階として扱わない
    while len(levels) > 1 and not any(x['si'] == len(levels) - 1
                                      for x in eles):
        levels.pop()
        tops.pop()
    tops[-1] = max(tops[-1], z_top_wall)
    n_story = len(levels)
    for x in eles:
        x['si'] = min(x['si'], n_story - 1)

    # z1 は階の物理的な上端 (柱をどの階に入れるかの判定に使う)。
    # 階高は壁ごとに H を採るので、下の壁の集計後に h/h_disp を入れ直す。
    stories = []
    for i, s in enumerate(levels):
        stories.append({
            'no': i + 1, 'name': s['name'], 'levels': s['levels'],
            'z0': s['levels'][0], 'z1': tops[i],
            'h': tops[i] - s['levels'][0], 'nwall': 0, 'h_disp': '-'})
    z_base = stories[0]['z0']
    z_top = tops[-1]

    # --- 壁のグループ化 (階・厚みID・同一平面) ------------------------------
    buckets = {}
    for i, x in enumerate(eles):
        buckets.setdefault((x['si'], x['tid']), []).append(i)

    groups = []
    for idxs in buckets.values():
        parent = {i: i for i in idxs}

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                i, j = idxs[a], idxs[b]
                if not _same_plane(eles[i]['n'], eles[i]['c3'],
                                   eles[j]['n'], eles[j]['c3']):
                    continue
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri
        g = {}
        for i in idxs:
            g.setdefault(find(i), []).append(i)
        groups.extend(g.values())

    walls = []
    for idxs in groups:
        mem = [eles[i] for i in idxs]
        u = mem[0]['u']                     # 同一平面なので通り方向は共通
        xy = np.asarray([p[:2] for x in mem for p in x['pts']])
        c = xy.mean(axis=0)
        cu = float(c @ u)
        # 各要素の水平投影区間。隙間 (開口) で切って run にまとめ、
        # run 1つを 1枚の耐力壁として扱う。メッシュ分割された同じ壁は
        # 1つの run にまとまるので二重には数えない。
        items = []
        for x in mem:
            sp = np.asarray([p[:2] for p in x['pts']]) @ u
            items.append((float(sp.min()), float(sp.max()), x))
        beta = mem[0]['beta']
        beta_raw = mem[0]['beta_raw']
        slope = float(np.mean([x['slope'] for x in mem]))
        sloped = slope < 90.0 - VERT_TOL
        si = mem[0]['si']
        st = stories[si]
        ang = math.atan2(abs(float(u[1])), abs(float(u[0])))

        for a, b, run in _merge_runs(items):
            L = b - a
            pts = [p for x in run for p in x['pts']]
            zs = [float(p[2]) for p in pts]
            H = float(max(zs) - min(zs))
            base = min(x['base'] for x in run)
            # 下端が床レベルからどれだけ浮いているか。垂れ壁の判別に使う
            gap = min(zs) - base
            # 階高は壁ごとに、その壁自身の高さ H を採る。
            # 階の上端をモデルから決めようとすると、屋根なりに伸びた妻壁の
            # 頂点が同じ階の他の壁の階高まで引き上げてしまうため。
            h_story = H
            p1 = c + u * (a - cu)
            p2 = c + u * (b - cu)
            xr = (min(float(p1[0]), float(p2[0])),
                  max(float(p1[0]), float(p2[0])))
            yr = (min(float(p1[1]), float(p2[1])),
                  max(float(p1[1]), float(p2[1])))
            # ll = 各方向へ算入する壁長。斜め壁は方向余弦で成分に分ける
            if ang <= DIR_TOL:                  # X方向に通る壁 = X方向に効く
                wdir, loc, rng = 'X', 'Y=%.3f' % c[1], 'X %.3f〜%.3f' % xr
                ll = {'X': L, 'Y': 0.0}
                skey = (si, 0, round(float(c[1]), 3), xr[0])
            elif ang >= math.pi / 2 - DIR_TOL:   # Y方向に通る壁
                wdir, loc, rng = 'Y', 'X=%.3f' % c[0], 'Y %.3f〜%.3f' % yr
                ll = {'X': 0.0, 'Y': L}
                skey = (si, 1, round(float(c[0]), 3), yr[0])
            else:
                deg = math.degrees(math.atan2(float(u[1]),
                                              float(u[0]))) % 180.0
                wdir = '斜め%.0f°' % deg
                loc = '-'
                rng = 'X %.3f〜%.3f / Y %.3f〜%.3f' % (xr + yr)
                ll = ({'X': L * abs(float(u[0])),
                       'Y': L * abs(float(u[1]))}
                      if diag_split else {'X': 0.0, 'Y': 0.0})
                skey = (si, 2, xr[0], yr[0])
            lb = {'X': ll['X'] * beta, 'Y': ll['Y'] * beta}

            # 階高/壁長。細長すぎる壁は耐力壁として算入できない
            aspect = (h_story / L) if L > 1e-9 else float('inf')
            used = True
            why = ''
            if max_gap > 0 and gap > max_gap + 1e-9:
                used, why = False, ('垂れ壁・下端が床から %.3f m 浮いている'
                                    % gap)
            elif min_len > 0 and L < min_len - 1e-9:
                used, why = False, ('壁長 %.3f m < %.3f m'
                                    % (L, min_len))
            elif max_aspect > 0 and aspect > max_aspect + 1e-9:
                used, why = False, ('階高/壁長 = %.2f > %.1f'
                                    % (aspect, max_aspect))
            elif wdir.startswith('斜め') and not diag_split:
                used, why = False, '斜め壁で成分分解しない設定'
            if not used:
                ll = {'X': 0.0, 'Y': 0.0}
                lb = {'X': 0.0, 'Y': 0.0}

            walls.append({'story': si + 1, 'story_name': stories[si]['name'],
                          'dir': wdir, 'loc': loc, 'range': rng,
                          'zrange': '%.3f〜%.3f' % (min(zs), max(zs)),
                          'base': base, 'h_story': h_story, 'gap': gap,
                          'slope': slope, 'sloped': sloped,
                          'x1': float(p1[0]), 'y1': float(p1[1]),
                          'x2': float(p2[0]), 'y2': float(p2[1]),
                          'cx': float(p1[0] + p2[0]) / 2.0,
                          'cy': float(p1[1] + p2[1]) / 2.0,
                          'aspect': aspect,
                          'L': L, 'beta': beta, 'beta_raw': beta_raw,
                          'beta_capped': beta_raw > beta,
                          'LB': lb['X'] + lb['Y'],
                          'lx': ll['X'], 'ly': ll['Y'],
                          'lbx': lb['X'], 'lby': lb['Y'],
                          'H': H,
                          'used': used, 'why': why,
                          'thick_name': tname.get(mem[0]['tid'], ''),
                          'mat_name': wall_mats.get(mem[0]['mat'], ''),
                          'eles': sorted(x['ele'] for x in run),
                          '_skey': skey})

    walls.sort(key=lambda wl: wl['_skey'])
    cnt = {}
    for wl in walls:
        # 壁符号は階ごとの通し番号 (平面図では□囲みの数字で示す)
        cnt[wl['story']] = cnt.get(wl['story'], 0) + 1
        wl['no'] = cnt[wl['story']]
        wl['name'] = str(cnt[wl['story']])
        del wl['_skey']

    exist = {st['no']: {'X': 0.0, 'Y': 0.0} for st in stories}
    for wl in walls:
        exist[wl['story']]['X'] += wl['lbx']
        exist[wl['story']]['Y'] += wl['lby']
    # 柱 (鉛直な線材) を階ごとに割り当てる。平面図に外形を示すのと、
    # 4分割法で割る「平面の範囲」を壁だけでなく柱まで含めて決めるため。
    cols_all = vertical_members(mgt_path, pos)
    for st in stories:
        mine = [wl for wl in walls if wl['story'] == st['no']]
        st['nwall'] = len(mine)
        cs = [c for c in cols_all
              if min(c['zmax'], st['z1']) - max(c['zmin'], st['z0'])
              > COL_OVERLAP]
        seen = set()
        st['cols'] = []
        for c in cs:
            k = (round(c['x'], 3), round(c['y'], 3))
            if k in seen:
                continue
            seen.add(k)
            st['cols'].append([c['x'], c['y']])
        xs2 = ([w['x1'] for w in mine] + [w['x2'] for w in mine]
               + [c[0] for c in st['cols']])
        ys2 = ([w['y1'] for w in mine] + [w['y2'] for w in mine]
               + [c[1] for c in st['cols']])
        st['bbox'] = ([min(xs2), max(xs2), min(ys2), max(ys2)]
                      if xs2 else None)

    if not cols_all:
        notes.append('鉛直な線材 (柱) が見つかりませんでした。平面図の外形と'
                     '4分割法で割る平面の範囲は、壁の範囲から決めています。')
    assign_quarter(stories, walls)      # 4分割法の側端部分への振り分け

    # 階高は壁ごとの H なので、階の表示は その階の壁の高さの範囲にする
    for st in stories:
        hs = [wl['H'] for wl in walls if wl['story'] == st['no']]
        if not hs:
            continue
        st['h'] = max(hs)
        st['h_disp'] = ('%.3f' % hs[0]
                        if len(set(round(v, 3) for v in hs)) == 1
                        else '%.3f〜%.3f' % (min(hs), max(hs)))
    notes.append('階高は壁ごとに その壁自身の高さ H を採っています '
                 '(階の上端をモデルから決めると、屋根なりに伸びた妻壁の頂点が'
                 '同じ階の他の壁の階高まで引き上げてしまうため)。'
                 'したがって「階高/壁長」は その壁の H÷壁長 です。')

    n_gap = sum(1 for wl in walls
                if not wl['used'] and wl['why'].startswith('垂れ壁'))
    if n_gap:
        notes.append('下端が床レベルから %.0fmm より浮いている壁 (垂れ壁) が'
                     ' %d 面あり、算入しませんでした。'
                     % (max_gap * 1000, n_gap))
    n_short = sum(1 for wl in walls
                  if not wl['used'] and wl['why'].startswith('壁長'))
    if n_short:
        notes.append('壁長が %.0fmm 未満のため算入しなかった壁が %d 面'
                     'あります。' % (min_len * 1000, n_short))
    n_thin = sum(1 for wl in walls
                 if not wl['used'] and wl['why'].startswith('階高/壁長'))
    if n_thin:
        notes.append('階高/壁長 が %.1f を超えるため算入しなかった壁が'
                     ' %d 面あります。' % (max_aspect, n_thin))
    n_slope = sum(1 for wl in walls if wl['sloped'])
    if n_slope:
        notes.append('勾配のついた壁 (鉛直でない壁) が %d 面あります。'
                     '勾配 %.0f° 以上なので壁量に算入し、壁長は水平投影長さで'
                     '数えました。一覧の「勾配」欄を確認してください。'
                     % (n_slope, slope_min))
    n_diag = sum(1 for wl in walls if wl['dir'].startswith('斜め'))
    if n_diag and diag_split:
        notes.append('平面的に斜めな壁が %d 面あります。方向余弦で X/Y 成分'
                     'に分けて算入しました (一覧の「X方向L」「Y方向L」欄)。'
                     % n_diag)
    elif n_diag:
        notes.append('平面的に斜めな壁が %d 面あります。存在壁量には算入して'
                     'いません (「斜め壁を成分分解して算入」で X/Y 成分に'
                     '分けられます)。' % n_diag)

    xs = ([float(p[0]) for x in eles for p in x['pts']]
          + [c[0] for st in stories for c in st['cols']])
    ys = ([float(p[1]) for x in eles for p in x['pts']]
          + [c[1] for st in stories for c in st['cols']])
    model = {'x0': min(xs), 'x1': max(xs), 'y0': min(ys), 'y1': max(ys),
             'z_base': z_base, 'z_top': z_top, 'h_bldg': z_top - z_base,
             'ncol': len(cols_all)}

    return {'unit': unit, 'len2m': len2m, 'stories': stories,
            'walls': walls, 'exist': exist, 'model': model,
            'axes': axis_grid(mgt_path, len2m),
            'keyword': keyword, 'qa_unit': QA_UNIT, 'slope_min': slope_min,
            'min_len': min_len, 'max_aspect': max_aspect,
            'beta_cap': beta_cap, 'max_gap': max_gap,
            'materials': sorted(wall_mats.values()),
            'story_src': story_src,
            'story_def': [{'name': s['name'], 'levels': s['levels']}
                          for s in stories]}
