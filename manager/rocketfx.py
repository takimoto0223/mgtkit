# -*- coding: utf-8 -*-
"""ロケット演出 (ベクター版): 機体・炎・煙・パーツのフレームアニメーション.

Flet の Image は SVG 文字列をそのまま src に描画できる。機体やパーツは
SVG で持ち、背景スレッドが約 30fps で位置・回転・透明度を書き換えて
動かす。演出は数秒で終わり、終了時に overlay から自分を片づける。

基点はカードのボタン行右端のロケット定位置。発射も転倒・爆発も
そこから始まる。
"""
import logging
import math
import threading
import time

import flet as ft

log = logging.getLogger(__name__)

_FRAME = 0.033          # 約 30fps
_G = 420.0              # 破片の重力 (px/s^2)

_DEFS = (
    '<defs>'
    '<linearGradient id="bg" x1="0" y1="0" x2="1" y2="0">'
    '<stop offset="0" stop-color="#fde5e2"/>'
    '<stop offset=".28" stop-color="#ef8d85"/>'
    '<stop offset=".55" stop-color="#d64040"/>'
    '<stop offset="1" stop-color="#7e1f1f"/></linearGradient>'
    '<linearGradient id="ng" x1="0" y1="0" x2="1" y2="0">'
    '<stop offset="0" stop-color="#bbb"/><stop offset=".5" stop-color="#666"/>'
    '<stop offset="1" stop-color="#2f2f2f"/></linearGradient>'
    '</defs>'
)

_BODY = (
    '<path d="M0,-36 C11,-21 11,-2 7.5,17 L-7.5,17 C-11,-2 -11,-21 0,-36 Z"'
    ' fill="url(#bg)"/>'
    '<path d="M0,-36 C4.5,-30 6.5,-26 7.5,-21 L-7.5,-21 C-6.5,-26 -4.5,-30'
    ' 0,-36 Z" fill="#5f1616"/>'
    '<path d="M0,-36 C1.5,-33 2.5,-31 3.2,-28 L-3.2,-28 C-2.5,-31 -1.5,-33'
    ' 0,-36 Z" fill="#ffd0c9" opacity=".6"/>'
    '<rect x="-7.5" y="-4" width="15" height="2" fill="#5f1616"'
    ' opacity=".55"/>'
    '<circle cx="-5.4" cy="-2.9" r=".7" fill="#4a1010"/>'
    '<circle cx="-1.8" cy="-2.9" r=".7" fill="#4a1010"/>'
    '<circle cx="1.8" cy="-2.9" r=".7" fill="#4a1010"/>'
    '<circle cx="5.4" cy="-2.9" r=".7" fill="#4a1010"/>'
    '<path d="M-7.5,2 L-18,24 L-9,18.5 L-7.5,17 Z" fill="#962626"/>'
    '<path d="M-7.5,2 L-13,14 L-7.5,11 Z" fill="#c96a6a" opacity=".8"/>'
    '<path d="M7.5,2 L18,24 L9,18.5 L7.5,17 Z" fill="#5c1414"/>'
    '<circle cx="0" cy="-12" r="6" fill="#0e2a44"/>'
    '<circle cx="0" cy="-12" r="6" fill="none" stroke="#e8e8e8"'
    ' stroke-width="2"/>'
    '<circle cx="0" cy="-12" r="6" fill="none" stroke="#5f1616"'
    ' stroke-width=".8"/>'
    '<path d="M-3.4,-14.8 A4.6,4.6 0 0 1 1.6,-16.2" stroke="#9fd4ff"'
    ' stroke-width="1.6" fill="none" stroke-linecap="round"/>'
    '<path d="M-5,17 L5,17 L6.5,25 L-6.5,25 Z" fill="url(#ng)"/>'
    '<ellipse cx="0" cy="25" rx="6.5" ry="1.6" fill="#222"/>'
)

_FLAME_S = (
    '<g transform="translate(0,25)">'
    '<path d="M0,0 C6,5 5,11 0,17 C-5,11 -6,5 0,0 Z" fill="#ff8c1a"/>'
    '<path d="M0,2 C3.4,5 3,8 0,12 C-3,8 -3.4,5 0,2 Z" fill="#ffd93b"/>'
    '</g>'
)
_FLAME_M = (
    '<g transform="translate(0,25)">'
    '<path d="M0,0 C7,6 6,15 0,23 C-6,15 -7,6 0,0 Z" fill="#ff8c1a"/>'
    '<path d="M0,2 C4.2,6 3.8,11 0,17 C-3.8,11 -4.2,6 0,2 Z"'
    ' fill="#ffd93b"/>'
    '<path d="M0,4 C2,6.5 1.8,9 0,11.5 C-1.8,9 -2,6.5 0,4 Z"'
    ' fill="#fff7c0"/></g>'
)
_FLAME_L = (
    '<g transform="translate(0,25)">'
    '<path d="M0,0 C8,8 7,20 0,32 C-7,20 -8,8 0,0 Z" fill="#ff8c1a"/>'
    '<path d="M0,2 C5,8 4.5,15 0,24 C-4.5,15 -5,8 0,2 Z" fill="#ffd93b"/>'
    '<path d="M0,4 C2.6,8 2.4,12 0,16 C-2.4,12 -2.6,8 0,4 Z"'
    ' fill="#fff7c0"/></g>'
)


def _rocket_svg(flame):
    """機体 + 炎 (同一 viewBox に収めて 1 枚の画像として回す)."""
    return ('<svg xmlns="http://www.w3.org/2000/svg"'
            ' viewBox="-18 -36 36 96">' + _DEFS + _BODY + flame + '</svg>')


# 素の機体は炎ぶんの余白なしの viewBox (描画幅が縮まないように)
ROCKET = ('<svg xmlns="http://www.w3.org/2000/svg"'
          ' viewBox="-18 -36 36 61">' + _DEFS + _BODY + '</svg>')
ROCKET_FL_S = _rocket_svg(_FLAME_S)
ROCKET_FL_M = _rocket_svg(_FLAME_M)
ROCKET_FL_L = _rocket_svg(_FLAME_L)

SMOKE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><defs>'
    '<radialGradient id="s" cx=".5" cy=".5" r=".5">'
    '<stop offset="0" stop-color="#c9d0dc"/>'
    '<stop offset=".55" stop-color="#c9d0dc" stop-opacity=".75"/>'
    '<stop offset="1" stop-color="#c9d0dc" stop-opacity="0"/>'
    '</radialGradient></defs>'
    '<circle cx="10" cy="10" r="10" fill="url(#s)"/></svg>'
)

BURST = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40"><defs>'
    '<radialGradient id="b" cx=".5" cy=".5" r=".5">'
    '<stop offset="0" stop-color="#ffffff"/>'
    '<stop offset=".22" stop-color="#ffe873"/>'
    '<stop offset=".55" stop-color="#ff9a2e"/>'
    '<stop offset="1" stop-color="#ff5722" stop-opacity="0"/>'
    '</radialGradient></defs>'
    '<circle cx="20" cy="20" r="20" fill="url(#b)"/></svg>'
)

EMBER = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8"><defs>'
    '<radialGradient id="e" cx=".5" cy=".5" r=".5">'
    '<stop offset="0" stop-color="#ffe9a8"/>'
    '<stop offset=".6" stop-color="#ff9d2e"/>'
    '<stop offset="1" stop-color="#ff9d2e" stop-opacity="0"/>'
    '</radialGradient></defs>'
    '<circle cx="4" cy="4" r="4" fill="url(#e)"/></svg>'
)

SPARKLE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="-20 -20 40 40">'
    '<path d="M0,-18 L3,-4 L18,0 L3,4 L0,18 L-3,4 L-18,0 L-3,-4 Z"'
    ' fill="#ffd93b"/>'
    '<path d="M0,-9 L1.6,-2 L9,0 L1.6,2 L0,9 L-1.6,2 L-9,0 L-1.6,-2 Z"'
    ' fill="#fff7c0"/></svg>'
)


def _part_svg(vb, body):
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="%s">%s%s</svg>'
            % (vb, _DEFS, body))


# 分解パーツ (ノーズ・胴体上下・窓・フィン×2・ノズル)
P_NOSE = _part_svg('-8 -8 16 16', (
    '<path d="M0,-7.5 C4.5,-1.5 6.5,2.5 7.5,7.5 L-7.5,7.5 C-6.5,2.5'
    ' -4.5,-1.5 0,-7.5 Z" fill="#5f1616"/>'
    '<path d="M0,-7.5 C1.5,-4.5 2.5,-2.5 3.2,0.5 L-3.2,0.5 C-2.5,-2.5'
    ' -1.5,-4.5 0,-7.5 Z" fill="#ffd0c9" opacity=".5"/>'))
P_BODY_U = _part_svg('-9 -9 18 15', (
    '<path d="M0,-9 C8,-8 9,-4 8.5,2 L4.5,5.5 L1,2.5 L-2.5,6 L-6,3 L-8.5,2'
    ' C-9,-4 -8,-8 0,-9 Z" fill="url(#bg)"/>'
    '<path d="M-6,-7 C-7.5,-4 -7.8,-1 -7.6,2 L-5.6,2 C-5.9,-1 -5.6,-5'
    ' -4.4,-7.6 Z" fill="#ffd0c9" opacity=".45"/>'))
P_BODY_L = _part_svg('-9 -8 18 18', (
    '<path d="M-8.5,-7 L-5,-3.5 L-1,-7.5 L3,-4 L8.5,-7 L7.5,9 L-7.5,9 Z"'
    ' fill="url(#bg)"/>'
    '<rect x="-7.5" y="-1" width="15" height="2" fill="#5f1616"'
    ' opacity=".55"/>'
    '<circle cx="-5" cy="0" r=".7" fill="#4a1010"/>'
    '<circle cx="-1.6" cy="0" r=".7" fill="#4a1010"/>'
    '<circle cx="1.8" cy="0" r=".7" fill="#4a1010"/>'
    '<circle cx="5.2" cy="0" r=".7" fill="#4a1010"/>'))
P_WINDOW = _part_svg('-8 -8 16 16', (
    '<circle r="6" fill="#0e2a44"/>'
    '<circle r="6" fill="none" stroke="#e8e8e8" stroke-width="2"/>'
    '<circle r="6" fill="none" stroke="#5f1616" stroke-width=".8"/>'
    '<path d="M-3.4,-2.8 A4.6,4.6 0 0 1 1.6,-4.2" stroke="#9fd4ff"'
    ' stroke-width="1.6" fill="none" stroke-linecap="round"/>'))
P_FIN_L = _part_svg('-9 -11 12 22', (
    '<path d="M2,-11 L-8.5,11 L0.5,5.5 L2,4 Z" fill="#962626"/>'
    '<path d="M2,-11 L-3.5,1 L2,-2 Z" fill="#c96a6a" opacity=".8"/>'))
P_FIN_R = _part_svg('-3 -11 12 22', (
    '<path d="M-2,-11 L8.5,11 L-0.5,5.5 L-2,4 Z" fill="#5c1414"/>'))
P_NOZZLE = _part_svg('-7 -6 14 12', (
    '<path d="M-5,-4 L5,-4 L6.5,4 L-6.5,4 Z" fill="url(#ng)"/>'
    '<ellipse cx="0" cy="4" rx="6.5" ry="1.6" fill="#222"/>'))

# 残骸 (畳んだ行・却下確定カード用の静止画)
WRECK = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 26">'
    + _DEFS +
    '<ellipse cx="30" cy="21" rx="26" ry="3.5" fill="#6b625c"'
    ' opacity=".3"/>'
    '<g transform="translate(14,16) rotate(176)">'
    '<path d="M-8.5,-7 L-5,-3.5 L-1,-7.5 L3,-4 L8.5,-7 L7.5,9 L-7.5,9 Z"'
    ' fill="url(#bg)"/></g>'
    '<g transform="translate(33,15) rotate(-80)">'
    '<path d="M0,-7.5 C4.5,-1.5 6.5,2.5 7.5,7.5 L-7.5,7.5 C-6.5,2.5'
    ' -4.5,-1.5 0,-7.5 Z" fill="#5f1616"/></g>'
    '<g transform="translate(46,18) rotate(-200)">'
    '<path d="M2,-11 L-8.5,11 L0.5,5.5 L2,4 Z" fill="#962626"/></g>'
    '<g transform="translate(52,10)">'
    '<circle r="4.5" fill="#0e2a44"/>'
    '<circle r="4.5" fill="none" stroke="#e8e8e8" stroke-width="1.6"/></g>'
    '<g transform="translate(24,7)">'
    '<circle cx="0" cy="0" r="3" fill="#c9d0dc" opacity=".7"/>'
    '<circle cx="2" cy="-5" r="2.2" fill="#c9d0dc" opacity=".5"/></g>'
    '</svg>'
)

# 機体スプライトの標準サイズ (常駐ロケットと同じ幅 13 に統一)
_RW, _RH = 13, 35


def _sprite(svg, w, h, left=0.0, top=0.0, angle=0.0, opacity=1.0):
    return ft.Container(content=ft.Image(src=svg, width=w, height=h),
                        left=left, top=top, width=w, height=h,
                        rotate=ft.Rotate(angle), opacity=opacity)


def _swap(sprite, svg):
    sprite.content.src = svg


class _Stage:
    """page.overlay に敷く全画面 Stack と、その上のスプライト管理."""

    def __init__(self, page):
        self.page = page
        self.stack = ft.Stack(
            [], width=int(page.width or 760), height=int(page.height or 640))
        page.overlay.append(self.stack)

    def add(self, sprite):
        self.stack.controls.append(sprite)
        return sprite

    def close(self):
        try:
            self.page.overlay.remove(self.stack)
        except ValueError:
            pass
        self.page.update()


def _animate(page, stage, step, done=None):
    """step(t) が False を返すまで約 30fps で回す."""
    def loop():
        t0 = time.time()
        try:
            while True:
                if not step(time.time() - t0):
                    break
                page.update()
                time.sleep(_FRAME)
        except Exception:
            log.debug('ロケット演出を中断', exc_info=True)
        stage.close()
        if done:
            try:
                done()
            except Exception:
                log.debug('ロケット演出の後処理に失敗', exc_info=True)
    threading.Thread(target=loop, daemon=True).start()


class _Puffs:
    """煙・火の粉のスプライトプール."""

    def __init__(self, stage, svg, n, base_size):
        self.items = []
        for _ in range(n):
            s = _sprite(svg, base_size, base_size, opacity=0.0)
            stage.add(s)
            self.items.append({'s': s, 'busy': False})

    def spawn(self, x, y, vx, vy, life, size0, size1, opacity=0.9):
        for it in self.items:
            if not it['busy']:
                it.update(busy=True, x=x, y=y, vx=vx, vy=vy, age=0.0,
                          life=life, s0=size0, s1=size1, op=opacity)
                return

    def tick(self, dt):
        for it in self.items:
            if not it['busy']:
                continue
            it['age'] += dt
            k = it['age'] / it['life']
            if k >= 1.0:
                it['busy'] = False
                it['s'].opacity = 0.0
                continue
            it['x'] += it['vx'] * dt
            it['y'] += it['vy'] * dt
            size = it['s0'] + (it['s1'] - it['s0']) * k
            s = it['s']
            s.left = it['x'] - size / 2
            s.top = it['y'] - size / 2
            s.width = s.height = size
            s.content.width = s.content.height = size
            s.opacity = it['op'] * (1.0 - k)


def play_launch(page, start_x, start_y, target_x=45, target_y=92,
                done=None):
    """発射: 点火 → 震え → 加速上昇 → 弧を描いて「起動」タブへ → ✨.

    到達点の既定は「起動」タブ付近 (リリースは自動更新となって
    起動タブの黄色いタグに現れるため、その予告として飛ばす)。

    (start_x, start_y) はカード右のロケット定位置 (機体中心)。
    """
    stage = _Stage(page)
    smoke = _Puffs(stage, SMOKE, 14, 10)
    embers = _Puffs(stage, EMBER, 6, 4)
    rocket = _sprite(ROCKET_FL_S, _RW, _RH,
                     left=start_x - _RW / 2, top=start_y - 11)
    stage.add(rocket)
    spark = _sprite(SPARKLE, 10, 10, left=target_x - 5, top=target_y - 5,
                    opacity=0.0)
    stage.add(spark)
    page.update()

    top_y = start_y - 105          # まっすぐ上昇の到達点
    state = {'last_smoke': 0.0, 'last_trail': 0.0, 'prev': None}

    def step(t):
        dt = _FRAME
        smoke.tick(dt)
        embers.tick(dt)
        if t < 0.45:
            # 点火: 炎の揺らめき + 機体の震え
            _swap(rocket, ROCKET_FL_M if int(t * 16) % 2 else ROCKET_FL_S)
            rocket.left = start_x - _RW / 2 + (1.6 if int(t * 24) % 2
                                               else -1.6)
            if t - state['last_smoke'] > 0.12:
                state['last_smoke'] = t
                smoke.spawn(start_x + (10 if int(t * 8) % 2 else -10),
                            start_y + 22, (18 if int(t * 8) % 2 else -18),
                            -4, 0.9, 5, 12, 0.7)
        elif t < 1.15:
            # 離陸: 加速しながら真上へ。発射跡に煙だまり
            p = (t - 0.45) / 0.7
            _swap(rocket, ROCKET_FL_L)
            rocket.left = start_x - _RW / 2
            rocket.top = start_y - 11 - 105 * p * p
            if t - state['last_smoke'] > 0.07:
                state['last_smoke'] = t
                side = 22 if int(t * 14) % 2 else -22
                smoke.spawn(start_x, start_y + 20, side * 0.8, -6,
                            1.1, 6, 16, 0.85)
                embers.spawn(start_x, start_y + 24 - 105 * p * p * 0.4,
                             side * 0.2, 30, 0.5, 3, 5, 0.9)
        elif t < 1.95:
            # 弧を描いて「起動」タブへ (機首は進行方向)
            q = (t - 1.15) / 0.8
            e = 1 - (1 - q) * (1 - q)
            p0x, p0y = start_x, top_y
            p1x, p1y = start_x - 30, top_y - 90
            p2x, p2y = target_x, target_y
            u = 1 - e
            x = u * u * p0x + 2 * u * e * p1x + e * e * p2x
            y = u * u * p0y + 2 * u * e * p1y + e * e * p2y
            dx = 2 * u * (p1x - p0x) + 2 * e * (p2x - p1x)
            dy = 2 * u * (p1y - p0y) + 2 * e * (p2y - p1y)
            rocket.rotate = ft.Rotate(math.atan2(dx, -dy))
            _swap(rocket, ROCKET_FL_M)
            rocket.left = x - _RW / 2
            rocket.top = y - 11
            if t - state['last_trail'] > 0.09:
                state['last_trail'] = t
                smoke.spawn(x, y + 14, 0, 8, 0.7, 4, 9, 0.6)
        elif t < 2.5:
            # 到達: 機体は消え、タブで ✨ が弾ける
            k = (t - 1.95) / 0.55
            rocket.opacity = max(0.0, 1.0 - k * 3)
            size = 10 + 26 * min(1.0, k * 1.6)
            spark.opacity = 1.0 if k < 0.7 else (1.0 - (k - 0.7) / 0.3)
            spark.left = target_x - size / 2
            spark.top = target_y - size / 2
            spark.width = spark.height = size
            spark.content.width = spark.content.height = size
            spark.rotate = ft.Rotate(k * 0.9)
        else:
            return False
        return True

    _animate(page, stage, step, done)


def play_crash(page, start_x, start_y, done=None):
    """却下確定: ぐらつき → 転倒 → 爆発と同時にパーツ分解 → 散乱."""
    stage = _Stage(page)
    smoke = _Puffs(stage, SMOKE, 12, 10)
    embers = _Puffs(stage, EMBER, 5, 4)
    rocket = _sprite(ROCKET, _RW, 22, left=start_x - _RW / 2,
                     top=start_y - 11)
    stage.add(rocket)
    burst = _sprite(BURST, 13, 13, opacity=0.0)
    stage.add(burst)
    ground = start_y + 17

    # 破片: (svg, 幅, 初速x, 初速y, 回転速度)
    spec = [
        (P_NOSE, 8, -55, -170, -6.5),
        (P_WINDOW, 7, 62, -195, 4.5),
        (P_BODY_U, 8, -30, -120, -5.0),
        (P_BODY_L, 8, 42, -100, 5.5),
        (P_FIN_L, 7, -95, -70, -8.0),
        (P_FIN_R, 7, 98, -75, 7.5),
        (P_NOZZLE, 7, -18, -45, -4.0),
    ]
    parts = []
    for svg, w, vx, vy, rv in spec:
        s = _sprite(svg, w, w, opacity=0.0)
        stage.add(s)
        parts.append({'s': s, 'x': start_x, 'y': start_y + 6, 'vx': vx,
                      'vy': vy, 'rv': rv, 'a': 0.0, 'landed': False})
    page.update()

    state = {'burst_done': False, 'last_smoke': 0.0}

    def step(t):
        dt = _FRAME
        smoke.tick(dt)
        embers.tick(dt)
        if t < 0.5:
            # ぐらつき (振れ幅がだんだん大きく)
            rocket.rotate = ft.Rotate(0.16 * math.sin(t * 17) * (t / 0.5))
        elif t < 0.95:
            # 転倒: 少し行き過ぎてから横倒しに落ち着く
            p = (t - 0.5) / 0.45
            over = 1.62 * (1 - (1 - p) * (1 - p))
            if p > 0.7:
                over = 1.62 - 0.15 * math.sin((p - 0.7) / 0.3 * math.pi)
            rocket.rotate = ft.Rotate(min(1.47, over))
            rocket.top = start_y - 11 + 8 * p
            if 0.68 < t < 0.78:
                smoke.spawn(start_x - 14, start_y + 18, -20, -8,
                            0.7, 4, 9, 0.6)
        elif t < 2.5:
            if not state['burst_done']:
                # 爆発と同時にパーツへ差し替え
                state['burst_done'] = True
                rocket.opacity = 0.0
                for pt in parts:
                    pt['s'].opacity = 1.0
                burst.opacity = 1.0
            k = min(1.0, (t - 0.95) / 0.5)
            size = 13 + 34 * k
            burst.left = start_x - size / 2
            burst.top = start_y - size / 2
            burst.width = burst.height = size
            burst.content.width = burst.content.height = size
            burst.opacity = max(0.0, 1.0 - k * 1.15)
            for pt in parts:
                if pt['landed']:
                    continue
                pt['vy'] += _G * dt
                pt['x'] += pt['vx'] * dt
                pt['y'] += pt['vy'] * dt
                pt['a'] += pt['rv'] * dt
                if pt['y'] >= ground and pt['vy'] > 0:
                    pt['y'] = ground
                    pt['landed'] = True
                s = pt['s']
                s.left = pt['x'] - s.width / 2
                s.top = pt['y'] - s.height / 2
                s.rotate = ft.Rotate(pt['a'])
            if t - state['last_smoke'] > 0.14 and t < 2.0:
                state['last_smoke'] = t
                smoke.spawn(start_x + (8 if int(t * 7) % 2 else -8),
                            start_y - 4, 4, -34, 1.2, 6, 14, 0.8)
                if t < 1.5:
                    embers.spawn(start_x, start_y, (30 if int(t * 9) % 2
                                                    else -30), -60,
                                 0.5, 3, 4, 0.9)
        else:
            return False
        return True

    _animate(page, stage, step, done)


# ---- カード右の常駐ロケット (一覧内の小さな表示) ----

_ZW, _ZH = 13, 35      # 常駐サイズ (機体 + 炎ぶんの余白)


# 状態ごとの説明 (装飾を「読める部品」にするツールチップ)
_ZONE_TIPS = {
    'idle': '承認がそろうと正式版として発射します',
    'ignited': 'あと 1 人の承認で正式版になります',
    'smoking': '却下が 1 件あります',
    'wreck': '却下が確定しました',
}


def _zone_box(inner, state):
    """全状態を同じ寸法の舞台に載せる (機体・地面の位置がぶれないよう
    上端をそろえ、炎は下の余白へ伸ばす)."""
    return ft.Container(content=inner, width=_ZW + 18, height=36,
                        alignment=ft.Alignment(0, -1),
                        tooltip=_ZONE_TIPS[state])


def zone(state):
    """ボタン行末尾の常駐ロケットを返す。(control, 開いたとき一度の演出fn).

    state: 'idle' (承認まだ) / 'ignited' (承認1つ=点火) /
           'smoking' (却下1つ=煙) / 'wreck' (却下確定=残骸)
    """
    if state == 'wreck':
        inner = ft.Container(content=ft.Image(src=WRECK, width=30,
                                              height=13),
                             margin=ft.Margin.only(top=10))
        return _zone_box(inner, state), None
    if state == 'smoking':
        rocket = ft.Image(src=ROCKET, width=_ZW, height=22)
        puffs = [ft.Container(content=ft.Image(src=SMOKE, width=8,
                                               height=8),
                              width=8, height=8, opacity=0.0,
                              offset=ft.Offset(0, 0),
                              animate_opacity=ft.Animation(400),
                              animate_offset=ft.Animation(
                                  1100, ft.AnimationCurve.EASE_OUT))
                 for _ in range(2)]

        def drift(page_update, puffs=puffs):
            for i, p in enumerate(puffs):
                p.opacity = 0.95 - i * 0.2
                p.offset = ft.Offset(0.35 + i * 0.55, -1.9 - i * 1.0)
                page_update()
                time.sleep(0.35)
            time.sleep(0.9)
            for p in puffs:
                p.opacity = 0.35
            page_update()
        col = ft.Column([ft.Stack([rocket] + puffs, width=_ZW + 8,
                                  height=24)],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER)
        return _zone_box(col, state), drift
    if state == 'ignited':
        img = ft.Image(src=ROCKET_FL_S, width=_ZW, height=_ZH)

        def flicker(page_update, img=img):
            for i in range(8):
                img.src = ROCKET_FL_M if i % 2 else ROCKET_FL_S
                page_update()
                time.sleep(0.16)
            img.src = ROCKET_FL_S
            page_update()
        return _zone_box(img, state), flicker
    img = ft.Image(src=ROCKET, width=_ZW, height=22, opacity=0.5)
    return _zone_box(img, state), None
