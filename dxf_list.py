# -*- coding: utf-8 -*-
"""構造図DXF (新規版) の部材リスト図.

struct_cad (tools/struct_cad_py) の断面リスト (draw/section_list.py) と同じ表を、
mgtkit の読み込みデータ (mgtopen_section / mgtopen_RCcolumn / mgtopen_RCbeam) で作る。

表の作り (struct_cad と同じ):
  - 区分: 鉄骨部材リスト / 木造断面リスト / その他断面リスト / RC柱リスト / RC梁リスト
    (RC 柱・梁は配筋 *REBAR-COLUMN / *REBAR-BEAM がある断面。それ以外は材料で S / W / その他)
  - 断面を列、属性を行にした転置表を 6 断面ごとに折り返す
  - 断面図は実寸。RC 柱は帯筋 (かぶり内側の矩形) + 中子筋 + 主筋記号、RC 梁はあばら筋 + 上下端筋
  - 主筋記号は径別 (D10 ● / D13 × / D16 ∅ / D19 ● / D22 ○ ...)
B (dxf_struct.py) に合わせたところ・変えたところ:
  - 文字は紙面 mm × 縮尺。表の寸法は文字高さに比例 (struct_cad は 12pt@1:100 固定)
  - 列幅は文字の長さに合わせて広げる (struct_cad は固定幅で形状表記がはみ出す)
  - 断面行の高さ・列幅は、そのブロックで一番大きい断面に余白を足した大きさ
    (struct_cad は区分ごとの固定値)
  - 縮尺はブロック (6 断面) ごとに、用紙に収まる最小の縮尺 (1/10 から)。同じ縮尺が続くブロックを
    用紙に入るだけ 1 枚にまとめ、縮尺が変わるか用紙からあふれたら次の図にする。
    同じ図のブロックは列幅・断面行高をそろえる
  - 行高は文字高さの 2 倍、断面の周りの余白は紙面 5mm
  - 配筋記号の大きさは紙面で固定 (D13 φ1.6 / D16 φ1.8 / D19 φ2.0 / D22 以上 φ2.2、最小 1.5mm)。位置は実寸
  - 主筋の中心は mgt のかぶり (DO / DT / DB = 縁から主筋重心) の位置。帯筋・あばら筋はその外側
    (主筋最外径/2 + 帯筋径/2)。梁の横かぶりはデータに無いので 40mm + あばら筋径/2
  - 2 段筋の中心間 = 最外径 + max(25mm, 1.5d)
  - 円形の RC 柱は BxD 欄を φ、帯筋を円、主筋を円周に等分配置 (struct_cad は角柱として描く)
  - レイヤ: 罫線・文字・主筋 = S-Red10、断面外形 = 材質別の断面レイヤ (S-鉄骨断面_30 等)、
    帯筋・あばら筋 = S-Cyan20
  - ダミー断面の下限は画面の「ダミー材断面番号下限」(limit_sec_no)
mgtkit のパーサに無い値 (配筋のかぶり DO、材料名) だけ mgt から直接読む。
"""
import math
import re

import numpy as np

from .dxf_struct import (LAYER_SEC, LAYER_TEXT, PAPER_MM, PAPER_MARGIN_MM,
                         SCALE_SERIES, read_mgt_text)

LAYER_HOOP = ('S-Cyan20', 4)

# 区分ごとの表仕様: (タイトル, 属性行)
SPECS = {
    'S': ('鉄骨部材リスト', ['符号', '位置', '断面', '形状', '鋼種', '備考']),
    'W': ('木造断面リスト', ['符号', '位置', '断面', '形状', '材種', '等級', '備考']),
    'OTHER': ('その他断面リスト',
              ['符号', '位置', '断面', '形状', '材種', '等級', '備考']),
    'RC': ('RC柱リスト',
           ['符号', '位置', '断面', 'BxD', '主筋', 'HOOP', 'Fc', '備考']),
    'RCB': ('RC梁リスト',
            ['符号', '位置', '断面', 'bxD', '上端筋', '下端筋', 'STP', 'Fc', '備考']),
}
CATEGORY_ORDER = ('S', 'W', 'OTHER', 'RC', 'RCB')

# struct_cad の表寸法 [m] は「文字 0.18m」基準の比率 (_S 倍) で決まっている。
# ここでは文字高さ th [mm] から k = th/180 を作り、同じ比率で表を組む。
#   区分: (断面行高[mm 実寸], その他行高[×k], 断面列幅[×k])
DIMS = {
    'S': (1500.0, 250.0, 1500.0),
    'W': (1500.0, 250.0, 1500.0),
    'RC': (2500.0, 250.0, 2000.0),
    'RCB': (2500.0, 250.0, 2000.0),
}
DIMS_DEFAULT = (2000.0, 500.0, 1500.0)
LABEL_W = 1000.0          # 見出し列幅 [×k]
BLOCK_GAP = 1000.0        # ブロック間 [×k]
COLS_PER_BLOCK = 6
ROW_H_RATIO = 2.0         # その他行の高さ = 文字高さ × これ
SECTION_MARGIN_PAPER = 5.0  # 断面図の周りの余白 [紙面 mm]
LIST_SCALE_SERIES = [10, 15] + [s for s in SCALE_SERIES if s >= 20]
TITLE_RESERVE_PAPER = 13.0  # 表題のために用紙上端に取っておく高さ [紙面 mm]
BEAM_SIDE_COVER = 40.0    # 梁の横かぶり (あばら筋外面まで) [mm]。データに無いので固定
# 配筋記号の直径 [紙面 mm]
REBAR_SYMBOL_PAPER = {10: 1.5, 13: 1.6, 16: 1.8, 19: 2.0}
REBAR_SYMBOL_PAPER_LARGE = 2.2
REBAR_SYMBOL_PAPER_MIN = 1.5

# 異形鉄筋の最外径 (JIS): 呼び径 -> 最外径 [mm]
_OUTF = {10: 11, 13: 14, 16: 18, 19: 21, 22: 25, 25: 28,
         29: 33, 32: 36, 35: 40, 38: 43, 41: 47, 51: 57}


# ---------------------------------------------------------------------------
# データの組み立て (mgtkit の読み込み結果から)
# ---------------------------------------------------------------------------

class ListSection(object):
    """リスト 1 列分の断面. 寸法は m."""

    def __init__(self, no, name, shape, dims, steel_dims=None, dims_j=None):
        self.no = int(no)
        self.name = name
        self.shape = shape            # H / SB / SR / P / C / BOX / T / L / SRC
        self.dims = list(dims)        # H: 成,幅,tw,tf / BOX: 成,幅,t / P: 径,t / SR: 径 /
        self.steel_dims = steel_dims  # L: 成,幅,t / SB,C,T: 成,幅 / SRC: 成,幅 (+鋼材 H)
        self.dims_j = dims_j          # テーパーの j 端

    def _pick(self, dims, which):
        if not dims:
            return 0.0
        if self.shape in ('P', 'SR'):
            return dims[0]
        return dims[which] if which < len(dims) else 0.0

    @property
    def depth(self):
        d = self._pick(self.dims, 0)
        return max(d, self._pick(self.dims_j, 0)) if self.dims_j else d

    @property
    def width(self):
        w = self._pick(self.dims, 1)
        return max(w, self._pick(self.dims_j, 1)) if self.dims_j else w

    @property
    def symbol(self):
        return (self.name or '').strip().split('_', 1)[0]


def _rows(arr):
    if arr is None or np.size(arr) == 0:
        return []
    return [list(map(float, r)) for r in np.atleast_2d(np.asarray(arr, dtype=float))]


def _positive(vals):
    return [v for v in vals if v > 0]


def list_sections(M):
    """mgtopen_section の形状別テーブル → {断面番号: ListSection} (リストに載せられるもの)."""
    out = {}
    names = M.sec_names
    tables = M.sections
    for k, arr in enumerate(tables):
        for r in _rows(arr):
            no = int(r[0])
            v = r[1:]
            name = names.get(no, str(no))
            sec = None
            if k == 1 and len(v) >= 4:            # H [H, B, tw, tf1, B2, tf2, r1]
                sec = ListSection(no, name, 'H', v[:4])
            elif k == 2 and len(v) >= 2:          # SB [H, B]
                sec = ListSection(no, name, 'SB', v[:2])
            elif k == 3 and len(v) >= 1:          # SR [D]
                sec = ListSection(no, name, 'SR', v[:1])
            elif k == 4 and len(v) >= 2:          # P [D, t]
                sec = ListSection(no, name, 'P', v[:2])
            elif k == 5 and len(v) >= 2:          # C [H, B, tw, tf]
                sec = ListSection(no, name, 'C', v[:2])
            elif k == 7 and len(v) >= 3:          # BOX [H, B, tw, tf, 0]
                sec = ListSection(no, name, 'BOX', v[:3])
            elif k == 8 and len(v) >= 2:          # T [H, B, tw, tf]
                sec = ListSection(no, name, 'T', v[:2])
            elif k == 9 and len(v) >= 3:          # L [H, B, tw, tf]
                sec = ListSection(no, name, 'L', v[:3])
            elif k == 10 and len(v) >= 2:         # SRC RHB [conc D1, D2, steel H, B, tw, tf, ...]
                steel = _positive(v[2:])[:4]
                sec = ListSection(no, name, 'SRC', v[:2],
                                  steel_dims=steel if len(steel) >= 4 else None)
            elif k == 11 and len(v) >= 12:        # テーパーH [i: v1..v7, j: v9..v15]
                sec = ListSection(no, name, 'H', v[0:4], dims_j=v[7:11])
            elif k == 12 and len(v) >= 2:         # テーパーSB [i..., j...]
                pv = _positive(v)
                half = len(pv) // 2 or len(pv)
                sec = ListSection(no, name, 'SB', pv[:half][:2],
                                  dims_j=pv[half:][:2] or None)
            elif k in (13, 14, 15, 16, 17):       # SRC CPO/CHB/RH2T, CFT: コンクリート外形のみ
                pv = _positive(v)
                if len(pv) >= 2:
                    sec = ListSection(no, name, 'SRC', pv[:2])
            if sec is not None and sec.depth > 0:
                out.setdefault(no, sec)
    return out


def _material_table(mgt_path):
    """*MATERIAL を寛容に読む → {材料番号: (TYPE, 名前)}."""
    out = {}
    ins = False
    for ln in read_mgt_text(mgt_path).split('\n'):
        st = ln.strip()
        if st.startswith('*'):
            ins = st.startswith('*MATERIAL')
            continue
        if not ins or not st or st.startswith(';'):
            continue
        f = [x.strip() for x in ln.split(',')]
        if len(f) < 3 or not f[2]:
            continue
        try:
            out[int(float(f[0]))] = (f[1].upper(), f[2])
        except ValueError:
            continue
    return out


def _rebar_covers(mgt_path):
    """配筋のかぶり (mgtkit のパーサに無い値) → ({柱断面: DO}, {梁断面: (上, 下)}) [m]."""
    col, beam = {}, {}
    block = ''
    for ln in read_mgt_text(mgt_path).split('\n'):
        st = ln.strip()
        if st.startswith('*'):
            block = st.split()[0].upper()
            continue
        if not st or st.startswith(';'):
            continue
        f = [x.strip() for x in ln.split(',')]
        try:
            if block == '*REBAR-COLUMN' and len(f) >= 9:
                col[int(float(f[0]))] = float(f[8])
            elif (block == '*REBAR-BEAM' and len(f) >= 5 and f[2]
                  and f[2][0].isalpha()):
                beam[int(float(f[0]))] = (float(f[3]), float(f[4]))
        except ValueError:
            continue
    return col, beam


def _dia(v):
    return int(round(float(v)))


def rebar_tables(M):
    """RC 柱・梁の配筋 → ({断面: dict}, {断面: dict}). 値は mgtopen_RCcolumn / mgtopen_RCbeam から."""
    from .mgt import mgtopen_RCbeam, mgtopen_RCcolumn
    covers_c, covers_b = _rebar_covers(M.mgt_path)
    cols, beams = {}, {}
    try:
        for r in _rows(mgtopen_RCcolumn(M.mgt_path)):
            no = int(r[0])
            cols[no] = {
                'total': int(r[2]), 'main_dia': _dia(r[3]), 'n_row': int(r[4]),
                'n_width': int(r[5]), 'hoop_dia': _dia(r[6]),
                'hoop_pitch': r[7] / 1000.0,
                'hoop_x': int(r[8]) if len(r) > 8 else 2,
                'hoop_y': int(r[9]) if len(r) > 9 else 2,
                'cover': covers_c.get(no, 0.05),
            }
    except Exception as e:  # noqa: BLE001
        print('注意: RC柱の配筋を読めませんでした (%s)' % e)
    try:
        for item in mgtopen_RCbeam(M.mgt_path):
            no = int(item[0])
            m0 = np.atleast_2d(np.asarray(item[1], dtype=float))[0]   # i端を代表
            ct, cb = covers_b.get(no, (0.05, 0.05))
            beams[no] = {
                'top_rb1': int(m0[1]), 'top_rb2': int(m0[2]), 'top_dia': _dia(m0[4]),
                'bot_rb1': int(m0[8]), 'bot_rb2': int(m0[9]), 'bot_dia': _dia(m0[11]),
                'stir_dia': _dia(m0[14]), 'stir_pitch': m0[15] / 1000.0,
                'stir_legs': int(m0[16]), 'cover_top': ct, 'cover_bot': cb,
            }
    except Exception as e:  # noqa: BLE001
        print('注意: RC梁の配筋を読めませんでした (%s)' % e)
    return cols, beams


def _section_materials(M):
    """断面番号 → 材料番号 (その断面を使う最初の要素の材料)."""
    out = {}
    for r in np.atleast_2d(M.element):
        out.setdefault(int(r[2]), int(r[1]))
    return out


def _category(mtype, name):
    if mtype == 'STEEL':
        return 'S'
    if mtype == 'USER' and name.startswith('W-'):
        return 'W'
    return 'OTHER'


def _mclass(mtype, name):
    """断面外形のレイヤ用の材質分類 (dxf_struct の LAYER_SEC のキー)."""
    if mtype == 'STEEL':
        return 'STEEL'
    if mtype in ('CONC', 'SRC'):
        return 'RC'
    if mtype == 'USER' and ('W-' in name.upper() or 'W_' in name.upper()
                            or 'CLT' in name.upper()):
        return 'WOOD'
    return 'OTHER'


def list_model(M):
    """リストに載せる断面を区分ごとに集める → {区分: [(ListSection, 付帯情報)]}."""
    if hasattr(M, '_list_model'):
        return M._list_model
    secs = list_sections(M)
    mats = _material_table(M.mgt_path)
    sec_mat = _section_materials(M)
    cols, beams = rebar_tables(M)
    cats = {c: [] for c in CATEGORY_ORDER}
    for no in sorted(secs):
        if no >= M.limit_sec_no:
            continue   # ダミー断面
        sec = secs[no]
        mtype, mname = mats.get(sec_mat.get(no, -1), ('', ''))
        info = {'mtype': mtype, 'mname': mname, 'col': cols.get(no),
                'beam': beams.get(no), 'mclass': _mclass(mtype, mname)}
        if no in beams:
            cats['RCB'].append((sec, info))
        elif no in cols:
            cats['RC'].append((sec, info))
        else:
            cats[_category(mtype, mname)].append((sec, info))
    M._list_model = cats
    return cats


def list_categories(M):
    """断面がある区分 → list of dict {key, label, n}."""
    return [{'key': c, 'label': SPECS[c][0], 'n': len(v)}
            for c, v in list_model(M).items() if v]


# ---------------------------------------------------------------------------
# 表の文字
# ---------------------------------------------------------------------------

def _g(v):
    return ('%g' % round(v * 1000.0, 3))


def shape_text(sec):
    d = sec.dims
    get = (lambda i: _g(d[i]) if i < len(d) else '0')
    if sec.shape == 'H':
        return 'H-%sx%sx%sx%s' % (get(0), get(1), get(2), get(3))
    if sec.shape == 'BOX':
        return 'B-%sx%sx%s' % (get(0), get(1), get(2))
    if sec.shape == 'P':
        return 'P-%sx%s' % (get(0), get(1))
    if sec.shape == 'SR':
        return '○-%s' % get(0)
    mark = '□'
    return '%s-%sx%s' % (mark, _g(sec.width), _g(sec.depth))


def _bar_text(n1, n2, dia):
    return ('%d/%d-D%d' % (n1, n2, dia)) if n2 > 0 else ('%d-D%d' % (n1, dia))


def attr_value(sec, info, attr):
    mname = info['mname']
    if attr == '符号':
        return sec.symbol
    if attr == '位置':
        return '全断面'
    if attr == '形状':
        return shape_text(sec)
    if attr == '備考':
        return '' if info['beam'] else '–'
    if attr in ('BxD', 'bxD'):
        if sec.shape in ('SR', 'P'):
            return 'φ%.0f' % (sec.depth * 1000)
        return '%.0fx%.0f' % (sec.width * 1000, sec.depth * 1000)
    rc, rb = info['col'], info['beam']
    if attr == '主筋':
        return '%d-D%d' % (rc['total'], rc['main_dia']) if rc else ''
    if attr == 'HOOP':
        return ('%d(x)/%d(y)-D%d@%.0f' % (rc['hoop_x'], rc['hoop_y'], rc['hoop_dia'],
                                          rc['hoop_pitch'] * 1000) if rc else '')
    if attr == '上端筋':
        return _bar_text(rb['top_rb1'], rb['top_rb2'], rb['top_dia']) if rb else ''
    if attr == '下端筋':
        return _bar_text(rb['bot_rb1'], rb['bot_rb2'], rb['bot_dia']) if rb else ''
    if attr == 'STP':
        return ('%d-D%d@%.0f' % (rb['stir_legs'], rb['stir_dia'], rb['stir_pitch'] * 1000)
                if rb else '')
    if attr in ('Fc', '鋼種'):
        return mname.split('_')[0] if mname else ''
    is_wood = info['mtype'] == 'USER' and mname.startswith('W-')
    main = mname[2:].partition('_')[0] if is_wood else ''
    if attr == '材種':
        return main.partition('-')[0] if is_wood else mname
    if attr == '等級':
        return main.partition('-')[2] if is_wood else ''
    return ''


# ---------------------------------------------------------------------------
# 図形 (prims): ('poly', [(x,y)...], closed, layer) / ('line', p1, p2, layer) /
#              ('circle', (x,y), r, layer) / ('text', (x,y), str, h, layer) /
#              ('rebar', dia, (x,y), 半径, layer)
# 座標は mm 実寸。表の左上が原点、下向きが負。
# ---------------------------------------------------------------------------

def _rect(cx, cy, w, h, layer):
    hw, hh = w / 2.0, h / 2.0
    return ('poly', [(cx - hw, cy - hh), (cx + hw, cy - hh), (cx + hw, cy + hh),
                     (cx - hw, cy + hh)], True, layer)


def _shape_prims(sec, cx, cy, layer):
    """断面外形 (成=鉛直, 幅=水平) [mm]."""
    mm = 1000.0
    H, B = sec.depth * mm, sec.width * mm
    d = [v * mm for v in sec.dims]
    if sec.shape in ('P', 'SR'):
        r = d[0] / 2.0
        out = [('circle', (cx, cy), r, layer)]
        if sec.shape == 'P' and len(d) >= 2 and d[1] > 0:
            out.append(('circle', (cx, cy), r - d[1], layer))
        return out
    if sec.shape == 'BOX':
        out = [_rect(cx, cy, B, H, layer)]
        if len(d) >= 3 and d[2] > 0:
            out.append(_rect(cx, cy, B - 2 * d[2], H - 2 * d[2], layer))
        return out
    if sec.shape == 'SRC':
        out = [_rect(cx, cy, B, H, layer)]
        st = [v * mm for v in (sec.steel_dims or [])]
        if len(st) >= 4:
            out.append(_h_prim(cx, cy, st[0], st[1], st[2], st[3], layer))
        return out
    if sec.shape == 'H' and len(d) >= 4:
        return [_h_prim(cx, cy, d[0], d[1], d[2], d[3], layer)]
    if sec.shape == 'L' and len(d) >= 3:
        hb, hh, t = d[1] / 2.0, d[0] / 2.0, d[2]
        pts = [(cx - hb, cy - hh), (cx + hb, cy - hh), (cx + hb, cy - hh + t),
               (cx - hb + t, cy - hh + t), (cx - hb + t, cy + hh), (cx - hb, cy + hh)]
        return [('poly', pts, True, layer)]
    return [_rect(cx, cy, B, H, layer)]


def _h_prim(cx, cy, Hd, Bd, tw, tf, layer):
    hb, hh, tws = Bd / 2.0, Hd / 2.0, tw / 2.0
    pts = [(cx - hb, cy + hh), (cx + hb, cy + hh), (cx + hb, cy + hh - tf),
           (cx + tws, cy + hh - tf), (cx + tws, cy - hh + tf), (cx + hb, cy - hh + tf),
           (cx + hb, cy - hh), (cx - hb, cy - hh), (cx - hb, cy - hh + tf),
           (cx - tws, cy - hh + tf), (cx - tws, cy + hh - tf), (cx - hb, cy + hh - tf)]
    return ('poly', pts, True, layer)


def _pick_even(items, n):
    m = len(items)
    if n <= 0 or m == 0:
        return []
    if n >= m:
        return list(items)
    return [items[round(i * (m - 1) / (n - 1)) if n > 1 else m // 2]
            for i in range(n)]


def _outer(dia):
    return float(_OUTF.get(int(dia), dia))


def _symbol_r(dia, n_scale):
    """配筋記号の半径 [mm 実寸] (紙面で固定の大きさ)."""
    d_paper = REBAR_SYMBOL_PAPER.get(int(dia), REBAR_SYMBOL_PAPER_LARGE
                                     if int(dia) > 19 else REBAR_SYMBOL_PAPER_MIN)
    return max(d_paper, REBAR_SYMBOL_PAPER_MIN) * n_scale / 2.0


def _rc_column_prims(rc, sec, cx, cy, n_scale):
    """RC 柱: 帯筋 + 中子筋 + 主筋記号 [mm].

    主筋の中心 = 縁からかぶり DO。帯筋の中心線 = 主筋中心から (主筋最外径/2 + 帯筋径/2) 外側。
    """
    mm = 1000.0
    out = []
    cover = rc['cover'] * mm
    ext = _outer(rc['main_dia']) / 2.0 + rc['hoop_dia'] / 2.0
    r_sym = _symbol_r(rc['main_dia'], n_scale)
    if sec.shape in ('SR', 'P'):
        rb = sec.depth * mm / 2.0 - cover
        if rb <= 0:
            return out
        out.append(('circle', (cx, cy), rb + ext, LAYER_HOOP[0]))
        nbar = max(int(rc['total']), 1)
        for i in range(nbar):
            a = math.pi / 2 + 2 * math.pi * i / nbar
            out.append(('rebar', rc['main_dia'], (cx + rb * math.cos(a), cy + rb * math.sin(a)),
                        r_sym, LAYER_TEXT[0]))
        return out
    hb = sec.width * mm / 2.0 - cover
    hd = sec.depth * mm / 2.0 - cover
    if hb <= 0 or hd <= 0:
        return out
    nh = max(rc['n_width'], 1)
    nv = max(rc['n_row'], 1)
    xs = [0.0] if nh == 1 else [-hb + 2 * hb * i / (nh - 1) for i in range(nh)]
    ys = [0.0] if nv == 1 else [hd - 2 * hd * j / (nv - 1) for j in range(nv)]
    cb, cd = hb + ext, hd + ext
    out.append(_rect(cx, cy, 2 * cb, 2 * cd, LAYER_HOOP[0]))
    for x in _pick_even(xs[1:-1], rc['hoop_x'] - 2):
        out.append(('line', (cx + x, cy - cd), (cx + x, cy + cd), LAYER_HOOP[0]))
    for y in _pick_even(ys[1:-1], rc['hoop_y'] - 2):
        out.append(('line', (cx - cb, cy + y), (cx + cb, cy + y), LAYER_HOOP[0]))
    pts = []
    for x in xs:
        pts.append((x, ys[0]))
        if len(ys) > 1:
            pts.append((x, ys[-1]))
    for j in range(1, len(ys) - 1):
        pts.append((xs[0], ys[j]))
        pts.append((xs[-1], ys[j]))
    for x, y in pts:
        out.append(('rebar', rc['main_dia'], (cx + x, cy + y), r_sym, LAYER_TEXT[0]))
    return out


def _rc_beam_prims(rb, sec, cx, cy, n_scale):
    """RC 梁: あばら筋 + 上端筋 / 下端筋 [mm].

    主筋の中心: 上下は縁から DT / DB、左右はあばら筋の内側に接する位置
    (横かぶり BEAM_SIDE_COVER + あばら筋径 + 主筋最外径/2)。
    あばら筋の中心線 = 主筋中心から (主筋最外径/2 + あばら筋径/2) 外側。
    2 段目の中心間 = 最外径 + max(25mm, 1.5d)。
    """
    mm = 1000.0
    B, H = sec.width * mm, sec.depth * mm
    sd = rb['stir_dia'] or 10
    dia_t = rb['top_dia'] or 13
    dia_b = rb['bot_dia'] or 13
    ext_t = _outer(dia_t) / 2.0 + sd / 2.0
    ext_b = _outer(dia_b) / 2.0 + sd / 2.0
    y_top = H / 2.0 - rb['cover_top'] * mm       # 上端 1 段目の中心
    y_bot = -(H / 2.0 - rb['cover_bot'] * mm)    # 下端 1 段目の中心
    s_half = B / 2.0 - BEAM_SIDE_COVER - sd / 2.0  # あばら筋中心線の半幅
    top_line, bot_line = y_top + ext_t, y_bot - ext_b
    if s_half <= 0 or top_line <= bot_line:
        return []
    out = [('poly', [(cx - s_half, cy + bot_line), (cx + s_half, cy + bot_line),
                     (cx + s_half, cy + top_line), (cx - s_half, cy + top_line)],
            True, LAYER_HOOP[0])]

    def row(n, dia, y):
        if n <= 0:
            return
        pb = max(s_half - sd / 2.0 - _outer(dia) / 2.0, 0.0)
        xs = [0.0] if n == 1 else [-pb + 2 * pb * k / (n - 1) for k in range(n)]
        for x in xs:
            out.append(('rebar', dia, (cx + x, cy + y), _symbol_r(dia, n_scale),
                        LAYER_TEXT[0]))

    gap_t = _outer(dia_t) + max(25.0, 1.5 * dia_t)
    gap_b = _outer(dia_b) + max(25.0, 1.5 * dia_b)
    row(rb['top_rb1'], dia_t, y_top)
    if rb['top_rb2']:
        row(rb['top_rb2'], dia_t, y_top - gap_t)
    row(rb['bot_rb1'], dia_b, y_bot)
    if rb['bot_rb2']:
        row(rb['bot_rb2'], dia_b, y_bot + gap_b)
    return out


def _text_w(text, th):
    """文字列のおおよその幅 [mm]. 全角 1.45、半角 0.85 × 文字高さ.

    TrueType (Arial 系) の実測で、数字・英字は文字高さの 0.78〜0.85 倍、
    全角は最大 1.4 倍 (2026-09-17 ezdxf のフォント計測)。CAD ごとのフォント差を見て大きめに取る。
    """
    return sum((1.45 if ord(ch) > 0x2E7F else 0.85) for ch in text) * th


def _block_dims(cat, block, th, k, n_scale):
    """ブロックの (断面行高, その他行高, 断面列幅, 見出し列幅) [mm 実寸].

    断面行高・列幅は、ブロック内で一番大きい断面 + 余白と、文字の長さで決める。
    """
    _sec_h, _other_k, col_k = DIMS.get(cat, DIMS_DEFAULT)
    other_h = ROW_H_RATIO * th
    rows = SPECS[cat][1]
    pad = 2 * SECTION_MARGIN_PAPER * n_scale
    big_h = max((s.depth for s, _i in block), default=0.0) * 1000 + pad
    big_w = max((s.width for s, _i in block), default=0.0) * 1000 + pad
    texts = [attr_value(s, i, a) for s, i in block for a in rows if a != '断面']
    txt_w = max((_text_w(t, th) for t in texts), default=0.0) + 1.2 * th
    lab_w = max((_text_w(a, th) for a in rows), default=0.0) + 1.2 * th
    return (max(big_h, 4 * other_h), other_h, max(col_k * k, big_w, txt_w),
            max(LABEL_W * k, lab_w))


def _block_prims(cat, block, x0, ytop, th, k, dims, n_scale):
    """1 ブロック (断面を列・属性を行) の図形. 戻り値 (prims, 下端 y)."""
    rows = SPECS[cat][1]
    sec_h, other_h, sec_w, lw = dims
    lt = LAYER_TEXT[0]
    prims = []
    y = ytop
    for attr in rows:
        h = sec_h if attr == '断面' else other_h
        cy = y - h / 2.0
        prims.append(('poly', [(x0, y - h), (x0 + lw, y - h), (x0 + lw, y), (x0, y)],
                      True, lt))
        prims.append(('text', (x0 + lw / 2.0, cy), attr, th, lt))
        for ci, (sec, info) in enumerate(block):
            cx0 = x0 + lw + ci * sec_w
            cx = cx0 + sec_w / 2.0
            prims.append(('poly', [(cx0, y - h), (cx0 + sec_w, y - h), (cx0 + sec_w, y),
                                   (cx0, y)], True, lt))
            if attr == '断面':
                prims += _shape_prims(sec, cx, cy, LAYER_SEC[info['mclass']][0])
                if info['col']:
                    prims += _rc_column_prims(info['col'], sec, cx, cy, n_scale)
                elif info['beam']:
                    prims += _rc_beam_prims(info['beam'], sec, cx, cy, n_scale)
            else:
                val = attr_value(sec, info, attr)
                if val:
                    prims.append(('text', (cx, cy), val, th, lt))
        y -= h
    return prims, y


def _layout_block(cat, block, n_scale, text_paper_mm, size_items=None):
    """1 ブロックを n_scale で組む → (prims, 幅, 高さ, ブロック間の空き) [mm 実寸].

    size_items: 列幅・断面行高を決める断面 (同じ図のブロックでそろえるとき)。省略時は block。
    """
    th = text_paper_mm * n_scale
    k = th / 180.0
    dims = _block_dims(cat, size_items or block, th, k, n_scale)
    prims, ybot = _block_prims(cat, block, 0.0, 0.0, th, k, dims, n_scale)
    width = dims[3] + len(block) * dims[2]
    return prims, width, -ybot, BLOCK_GAP * k


def build_list_figures(M, scale=None, paper='A3', text_paper_mm=2.5,
                       categories=None):
    """部材リストの図 → list of (区分, 図 dict). 用紙に入らないブロックは次の図へ送る."""
    pw, ph = PAPER_MM.get(paper, PAPER_MM['A3'])
    aw = pw - 2 * PAPER_MARGIN_MM
    ah = ph - 2 * PAPER_MARGIN_MM - TITLE_RESERVE_PAPER
    figs = []
    for cat, items in list_model(M).items():
        if not items or (categories and cat not in categories):
            continue
        # ブロックごとに縮尺を決める
        chunks = [items[i:i + COLS_PER_BLOCK] for i in range(0, len(items), COLS_PER_BLOCK)]
        scales = []
        for block in chunks:
            if scale:
                scales.append(int(scale))
                continue
            n = LIST_SCALE_SERIES[-1]
            for cand in LIST_SCALE_SERIES:   # 用紙に収まる最小の縮尺
                _p, w, h, _g = _layout_block(cat, block, cand, text_paper_mm)
                if w / cand <= aw and h / cand <= ah:
                    n = cand
                    break
            scales.append(n)
        # 同じ縮尺が続くブロックを、列幅をそろえたうえで用紙の高さに入るだけ 1 枚にまとめる
        pages = []
        for block, n in zip(chunks, scales):
            if pages and pages[-1]['n'] == n:
                cand = pages[-1]['blocks'] + [block]
                if _page_height(cat, cand, n, text_paper_mm) / n <= ah and \
                        _page_width(cat, cand, n, text_paper_mm) / n <= aw:
                    pages[-1]['blocks'] = cand
                    continue
            pages.append({'n': n, 'blocks': [block]})
        for pg in pages:
            pg['items'] = []
            size_items = [it for b in pg['blocks'] for it in b]
            y = 0.0
            for b in pg['blocks']:
                prims, _w, h, gap = _layout_block(cat, b, pg['n'], text_paper_mm, size_items)
                pg['items'].append((prims, y))
                y += h + gap
        title0 = SPECS[cat][0]
        for pi, pg in enumerate(pages):
            prims = []
            for bprims, yoff in pg['items']:
                prims += [_shift(p, 0.0, -yoff) for p in bprims]
            title = title0 + ('' if len(pages) == 1 else ' (%d/%d)' % (pi + 1, len(pages)))
            n = pg['n']
            figs.append((cat, _prims_fig(prims, '%s  S=1/%d (%s)' % (title, n, paper), n)))
    return figs


def _page_height(cat, blocks, n, text_paper_mm):
    size_items = [it for b in blocks for it in b]
    total = 0.0
    for i, b in enumerate(blocks):
        _p, _w, h, gap = _layout_block(cat, b, n, text_paper_mm, size_items)
        total += h + (gap if i else 0.0)
    return total


def _page_width(cat, blocks, n, text_paper_mm):
    size_items = [it for b in blocks for it in b]
    return max(_layout_block(cat, b, n, text_paper_mm, size_items)[1] for b in blocks)


def _shift(p, dx, dy):
    t = p[0]
    mv = (lambda q: (q[0] + dx, q[1] + dy))
    if t == 'poly':
        return ('poly', [mv(q) for q in p[1]], p[2], p[3])
    if t == 'line':
        return ('line', mv(p[1]), mv(p[2]), p[3])
    if t == 'circle':
        return ('circle', mv(p[1]), p[2], p[3])
    if t == 'text':
        return ('text', mv(p[1]), p[2], p[3], p[4])
    if t == 'rebar':
        return ('rebar', p[1], mv(p[2]), p[3], p[4])
    return p


def _prims_fig(prims, title, n_scale):
    xs, ys = [], []
    for p in prims:
        t = p[0]
        if t == 'poly':
            xs += [q[0] for q in p[1]]
            ys += [q[1] for q in p[1]]
        elif t == 'line':
            xs += [p[1][0], p[2][0]]
            ys += [p[1][1], p[2][1]]
        elif t in ('circle', 'rebar'):
            c, r = (p[1], p[2]) if t == 'circle' else (p[2], p[3])
            xs += [c[0] - r, c[0] + r]
            ys += [c[1] - r, c[1] + r]
        elif t == 'text':
            xs.append(p[1][0])
            ys.append(p[1][1])
    bounds = (min(xs), min(ys), max(xs), max(ys)) if xs else (0, 0, 1, 1)
    return {'rects': {}, 'lines': {}, 'inserts': [], 'texts': [], 'prims': prims,
            'title': title, 'scale': n_scale,
            'extent': (bounds[2] - bounds[0], bounds[3] - bounds[1]),
            'bounds': bounds}


# ---------------------------------------------------------------------------
# DXF 書き出し
# ---------------------------------------------------------------------------

def _ensure_rebar_block(doc, dia):
    """径別の鉄筋記号ブロック (単位半径 1、色 BYBLOCK). struct_cad と同じ記号."""
    name = 'D%d' % int(dia)
    if name in doc.blocks:
        return name
    blk = doc.blocks.new(name=name)
    R = 1.0
    d = R / math.sqrt(2.0)
    attr = {'layer': '0', 'color': 0}

    def circle(r):
        blk.add_circle((0.0, 0.0), r, dxfattribs=attr)

    def filled(r):
        h = blk.add_hatch(color=0, dxfattribs={'layer': '0'})
        h.paths.add_edge_path().add_ellipse((0.0, 0.0), major_axis=(r, 0.0), ratio=1.0)
        h.set_solid_fill(color=0)

    def line(x1, y1, x2, y2):
        blk.add_line((x1, y1), (x2, y2), dxfattribs=attr)

    dia = int(dia)
    if dia == 10:
        filled(R * 0.55)
    elif dia == 13:
        line(-d, -d, d, d)
        line(-d, d, d, -d)
    elif dia == 16:
        circle(R)
        line(-d, d, d, -d)
    elif dia == 19:
        filled(R)
    elif dia == 22:
        circle(R)
    elif dia == 25:
        circle(R)
        filled(R * 0.28)
    elif dia == 29:
        circle(R)
        line(-d, -d, d, d)
        line(-d, d, d, -d)
    elif dia == 32:
        circle(R)
        circle(R * 0.5)
    elif dia == 35:
        circle(R)
        circle(R * 0.62)
        circle(R * 0.3)
    elif dia == 38:
        circle(R)
        circle(R * 0.62)
        line(-d, -d, d, d)
    elif dia == 41:
        circle(R)
        circle(R * 0.62)
        circle(R * 0.28)
    elif dia == 51:
        circle(R)
        circle(R * 0.62)
        line(-d, -d, d, d)
        line(-d, d, d, -d)
    else:
        circle(R)
    return name


def prims_to_msp(prims, msp, origin=(0.0, 0.0)):
    from ezdxf.enums import TextEntityAlignment
    doc = msp.doc
    ox, oy = origin
    for name, color in (LAYER_HOOP,):
        if name not in doc.layers:
            doc.layers.add(name, color=color)
    for p in prims:
        t = p[0]
        if t == 'poly':
            msp.add_lwpolyline([(x + ox, y + oy) for x, y in p[1]], close=p[2],
                               dxfattribs={'layer': p[3]})
        elif t == 'line':
            msp.add_line((p[1][0] + ox, p[1][1] + oy), (p[2][0] + ox, p[2][1] + oy),
                         dxfattribs={'layer': p[3]})
        elif t == 'circle':
            msp.add_circle((p[1][0] + ox, p[1][1] + oy), p[2], dxfattribs={'layer': p[3]})
        elif t == 'text':
            e = msp.add_text(p[2], dxfattribs={'layer': p[4], 'height': p[3]})
            e.set_placement((p[1][0] + ox, p[1][1] + oy),
                            align=TextEntityAlignment.MIDDLE_CENTER)
        elif t == 'rebar':
            bname = _ensure_rebar_block(doc, p[1])
            msp.add_blockref(bname, (p[2][0] + ox, p[2][1] + oy), dxfattribs={
                'layer': p[4], 'xscale': p[3], 'yscale': p[3]})


# プレビューの色 (DXF のレイヤ色 ACI に合わせる)
_ACI_HEX = {1: '#d33', 2: '#c8b400', 3: '#2a9d2a', 4: '#00a0c8', 6: '#c040c0',
            8: '#888888', 30: '#e07b00', 161: '#4f8fd0'}


def _layer_color(layer):
    from .dxf_struct import LAYER_DEF
    for name, aci in (list(LAYER_SEC.values()) + list(LAYER_DEF.values())
                      + [LAYER_TEXT, LAYER_HOOP]):
        if name == layer:
            return _ACI_HEX.get(aci, '#444')
    return '#444'


def _rebar_glyph_axes(ax, dia, c, r, color):
    """DXF の鉄筋記号 (_ensure_rebar_block) と同じ形をプレビューに描く."""
    import matplotlib.pyplot as plt
    x, y = c
    d = r / math.sqrt(2.0)

    def circle(rr, fill=False):
        ax.add_patch(plt.Circle((x, y), rr, fill=fill, ec=color, fc=color, lw=0.4))

    def line(x1, y1, x2, y2):
        ax.plot([x + x1, x + x2], [y + y1, y + y2], color=color, lw=0.4)

    dia = int(dia)
    if dia == 10:
        circle(r * 0.55, True)
    elif dia == 13:
        line(-d, -d, d, d)
        line(-d, d, d, -d)
    elif dia == 16:
        circle(r)
        line(-d, d, d, -d)
    elif dia == 19:
        circle(r, True)
    elif dia == 25:
        circle(r)
        circle(r * 0.28, True)
    elif dia == 29:
        circle(r)
        line(-d, -d, d, d)
        line(-d, d, d, -d)
    elif dia in (32, 35, 38, 41, 51):
        circle(r)
        circle(r * 0.55)
    else:
        circle(r)


def prims_to_axes(prims, ax, pt_per_mm=None):
    """用紙プレビュー (matplotlib). pt_per_mm: 実寸 1mm あたりの文字ポイント (文字を実寸で出す)."""
    import matplotlib.pyplot as plt
    for p in prims:
        t = p[0]
        if t == 'poly':
            xs = [q[0] for q in p[1]] + ([p[1][0][0]] if p[2] else [])
            ys = [q[1] for q in p[1]] + ([p[1][0][1]] if p[2] else [])
            ax.plot(xs, ys, color=_layer_color(p[3]), lw=0.5)
        elif t == 'line':
            ax.plot([p[1][0], p[2][0]], [p[1][1], p[2][1]],
                    color=_layer_color(p[3]), lw=0.5)
        elif t == 'circle':
            ax.add_patch(plt.Circle(p[1], p[2], fill=False, ec=_layer_color(p[3]), lw=0.5))
        elif t == 'rebar':
            _rebar_glyph_axes(ax, p[1], p[2], p[3], _layer_color(p[4]))
        elif t == 'text':
            fs = max(2.0, p[3] * pt_per_mm) if pt_per_mm else 5
            ax.text(p[1][0], p[1][1], p[2], fontsize=fs, color=_layer_color(p[4]),
                    ha='center', va='center')
