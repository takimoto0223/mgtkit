"""READ — mgt / mgtx を読み Model IR を作る (tools/struct_cad_py の read/ から mgt 系だけを移植)."""
from __future__ import annotations

from .mgt import read_mgt, split_blocks

__all__ = ["read_mgt", "split_blocks", "detect_encoding"]


def detect_encoding(path: str, given: str | None = None) -> str:
    """中身を見て文字コードを判定する (BOM付き UTF-8 → 厳密 UTF-8 → CP932).

    Gen NX が書き出す .mgtx は CP932、外部ツールが作った .mgtx は UTF-8 のことがある。
    日本語を含む CP932 のファイルが UTF-8 として正しく読めることはまず無い。
    """
    if given:
        return given
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return "cp932"
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "cp932"
