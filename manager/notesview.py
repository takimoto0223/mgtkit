# -*- coding: utf-8 -*-
"""更新内容・制限事項・リリースノートの表示 (見出し行と子項目を描き分ける).

本文は Markdown の箇条書き (「- 見出し」と字下げした「  - 子項目」) で
保存されている。文字のまま出すと、半角 2 つの字下げだけでは階層が
読み取りにくい (管理者指示 2026-10)。画面では次のように描き分ける:

- 1 段目: 紺の四角の印 + やや太い字 (項目の見出し)
- 2 段目以降: 1 段ごとに 22px 字下げ + 灰色の「•」(子項目)
- 折り返した行は印の右にそろえる (ぶら下げ字下げ)
- 「## 見出し」は太字の小見出し、印の無い行はそのまま

差分の画面・起動タブの現行版の更新内容・過去の更新ログで共通に使う。
"""
import flet as ft

from . import history

NAVY = '#2b4a6f'
_INDENT = 22        # 1 段の字下げ (px)
_MARK_W = 16        # 印の列の幅 (px)。Row の間隔 2px と合わせて 18px
# 節の見出し (「更新内容」など) の上の余白。項目どうし (6px) より広く取り、
# 節の始まりが「次の項目」に見えないようにする
HEAD_GAP = 14


def _row(mark, body, level, mark_top=0):
    # mark_top: 印を文字の中心へ下げる量 (■ は行箱が文字より低く上に浮く)
    return ft.Container(
        padding=ft.Padding(level * _INDENT, 0, 0, 0),
        content=ft.Row([ft.Container(mark, width=_MARK_W,
                                     padding=ft.Padding(0, mark_top, 0, 0)),
                        body],
                       spacing=2, tight=True,
                       vertical_alignment=ft.CrossAxisAlignment.START))


def notes_column(text, size=13, color='#1f2937', selectable=True):
    """本文を、見出し行と子項目を描き分けた Column にする."""
    rows = []
    prev = None
    for kind, level, body in history.bullet_lines(text):
        if kind == 'blank':
            prev = kind
            continue
        if kind == 'head':
            rows.append(ft.Container(
                ft.Text(body, size=size + 1, weight=ft.FontWeight.BOLD,
                        color='#1f2937', selectable=selectable),
                margin=ft.Margin(0, HEAD_GAP if rows else 0, 0, 0)))
        elif kind == 'item' and level == 0:
            mark = ft.Text('■', size=size - 4, color=NAVY)
            body_t = ft.Text(body, size=size, color='#1f2937',
                             weight=ft.FontWeight.W_600, expand=True,
                             selectable=selectable)
            row = _row(mark, body_t, 0, mark_top=2)
            # 項目と項目の間を子項目どうしより広げる (塊で読めるように)
            if rows and prev != 'head':
                row.margin = ft.Margin(0, 6, 0, 0)
            rows.append(row)
        elif kind == 'item':
            mark = ft.Text('•', size=size, color='#6b7280')
            body_t = ft.Text(body, size=size, color=color, expand=True,
                             selectable=selectable)
            rows.append(_row(mark, body_t, level))
        else:
            rows.append(ft.Text(body, size=size, color=color,
                                selectable=selectable))
        prev = kind
    return ft.Column(rows, spacing=2, tight=True)
