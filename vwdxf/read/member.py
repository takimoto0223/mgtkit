"""*MEMBER（結合部材）パーサ。

行: iKEY, ELEM, bREVERSE, AELEM1, AELEM2, ...
結合部材の構成要素 = [ELEM] + [AELEM...]（先頭 ELEM を代表とする）。
"""
from __future__ import annotations

from ..model import Model
from .group import _merge_continuations


def _int(s: str):
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def parse_members(lines: list[str], model: Model) -> None:
    for rec in _merge_continuations(lines):
        f = [x.strip() for x in rec.split(",")]
        if len(f) < 2:
            continue
        prim = _int(f[1])              # ELEM（代表）
        if prim is None:
            continue
        elems = [prim]
        for s in f[3:]:                # AELEM...（f[2]=bREVERSE は飛ばす）
            v = _int(s)
            if v is not None:
                elems.append(v)
        # 1 要素だけの *MEMBER も分割部材として持つ (構造図βと同じ)
        model.members.append(elems)
