"""*MATERIAL パーサ ＋ 材料カテゴリ判定。

行: iMAT, TYPE, MNAME, ...
カテゴリ判定（TYPE 主判定、USERのみ命名規約）:
  STEEL → S / CONC → RC / SRC → SRC
  USER  → 名前が 'W-' で始まれば W(木材)、それ以外は OTHER
木材の名前規約: W-<材種>-<等級>_<補足>
  例) W-カラマツ集成材-E95F315_柱 → 材種=カラマツ集成材, 等級=E95F315, 補足=柱
"""
from __future__ import annotations

from ..model import Material, Model

_WOOD_PREFIX = "W-"


def classify(mtype: str, name: str) -> str:
    t = (mtype or "").upper()
    if t == "STEEL":
        return "S"
    if t == "CONC":
        return "RC"
    if t == "SRC":
        return "SRC"
    if t == "USER" and name.startswith(_WOOD_PREFIX):
        return "W"
    return "OTHER"


def wood_parts(name: str) -> tuple[str, str, str]:
    """W-<材種>-<等級>_<補足> を (材種, 等級, 補足) に分解。"""
    if not name.startswith(_WOOD_PREFIX):
        return ("", "", "")
    body = name[len(_WOOD_PREFIX):]
    main, _, suppl = body.partition("_")     # _ で補足を分離
    species, _, grade = main.partition("-")  # - で材種/等級を分離
    return (species, grade, suppl)


def parse_materials(lines: list[str], model: Model) -> None:
    for ln in lines:
        f = [x.strip() for x in ln.split(",")]
        if len(f) < 3:
            continue
        try:
            mid = int(float(f[0]))
        except ValueError:
            continue
        mtype = f[1]
        name = f[2]
        if not name:
            continue
        cat = classify(mtype, name)
        species, grade = "", ""
        if cat == "W":
            species, grade, _ = wood_parts(name)
        model.materials[mid] = Material(mid, mtype, name, cat, species, grade)
