# -*- coding: utf-8 -*-
"""本物のマネージャー UI (架空データ) のスクリーンショット撮影。

CDN は全てローカル資産で差し替える (canvaskit / rive / フォント)。
フォントは Noto Sans JP を供給して実機 (Windows) に近い見た目にする。

1 枚につき新しいページを開いて撮る。ダイアログは modal=True で Escape が
効かないため、連続撮影で前の画面を閉じて回ることはしない。

  python shoot_real.py            # 既定の一式を撮る
  python shoot_real.py beta       # 1 枚 (または 1 グループ) だけ撮る
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

# 座標クリックの位置 (flutter は canvas 描画のため要素を掴めない)。
# タブの並びは 起動 / β版の確認と承認 / 更新版を提出
TAB_LAUNCH = (30, 93)
TAB_BETA = (145, 93)
TAB_SUBMIT = (285, 93)
BTN_HISTORY = (204, 222)        # 起動タブ「過去の更新ログ」
BTN_USAGE = (1142, 30)          # 右上の API 利用量アイコン
BTN_PICK_ZIP = (110, 294)       # 提出タブ「ZIP を選んで提出」
RADIO_MANUAL = (339, 494)       # 確認ダイアログ「自分で入力する」
BTN_CONFIRM_SUBMIT = (819, 563)  # 確認ダイアログ「提出する」
BTN_FEEDBACK = (340, 404)       # β版タブ #21 の「フィードバック 2 件」


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


async def new_page(p):
    browser = await p.chromium.launch(
        executable_path='/opt/pw-browsers/chromium')
    pg = await browser.new_page(viewport={'width': 1180, 'height': 860})
    for pat in ('https://www.gstatic.com/**',
                'https://cdn.jsdelivr.net/**',
                'https://fonts.gstatic.com/**',
                'https://fonts.googleapis.com/**'):
        await pg.route(pat, handle)
    await pg.goto(URL)
    await pg.wait_for_timeout(14000)
    return browser, pg


def trim_bottom(path, margin=16):
    """下の余白 (地の色だけの行) を切り詰める.

    ガイドに貼る図は縦の空きが大きいと本文から浮くため、既存の図と同じく
    中身の下端 + margin で切る。地の色は最下行の色で判定する (通常画面は
    ページの地色、ダイアログ表示中は暗幕の色)。
    """
    from PIL import Image
    im = Image.open(path).convert('RGB')
    w, h = im.size
    px = im.load()
    ground = [px[x, h - 1] for x in range(0, w, 7)]
    bottom = h
    for y in range(h - 1, -1, -1):
        if [px[x, y] for x in range(0, w, 7)] != ground:
            bottom = y + 1
            break
    new_h = min(h, bottom + margin)
    if new_h < h:
        im.crop((0, 0, w, new_h)).save(path)
    return new_h


async def shot(pg, name):
    path = os.path.join(BASE, name + '.png')
    await pg.screenshot(path=path)
    print('saved', name, '(高さ %d)' % trim_bottom(path))


async def click(pg, xy, wait=2500):
    await pg.mouse.click(*xy)
    await pg.wait_for_timeout(wait)


# --- 1 枚ぶんの手順 (それぞれ新しいページで実行される) ---

async def cut_firstrun(pg):
    # 初回登録ダイアログ。home/settings.json を消してから起動すること
    await shot(pg, 'real_firstrun')


async def cut_launch(pg):
    await shot(pg, 'real_launch')


async def cut_history(pg):
    await click(pg, BTN_HISTORY, 6000)
    await shot(pg, 'real_history')


async def cut_usage(pg):
    await click(pg, BTN_USAGE)
    await shot(pg, 'real_usage')


async def cut_submit(pg):
    await click(pg, TAB_SUBMIT)
    await shot(pg, 'real_submit')


async def cut_submit_dialog(pg):
    await click(pg, TAB_SUBMIT)
    await click(pg, BTN_PICK_ZIP, 4000)
    await shot(pg, 'real_submit_dialog')


async def cut_submit_manual(pg):
    await click(pg, TAB_SUBMIT)
    await click(pg, BTN_PICK_ZIP, 4000)
    await click(pg, RADIO_MANUAL)
    await shot(pg, 'real_submit_manual')


async def cut_submit_review(pg):
    # 既定の「Claude で自動作成する」のまま提出 → 自動作成の確認画面
    await click(pg, TAB_SUBMIT)
    await click(pg, BTN_PICK_ZIP, 4000)
    await click(pg, BTN_CONFIRM_SUBMIT, 4000)
    await shot(pg, 'real_submit_review')


async def cut_beta(pg):
    await click(pg, TAB_BETA, 7000)
    await shot(pg, 'real_beta')


async def cut_feedback(pg):
    await click(pg, TAB_BETA, 7000)
    await click(pg, BTN_FEEDBACK, 4000)
    await shot(pg, 'real_feedback')


CUTS = {
    'firstrun': cut_firstrun,
    'launch': cut_launch,
    'history': cut_history,
    'usage': cut_usage,
    'submit': cut_submit,
    'submit_dialog': cut_submit_dialog,
    'submit_manual': cut_submit_manual,
    'submit_review': cut_submit_review,
    'beta': cut_beta,
    'feedback': cut_feedback,
}
# firstrun は未登録の状態が要るので既定の一式には入れない (README 参照)
DEFAULT = ['launch', 'history', 'usage', 'submit', 'submit_dialog',
           'submit_manual', 'submit_review', 'beta', 'feedback']


async def run(names):
    async with async_playwright() as p:
        for name in names:
            browser, pg = await new_page(p)
            try:
                await CUTS[name](pg)
            finally:
                await browser.close()


if __name__ == '__main__':
    args = sys.argv[1:] or DEFAULT
    unknown = [a for a in args if a not in CUTS]
    if unknown:
        sys.exit('不明な撮影名: %s (使えるのは %s)'
                 % (', '.join(unknown), ', '.join(CUTS)))
    asyncio.run(run(args))
