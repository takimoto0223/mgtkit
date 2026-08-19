"""A3 横 1 枚の原稿を PDF にする."""
import asyncio, os, sys
from playwright.async_api import async_playwright
SRC, OUT = sys.argv[1], sys.argv[2]


async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path='/opt/pw-browsers/chromium')
        pg = await b.new_page()
        await pg.goto('file://' + os.path.abspath(SRC))
        await pg.wait_for_timeout(1500)
        await pg.pdf(path=OUT, width='420mm', height='297mm',
                     print_background=True,
                     margin=dict(top='0', bottom='0', left='0', right='0'))
        await b.close()
    print('saved', OUT)

asyncio.run(run())
