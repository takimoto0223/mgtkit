# -*- coding: utf-8 -*-
"""Drawing IR — 図 1 枚分の図形 (DXF とプレビューが同じものを描く).

座標は原寸 mm。文字高さ・記号の大きさは作図の時点で「紙面 mm × 縮尺」にしてある。
render/dxf.py と render/preview.py はこの型だけを見る。
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


@dataclass
class Line:
    p1: Point
    p2: Point
    layer: str
    ltype: Optional[str] = None


@dataclass
class Circle:
    center: Point
    r: float
    layer: str


@dataclass
class Text:
    pos: Point
    text: str
    height: float
    layer: str
    align: str = 'BC'        # 'BC'=下中央 / 'MC'=中央 / 'BL'=左下
    rotation: float = 0.0


@dataclass
class Hatch:
    """斜線ハッチ (ANSI31). spacing = 線の間隔 [mm 実寸]. VW10J は読まない (ほかの CAD 向け)."""
    points: List[Point]
    layer: str
    spacing: float


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


@dataclass
class NameRef:
    """符号 (ブロック <符号>_NAME1、中身は中央揃えの文字 1 つ). struct_cad の NameInsert と同じ."""
    block: str
    text: str
    pos: Point
    height: float
    rotation: float
    layer: str


@dataclass
class RebarRef:
    """鉄筋記号 (径別のブロック). r = 記号の半径 [mm 実寸]."""
    dia: int
    pos: Point
    r: float
    layer: str


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
