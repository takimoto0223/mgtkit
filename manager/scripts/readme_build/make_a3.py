# -*- coding: utf-8 -*-
"""A3 横の原稿を PDF にする (ショートカットの行き先の線はここで引く).

線の座標を原稿に書き込むと、行を 1 つ増やしただけでずれる。そこで
**PDF を作るその場で位置を測って線を引く**。測るのも描くのも同じページ
なので、ツリーをいくら書き換えてもずれない。

線は #a-dst (入口のショートカットの行) から #a-tgt (その行き先) へ、
パネル右の余白を回って引く。途中の横切りは、群と群のあいだの
すき間 (どの行にも当たらない帯) を通す。
"""
import asyncio
import os
import sys

from playwright.async_api import async_playwright

SRC, OUT = sys.argv[1], sys.argv[2]

DRAW_WIRE = """() => {
  const panel = document.querySelector('.panel.wired');
  const svg = panel && panel.querySelector('.wires');
  const src = document.getElementById('a-dst');
  const dst = document.getElementById('a-tgt');
  if (!panel || !svg || !src || !dst) return 'skip';
  const pr = panel.getBoundingClientRect();
  const textRight = el => {
    const r = document.createRange();
    r.selectNodeContents(el);
    return r.getBoundingClientRect().right - pr.left;
  };
  const midY = el => {
    const r = el.getBoundingClientRect();
    return r.top + r.height / 2 - pr.top;
  };
  // 横切りは群と群のあいだのすき間を通す (どの行にも当たらない)
  const grp = src.closest('.grp');
  const next = grp.nextElementSibling;
  const gap = (grp.getBoundingClientRect().bottom
               + next.getBoundingClientRect().top) / 2 - pr.top;

  const x0 = textRight(src) + 6, y0 = midY(src);
  const lane = pr.width - 30;
  const xt = textRight(dst) + 9, yt = midY(dst);
  const d = [
    `M${x0},${y0}`,
    `C${x0 + 12},${y0} ${x0 + 16},${y0 + 6} ${x0 + 16},${gap - 8}`,
    `C${x0 + 16},${gap} ${x0 + 26},${gap} ${x0 + 36},${gap}`,
    `L${lane - 16},${gap}`,
    `C${lane},${gap} ${lane},${gap + 12} ${lane},${gap + 24}`,
    `L${lane},${yt - 20}`,
    `C${lane},${yt - 8} ${lane - 12},${yt} ${lane - 24},${yt}`,
    `L${xt},${yt}`,
  ].join(' ');
  svg.setAttribute('viewBox', `0 0 ${pr.width} ${pr.height}`);
  svg.setAttribute('preserveAspectRatio', 'none');
  // 矢じりは **入口の行の側** に付ける。「奥の .bat の中身が、入口の
  // ショートカットとして出てくる」向き (赤入れ 2026-08)
  svg.innerHTML =
    `<path d="${d}"/><path d="M${x0},${y0} l7,-3.5 M${x0},${y0} l7,3.5"/>`;
  return d;
}"""


async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path='/opt/pw-browsers/chromium')
        pg = await b.new_page(viewport={'width': 1587, 'height': 1123})
        await pg.goto('file://' + os.path.abspath(SRC))
        await pg.wait_for_timeout(1500)
        print('線:', (await pg.evaluate(DRAW_WIRE))[:40], '...')
        await pg.pdf(path=OUT, width='420mm', height='297mm',
                     print_background=True,
                     margin=dict(top='0', bottom='0', left='0', right='0'))
        await b.close()
    print('saved', OUT)

asyncio.run(run())
