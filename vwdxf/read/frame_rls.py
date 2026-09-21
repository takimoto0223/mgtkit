"""*FRAME-RLS（部材端の接合解放）パーサ。

書式は要素ごとに2行:
    ELEM_LIST, bVALUE, FLAG-i, Fxi,Fyi,Fzi,Mxi,Myi,Mzi   ; 1行目(i端)
               FLAG-j, Fxj,Fyj,Fzj,Mxj,Myj,Mzj, GROUP    ; 2行目(j端)
FLAG は 6桁('Fx Fy Fz Mx My Mz'の解放フラグ)。'000011'=My,Mz解放=ピン。
'000000'=解放なし=剛。ここでは「フラグに1が含まれる=解放」とみなす（MATLAB準拠）。
"""
from __future__ import annotations

from ..model import Model
from .group import _expand_list


def _released(flag: str) -> bool:
    flag = flag.strip()
    return flag.isdigit() and ("1" in flag)


def parse_frame_rls(lines: list[str], model: Model) -> None:
    cur_eles: list[int] = []
    for ln in lines:
        f = [x.strip() for x in ln.split(",")]
        if len(f) >= 2 and f[1].upper() in ("NO", "YES"):
            # 1行目（i端）
            cur_eles = _expand_list(f[0])
            rel_i = _released(f[2]) if len(f) > 2 else False
            for e in cur_eles:
                model.releases[e] = (rel_i, False)
                model.frame_flags[e] = (f[2] if len(f) > 2 else '', '')
        else:
            # 2行目（j端）
            rel_j = _released(f[0]) if f else False
            for e in cur_eles:
                ri = model.releases.get(e, (False, False))[0]
                model.releases[e] = (ri, rel_j)
                fi = model.frame_flags.get(e, ('', ''))[0]
                model.frame_flags[e] = (fi, f[0] if f else '')
