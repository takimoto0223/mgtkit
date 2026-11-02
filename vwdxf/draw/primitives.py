# -*- coding: utf-8 -*-
"""Drawing IR — 図 1 枚分の図形 (DXF とプレビューが同じものを描く).

座標は原寸 mm。文字高さ・記号の大きさは作図の時点で「紙面 mm × 縮尺」にしてある。
render/dxf.py と render/preview.py はこの型だけを見る。
各図形の tag は部材 ID などの印 (dict)。DXF では XDATA (アプリ名 STRUCTURAL_CAD) になり、
見た目には関わらない (2026-10-13 追加、structural-cad 向け)。
"""
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from ..style import (DASHED_SUFFIX, LAYER_DOWN_COLUMN, LAYER_GRID, LAYER_HOOP, LAYER_MEMBER,
                     LAYER_SECTION, LAYER_TEXT, LAYER_TITLE, LAYER_TRUSS, LAYER_WALL,
                     LAYER_WALL_ELEV)

Point = Tuple[float, float]

# 線種: None=実線 / 'grid'=通り芯・レベル線の一点鎖線 / 'dashed'=破線 (木造柱伏図の梁)
# ('grid' と 'dashed' は縮尺ごとに紙面寸法を焼き込んだ線種になる。DXF 標準の DASHED は
#  周期 1.5mm 程度で縮尺が入らず、VW10J ではほぼ実線に見えるので使わない)


@dataclass
class Poly:
    points: List[Point]
    layer: str
    closed: bool = True
    ltype: Optional[str] = None
    tag: Optional[dict] = None


@dataclass
class Line:
    p1: Point
    p2: Point
    layer: str
    ltype: Optional[str] = None
    tag: Optional[dict] = None


@dataclass
class Circle:
    center: Point
    r: float
    layer: str
    tag: Optional[dict] = None


@dataclass
class Text:
    pos: Point
    text: str
    height: float
    layer: str
    align: str = 'BC'        # 'BC'=下中央 / 'MC'=中央 / 'BL'=左下
    rotation: float = 0.0
    tag: Optional[dict] = None


@dataclass
class Hatch:
    """斜線ハッチ (ANSI31). spacing = 線の間隔 [mm 実寸]. VW10J は読まない (ほかの CAD 向け)."""
    points: List[Point]
    layer: str
    spacing: float
    tag: Optional[dict] = None


@dataclass
class SectionRef:
    """断面記号 (ブロック <符号>_section / 下柱は <符号>_section2).

    基点 = 断面の中心、局所 X = せい・局所 Y = 幅 (struct_cad と同じ)、rotation で向きを合わせる。
    sec は Section (形は sections.symbol_shape)。down=True は下柱 (×印のみ)。
    """
    name: str
    sec: Any
    pos: Point
    rotation: float
    layer: str
    down: bool = False
    tag: Optional[dict] = None


@dataclass
class NameRef:
    """符号 (ブロック <符号>_NAME1、中身は中央揃えの文字 1 つ). struct_cad の NameInsert と同じ."""
    block: str
    text: str
    pos: Point
    height: float
    rotation: float
    layer: str
    tag: Optional[dict] = None


@dataclass
class RebarRef:
    """鉄筋記号 (径別のブロック). r = 記号の半径 [mm 実寸]."""
    dia: int
    pos: Point
    r: float
    layer: str
    tag: Optional[dict] = None


@dataclass
class Figure:
    kind: str                 # 'axis' / 'plan' / 'list'
    key: str                  # 通り・フロア・区分のキー
    title: str
    scale: int
    bounds: Tuple[float, float, float, float]   # 図形の範囲 (見出しを含む)
    prims: list = field(default_factory=list)
    sheet: Optional[str] = None                  # 木造の伏図: 'beam' / 'column' / 'full'
    frame_box: Optional[Tuple[float, float, float, float]] = None   # 並べるときの基準範囲 [mm]
    dirkey: Optional[str] = None                 # 軸組図の向き 'x' / 'y' / None (斜め)
    fig_id: str = ''                             # 図の ID (XDATA の FIG・付属 JSON の figures[].id)
    meta: dict = field(default_factory=dict)     # 付属 JSON 用 (grids / levels / members / origin)


# ---------------------------------------------------------------------------
# 部材 ID の印 (XDATA STRUCTURAL_CAD の KEY=VALUE。仕様は vwdxf/README.md「部材 ID と付属 JSON」)
# ---------------------------------------------------------------------------

def figure_id(kind, name, variant=None, page=None):
    """図の ID. XDATA の FIG と付属 JSON の figures[].id はここだけで作る.

    伏図 plan:<床名>[:beam|:column] (木造の 1 枚もの 'full' は付けない) /
    軸組図 axis:<通り名> / 部材リスト list:<題名>[:<頁>] (複数ページのときだけ頁)。
    """
    parts = [kind, str(name)]
    if variant and variant != 'full':
        parts.append(str(variant))
    if page:
        parts.append(str(page))
    return ':'.join(parts)


def member_kind(m):
    """KIND の値: トラス要素は 'truss'、それ以外は column / beam / brace."""
    return 'truss' if m['truss'] else m['kind']


def member_tag(m, role, **extra):
    """線材 m (members.py の member) の印. MID = 代表の要素番号、EL = 結合した要素 (2 つ以上のとき)."""
    tag = {'MID': m['elem']}
    if len(m['eles']) > 1:
        tag['EL'] = ','.join(str(e) for e in m['eles'])
    tag['KIND'] = member_kind(m)
    if m['code']:
        tag['CODE'] = m['code']
    tag['SEC'] = m['sec_name']
    tag['ROLE'] = role
    tag.update(extra)
    return tag


def wall_tag(el_id, name, role, **extra):
    """面要素の壁の印. MID = 面要素の要素番号、CODE = 板厚の名前."""
    tag = {'MID': int(el_id), 'KIND': 'wall'}
    if name:
        tag['CODE'] = name
    tag['ROLE'] = role
    tag.update(extra)
    return tag


def stamp(prims, fig_id):
    """図の全図形に FIG を入れる. 印の無い図形は ROLE=other."""
    for p in prims:
        if p.tag is None:
            p.tag = {'ROLE': 'other'}
        p.tag.setdefault('ROLE', 'other')
        p.tag['FIG'] = fig_id
    return prims


def prims_bounds(prims):
    """図形の範囲 [mm] (文字は位置 ± 高さ×文字数の目安、断面記号はせい・幅の大きい方)."""
    xs, ys = [], []
    for p in prims:
        if isinstance(p, (Poly, Hatch)):
            xs += [q[0] for q in p.points]
            ys += [q[1] for q in p.points]
        elif isinstance(p, Line):
            xs += [p.p1[0], p.p2[0]]
            ys += [p.p1[1], p.p2[1]]
        elif isinstance(p, Circle):
            xs += [p.center[0] - p.r, p.center[0] + p.r]
            ys += [p.center[1] - p.r, p.center[1] + p.r]
        elif isinstance(p, RebarRef):
            xs += [p.pos[0] - p.r, p.pos[0] + p.r]
            ys += [p.pos[1] - p.r, p.pos[1] + p.r]
        elif isinstance(p, (Text, NameRef)):
            w = 0.5 * p.height * max(len(p.text), 1)
            xs += [p.pos[0] - w, p.pos[0] + w]
            ys += [p.pos[1] - p.height, p.pos[1] + p.height]
        elif isinstance(p, SectionRef):
            from ..sections import depth, width
            r = 500.0 * max(depth(p.sec), width(p.sec), 0.001)
            xs += [p.pos[0] - r, p.pos[0] + r]
            ys += [p.pos[1] - r, p.pos[1] + r]
    if not xs:
        return (0.0, 0.0, 1.0, 1.0)
    return (min(xs), min(ys), max(xs), max(ys))


def layer_color(layer):
    """レイヤ名 → ACI 色 (DXF のレイヤ定義とプレビューの色に使う)."""
    table = dict(list(LAYER_MEMBER.values()) + list(LAYER_SECTION.values())
                 + [LAYER_DOWN_COLUMN, LAYER_TEXT, LAYER_TITLE, LAYER_GRID, LAYER_WALL,
                    LAYER_WALL_ELEV, LAYER_TRUSS, LAYER_HOOP])
    if layer in table:
        return table[layer]
    if layer.endswith(DASHED_SUFFIX):
        return table.get(layer[:-len(DASHED_SUFFIX)], 7)
    return 7
