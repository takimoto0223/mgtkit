# -*- coding: utf-8 -*-
"""mgt / mgtx の荷重ブロックを読む (mgt.py に無いため新規).

mgt.py の mgtopen_* は *NODE / *ELEMENT のように「見出しが1つだけ」の
セクションを前提にしている。荷重は

    *USE-STLD, 1F
    *PRESSURE
    ...
    *USE-STLD, ST
    *CONLOAD

のように **同じ見出しが荷重ケースごとに何度も現れる**ため、直前の
*USE-STLD を覚えながら読む専用のスキャナが要る。

対応ブロック:
    *STLDCASE   静的荷重ケースの定義
    *FLOADTYPE  床荷重タイプ (タイプ名 -> 荷重ケース名と荷重値)
    *FLOORLOAD  床荷重の割り当て (領域＝節点番号の並び)
    *PRESSURE   面荷重 (プレート要素)
    *CONLOAD    節点荷重
    *BEAMLOAD   部材荷重 (等分布・集中)

注意: **床荷重がどの荷重ケースに属するかは *USE-STLD では決まらない。**
*FLOORLOAD はファイル末尾にまとめて置かれ、所属は床荷重タイプ
(*FLOADTYPE の2行目 LCNAME) が持っている。*PRESSURE と *CONLOAD は逆に
*USE-STLD で決まる。
"""
from __future__ import annotations

from dataclasses import dataclass, field


# --- データ構造 -------------------------------------------------------------

@dataclass
class LoadCase:
    """*STLDCASE の1行。"""
    name: str
    type: str = "USER"
    desc: str = ""


@dataclass
class FloorLoadType:
    """*FLOADTYPE の1エントリ。

    items は (荷重ケース名, 荷重値, 自重加算) の並び（最大8）。
    """
    name: str
    desc: str = ""
    items: list[tuple[str, float, bool]] = field(default_factory=list)

    @property
    def case_names(self) -> list[str]:
        return [c for c, _v, _s in self.items]

    def value_of(self, case: str) -> float | None:
        for c, v, _s in self.items:
            if c == case:
                return v
        return None


@dataclass
class FloorLoad:
    """*FLOORLOAD の1行（床荷重の割り当て領域）。

    seq は MIDAS 画面のラベル "(3)-RF2(One)" の (3) にあたる通し番号。
    読込後に assign_floor_load_numbers() が付ける。
    """
    type_name: str
    dist: int                    # 1=One Way / 2=Two Way / 3=Polygon-Centroid / 4=Polygon-Length
    nodes: list[int]
    direction: str = "GZ"
    angle: float = 0.0
    projected: bool = False
    desc: str = ""
    group: str = ""
    seq: int = 0

    #: iDIST → MIDAS のラベル表記
    _DIST_LABEL = {1: "One", 2: "Two", 3: "Centroid", 4: "Length"}

    @property
    def dist_label(self) -> str:
        return self._DIST_LABEL.get(self.dist, str(self.dist))

    def label(self) -> str:
        """MIDAS 画面と同じ表示文字列 "(3)-RF2(One)"。"""
        return f"({self.seq})-{self.type_name}({self.dist_label})"


@dataclass
class PressureLoad:
    """*PRESSURE の1行（面要素への圧力）。

    pu は一様圧力、p1..p4 は節点ごとに値を変えるときの4点値。
    """
    case: str
    elements: list[int]
    etype: str = "PLATE"        # PLATE / PLANE / SOLID
    ltype: str = "FACE"
    direction: str = "GZ"
    pu: float = 0.0
    p: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    projected: bool = False
    group: str = ""

    @property
    def value(self) -> float:
        """代表値。一様圧力があればそれ、無ければ4点値の平均。"""
        if self.pu:
            return self.pu
        vals = [v for v in self.p if v]
        return sum(vals) / len(vals) if vals else 0.0


@dataclass
class BeamLoad:
    """*BEAMLOAD の1行（部材荷重）。

    points は (位置, 荷重値) の並び。UNILOAD/TRAPEZOIDAL なら位置は
    材長比 0〜1、CONLOAD なら集中荷重の位置。
    """
    case: str
    elements: list[int]
    cmd: str = "BEAM"
    type: str = "UNILOAD"          # UNILOAD / CONLOAD / UNIMOMENT / ...
    direction: str = "GZ"
    projected: bool = False
    points: list[tuple[float, float]] = field(default_factory=list)
    group: str = ""

    @property
    def is_moment(self) -> bool:
        return "MOMENT" in self.type.upper()

    @property
    def values(self) -> list[float]:
        """0 でない荷重値。"""
        return [p for _d, p in self.points if p]

    @property
    def value(self) -> float:
        """代表値（絶対値が最大のもの）。"""
        vals = self.values
        return max(vals, key=abs) if vals else 0.0

    def span(self) -> tuple[float, float]:
        """荷重が載る範囲（材長比）。等分布でない場合も端から端で返す。"""
        ds = [d for d, p in self.points if p] or [0.0, 1.0]
        return (min(ds), max(ds))


@dataclass
class NodalLoad:
    """*CONLOAD の1行（節点荷重）。"""
    case: str
    nodes: list[int]
    fx: float = 0.0
    fy: float = 0.0
    fz: float = 0.0
    mx: float = 0.0
    my: float = 0.0
    mz: float = 0.0
    group: str = ""


@dataclass
class LoadSet:
    """1モデル分の荷重データ。"""
    cases: list[LoadCase] = field(default_factory=list)
    floor_types: list[FloorLoadType] = field(default_factory=list)
    floor_loads: list[FloorLoad] = field(default_factory=list)
    pressures: list[PressureLoad] = field(default_factory=list)
    nodal_loads: list[NodalLoad] = field(default_factory=list)
    beam_loads: list[BeamLoad] = field(default_factory=list)

    # --- 参照 ---
    def floor_type(self, name: str) -> FloorLoadType | None:
        for t in self.floor_types:
            if t.name == name:
                return t
        return None

    def case_of_floor_type(self, name: str) -> str:
        """床荷重タイプが属する荷重ケース名（先頭のもの）。"""
        t = self.floor_type(name)
        if t and t.items:
            return t.items[0][0]
        return name

    def floor_loads_of(self, case: str) -> list[FloorLoad]:
        return [f for f in self.floor_loads
                if case in (self.floor_type(f.type_name).case_names
                            if self.floor_type(f.type_name) else [f.type_name])]

    def pressures_of(self, case: str) -> list[PressureLoad]:
        return [p for p in self.pressures if p.case == case]

    def nodal_loads_of(self, case: str) -> list[NodalLoad]:
        return [n for n in self.nodal_loads if n.case == case]

    def beam_loads_of(self, case: str) -> list[BeamLoad]:
        return [b for b in self.beam_loads if b.case == case]

    def has_load(self, case: str) -> bool:
        return bool(self.floor_loads_of(case)
                    or self.pressures_of(case)
                    or self.nodal_loads_of(case)
                    or self.beam_loads_of(case))

    def cases_with_load(self) -> list[str]:
        """荷重が1つでも付いている荷重ケース名（*STLDCASE の定義順）。"""
        return [c.name for c in self.cases if self.has_load(c.name)]

    def stats(self) -> dict[str, int]:
        return {
            "cases": len(self.cases),
            "floor_types": len(self.floor_types),
            "floor_loads": len(self.floor_loads),
            "pressures": len(self.pressures),
            "nodal_loads": len(self.nodal_loads),
            "beam_loads": len(self.beam_loads),
        }


# --- スキャナ ---------------------------------------------------------------

def _iter_records(path: str, encoding: str = "cp932"):
    """(ブロック名, 荷重ケース名, データ行) を順に返す。

    - 継続行（行末 '\\'）は連結する
    - コメント（';' 以降）と空行は落とす
    - *USE-STLD, <名前> は「以降のブロックが属する荷重ケース」を切り替える
    """
    block: str | None = None
    case: str = ""
    pending = ""
    with open(path, "r", encoding=encoding, errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n").rstrip("\r")
            stripped = line.strip()
            if not stripped or stripped.startswith(";"):
                continue
            if stripped.startswith("*"):
                head = stripped[1:]
                name = head.split(",")[0].split(";")[0].strip().upper()
                if name == "USE-STLD":
                    case = head.split(",", 1)[1].split(";")[0].strip() if "," in head else ""
                    continue
                block = name
                pending = ""
                continue
            if block is None:
                continue
            data = line.split(";")[0].rstrip()
            if not data.strip():
                continue
            if data.rstrip().endswith("\\"):          # 継続行
                pending += data.rstrip()[:-1]
                continue
            yield block, case, (pending + data).strip()
            pending = ""


def _fields(line: str) -> list[str]:
    return [f.strip() for f in line.split(",")]


def _num(s: str, default: float = 0.0) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def expand_id_list(text: str) -> list[int]:
    """MIDAS の番号リスト表記を展開する。

    "1to5 7 9to11 129to243by38" -> [1,2,3,4,5,7,9,10,11, ...]
    展開そのものは mgtkit の util.get_byto を使う (同じ処理を増やさない)。
    """
    from mgtkit.util import get_byto

    out: list[int] = []
    for tok in text.replace(",", " ").split():
        for v in get_byto(tok):
            if v == v:                      # NaN は捨てる
                out.append(int(v))
    return out


# --- 各ブロックのパーサ -----------------------------------------------------

def _parse_stldcase(line: str, ls: LoadSet) -> None:
    """; LCNAME, LCTYPE, DESC"""
    f = _fields(line)
    if not f or not f[0]:
        return
    ls.cases.append(LoadCase(f[0], f[1] if len(f) > 1 else "USER",
                             f[2] if len(f) > 2 else ""))


def _parse_floadtype(lines: list[str], ls: LoadSet) -> None:
    """2行で1エントリ。

    1行目: NAME, DESC
    2行目: LCNAME1, FLOAD1, bSBU1, ... （最大8組）
    """
    i = 0
    while i < len(lines):
        f = _fields(lines[i])
        name = f[0]
        desc = f[1] if len(f) > 1 else ""
        items: list[tuple[str, float, bool]] = []
        if i + 1 < len(lines):
            g = _fields(lines[i + 1])
            for k in range(0, len(g) - 1, 3):
                lc = g[k]
                if not lc:
                    continue
                val = _num(g[k + 1]) if k + 1 < len(g) else 0.0
                sbu = (g[k + 2].upper() == "YES") if k + 2 < len(g) else False
                items.append((lc, val, sbu))
        ls.floor_types.append(FloorLoadType(name, desc, items))
        i += 2


def _parse_floorload(line: str, ls: LoadSet) -> None:
    """iDIST により列構成が変わる。

    iDIST=1,2: LTNAME, iDIST, ANGLE, iSBEAM, SBANG, SBUW, DIR, bPROJ, DESC,
               bEX, bAL, GROUP, NODE1..NODEn
    iDIST=3,4: LTNAME, iDIST, DIR, bPROJ, DESC, GROUP, NODE1..NODEn
    """
    f = _fields(line)
    if len(f) < 3:
        return
    dist = int(_num(f[1], 1))
    if dist in (1, 2):
        head, direction, proj, desc, group = 12, f[6], f[7], f[8], f[11]
        angle = _num(f[2])
    else:
        head, direction, proj, desc, group = 6, f[2], f[3], f[4], f[5]
        angle = 0.0
    nodes = [int(_num(x)) for x in f[head:] if x.strip()]
    ls.floor_loads.append(FloorLoad(
        type_name=f[0], dist=dist, nodes=nodes, direction=direction,
        angle=angle, projected=proj.upper() == "YES", desc=desc, group=group,
    ))


def _parse_pressure(case: str, line: str, ls: LoadSet) -> None:
    """PLATE/FACE を主対象にする（他の型は要素番号と方向だけ拾う）。

    ELEM_LIST, CMD, ETYP, LTYP, DIR, VX, VY, VZ, bPROJ, PU, P1..P4, GROUP, PSLTKEY
    """
    f = _fields(line)
    if len(f) < 5:
        return
    etype = f[2].upper() if len(f) > 2 else "PLATE"
    ltype = f[3].upper() if len(f) > 3 else ""
    if etype == "PLATE" and ltype == "FACE":
        # ... DIR(4), VX,VY,VZ(5-7), bPROJ(8), PU(9), P1..P4(10-13), GROUP(14)
        direction = f[4]
        pu = _num(f[9]) if len(f) > 9 else 0.0
        p = tuple(_num(f[i]) if len(f) > i else 0.0 for i in (10, 11, 12, 13))
        proj = f[8].upper() == "YES" if len(f) > 8 else False
        group = f[14] if len(f) > 14 else ""
    else:
        direction = f[4] if len(f) > 4 else "GZ"
        pu, p, proj, group = 0.0, (0.0, 0.0, 0.0, 0.0), False, ""
    ls.pressures.append(PressureLoad(
        case=case, elements=expand_id_list(f[0]), etype=etype, ltype=ltype,
        direction=direction, pu=pu, p=p, projected=proj, group=group,
    ))


#: 荷重方向の記号（部材荷重の列構成の判別に使う）
_DIR_TOKENS = {"GX", "GY", "GZ", "LX", "LY", "LZ"}


def _parse_beamload(case: str, line: str, ls: LoadSet) -> None:
    """*BEAMLOAD の1行。

    ELEM_LIST, CMD, TYPE, DIR, bPROJ, [ECCEN], [VALUE], GROUP
    ELEM_LIST, CMD, TYPE, TYPE, DIR, VX, VY, VZ, bPROJ, [ECCEN], [VALUE], GROUP
      [ECCEN] : bECCEN, ECCDIR, I-END, J-END, bJ-END          (5列)
      [VALUE] : D1, P1, D2, P2, D3, P3, D4, P4                (8列)

    方向を指定する列が DIR 記号か否かで列構成が変わるので、そこで見分ける。
    """
    f = _fields(line)
    if len(f) < 6:
        return
    if f[3].upper() in _DIR_TOKENS:
        direction, proj, rest = f[3], f[4], f[5:]
    else:                                   # 方向ベクトル指定（VX,VY,VZ 付き）
        direction, proj, rest = (f[4] if len(f) > 4 else "GZ"), \
            (f[8] if len(f) > 8 else "NO"), f[9:]
    vals = rest[5:13]                       # ECCEN 5列のあと VALUE 8列
    points = []
    for k in range(0, 8, 2):
        d = _num(vals[k]) if k < len(vals) else 0.0
        pv = _num(vals[k + 1]) if k + 1 < len(vals) else 0.0
        points.append((d, pv))
    ls.beam_loads.append(BeamLoad(
        case=case, elements=expand_id_list(f[0]), cmd=f[1], type=f[2].upper(),
        direction=direction, projected=proj.strip().upper() == "YES",
        points=points, group=(rest[13] if len(rest) > 13 else ""),
    ))


def _parse_conload(case: str, line: str, ls: LoadSet) -> None:
    """NODE_LIST, FX, FY, FZ, MX, MY, MZ, GROUP, STRTYPENAME"""
    f = _fields(line)
    if len(f) < 4:
        return
    ls.nodal_loads.append(NodalLoad(
        case=case, nodes=expand_id_list(f[0]),
        fx=_num(f[1]), fy=_num(f[2]), fz=_num(f[3]),
        mx=_num(f[4]) if len(f) > 4 else 0.0,
        my=_num(f[5]) if len(f) > 5 else 0.0,
        mz=_num(f[6]) if len(f) > 6 else 0.0,
        group=f[7] if len(f) > 7 else "",
    ))


def assign_floor_load_numbers(ls: LoadSet) -> None:
    """床荷重に MIDAS 画面と同じ通し番号を振る。

    MIDAS の表示は "(3)-RF2(One)" の形。番号は **床荷重タイプの定義順
    （*FLOADTYPE の並び）でグループ化し、その中は *FLOORLOAD の出現順**。
    一新亭モデル（34面）で既存 PDF の (1)〜(34) と一致することを確認済み。
    """
    order = {t.name: i for i, t in enumerate(ls.floor_types)}
    indexed = sorted(
        enumerate(ls.floor_loads),
        key=lambda kv: (order.get(kv[1].type_name, len(order)), kv[0]),
    )
    for seq, (_i, fl) in enumerate(indexed, start=1):
        fl.seq = seq


def read_loads(path: str, encoding: str = "cp932") -> LoadSet:
    """mgt / mgtx から荷重データを読む。"""
    ls = LoadSet()
    floadtype_lines: list[str] = []
    for block, case, line in _iter_records(path, encoding):
        if block == "STLDCASE":
            _parse_stldcase(line, ls)
        elif block == "FLOADTYPE":
            floadtype_lines.append(line)
        elif block == "FLOORLOAD":
            _parse_floorload(line, ls)
        elif block == "PRESSURE":
            _parse_pressure(case, line, ls)
        elif block == "CONLOAD":
            _parse_conload(case, line, ls)
        elif block == "BEAMLOAD":
            _parse_beamload(case, line, ls)
    _parse_floadtype(floadtype_lines, ls)
    assign_floor_load_numbers(ls)
    return ls
