"""*THICKNESS パーサ（板厚）。

VALUE型: iTHK, TYPE, [NAME,] bSAME, THIK-IN, THIK-OUT, ...
板厚(THIK-IN)= 先頭id以降の最初の実数。
STIFFENED等の続き行（先頭が数値）は読み飛ばす。
"""
from __future__ import annotations

from ..model import Model


def _isfloat(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


_KEYWORDS = {"VALUE", "STIFFENED", "USER", "YES", "NO", "DB"}


def parse_thickness(lines: list[str], model: Model) -> None:
    for ln in lines:
        f = [x.strip() for x in ln.split(",")]
        if len(f) < 2 or _isfloat(f[1]):
            continue                       # 続き行/寸法行は飛ばす
        try:
            tid = int(float(f[0]))
        except ValueError:
            continue
        val = 0.0
        name = ""
        for s in f[1:]:                    # id以降の最初の実数 = THIK-IN
            if _isfloat(s):
                val = float(s)
                break
            if not name and s.upper() not in _KEYWORDS:
                name = s                   # 型/真偽語でない最初の語 = 名称(符号)
        model.thicknesses[tid] = val
        model.thickness_names[tid] = name
