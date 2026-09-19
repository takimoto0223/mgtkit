# -*- coding: utf-8 -*-
"""Drawing IR → DXF (Vectorworks 10J 向けの書式).

書式 (style.py の DXF_VERSION / DXF_CODEPAGE / TEXT_STYLE):
  R2000 + Shift-JIS (ANSI_932) + 文字スタイル MSUIGothic (フォント欄 'MS UI Gothic')。
  VW10J 自身が書き出す DXF と同じ形。R2010 + Standard/txt では VW10J で文字が潰れた (2026-09-19)。
  試験ファイルは works/mgtkit-dxf-kaizen/font_test/ (A〜F、ここは E 案)。
線種は縮尺ごとに紙面寸法を焼き込んで定義する (LTSCALE の扱いが CAD ごとに違うため)。
"""
import math
import os
import re

from ..style import (ANSI31_SPACING, DASHED_PAPER_MM, DXF_CODEPAGE,
                     DXF_VERSION, GRID_DASHDOT_PAPER_MM, TEXT_STYLE,
                     TEXT_STYLE_FONT_FILE)
from .. import sections
from ..draw.primitives import (Circle, Hatch, Line, NameRef, Poly, RebarRef, SectionRef,
                               Text, layer_color)

_TEXT_FALLBACK = {'–': '-', '—': '-', '−': '-'}
_ALIGN = {'BC': 'BOTTOM_CENTER', 'MC': 'MIDDLE_CENTER', 'BL': 'BOTTOM_LEFT'}


def dxf_text(txt):
    """DXF に書く文字列. Shift-JIS に無い字は近い字か '?' にする.

    Shift-JIS で書けない字を ezdxf は \\U+XXXX で書くが、VW10J はそれを解かずにそのまま表示する。
    """
    txt = str(txt)
    if DXF_CODEPAGE != 'ANSI_932':
        return txt
    out = []
    for ch in txt:
        ch = _TEXT_FALLBACK.get(ch, ch)
        try:
            ch.encode('cp932')
        except UnicodeEncodeError:
            ch = '?'
        out.append(ch)
    return ''.join(out)


def new_doc():
    import ezdxf
    # 寸法スタイルは使わないので入れない (入れると矢印のブロックがシンボルとして VW10J に入る)
    doc = ezdxf.new(DXF_VERSION, setup=['linetypes', 'styles'])
    doc.header['$INSUNITS'] = 4      # mm (ezdxf の既定は 6 = m で、VW10J が 1000 倍に読む)
    doc.header['$MEASUREMENT'] = 1   # メートル法
    if DXF_CODEPAGE:
        doc.header['$DWGCODEPAGE'] = DXF_CODEPAGE
        doc.encoding = {'ANSI_932': 'cp932'}.get(DXF_CODEPAGE, doc.encoding)
    name, face = TEXT_STYLE
    if name not in doc.styles:
        if TEXT_STYLE_FONT_FILE:
            st = doc.styles.add(name, font=TEXT_STYLE_FONT_FILE)
            st.set_extended_font_data(family=face)
        else:
            doc.styles.add(name, font=face)
    return doc


def _layer(doc, name):
    if name not in doc.layers:
        doc.layers.add(name, color=layer_color(name))
    return name


def _scaled_linetype(doc, prefix, paper_pattern, n_scale):
    """紙面寸法のパターンに縮尺を焼き込んだ線種を定義して名前を返す."""
    name = '%s_S%d' % (prefix, int(n_scale))
    if name not in doc.linetypes:
        pat = [float(v) * n_scale for v in paper_pattern]
        total = sum(abs(v) for v in pat)
        doc.linetypes.add(name, pattern=[total] + pat,
                          description='%s 1/%d' % (prefix, int(n_scale)))
    return name


def _linetype(doc, ltype, n_scale):
    if ltype == 'grid':
        return _scaled_linetype(doc, 'GRID_DASHDOT', GRID_DASHDOT_PAPER_MM, n_scale)
    if ltype == 'dashed':
        return _scaled_linetype(doc, 'DASHED', DASHED_PAPER_MM, n_scale)
    return None           # 実線


def _unique_block(doc, name, key, reg_attr):
    """同じ名前で中身が違うブロックは _2, _3 … を付けて別名にする."""
    reg = getattr(doc, reg_attr, None)
    if reg is None:
        reg = {}
        setattr(doc, reg_attr, reg)
    if key in reg:
        return reg[key], False
    name = dxf_text(re.sub(r'[<>/\\":;?*|=`,\s]', '_', str(name))) or 'X'
    n2, c = name, 2
    while n2 in doc.blocks:
        n2 = '%s_%d' % (name, c)
        c += 1
    reg[key] = n2
    return n2, True


def _section_block(doc, p):
    """断面記号ブロック <符号>_section / <符号>_section2 (実寸、原点 = 断面の中心、局所 X = せい).

    中身は INSERT と同じレイヤ (= VW10J のクラス)。'0' だと VW10J でシンボルの中身がクラス無しになる。
    """
    shape = sections.symbol_shape(p.sec, p.down)
    geo = tuple((it[0], tuple((round(x, 5), round(y, 5)) for x, y in it[1]), it[2])
                if it[0] == 'poly' else (it[0], it[1], round(it[2], 5)) for it in shape)
    key = ('sec', p.name, geo, p.layer)          # 同じ符号・同じ形なら 1 つのブロックを使い回す
    name, new = _unique_block(doc, p.name, key, '_vwdxf_sec_reg')
    if not new:
        return name
    blk = doc.blocks.new(name)
    attr = {'layer': _layer(doc, p.layer)}
    for it in shape:
        if it[0] == 'poly':
            pts = [(x * 1000.0, y * 1000.0) for x, y in it[1]]
            if it[2]:
                blk.add_lwpolyline(pts, close=True, dxfattribs=attr)
            else:
                blk.add_line(pts[0], pts[1], dxfattribs=attr)
        else:
            (cx, cy), r = it[1], it[2]
            blk.add_circle((cx * 1000.0, cy * 1000.0), r * 1000.0, dxfattribs=attr)
    return name


def _name_block(doc, p):
    """符号ブロック <符号>_NAME1 (中央揃えの文字 1 つ、実寸). struct_cad と同じ."""
    from ezdxf.enums import TextEntityAlignment
    key = ('name', p.block, p.text, round(p.height, 3), p.layer)
    name, new = _unique_block(doc, p.block, key, '_vwdxf_name_reg')
    if not new:
        return name
    blk = doc.blocks.new(name)
    t = blk.add_text(dxf_text(p.text), dxfattribs={
        'layer': _layer(doc, p.layer), 'height': p.height, 'style': TEXT_STYLE[0]})
    t.set_placement((0.0, 0.0), align=TextEntityAlignment.MIDDLE_CENTER)
    return name


def _rebar_block(doc, dia, r, layer='0'):
    """径別の鉄筋記号ブロック (実寸・半径 r [mm]、倍率 1 で置く). struct_cad と同じ記号.

    VW10J はシンボルに倍率を持てず、HATCH も読まないので、実寸で定義し、塗りは SOLID で作る。
    中身は INSERT と同じレイヤ (= クラス)。

    D10:● 小 / D13:× / D16:∅ / D19:● / D22:○ / D25:⊙ / D29:⊗ / D32:◎ / D35:三重丸 /
    D38:二重丸に斜線 / D41:二重丸に小丸 / D51:二重丸に× / その他:○
    """
    dia = int(dia)
    reg = getattr(doc, '_vwdxf_rebar_reg', None)
    if reg is None:
        reg = doc._vwdxf_rebar_reg = {}
    key = (dia, round(float(r), 3), layer)
    if key in reg:
        return reg[key]
    name = 'D%d' % dia
    n2, c = name, 2
    while n2 in doc.blocks:
        n2 = '%s_%d' % (name, c)
        c += 1
    name = reg[key] = n2
    blk = doc.blocks.new(name=name)
    R = float(r)
    d = R / math.sqrt(2.0)
    attr = {'layer': _layer(doc, layer)}

    def circle(rr):
        blk.add_circle((0.0, 0.0), rr, dxfattribs=attr)

    def filled(rr, n=16):
        """塗りつぶした円 = SOLID の扇 (外周は円で重ねて滑らかに見せる)."""
        pts = [(rr * math.cos(2 * math.pi * k / n), rr * math.sin(2 * math.pi * k / n))
               for k in range(n)]
        for k in range(0, n, 2):
            a, b, e = pts[k], pts[(k + 1) % n], pts[(k + 2) % n]
            blk.add_solid([(0.0, 0.0), a, e, b], dxfattribs=attr)   # 四角形 (中心・a・b・e)
        circle(rr)

    def line(x1, y1, x2, y2):
        blk.add_line((x1, y1), (x2, y2), dxfattribs=attr)

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


def write_figure(fig, msp, origin=(0.0, 0.0)):
    """Figure 1 枚をモデル空間へ書く. origin は図の置き場所のずらし."""
    from ezdxf.enums import TextEntityAlignment
    doc = msp.doc
    ox, oy = origin
    n = fig.scale

    def mv(p):
        return (p[0] + ox, p[1] + oy)

    for p in fig.prims:
        if isinstance(p, Poly):
            attrs = {'layer': _layer(doc, p.layer)}
            lt = _linetype(doc, p.ltype, n)
            if lt:
                attrs['linetype'] = lt
            msp.add_lwpolyline([mv(q) for q in p.points], close=p.closed, dxfattribs=attrs)
        elif isinstance(p, Line):
            attrs = {'layer': _layer(doc, p.layer)}
            lt = _linetype(doc, p.ltype, n)
            if lt:
                attrs['linetype'] = lt
            msp.add_line(mv(p.p1), mv(p.p2), dxfattribs=attrs)
        elif isinstance(p, Circle):
            msp.add_circle(mv(p.center), p.r, dxfattribs={'layer': _layer(doc, p.layer)})
        elif isinstance(p, Text):
            t = msp.add_text(dxf_text(p.text), dxfattribs={
                'layer': _layer(doc, p.layer), 'height': p.height, 'rotation': p.rotation,
                'style': TEXT_STYLE[0]})
            t.set_placement(mv(p.pos), align=getattr(TextEntityAlignment, _ALIGN[p.align]))
        elif isinstance(p, Hatch):
            h = msp.add_hatch(dxfattribs={'layer': _layer(doc, p.layer)})
            h.set_pattern_fill('ANSI31', scale=p.spacing / ANSI31_SPACING)
            h.paths.add_polyline_path([mv(q) for q in p.points], is_closed=True)
        elif isinstance(p, SectionRef):
            msp.add_blockref(_section_block(doc, p), mv(p.pos), dxfattribs={
                'layer': _layer(doc, p.layer), 'rotation': p.rotation})
        elif isinstance(p, NameRef):
            msp.add_blockref(_name_block(doc, p), mv(p.pos), dxfattribs={
                'layer': _layer(doc, p.layer), 'rotation': p.rotation})
        elif isinstance(p, RebarRef):
            name = _rebar_block(doc, p.dia, p.r, p.layer)
            msp.add_blockref(name, mv(p.pos), dxfattribs={'layer': _layer(doc, p.layer)})


def _layout(figs):
    """1 ファイルでの図の置き場所 (struct_cad と同じ). → 各図の origin のリスト.

    伏図: 全階同じ原点・同じ幅で横一列 (ピッチ = 全体の幅 + max(4m, 幅の 1 割))。
    軸組図: 同じ向きの構面は共通の原点で左をそろえ、横一列 (ピッチ = 最大の幅 + max(4m, 1 割))。
    部材リスト: 横一列 (図の幅 + 間隔)。
    """
    if not figs:
        return []
    kind = figs[0].kind
    if kind == 'plan':
        x0 = min(f.frame_box[0] for f in figs)
        y0 = min(f.frame_box[1] for f in figs)
        w = max(f.frame_box[2] for f in figs) - x0
        pitch = w + max(4000.0, 0.1 * w)
        return [(i * pitch - x0, -y0) for i in range(len(figs))]
    if kind == 'axis':
        u0 = {}
        spans = []
        for f in figs:
            u0[f.dirkey] = min(u0.get(f.dirkey, f.frame_box[0]), f.frame_box[0])
            spans.append(f.frame_box[2] - f.frame_box[0])
        mx = max(spans)
        pitch = mx + max(4000.0, 0.1 * mx)
        return [(i * pitch - (u0[f.dirkey] if f.dirkey else f.frame_box[0]), 0.0)
                for i, f in enumerate(figs)]
    out, x = [], 0.0
    for f in figs:
        bx0, by0, bx1, _by1 = f.bounds
        out.append((x - bx0, -by0))
        x += (bx1 - bx0) + 0.15 * max(bx1 - bx0, 1000.0) + 20.0 * f.scale
    return out


def export(figs, out_dir, name):
    """Figure の列 (同じ種類・同じ縮尺) → 1 つの DXF <name>.dxf. 戻り値: (ファイル, [{title, scale}])."""
    os.makedirs(out_dir, exist_ok=True)
    doc = new_doc()
    msp = doc.modelspace()
    for fig, origin in zip(figs, _layout(figs)):
        write_figure(fig, msp, origin)
    out = os.path.join(out_dir, name.replace('/', '_').replace('\\', '_') + '.dxf')
    doc.saveas(out)
    return out, [{'title': f.title, 'scale': f.scale} for f in figs]
