"""Model IR — 構造モデルの語彙（葉モジュール／他に依存しない）。

tools/struct_cad_py の model.py を移植 (2026-09-19)。変更点: 部材端の解放フラグ文字列
frame_flags を持つ (ピン判定は構造図βと同じ「My か Mz の解放」で行うため)。

MIDAS の用語だけを持ち、図形や DXF の概念は一切持たない。
READ ステージの出力であり、DRAW ステージの入力になる。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Node:
    """節点。座標は mgt の単位系（サンプルは m）そのまま。"""
    id: int
    x: float
    y: float
    z: float

    @property
    def xyz(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)


@dataclass
class Element:
    """要素。nodes は節点ID列（フレーム/トラス=2, プレート=3〜4）。"""
    id: int
    type: str          # BEAM / TRUSS / TENSTR / PLATE ...
    mat: int           # 材料番号 iMAT
    prop: int          # 断面番号 iPRO
    nodes: list[int]
    angle: float = 0.0  # ANGLE(β角)[deg]。断面の部材軸まわり回転。面要素は無し
    # 部材の役柄。幾何では判別できない区別だけを持つ（大梁と小梁は同じ水平材）。
    # ""=不明/大梁 / "小梁"。READ が分かる場合だけ埋める。
    role: str = ""

    @property
    def is_line(self) -> bool:
        """2節点要素（線で描くもの）か。"""
        return len(self.nodes) == 2


@dataclass
class Section:
    """断面。dims は SHAPE 依存の寸法列（単位は mgt 準拠＝m）。

    shape: 'H' / 'SB'(中実角) / 'BOX' / 'P'(鋼管) / 'SR'(中実丸) /
           'C' / 'T' / 'L' / 'SRC' / 'TAPERED' / 'OTHER'
    sec_type: 'DBUSER' / 'VALUE' / 'SRC' / 'TAPERED' / 'COMBINED'
    """
    id: int
    shape: str
    name: str
    dims: list[float] = field(default_factory=list)
    sec_type: str = "DBUSER"
    # テーパー断面のj端寸法（None=一様断面。dims が i端側になる）
    dims_j: list[float] | None = None
    # SRC断面の内蔵鋼材寸法 [H, B, tw, tf]（None=非SRC）。dims はコンクリート外形
    steel_dims: list[float] | None = None
    # 断面の実名（SS7 の登録形状名 'H-250x250x9x14x13' など）。name が符号(C1)の
    # ときに、寸法そのものを表す名前を別に持ちたい場合に使う。空なら未設定。
    label: str = ""

    @property
    def is_taper(self) -> bool:
        return self.dims_j is not None


@dataclass
class Material:
    """材料。category は S/RC/SRC/W/OTHER。木材のみ species/grade を持つ。"""
    id: int
    type: str          # *MATERIAL の TYPE（STEEL/CONC/SRC/USER/…）
    name: str          # MNAME
    category: str = "OTHER"
    species: str = ""  # 木材の材種（W-<材種>-<等級>_<補足> の <材種>）
    grade: str = ""    # 木材の等級（<等級>）


@dataclass
class RebarColumn:
    """RC柱の配筋（*REBAR-COLUMN）。径は mm、ピッチ/かぶりは m。"""
    sec_id: int
    main_dia: int        # 主筋径 RBNAME
    main_total: int      # 主筋総本数 iNQRB
    n_row: int           # 深さ方向の段数 iNROW
    n_width: int         # 幅方向本数 = total/2 + 2 - n_row
    cover: float         # かぶり DO [m]
    hoop_dia: int        # せん断補強筋径 SRBNAME
    hoop_pitch: float    # HOOPピッチ SPACE [m]
    hoop_x: int = 2      # x方向せん断補強筋本数 iSRBN1
    hoop_y: int = 2      # y方向せん断補強筋本数 iSRBN2


@dataclass
class RebarBeam:
    """RC梁の配筋（*REBAR-BEAM, 代表としてi端位置を採用）。径mm・寸法m。"""
    sec_id: int
    stir_dia: int            # あばら筋径 SBARNAME
    cover_top: float         # 上かぶり DT [m]
    cover_bot: float         # 下かぶり DB [m]
    top_rb1: int = 0         # 上端 1段目本数
    top_rb2: int = 0         # 上端 2段目本数
    top_dia: int = 0         # 上端筋径
    bot_rb1: int = 0
    bot_rb2: int = 0
    bot_dia: int = 0
    stir_legs: int = 2       # あばら筋 脚数 iISRBN
    stir_pitch: float = 0.0  # あばら筋ピッチ ISPACE [m]
    side_num: int = 0        # 腹筋本数 iSIDENUM
    side_dia: int = 0        # 腹筋径 SIDEBAR


@dataclass
class Group:
    """*GROUP（通り芯）。node_ids/elem_ids は by/to 展開済み。"""
    name: str
    node_ids: list[int] = field(default_factory=list)
    elem_ids: list[int] = field(default_factory=list)


@dataclass
class Footing:
    """基礎フーチング。節点位置に width_x×width_y[m] の矩形として伏図に描く。"""
    node_id: int
    width_x: float
    width_y: float
    name: str = ""


@dataclass
class Model:
    """解析モデル全体（Model IR の本体）。"""
    nodes: dict[int, Node] = field(default_factory=dict)
    elements: list[Element] = field(default_factory=list)
    sections: dict[int, Section] = field(default_factory=dict)
    groups: dict[str, Group] = field(default_factory=dict)
    # *MEMBER（結合部材）: 各要素は結合される要素ID列（先頭=代表 ELEM）
    members: list[list[int]] = field(default_factory=list)
    materials: dict[int, "Material"] = field(default_factory=dict)  # mat_id -> Material
    thicknesses: dict[int, float] = field(default_factory=dict)     # 板厚id -> 厚[m]
    thickness_names: dict[int, str] = field(default_factory=dict)   # 板厚id -> 名称(符号)
    # RC柱配筋: sec_id -> RebarColumn
    rebar_columns: dict[int, RebarColumn] = field(default_factory=dict)
    # RC梁配筋: sec_id -> RebarBeam
    rebar_beams: dict[int, "RebarBeam"] = field(default_factory=dict)
    # 部材端の解放: ele_id -> (i端解放, j端解放)。解放=ピン接合。
    releases: dict[int, tuple[bool, bool]] = field(default_factory=dict)
    # *FRAME-RLS の解放フラグ: ele_id -> (i端 'Fx Fy Fz Mx My Mz' 6桁, j端 6桁)
    frame_flags: dict[int, tuple[str, str]] = field(default_factory=dict)
    # マージ後の要素が通過する全節点: ele_id -> 節点ID列（内部吸収した節点を含む）。
    # 端部処理で「線上を通過する相手部材」を隣接として拾うために使う。
    covered_nodes: dict[int, list[int]] = field(default_factory=dict)
    footings: list["Footing"] = field(default_factory=list)   # 基礎フーチング（STB由来）
    # 伏図の既定スタイル。"wood"=最上階以外2枚 / "steel"=各階1枚(柱梁符号・梁実線)。
    # READ が入力形式に応じて設定（mgt=wood / stb=steel）。GUIのmodeより優先される。
    floor_mode: str = "wood"
    unit_length: str = "M"   # *UNIT 由来。MVPでは記録のみ
    # *UNIT の長さ単位 → メートル換算係数（M=1.0 / MM=0.001…）。読込時に
    # 全長さ量をこの係数でメートルへ正規化済み（記録用）。
    length_unit_scale: float = 1.0

    def node(self, nid: int) -> Node | None:
        return self.nodes.get(nid)

    def section(self, sid: int) -> Section | None:
        return self.sections.get(sid)

    def element_section(self, el: "Element") -> "Section | None":
        return self.sections.get(el.prop)

    def section_material(self, sec_id: int) -> "Material | None":
        """断面番号を使う要素の材料。無ければ None。"""
        for e in self.elements:
            if e.prop == sec_id and e.mat in self.materials:
                return self.materials[e.mat]
        return None

    def section_material_name(self, sec_id: int) -> str:
        m = self.section_material(sec_id)
        return m.name if m else ""

    def section_category(self, sec_id: int) -> str:
        """断面のカテゴリ S/RC/SRC/W/OTHER（材料から継承）。"""
        m = self.section_material(sec_id)
        return m.category if m else "OTHER"

    # --- 簡易統計（検証・ログ用） ---
    def stats(self) -> dict[str, int]:
        by_type: dict[str, int] = {}
        for e in self.elements:
            by_type[e.type] = by_type.get(e.type, 0) + 1
        return {
            "nodes": len(self.nodes),
            "elements": len(self.elements),
            "sections": len(self.sections),
            **by_type,
        }
