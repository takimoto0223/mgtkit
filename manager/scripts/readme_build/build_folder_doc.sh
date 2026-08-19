#!/usr/bin/env bash
# 「フォルダ構成の見直し.pdf」を原稿から作り直す。
#
# 1 枚目 A3 横 + 2 枚目 A4 縦。Chromium は 1 つの PDF に複数の用紙サイズを
# 混ぜられないため、別々に出してから pdfunite でつなぐ。
# 必要なもの: playwright (chromium)、poppler-utils (pdfunite)
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
src="$here/../../readme/src"
out="$here/../../readme/フォルダ構成の見直し.pdf"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

python "$here/make_a3.py" "$src/フォルダ構成の見直し.html"   "$tmp/p1.pdf"
python "$here/make_a4.py" "$src/フォルダ構成の見直し-2.html" "$tmp/p2.pdf"
pdfunite "$tmp/p1.pdf" "$tmp/p2.pdf" "$out"
echo "作成しました: $out"
