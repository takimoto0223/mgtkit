"""*STORY パーサ（層設定）.

    NAME=1F, 2.14, NO , 0, 0, ...      ; 1 行目: 階名, レベル, ...
    AreaPosX1, AreaPosY1, ...          ; 2 行目以降 (NAME= で始まらない行) は読み飛ばす

mgtkit の n_value.py (_read_story_levels) と同じ読み方。レベルは低い順に並べる。
"""
from __future__ import annotations

from ..model import Model


def parse_story(lines: list[str], model: Model) -> None:
    out = []
    for ln in lines:
        s = ln.strip()
        if not s.upper().startswith("NAME="):
            continue
        parts = s.split(",")
        name = parts[0].split("=", 1)[1].strip()
        try:
            out.append((name, float(parts[1])))
        except (IndexError, ValueError):
            continue
    model.stories = sorted(out, key=lambda t: t[1])
