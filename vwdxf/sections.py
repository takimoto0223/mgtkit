# -*- coding: utf-8 -*-
"""断面の解釈 — 描画で使う代表寸法・符号・断面記号の形.

tools/struct_cad_py の sections.py と draw/section_shape.py を移植 (2026-09-19)。
寸法は Model IR のまま [m]。断面記号の形は「局所 X = せい D1 / 局所 Y = 幅 D2」、
原点 = 断面の中心 (struct_cad の section_block と同じ)。
"""
from __future__ import annotations

# shape ごとの (せいの添字, 幅の添字)。径系 (P/SR) は dims[0] をせい・幅に使う
_DEPTH_WIDTH_INDEX = {
    "H": (0, 1), "SB": (0, 1), "BOX": (0, 1), "C": (0, 1),
    "T": (0, 1), "L": (0, 1), "SRC": (0, 1),
}
ROUND_SHAPES = {"P", "SR"}


def _pick(shape, dims, which):
    if not dims:
        return 0.0
    if shape in ROUND_SHAPES:
        return dims[0]
    idx = _DEPTH_WIDTH_INDEX.get(shape)
    if idx is None:
        return max(dims)
    i = idx[which]
    return dims[i] if i < len(dims) else 0.0


def depth(sec):
    """せい [m] (テーパーは i・j 端の大きい方)。不明なら 0."""
    if sec is None:
        return 0.0
    di = _pick(sec.shape, sec.dims, 0)
    if sec.dims_j is not None:
        return max(di, _pick(sec.shape, sec.dims_j, 0))
    return di


def width(sec):
    """幅 [m]。不明なら 0."""
    if sec is None:
        return 0.0
    wi = _pick(sec.shape, sec.dims, 1)
    if sec.dims_j is not None:
        return max(wi, _pick(sec.shape, sec.dims_j, 1))
    return wi


def depth_ends(sec):
    """(i 端のせい, j 端のせい) [m]。一様断面なら同じ値."""
    if sec is None:
        return (0.0, 0.0)
    di = _pick(sec.shape, sec.dims, 0)
    if sec.dims_j is None:
        return (di, di)
    return (di, _pick(sec.shape, sec.dims_j, 0))


def is_dummy(sec, limit_sec_no):
    """ダミー断面か (断面番号がダミー材断面番号下限以上)."""
    return sec is None or sec.id >= limit_sec_no


def symbol(sec):
    """断面の符号 = 断面名を最初の '_' で切った前半 (例 'G1_105x180' → 'G1')。名前が無ければ ''."""
    if sec is None or not sec.name:
        return ""
    return sec.name.strip().split("_", 1)[0]


def block_name(sym, down=False):
    """断面記号のブロック名: 上へ伸びる柱・軸組図の梁 = <符号>_section / 下柱 = <符号>_section2."""
    return "%s_section2" % sym if down else "%s_section" % sym


def name_block(sym):
    """符号ブロック名 (例 'C1' → 'C1_NAME1')."""
    return sym.replace(" ", "") + "_NAME1"


# ---------------------------------------------------------------------------
# 断面記号の形 (struct_cad draw/section_shape.py)
#   ('poly', [(x, y), ...], closed) / ('circle', (x, y), r)。単位 m、原点中心。
# ---------------------------------------------------------------------------

def _rect(w, h):
    hw, hh = w / 2.0, h / 2.0
    return ('poly', [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)], True)


def _h_shape(Hd, Bd, tw, tf):
    hb, hh = Bd / 2.0, Hd / 2.0
    tws = tw / 2.0
    pts = [(-hb, hh), (hb, hh), (hb, hh - tf), (tws, hh - tf),
           (tws, -hh + tf), (hb, -hh + tf), (hb, -hh), (-hb, -hh),
           (-hb, -hh + tf), (-tws, -hh + tf), (-tws, hh - tf), (-hb, hh - tf)]
    return [('poly', pts, True)]


def _l_shape(Hd, Bd, t):
    hb, hh = Bd / 2.0, Hd / 2.0
    pts = [(-hb, -hh), (hb, -hh), (hb, -hh + t), (-hb + t, -hh + t),
           (-hb + t, hh), (-hb, hh)]
    return [('poly', pts, True)]


def section_shape(sec):
    """断面の形 (幅 B = 水平 x、せい H = 鉛直 y、原点中心) [m]."""
    if sec is None:
        return []
    H = depth(sec)
    B = width(sec)
    dims = sec.dims or []
    shape = sec.shape
    if shape in ROUND_SHAPES:
        r = (dims[0] if dims else H) / 2.0
        out = [('circle', (0.0, 0.0), r)]
        if shape == "P" and len(dims) >= 2 and dims[1] > 0:     # 鋼管の内円
            out.append(('circle', (0.0, 0.0), r - dims[1]))
        return out
    if shape == "BOX":
        out = [_rect(B, H)]
        t = dims[2] if len(dims) >= 3 else 0.0
        if t > 0:
            out.append(_rect(B - 2 * t, H - 2 * t))
        return out
    if shape == "SRC":
        out = [_rect(B, H)]
        st = sec.steel_dims or []
        if len(st) >= 4:
            out += _h_shape(st[0], st[1], st[2], st[3])
        return out
    if shape == "H" and len(dims) >= 4:
        return _h_shape(dims[0], dims[1], dims[2], dims[3])
    if shape == "L" and len(dims) >= 3:
        return _l_shape(dims[0], dims[1], dims[2])
    return [_rect(B, H)]


def _rot90(items):
    """原点まわりに -90° 回す (「せいが鉛直」→「せいが局所 X」)."""
    out = []
    for it in items:
        if it[0] == 'poly':
            out.append(('poly', [(y, -x) for x, y in it[1]], it[2]))
        else:
            out.append(it)
    return out


def symbol_shape(sec, down=False):
    """断面記号の形 (局所 X = せい、局所 Y = 幅、原点 = 中心) [m].

    down=True (下柱) は外形ではなく、せい×幅の対角線の×印。
    """
    if down:
        a, b = depth(sec) / 2.0, width(sec) / 2.0
        return [('poly', [(-a, -b), (a, b)], False), ('poly', [(-a, b), (a, -b)], False)]
    return _rot90(section_shape(sec))


__all__ = ['ROUND_SHAPES', 'block_name', 'depth', 'depth_ends', 'is_dummy', 'name_block',
           'section_shape', 'symbol', 'symbol_shape', 'width']
