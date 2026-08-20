# -*- coding: utf-8 -*-
"""使い方ガイドの原稿 (readme/src/使い方ガイド.html) を PDF にする.

設定は原稿の冒頭コメントに書かれているものをそのまま使う
(A4・上下 14mm・左右は CSS の @page 任せ・ページ番号のフッター)。

  python make_guide.py ../../readme/src/使い方ガイド.html out.pdf
"""
import asyncio
import os
import sys

from playwright.async_api import async_playwright

SRC, OUT = sys.argv[1], sys.argv[2]

FOOTER = (
    '<div style="width:100%;text-align:center;font-size:8pt;'
    'color:#9ca3af;font-family:sans-serif;">'
    '<span class="pageNumber"></span> / <span class="totalPages"></span>'
    '</div>'
)


async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path='/opt/pw-browsers/chromium')
        pg = await b.new_page()
        await pg.goto('file://' + os.path.abspath(SRC))
        await pg.wait_for_timeout(1500)
        await pg.pdf(path=OUT, format='A4', print_background=True,
                     display_header_footer=True,
                     header_template='<div></div>', footer_template=FOOTER,
                     margin=dict(top='14mm', bottom='14mm',
                                 left='0', right='0'))
        await b.close()
    print('saved', OUT)

asyncio.run(run())
