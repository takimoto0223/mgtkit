"""*SECTION パーサ（Step2）。

MIDAS の *SECTION は形式が多い（DB/USER/VALUE/SRC/COMBINED/TAPERED）。
Step2 では **単一行の DBUSER/VALUE 系**（H/SB/SR/P/BOX/C/T/L）を対象にする。
SRC・TAPERED は次行に寸法が続く複数行形式のため、ここでは shape を記録し
寸法は後続 Step で対応する（当面 dims 空でフォールバック描画）。

1行の並び（サンプル実測, 0-based）:
    0:iSEC 1:TYPE 2:SNAME 3..9:OFFSET(7) 10:bSD 11:bWE 12:SHAPE 13:dataFlag 14..:D1,D2,...

SHAPE トークン（カンマ区切りでトリム後）:
    'H' / 'SB' / 'SR' / 'P' / 'C' / 'T' / 'L' / 'B' / 'CB'
    → 'B','CB' は BOX（角形鋼管）に正規化。
寸法の意味（描画で使う成H・幅Bのため）:
    H  : D1=成H, D2=幅B, D3=tw, D4=tf, ...
    SB : D1=成H, D2=幅B
    BOX: D1=成H, D2=幅B, ...
    P  : D1=径
    SR : D1=径
"""
from __future__ import annotations

import re

from ..model import Model, Section

# SHAPE トークン（トリム後） → 正規化 shape
_SHAPE_MAP = {
    "H": "H",
    "SB": "SB",
    "SR": "SR",
    "P": "P",
    "C": "C",
    "T": "T",
    "L": "L",
    "B": "BOX",
    "CB": "BOX",
    "2C": "C",   # 二丁溝形鋼 (mgtkit 拡張)。外形は単材 C の寸法で代表
    "2L": "L",   # 二丁山形鋼 (mgtkit 拡張)
}
# DB 名の寸法の並び (mm): H 成x幅xtwxtf / P 径x厚 / BOX 成x幅x厚 / L 成x幅x厚 / C 成x幅 ...
_DB_DIMS = {"H": 4, "BOX": 3, "P": 2, "L": 3, "C": 2, "T": 2, "SB": 2, "SR": 1}


def _dims_from_db_name(shape: str, fields: list[str]) -> list[float]:
    """DB 指定 (データ種別 1) の断面: 行末の DB 名 'PG 60.5x4' 等から寸法 [m] を戻す."""
    for s in reversed(fields):
        m = re.search(r"(\d+(?:\.\d+)?(?:\s*[xX×*]\s*\d+(?:\.\d+)?)+)", s)
        if m:
            vals = [float(v) for v in re.split(r"\s*[xX×*]\s*", m.group(1))]
            return [v / 1000.0 for v in vals[:_DB_DIMS.get(shape, 2)]]
    return []
_MULTILINE_TYPES = {"SRC", "TAPERED"}


def _to_float(s: str):
    try:
        return float(s)
    except ValueError:
        return None


def _find_shape_index(fields: list[str]) -> int:
    """SHAPE トークンのフィールド位置を返す。見つからなければ -1。"""
    # OFFSET/フラグより後（index>=3）を走査
    for i in range(3, len(fields)):
        if fields[i].strip() in _SHAPE_MAP:
            return i
    return -1


def _numbers(fields: list[str]) -> list[float]:
    out: list[float] = []
    for s in fields:
        v = _to_float(s)
        if v is not None:
            out.append(v)
    return out


def parse_sections(lines: list[str], model: Model) -> None:
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        f = [x.strip() for x in ln.split(",")]
        i += 1
        if len(f) < 4:
            continue
        try:
            sid = int(float(f[0]))
        except ValueError:
            continue
        sec_type = f[1].upper()
        name = f[2] if len(f) > 2 else ""

        # --- テーパー: 2行目に i端/j端の寸法が続く（ベストエフォート） ---
        if sec_type == "TAPERED":
            si = _find_shape_index(f)
            shape = _SHAPE_MAP[f[si].strip()] if si >= 0 else "OTHER"
            dims_i: list[float] = []
            dims_j: list[float] = []
            if i < n:
                nums = _numbers([x.strip() for x in lines[i].split(",")])
                i += 1  # 2行目を消費
                half = len(nums) // 2
                dims_i = nums[:half] if half else nums
                dims_j = nums[half:] if half else nums
            model.sections[sid] = Section(sid, shape, name, dims_i, "TAPERED",
                                          dims_j=dims_j or None)
            continue

        # SRC: 2行目に「コンクリート D1,D2, [鋼材 spec]」が続く（ベストエフォート）。
        if sec_type == "SRC":
            conc, steel = [], []
            if i < n:
                nums = _numbers([x.strip() for x in lines[i].split(",")])
                i += 1  # 2行目を消費
                conc = nums[:2]
                rest = nums[2:]
                # 先頭が種別フラグ(1/2)なら飛ばし、残りを鋼材寸法(H,B,tw,tf)に
                if rest and rest[0] in (1.0, 2.0):
                    rest = rest[1:]
                steel = [v for v in rest if v > 0][:4]
            model.sections[sid] = Section(sid, "SRC", name, conc, "SRC",
                                          steel_dims=steel or None)
            continue

        # その他の複数行形式は shape のみ記録し寸法行を読み飛ばす
        if sec_type in _MULTILINE_TYPES:
            model.sections[sid] = Section(sid, sec_type, name, [], sec_type)
            if i < n and _find_shape_index([x.strip() for x in lines[i].split(",")]) < 0:
                i += 1  # 寸法だけの続き行を消費
            continue

        si = _find_shape_index(f)
        if si < 0:
            model.sections[sid] = Section(sid, "OTHER", name, [], sec_type)
            continue

        shape = _SHAPE_MAP[f[si].strip()]
        # SHAPE の次はデータ種別フラグ(1=DB,2=VALUE)。その後が数値寸法。
        dims = _numbers(f[si + 2:])
        if (len(f) > si + 1 and f[si + 1].strip() == "1") or not dims:
            dims = _dims_from_db_name(shape, f[si + 2:]) or dims
        # 末尾の 0 詰めを軽く落とす（成・幅は先頭側にある）
        while len(dims) > 2 and dims[-1] == 0.0:
            dims.pop()
        model.sections[sid] = Section(sid, shape, name, dims, sec_type)
