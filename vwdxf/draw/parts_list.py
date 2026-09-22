# -*- coding: utf-8 -*-
"""構造図DXF (新規版) の部材リスト図.

struct_cad (tools/struct_cad_py) の断面リスト (draw/section_list.py) と同じ表を、
Model IR (read/: 断面・材料・配筋・かぶり) から作る。

表の作り (struct_cad と同じ):
  - 区分: 鉄骨部材リスト / 木造断面リスト / その他断面リスト / RC柱リスト / RC梁リスト
    (RC 柱・梁は配筋 *REBAR-COLUMN / *REBAR-BEAM がある断面。それ以外は材料で S / W / その他)
  - 断面を列、属性を行にした転置表を 6 断面ごとに折り返す
  - 断面図は実寸。RC 柱は帯筋 (かぶり内側の矩形) + 中子筋 + 主筋記号、RC 梁はあばら筋 + 上下端筋
  - 主筋記号は径別 (D10 ● / D13 × / D16 ∅ / D19 ● / D22 ○ ...)
B (dxf_struct.py) に合わせたところ・変えたところ:
  - 文字は画面の pt (既定 12pt) を紙面 mm × 縮尺にする。表の寸法は文字高さに比例 (struct_cad は 12pt@1:100 固定)
  - 列幅は文字の長さに合わせて広げる (struct_cad は固定幅で形状表記がはみ出す)
  - 断面行の高さ・列幅は、そのブロックで一番大きい断面に余白を足した大きさ
    (struct_cad は区分ごとの固定値)
  - 縮尺は全ページ共通で画面の「部材リストの縮尺」(既定 1/60)。ブロック (6 断面) を
    用紙に入るだけ 1 枚にまとめ、あふれたら次の図にする。
    同じ図のブロックは列幅・断面行高をそろえる
  - 行高は文字高さの 2 倍、断面の周りの余白は紙面 5mm
  - 配筋記号は実寸で直径 = 鉄筋径 + 2mm (struct_cad と同じ)。VW10J のシンボルは倍率を持てないため
  - 主筋の中心は mgt のかぶり (DO / DT / DB = 縁から主筋重心) の位置。帯筋・あばら筋はその外側
    (主筋最外径/2 + 帯筋径/2)。梁の横かぶりはデータに無いので 40mm + あばら筋径/2
  - 2 段筋の中心間 = 最外径 + max(25mm, 1.5d)
  - 円形の RC 柱は BxD 欄を φ、帯筋を円、主筋を円周に等分配置 (struct_cad は角柱として描く)
  - レイヤ: 罫線・文字・主筋 = S-Red09、断面外形 = 柱 S-Green30 / 梁 S-Magenda30、
    帯筋・あばら筋 = S-Cyan20 (struct_cad と同じ)
  - ダミー断面の下限は画面の「ダミー材断面番号下限」(limit_sec_no)
"""

import math

from ..style import (LAYER_HOOP, LAYER_SECTION, LAYER_TEXT, LAYER_TITLE, PAPER_MARGIN_MM,
                     PAPER_MM)
from .primitives import Circle, Figure, Line, Poly, RebarRef, Text, prims_bounds

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
#   区分: 断面列幅の最小 [×k] (断面行高・その他行高は断面の大きさと文字高さから決める)
COL_W = {'S': 1500.0, 'W': 1500.0, 'RC': 2000.0, 'RCB': 2000.0}
COL_W_DEFAULT = 1500.0
LABEL_W = 1000.0          # 見出し列幅 [×k]
BLOCK_GAP = 1000.0        # ブロック間 [×k]
COLS_PER_BLOCK = 6
ROW_H_RATIO = 2.0         # その他行の高さ = 文字高さ × これ
SECTION_MARGIN_PAPER = 5.0  # 断面図の周りの余白 [紙面 mm]
LIST_SCALE_DEFAULT = 60   # 部材リストの縮尺 (事務所の想定 1/60)
TITLE_RESERVE_PAPER = 17.0  # 表題のために用紙上端に取っておく高さ [紙面 mm] (18pt の見出し × 2.5 行)
BEAM_SIDE_COVER = 40.0    # 梁の横かぶり (あばら筋外面まで) [mm]。データに無いので固定

# 異形鉄筋の最外径 (JIS): 呼び径 -> 最外径 [mm]
_OUTF = {10: 11, 13: 14, 16: 18, 19: 21, 22: 25, 25: 28,
         29: 33, 32: 36, 35: 40, 38: 43, 41: 47, 51: 57}




def list_sections(M):
    """Model IR の断面 → {断面番号: ListSection} (リストに載せられるもの)."""
    out = {}
    for no, s in sorted(M.ir.sections.items()):
        name = M.sec_names.get(no, str(no))
        d = list(s.dims or [])
        sec = None
        if s.shape == 'H' and len(d) >= 4:
            sec = ListSection(no, name, 'H', d[:4],
                              dims_j=list(s.dims_j[:4]) if s.dims_j else None)
        elif s.shape in ('SB', 'C', 'T') and len(d) >= 2:
            sec = ListSection(no, name, s.shape, d[:2],
                              dims_j=list(s.dims_j[:2]) if s.dims_j else None)
        elif s.shape in ('SR',) and len(d) >= 1:
            sec = ListSection(no, name, 'SR', d[:1])
        elif s.shape == 'P' and len(d) >= 2:
            sec = ListSection(no, name, 'P', d[:2])
        elif s.shape in ('BOX', 'L') and len(d) >= 3:
            sec = ListSection(no, name, s.shape, d[:3])
        elif s.shape == 'SRC' and len(d) >= 2:
            st = [v for v in (s.steel_dims or []) if v > 0][:4]
            sec = ListSection(no, name, 'SRC', d[:2], steel_dims=st if len(st) >= 4 else None)
        if sec is not None and sec.depth > 0:
            out[no] = sec
    return out


def _section_materials(M):
    """断面番号 → 材料 (その断面を使う最初の線要素の材料)."""
    out = {}
    for e in M.ir.elements:
        if e.is_line:
            out.setdefault(int(e.prop), M.ir.materials.get(e.mat))
    return out


def rebar_tables(M):
    """RC 柱・梁の配筋 (Model IR) → ({断面: dict}, {断面: dict})."""
    cols, beams = {}, {}
    for no, rc in M.ir.rebar_columns.items():
        cols[int(no)] = {
            'total': rc.main_total, 'main_dia': rc.main_dia, 'n_row': rc.n_row,
            'n_width': rc.n_width, 'hoop_dia': rc.hoop_dia, 'hoop_pitch': rc.hoop_pitch,
            'hoop_x': rc.hoop_x, 'hoop_y': rc.hoop_y, 'cover': rc.cover,
        }
    for no, rb in M.ir.rebar_beams.items():
        beams[int(no)] = {
            'top_rb1': rb.top_rb1, 'top_rb2': rb.top_rb2, 'top_dia': rb.top_dia,
            'bot_rb1': rb.bot_rb1, 'bot_rb2': rb.bot_rb2, 'bot_dia': rb.bot_dia,
            'stir_dia': rb.stir_dia, 'stir_pitch': rb.stir_pitch, 'stir_legs': rb.stir_legs,
            'cover_top': rb.cover_top, 'cover_bot': rb.cover_bot,
        }
    return cols, beams


def list_model(M):
    """リストに載せる断面を区分ごとに集める → {区分: [(ListSection, 付帯情報)]}."""
    if hasattr(M, '_list_model'):
        return M._list_model
    secs = list_sections(M)
    sec_mat = _section_materials(M)
    cols, beams = rebar_tables(M)
    col_secs = {m['sec_no'] for m in M.members if m['kind'] == 'column'}
    cats = {c: [] for c in CATEGORY_ORDER}
    for no in sorted(secs):
        if no >= M.limit_sec_no:
            continue   # ダミー断面
        sec = secs[no]
        mat = sec_mat.get(no)
        mtype, mname = ((mat.type or '').upper(), mat.name) if mat else ('', '')
        info = {'mtype': mtype, 'mname': mname, 'col': cols.get(no),
                'beam': beams.get(no),
                # 断面外形のクラス: RC は柱/梁リスト、それ以外は柱に使われていれば柱
                'role': ('beam' if no in beams else 'column' if no in cols
                         else 'column' if no in col_secs else 'beam')}
        if no in beams:
            cats['RCB'].append((sec, info))
        elif no in cols:
            cats['RC'].append((sec, info))
        else:
            cats[_category(mtype, mname)].append((sec, info))
    M._list_model = cats
    return cats


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


def _category(mtype, name):
    if mtype == 'STEEL':
        return 'S'
    if mtype == 'USER' and name.startswith('W-'):
        return 'W'
    return 'OTHER'


def list_categories(M):
    """断面がある区分 → list of dict {key, label, n}."""
    return [{'key': c, 'label': SPECS[c][0], 'n': len(v)}
            for c, v in list_model(M).items() if v]


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


def _symbol_r(dia):
    """配筋記号の半径 [mm 実寸] = (鉄筋径 + 2) / 2. 縮尺によらない (struct_cad と同じ).

    VW10J のシンボルは倍率を持てない (実寸で定義し径ごとに別シンボル) ので、紙面固定にはしない。
    """
    return (int(dia or 13) + 2) / 2.0


def _rc_column_prims(rc, sec, cx, cy):
    """RC 柱: 帯筋 + 中子筋 + 主筋記号 [mm].

    主筋の中心 = 縁からかぶり DO。帯筋の中心線 = 主筋中心から (主筋最外径/2 + 帯筋径/2) 外側。
    """
    mm = 1000.0
    out = []
    cover = rc['cover'] * mm
    ext = _outer(rc['main_dia']) / 2.0 + rc['hoop_dia'] / 2.0
    r_sym = _symbol_r(rc['main_dia'])
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


def _rc_beam_prims(rb, sec, cx, cy):
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
            out.append(('rebar', dia, (cx + x, cy + y), _symbol_r(dia),
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
    col_k = COL_W.get(cat, COL_W_DEFAULT)
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
                prims += _shape_prims(sec, cx, cy, LAYER_SECTION[info['role']][0])
                if info['col']:
                    prims += _rc_column_prims(info['col'], sec, cx, cy)
                elif info['beam']:
                    prims += _rc_beam_prims(info['beam'], sec, cx, cy)
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


def _paper_area(paper):
    pw, ph = PAPER_MM.get(paper, PAPER_MM['A3'])
    return pw - 2 * PAPER_MARGIN_MM, ph - 2 * PAPER_MARGIN_MM - TITLE_RESERVE_PAPER


def _chunks(items):
    return [items[i:i + COLS_PER_BLOCK] for i in range(0, len(items), COLS_PER_BLOCK)]


def build_list_figures(M, scale=LIST_SCALE_DEFAULT, paper='A3', text_paper_mm=2.5,
                       categories=None, title_paper_mm=None):
    """部材リストの図 → list of (区分, 図 dict). 用紙に入らないブロックは次の図へ送る.

    縮尺は全ページで 1 つ (画面の「部材リストの縮尺」、既定 1/60)。
    """
    aw, ah = _paper_area(paper)
    n_all = int(scale or LIST_SCALE_DEFAULT)
    figs = []
    for cat, items in list_model(M).items():
        if not items or (categories and cat not in categories):
            continue
        chunks = _chunks(items)
        scales = [n_all] * len(chunks)
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
            figs.append((cat, _prims_fig(prims, '%s  S=1/%d (%s)' % (title, n, paper), n, cat,
                                         pi + 1, title_paper_mm or 5.0)))
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


def _to_prim(p):
    """表組みの内部形式 (タプル) → Drawing IR."""
    t = p[0]
    if t == 'poly':
        return Poly(list(p[1]), p[3], closed=p[2])
    if t == 'line':
        return Line(p[1], p[2], p[3])
    if t == 'circle':
        return Circle(p[1], p[2], p[3])
    if t == 'text':
        return Text(p[1], p[2], p[3], p[4], 'MC')
    if t == 'rebar':
        return RebarRef(int(p[1]), p[2], p[3], p[4])
    raise ValueError(t)


def _prims_fig(prims, title, n_scale, cat='', page=1, title_paper_mm=5.0):
    """表組み → Figure. 見出しは表の左上、1.5 行上."""
    P = [_to_prim(p) for p in prims]
    x0, _y0, _x1, y1 = prims_bounds(P) if P else (0.0, 0.0, 1.0, 1.0)
    th = title_paper_mm * n_scale
    P.append(Text((x0, y1 + th * 1.5), title, th, LAYER_TITLE[0], 'BL'))
    return Figure('list', cat, title, n_scale, prims_bounds(P), P)
