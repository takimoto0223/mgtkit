"""*GROUP パーサ（Step4）。

通り芯グループ: `NAME, NODE_LIST, ELEM_LIST, PLANE_TYPE`
- 物理行は末尾 '\\'（cp932の0x5C）で次行へ継続する。
- NODE_LIST / ELEM_LIST は空白区切り。各トークンは番号 or 'AtoB' or 'AtoBbyC'。
"""
from __future__ import annotations

from ..model import Group, Model


def get_byto(token: str) -> list[int]:
    """'9'→[9], '74to76'→[74,75,76], '56to131by25'→[56,81,106,131]"""
    t = token.strip()
    if not t:
        return []
    if "by" in t:
        head, by = t.split("by", 1)
        a, b = head.split("to", 1)
        start, stop, step = int(float(a)), int(float(b)), int(float(by))
        if step == 0:
            return [start]
        return list(range(start, stop + (1 if step > 0 else -1), step))
    if "to" in t:
        a, b = t.split("to", 1)
        return list(range(int(float(a)), int(float(b)) + 1))
    try:
        return [int(float(t))]
    except ValueError:
        return []


def _expand_list(s: str) -> list[int]:
    out: list[int] = []
    for tok in s.split():
        out.extend(get_byto(tok))
    return out


def _merge_continuations(lines: list[str]) -> list[str]:
    """末尾 '\\' を継続とみなして論理行に結合する。"""
    merged: list[str] = []
    buf = ""
    for ln in lines:
        s = ln.rstrip()
        if s.endswith("\\") or s.endswith("¥"):
            buf += s[:-1] + " "
        else:
            buf += s
            merged.append(buf)
            buf = ""
    if buf.strip():
        merged.append(buf)
    return merged


def parse_groups(lines: list[str], model: Model) -> None:
    for rec in _merge_continuations(lines):
        f = [x.strip() for x in rec.split(",")]
        if len(f) < 3 or not f[0]:
            continue
        name = f[0].replace(" ", "")
        node_ids = _expand_list(f[1]) if len(f) > 1 else []
        elem_ids = _expand_list(f[2]) if len(f) > 2 else []
        model.groups[name] = Group(name, node_ids, elem_ids)
