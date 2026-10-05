# -*- coding: utf-8 -*-
"""wood_joint_calc.html の樹種リスト(W_SPECIES)を data/W_AL.json から再生成する.

mgtkit の木材許容応力度表 (W_AL) と接合部計算ツールの Fs/Ft プリセットを同期させる。
W_AL 列: [Fc, Ft, Fb強軸, Fs, Fb弱軸, ...] → Ft=列2, Fs=列4 を使用。

使い方:  python build_species.py
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
WAL_PATH = os.path.join(HERE, "..", "data", "W_AL.json")
HTML_PATH = os.path.join(HERE, "wood_joint_calc.html")

START = "/*W_SPECIES_START*/"
END = "/*W_SPECIES_END*/"

GROUP_ORDER = [
    "製材（無等級材）",
    "製材（目視等級）",
    "製材（機械等級）",
    "対称異等級集成材",
    "同一等級集成材",
    "同一等級集成材（4枚以上）",
    "同一等級集成材（3枚）",
    "同一等級集成材（2枚）",
    "構造用合板",
    "その他",
]


def category(name: str) -> str:
    if "構造用合板" in name:
        return "構造用合板"
    if name.startswith("同一等級集成材4枚"):
        return "同一等級集成材（4枚以上）"
    if name.startswith("同一等級集成材3枚"):
        return "同一等級集成材（3枚）"
    if name.startswith("同一等級集成材2枚"):
        return "同一等級集成材（2枚）"
    if name.startswith("同一等級集成材"):
        return "同一等級集成材"
    if "対称異等級" in name:
        return "対称異等級集成材"
    if "無等級" in name:
        return "製材（無等級材）"
    if re.search(r"[甲乙][123]級", name):
        return "製材（目視等級）"
    if re.search(r"[：:]\s*E\d+", name):
        return "製材（機械等級）"
    return "その他"


def main():
    with open(WAL_PATH, encoding="utf-8") as f:
        d = json.load(f)
    names, rows = d["W_name"], d["W_AL"]

    groups = {g: [] for g in GROUP_ORDER}
    for i, (name, row) in enumerate(zip(names, rows)):
        ft, fs = float(row[1]), float(row[3])
        groups[category(name)].append([i, name.strip(), ft, fs])

    species = [{"g": g, "items": groups[g]} for g in GROUP_ORDER if groups[g]]
    js = (
        START
        + "\nconst W_SPECIES="
        + json.dumps(species, ensure_ascii=False, separators=(",", ":"))
        + ";\n"
        + END
    )

    with open(HTML_PATH, encoding="utf-8") as f:
        html = f.read()
    pat = re.compile(re.escape(START) + r"[\s\S]*?" + re.escape(END))
    if not pat.search(html):
        raise SystemExit("マーカー %s ... %s が見つかりません" % (START, END))
    html = pat.sub(lambda _: js, html, count=1)
    with open(HTML_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    n = sum(len(g["items"]) for g in species)
    print("W_SPECIES を再生成: %d 種 / %d グループ" % (n, len(species)))


if __name__ == "__main__":
    main()
