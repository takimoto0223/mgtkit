# -*- coding: utf-8 -*-
"""本物のマネージャー UI (架空データ) のスクリーンショット撮影。

CDN は全てローカル資産で差し替える (canvaskit / rive / フォント)。
フォントは Noto Sans JP を供給して実機 (Windows) に近い見た目にする。
"""
import asyncio
import os
import sys

from playwright.async_api import async_playwright

BASE = os.path.dirname(os.path.abspath(__file__))
SCRATCH = os.path.dirname(BASE)
# 資産の置き場所は撮影環境で違うため環境変数で上書きできるようにする
# (GUIDE_SHOTS_CK / _RIVE / _FONT400 / _FONT700)。既定は同梱・標準の場所
CK = os.environ.get(
    'GUIDE_SHOTS_CK',
    '/usr/local/lib/python3.11/dist-packages/flet_web/web/canvaskit')
RIVE = os.environ.get('GUIDE_SHOTS_RIVE', os.path.join(SCRATCH, 'package'))
FONT400 = os.environ.get(
    'GUIDE_SHOTS_FONT400',
    os.path.join(SCRATCH, 'fonts', 'NotoSansJP-400.ttf'))
FONT700 = os.environ.get(
    'GUIDE_SHOTS_FONT700',
    os.path.join(SCRATCH, 'fonts', 'NotoSansJP-700.ttf'))
TYPES = {'.wasm': 'application/wasm', '.js': 'text/javascript',
         '.mjs': 'text/javascript', '.ttf': 'font/ttf'}
URL = 'http://127.0.0.1:8571/'


async def fulfill_file(route, path):
    ext = os.path.splitext(path)[1]
    with open(path, 'rb') as f:
        await route.fulfill(
            body=f.read(),
            content_type=TYPES.get(ext, 'application/octet-stream'))


async def handle(route):
    url = route.request.url
    if 'flutter-canvaskit/' in url:
        sub = url.split('flutter-canvaskit/', 1)[1].split('/', 1)[1]
        path = os.path.join(CK, sub)
        if os.path.isfile(path):
            return await fulfill_file(route, path)
    if 'cdn.jsdelivr.net' in url and 'rive' in url:
        sub = url.split('flutter-native-wasm@42.0.0/', 1)[1]
        path = os.path.join(RIVE, sub)
        if os.path.isfile(path):
            return await fulfill_file(route, path)
    if 'fonts.gstatic.com' in url:
        low = url.lower()
        if 'emoji' in low:
            return await fulfill_file(
                route, '/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf')
        bold = ('bold' in low or 'wght@700' in low or '/kfol' in low)
        path = FONT700 if bold else FONT400
        if not os.path.isfile(path):   # 用意が無ければ標準の日本語書体
            path = '/usr/share/fonts/opentype/ipafont-gothic/ipagp.ttf'
        return await fulfill_file(route, path)
    if 'fonts.googleapis.com' in url:
        return await route.fulfill(body='', content_type='text/css')
    await route.abort()


async def new_page(p, height=860):
    browser = await p.chromium.launch(
        executable_path='/opt/pw-browsers/chromium')
    pg = await browser.new_page(viewport={'width': 1180, 'height': height})
    for pat in ('https://www.gstatic.com/**',
                'https://cdn.jsdelivr.net/**',
                'https://fonts.gstatic.com/**',
                'https://fonts.googleapis.com/**'):
        await pg.route(pat, handle)
    await pg.goto(URL)
    await pg.wait_for_timeout(14000)
    return browser, pg


# 図ごとの高さ (px)。撮影は 1180x860 で行い、下の余白を落として
# ガイドに載る形にそろえる (載せる図の縦横比を安定させるため)
CROP = {
    'real_launch': 551, 'real_beta': 636, 'real_submit': 500,
    'real_usage': 760, 'real_submit_dialog': 640,
    'real_submit_manual': 760, 'real_submit_review': 700,
    'real_firstrun': 647,
}


async def shot(pg, name):
    path = os.path.join(BASE, name + '.png')
    await pg.screenshot(path=path)
    h = CROP.get(name)
    if h:
        try:
            from PIL import Image
            im = Image.open(path)
            if im.height > h:
                im.crop((0, 0, im.width, h)).save(path)
        except ImportError:
            print('  (Pillow が無いため切り抜きは省略)')
    print('saved', name)


# 画面ごとの座標 (flutter は canvas 描画のため座標クリック)。
# タブの並びは 起動 / β版の確認と承認 / 更新版を提出 (#134)
TAB_LAUNCH = (30, 93)
TAB_BETA = (145, 93)
TAB_SUBMIT = (285, 93)


async def run(mode):
    """1 モード = 1 ページ。ダイアログは Escape で閉じない (modal=True) ため、
    ダイアログを開く撮影はモードを分けてページを開き直す。"""
    async with async_playwright() as p:
        browser, pg = await new_page(p)
        if mode == 'firstrun':
            await shot(pg, 'real_firstrun')
        elif mode in ('launch', 'main'):
            await shot(pg, 'real_launch')
            # 過去の更新ログ
            await pg.mouse.click(204, 222)
            await pg.wait_for_timeout(6000)
            await shot(pg, 'real_history')
            await pg.keyboard.press('Escape')
            await pg.wait_for_timeout(1500)
            # API 利用量 (右上のアイコン)
            await pg.mouse.click(1142, 30)
            await pg.wait_for_timeout(2500)
            await shot(pg, 'real_usage')
        elif mode == 'submit':
            await pg.mouse.click(*TAB_SUBMIT)
            await pg.wait_for_timeout(2500)
            await shot(pg, 'real_submit')
            # 提出確認ダイアログ (pick_files はハーネスが差し込む)
            await pg.mouse.click(110, 294)
            await pg.wait_for_timeout(4000)
            await shot(pg, 'real_submit_dialog')
            # 「自分で入力する」を選ぶと同じ画面に記入欄が出る
            await pg.mouse.click(339, 494)
            await pg.wait_for_timeout(2500)
            await shot(pg, 'real_submit_manual')
        elif mode == 'review':
            # 「Claude で自動作成する」(既定) のまま提出 → 確認画面
            await pg.mouse.click(*TAB_SUBMIT)
            await pg.wait_for_timeout(2500)
            await pg.mouse.click(110, 294)
            await pg.wait_for_timeout(4000)
            await pg.mouse.click(819, 563)
            await pg.wait_for_timeout(4000)
            await shot(pg, 'real_submit_review')
        elif mode == 'beta':
            await pg.mouse.click(*TAB_BETA)
            await pg.wait_for_timeout(7000)
            await shot(pg, 'real_beta')
            # 「フィードバック 2 件」(#21)
            await pg.mouse.click(340, 404)
            await pg.wait_for_timeout(4000)
            await shot(pg, 'real_feedback')
        else:
            raise SystemExit('mode: firstrun / launch / submit / review / beta')
        await browser.close()


if __name__ == '__main__':
    asyncio.run(run(sys.argv[1] if len(sys.argv) > 1 else 'launch'))
