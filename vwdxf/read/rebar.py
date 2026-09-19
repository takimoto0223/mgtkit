"""*REBAR-COLUMN（RC柱配筋）パーサ。

1断面1行:
  iSEC, iSUBSEC, HOOP, RBNAME, bUseCornerRB, CornerRBNAME, iNQRB, iNROW, DO,
  SRBNAME, SPACE, iSRBN1, iSRBN2, ...
  例) 121, 0, TIED, D16, NO, D16, 10, 2, 0.0635, D13, 0.1, 2, 2, 0, ...
主筋総本数 iNQRB と段数 iNROW から 幅本数 = total/2 + 2 - iNROW を求める。
（*REBAR-BEAM は後続対応）
"""
from __future__ import annotations

import re

from ..model import Model, RebarBeam, RebarColumn

_DIA = re.compile(r"(\d+)")


def _dia(s: str) -> int:
    m = _DIA.search(s or "")
    return int(m.group(1)) if m else 0


def parse_rebar_columns(lines: list[str], model: Model) -> None:
    for ln in lines:
        f = [x.strip() for x in ln.split(",")]
        if len(f) < 11:
            continue
        try:
            sec_id = int(float(f[0]))
            total = int(float(f[6]))
            n_row = int(float(f[7]))
            cover = float(f[8])
            pitch = float(f[10])
        except ValueError:
            continue
        main_dia = _dia(f[3])
        hoop_dia = _dia(f[9])
        n_width = total // 2 + 2 - n_row
        try:
            hoop_x = int(float(f[11]))
            hoop_y = int(float(f[12]))
        except (ValueError, IndexError):
            hoop_x = hoop_y = 2
        model.rebar_columns[sec_id] = RebarColumn(
            sec_id, main_dia, total, n_row, n_width, cover, hoop_dia, pitch,
            hoop_x, hoop_y)


def _int(s, default=0):
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return default


def _flt(s, default=0.0):
    try:
        return float(s)
    except (ValueError, TypeError):
        return default


def _is_beam_head(f: list[str]) -> bool:
    """*REBAR-BEAM の1行目か（3列目がバー名 Dxx で先頭が断面番号）。"""
    return len(f) >= 5 and bool(f[2]) and f[2][0].isalpha() and _isint(f[0])


def _isint(s: str) -> bool:
    try:
        int(float(s))
        return True
    except (ValueError, TypeError):
        return False


def parse_rebar_beams(lines: list[str], model: Model) -> None:
    """*REBAR-BEAM（要素ごと4行: 共通/ i端/中央/j端）をパース。

    代表として i端(2行目)の配筋を採用（位置=全断面）。
    """
    fields = [[x.strip() for x in ln.split(",")] for ln in lines]
    i, n = 0, len(fields)
    while i < n:
        f1 = fields[i]
        i += 1
        if not _is_beam_head(f1):
            continue
        sec_id = _int(f1[0])
        stir_dia = _dia(f1[2])
        cover_top = _flt(f1[3])
        cover_bot = _flt(f1[4])
        side_dia = _dia(f1[7]) if len(f1) > 7 else 0
        side_num = _int(f1[8]) if len(f1) > 8 else 0
        # 続くサブ行（i端/中央/j端）を集める
        subs = []
        while i < n and not _is_beam_head(fields[i]):
            subs.append(fields[i])
            i += 1
        rb = RebarBeam(sec_id, stir_dia, cover_top, cover_bot,
                       side_num=side_num, side_dia=side_dia)
        if subs:
            f2 = subs[0]                       # i端（代表）
            if len(f2) >= 12:
                rb.top_rb1, rb.top_rb2, rb.top_dia = _int(f2[1]), _int(f2[2]), _dia(f2[3])
                rb.bot_rb1, rb.bot_rb2, rb.bot_dia = _int(f2[6]), _int(f2[7]), _dia(f2[8])
                rb.stir_pitch = _flt(f2[10])
                rb.stir_legs = _int(f2[11])
        model.rebar_beams[sec_id] = rb
