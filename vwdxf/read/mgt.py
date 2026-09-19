"""mgt パーサ。

方針:
  1) split_blocks(): '*見出し' 単位でブロックに分割する汎用処理。
     コメント行(';' 始まり)と空行は落とす。
  2) 各ブロック(node/element/...)を Model へ流し込む。

MVP(Step0-1) では NODE と ELEMENT のみ対応。
他のセクション(section/material/group/...)は Step 以降で追加する。
mgt は Shift-JIS(cp932) 前提。
"""
from __future__ import annotations

from ..model import Element, Model, Node

# フレーム/トラス系（2節点）として扱う要素タイプ
_LINE_TYPES = {"BEAM", "TRUSS", "TENSTR", "COMPTR", "CABLE"}
# 面要素（3〜4節点）として扱う要素タイプ
_PLANAR_TYPES = {"PLATE", "WALL", "PLANE"}


def split_blocks(path: str, encoding: str = "cp932") -> dict[str, list[str]]:
    """mgt を '*見出し' → データ行リスト の辞書に分割する。

    見出しは大文字化してキーにする（例: 'NODE', 'ELEMENT'）。
    コメント行(';')・空行は除外。行末コメントも切り落とす。
    """
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    with open(path, "r", encoding=encoding, errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n").rstrip("\r")
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("*"):
                # 新しいブロック見出し
                name = stripped[1:].split(";")[0].split()[0].upper()
                current = name
                blocks.setdefault(current, [])
                continue
            if stripped.startswith(";"):
                continue  # コメント行
            if current is None:
                continue
            # 行末コメント(';' 以降)を除去
            data = line.split(";")[0].strip()
            if data:
                blocks[current].append(data)
    return blocks


def _split_fields(line: str) -> list[str]:
    return [f.strip() for f in line.split(",")]


# mgt の長さ単位 → メートル換算係数。内部は常にメートルに正規化する。
_UNIT_TO_M = {
    "M": 1.0, "MM": 0.001, "CM": 0.01, "KM": 1000.0,
    "FT": 0.3048, "IN": 0.0254, "INCH": 0.0254,
}


def parse_length_unit(lines: list[str]) -> float:
    """*UNIT ブロックの LENGTH（2番目のフィールド）→ メートル換算係数。

    例: 'KN, M, KJ, C' → 1.0 / 'KN, MM, KJ, C' → 0.001。不明時は 1.0(=m)。
    """
    for ln in lines:
        f = _split_fields(ln)
        if len(f) >= 2:
            return _UNIT_TO_M.get(f[1].upper(), 1.0)
    return 1.0


def _apply_unit_scale(model: Model, s: float) -> None:
    """モデル内の全ての長さ量を s 倍してメートルへ正規化する。

    節点座標・断面寸法・板厚・かぶり/ピッチが対象。鉄筋径(D13等)は
    公称mmで単位系に依らないためスケールしない。
    """
    if s == 1.0:
        return
    for n in model.nodes.values():
        n.x, n.y, n.z = n.x * s, n.y * s, n.z * s
    for sec in model.sections.values():
        sec.dims = [d * s for d in sec.dims]
        if sec.dims_j is not None:
            sec.dims_j = [d * s for d in sec.dims_j]
        if sec.steel_dims is not None:
            sec.steel_dims = [d * s for d in sec.steel_dims]
    for k in list(model.thicknesses):
        model.thicknesses[k] *= s
    for rc in model.rebar_columns.values():
        rc.cover *= s
        rc.hoop_pitch *= s
    for rb in model.rebar_beams.values():
        rb.cover_top *= s
        rb.cover_bot *= s
        rb.stir_pitch *= s


def _to_num(s: str):
    try:
        return float(s)
    except ValueError:
        return None


def parse_nodes(lines: list[str], model: Model) -> None:
    """NODE ブロック: iNO, X, Y, Z"""
    for ln in lines:
        f = _split_fields(ln)
        if len(f) < 4:
            continue
        try:
            nid = int(float(f[0]))
            x, y, z = float(f[1]), float(f[2]), float(f[3])
        except ValueError:
            continue
        model.nodes[nid] = Node(nid, x, y, z)


def parse_elements(lines: list[str], model: Model) -> None:
    """ELEMENT ブロック。タイプにより節点列の位置が変わる。

    フレーム/トラス: iEL, TYPE, iMAT, iPRO, iN1, iN2, ...
    面要素        : iEL, TYPE, iMAT, iPRO, iN1, iN2, iN3, iN4, ...
    """
    for ln in lines:
        f = _split_fields(ln)
        if len(f) < 6:
            continue
        try:
            eid = int(float(f[0]))
        except ValueError:
            continue
        etype = f[1].upper()
        try:
            mat = int(float(f[2]))
            prop = int(float(f[3]))
        except ValueError:
            mat = prop = 0

        angle = 0.0
        if etype in _PLANAR_TYPES:
            raw_nodes = f[4:8]
        else:  # 2節点（フレーム/トラス/未知タイプ）: 7番目が ANGLE(β角)
            raw_nodes = f[4:6]
            if len(f) > 6:
                a = _to_num(f[6])
                if a is not None:
                    angle = a

        nodes: list[int] = []
        for s in raw_nodes:
            try:
                n = int(float(s))
            except ValueError:
                continue
            if n > 0:  # 0 は「節点なし」（三角形プレート等）
                nodes.append(n)

        if len(nodes) >= 2:
            model.elements.append(Element(eid, etype, mat, prop, nodes, angle))


def read_mgt(path: str, encoding: str | None = None) -> Model:
    """mgt ファイルを読み Model(IR) を返す。

    対応ブロック: NODE / ELEMENT / SECTION（Step2で SECTION 追加）。
    """
    from .frame_rls import parse_frame_rls  # 局所importで循環回避
    from .group import parse_groups
    from .material import parse_materials
    from .member import parse_members
    from .rebar import parse_rebar_beams, parse_rebar_columns
    from .section import parse_sections
    from .thickness import parse_thickness

    from . import detect_encoding
    blocks = split_blocks(path, encoding=detect_encoding(path, encoding))
    model = Model()
    # 長さ単位を判定（mm/cm/m 等どれでも可）。内部は常にメートルへ正規化する。
    unit_scale = parse_length_unit(blocks["UNIT"]) if "UNIT" in blocks else 1.0
    model.length_unit_scale = unit_scale
    if "NODE" in blocks:
        parse_nodes(blocks["NODE"], model)
    if "ELEMENT" in blocks:
        parse_elements(blocks["ELEMENT"], model)
    if "MATERIAL" in blocks:
        parse_materials(blocks["MATERIAL"], model)
    if "SECTION" in blocks:
        parse_sections(blocks["SECTION"], model)
    if "THICKNESS" in blocks:
        parse_thickness(blocks["THICKNESS"], model)
    if "GROUP" in blocks:
        parse_groups(blocks["GROUP"], model)
    if "MEMBER" in blocks:
        parse_members(blocks["MEMBER"], model)
    if "FRAME-RLS" in blocks:
        parse_frame_rls(blocks["FRAME-RLS"], model)
    if "REBAR-COLUMN" in blocks:
        parse_rebar_columns(blocks["REBAR-COLUMN"], model)
    if "REBAR-BEAM" in blocks:
        parse_rebar_beams(blocks["REBAR-BEAM"], model)
    _apply_unit_scale(model, unit_scale)   # 全長さ量をメートルへ正規化
    return model
