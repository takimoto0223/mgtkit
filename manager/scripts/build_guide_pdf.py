# -*- coding: utf-8 -*-
"""readme/src の HTML 原稿から配布用 PDF を作る (Chromium で印刷).

使い方: python manager/scripts/build_guide_pdf.py [対象名 ...]
  対象名を省略すると全部。組み合わせは PAGES に定義する。

- 体裁 (A4・余白・ページ番号) は原稿の先頭コメントに書かれた条件と同じ
- フォントは原稿の指定どおり (fonts/ に Noto Sans JP があればそれ、
  無ければ IPA P ゴシック)。図は img/ の実画面 PNG をそのまま埋める
"""
import asyncio
import os
import sys

from playwright.async_api import async_playwright

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # manager/
SRC = os.path.join(BASE, 'readme', 'src')
OUT = os.path.join(BASE, 'readme')

# 対象名: (原稿, 出力, ページ番号を Chromium に振らせるか)。
# しくみ資料は原稿が自前でページ枠と柱を描くため、余白 0・柱なしで刷る
PAGES = {
    'guide': ('使い方ガイド.html', 'アプリマネージャー使い方ガイド.pdf', True),
    'shikumi': ('しくみ資料.html', 'バージョン管理と同時開発のしくみ.pdf',
                False),
}

FOOTER = (
    '<div style="width:100%;font-size:8pt;color:#6b7280;text-align:center;'
    'font-family:sans-serif">'
    '<span class="pageNumber"></span> / <span class="totalPages"></span>'
    '</div>')


async def build(names):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path='/opt/pw-browsers/chromium')
        pg = await browser.new_page()
        for name in names:
            src, dst, numbered = PAGES[name]
            await pg.goto('file://' + os.path.join(SRC, src))
            await pg.wait_for_timeout(1500)
            opts = {'display_header_footer': True,
                    'header_template': '<div></div>',
                    'footer_template': FOOTER,
                    'margin': {'top': '14mm', 'bottom': '14mm',
                               'left': '0', 'right': '0'}}
            if not numbered:
                opts = {'margin': {'top': '0', 'bottom': '0',
                                   'left': '0', 'right': '0'}}
            await pg.pdf(path=os.path.join(OUT, dst), format='A4',
                         print_background=True, **opts)
            print('built', dst)
        await browser.close()


if __name__ == '__main__':
    asyncio.run(build(sys.argv[1:] or list(PAGES)))
