# -*- coding: utf-8 -*-
"""提出差分のアプリ内ダイアログ (Flet 部品による描画).

HTML ビューワ (diffview.render_html) と同じモデル (diffview.collect_model)
を、同じ配色でアプリ内のダイアログとして描く。ブラウザとの行き来を
なくす管理者要望 (2026-08)。

OSS 調査 (2026-08) で借りた慣習:
- 差分行は 1 本の ListView に平らに積んで仮想化を効かせる
  (ファイルごとの入れ子スクロールにしない)
- 追加・削除は色 (緑・赤) に加えて +/- の記号を併記する (色覚対応)
- 変更のない行は「… 変更のない N 行 …」に折りたたむ
- ファイル一覧クリックでそのファイルの位置へジャンプ
"""
import logging
import math
import os
import re
import threading
import unicodedata

import flet as ft
import flet.canvas as cv

from . import diffview

log = logging.getLogger(__name__)

NAVY = '#1e3a5f'

# HTML ビューワ (_CSS) と同じ配色
_TAG_COLORS = {'tagM': ('#92400e', '#fef3c7'),
               'tagA': ('#14532d', '#bbf7d0'),
               'tagD': ('#7f1d1d', '#fecaca')}
_LN_BG, _LN_FG = '#fafbfc', '#9ca3af'
_GAP_BG, _GAP_FG = '#eef2f7', '#6b7280'
_L_BG = {'same': '#ffffff', 'change': '#fecaca',
         'del': '#d3ddee', 'add': '#e5e7eb'}
_R_BG = {'same': '#ffffff', 'change': '#bbf7d0',
         'add': '#bbf7d0', 'del': '#e5e7eb'}
_CODE_FONT = 'Consolas'
_CODE_SIZE = 11.5
_LN_W = 46

# 概算の行高 (ジャンプ先オフセット計算用。フォント差でずれるのは許容)
_H_SEC = 40
_H_SUMROW = 34
_H_FILEHEAD = 46
_H_NOTE = 34
_H_ROW = 16.5
_H_GAP = 24

# canvas の Text は page.theme のフォントを継承しないため、show() で
# 明示的に受け取ってここへ反映する (日本語の豆腐化対策。historyview と同じ)
_font_family = None


def _strip_tags(s):
    return re.sub(r'<[^>]+>', '', s)


def _bold_spans(html_text, size, color):
    """<b>強調</b> を太字 TextSpan にして残りのタグは捨てる.

    HTML 版のリスク説明と同じ要点強調を保つため (レビュー指摘)。
    """
    spans = []
    for i, part in enumerate(re.split(r'<b>(.*?)</b>', html_text)):
        if not part:
            continue
        spans.append(ft.TextSpan(_strip_tags(part), ft.TextStyle(
            size=size, color=color,
            weight=ft.FontWeight.BOLD if i % 2 else None)))
    return spans


def _disp_w(text):
    """コード欄での概算表示幅 (px)。全角は 1 文字ぶん、半角は 0.56 字."""
    w = 0.0
    for ch in text:
        if ch == '\t':
            w += _CODE_SIZE * 0.56 * 4
        elif unicodedata.east_asian_width(ch) in 'WFA':
            w += _CODE_SIZE
        else:
            w += _CODE_SIZE * 0.56
    return w


def _est_lines(text, code_w):
    """折り返し後の概算行数."""
    if not text:
        return 1
    return max(1, int(math.ceil(_disp_w(text) / max(code_w, 1.0))))


def _tag(jp, cls):
    fg, bg = _TAG_COLORS.get(cls, _TAG_COLORS['tagM'])
    return ft.Container(bgcolor=bg, border_radius=3,
                        padding=ft.Padding.symmetric(vertical=1,
                                                     horizontal=7),
                        content=ft.Text(jp, size=11, color=fg))


def _sec_header(text):
    return ft.Container(
        height=_H_SEC, alignment=ft.Alignment(-1, 0),
        content=ft.Container(
            border=ft.Border(left=ft.BorderSide(4, NAVY)),
            padding=ft.Padding.only(left=8),
            content=ft.Text(text, size=14, weight=ft.FontWeight.BOLD,
                            color=NAVY)))


def _stat_text(adds, dels):
    return ft.Text(spans=[
        ft.TextSpan('+%d' % adds, ft.TextStyle(
            size=12, color='#15803d', weight=ft.FontWeight.BOLD)),
        ft.TextSpan(' / ', ft.TextStyle(size=12, color='#6b7280')),
        ft.TextSpan('-%d' % dels, ft.TextStyle(
            size=12, color='#b91c1c', weight=ft.FontWeight.BOLD)),
    ])


def _note_box(note):
    return ft.Container(
        bgcolor='#eff6ff', border_radius=4,
        border=ft.Border(left=ft.BorderSide(3, '#93c5fd')),
        padding=ft.Padding.symmetric(vertical=3, horizontal=8),
        content=ft.Text(note, size=12, color='#1f2937'))


def _fit_left(ctrl):
    """expand する枠の中で子を内容幅のまま左寄せする (全幅に伸ばさない)."""
    return ft.Container(expand=True, alignment=ft.Alignment(-1, 0),
                        content=ctrl)


def _code_cell(text, bg, strike=False):
    style = ft.TextStyle(size=_CODE_SIZE, font_family=_CODE_FONT,
                         color='#64748b' if strike else '#1f2937',
                         decoration=(ft.TextDecoration.LINE_THROUGH
                                     if strike else None))
    return ft.Container(
        expand=True, bgcolor=bg,
        padding=ft.Padding.symmetric(vertical=0, horizontal=6),
        content=ft.Text(text or ' ', style=style, no_wrap=False))


def _ln_cell(lno, mark='', pad_lines=0):
    return ft.Container(
        width=_LN_W, bgcolor=_LN_BG,
        border=ft.Border(right=ft.BorderSide(1, '#eef1f5')),
        padding=ft.Padding.only(right=4),
        alignment=ft.Alignment(1, -1),
        content=ft.Text('%s%s%s' % (lno if lno else '', mark,
                                    '\n' * pad_lines),
                        size=11, color=_LN_FG))


def _diff_row(kind, lno, lt, rno, rt, code_w):
    """左右並びの 1 行 (色 + 記号の併記で色覚に依存しない).

    戻り値: (行のコントロール, 概算行数)。
    注意: ListView の項目内は高さ制約が無限のため STRETCH は使えない
    (レイアウトが壊れて以降の行が描画されなくなる)。左右で折り返し
    行数が違うと背景の高さが揃わないため、行数を概算して短い側に
    改行を足して高さを合わせる。
    """
    lmark = ' -' if kind in ('change', 'del') and lno else ''
    rmark = ' +' if kind in ('change', 'add') and rno else ''
    nl, nr = _est_lines(lt, code_w), _est_lines(rt, code_w)
    n = max(nl, nr)
    lt = (lt or ' ') + '\n' * (n - nl)
    rt = (rt or ' ') + '\n' * (n - nr)
    row = ft.Container(content=ft.Row([
        _ln_cell(lno, lmark, pad_lines=n - 1),
        _code_cell(lt, _L_BG[kind], strike=(kind == 'del')),
        _ln_cell(rno, rmark, pad_lines=n - 1),
        _code_cell(rt, _R_BG[kind]),
    ], spacing=0, vertical_alignment=ft.CrossAxisAlignment.START))
    return row, n


def _gap_row(n):
    return ft.Container(
        height=_H_GAP, bgcolor=_GAP_BG, alignment=ft.Alignment(0, 0),
        content=ft.Text('… 変更のない %d 行 …' % n, size=11,
                        color=_GAP_FG))


def _file_note_text(text):
    return ft.Container(
        padding=ft.Padding.symmetric(vertical=6, horizontal=8),
        content=ft.Text(text, size=12, color='#6b7280'))


def build_items(model, on_jump, code_w=420.0):
    """ダイアログの ListView に積む項目一式を組み立てる.

    code_w はコード欄 1 列の文字幅 (px)。折り返し行数の概算に使う。
    戻り値: (items, offsets)。offsets はファイルパス → ジャンプ先の
    概算スクロール位置 (px)。仮想化 ListView は未描画の項目へ
    key 指定でスクロールできない (Flutter の制約) ため、概算
    オフセット方式を採る。
    """
    items = []
    offsets = {}
    y = 0.0

    def add(ctrl, h):
        nonlocal y
        items.append(ctrl)
        y += h

    add(_sec_header('<1> フォルダ比較 (変更されたファイル %d 件)'
                    % len(model['files'])), _H_SEC)
    for e in model['files']:
        path = e['path']
        cells = [
            _tag(e['status_jp'], e['tag_cls']),
            ft.Container(
                on_click=(lambda _, p=path: on_jump(p)), ink=True,
                tooltip='この場所へ移動', border_radius=4,
                padding=ft.Padding.symmetric(vertical=2, horizontal=4),
                content=ft.Text(diffview._display_path(path), size=13,
                                color='#1d4ed8')),
        ]
        if e['note']:
            cells.append(_fit_left(_note_box(e['note'])))
        else:
            cells.append(ft.Container(expand=True))
        if e['kind'] == 'binary':
            cells.append(ft.Text('(表示対象外)', size=12,
                                 color='#6b7280'))
        else:
            cells.append(_stat_text(e['adds'], e['dels']))
        add(ft.Container(
            border=ft.Border(bottom=ft.BorderSide(1, '#eef1f5')),
            padding=ft.Padding.symmetric(vertical=4, horizontal=10),
            content=ft.Row(cells, spacing=8)), _H_SUMROW)

    legend = ft.Text(spans=[
        ft.TextSpan('ファイル名クリックでその場所へ移動します。'
                    '左 = 変更前 (現在の正式版) / 右 = 変更後 (提出内容)。',
                    ft.TextStyle(size=12, color='#6b7280')),
        ft.TextSpan(' 赤 ', ft.TextStyle(size=12, color='#1f2937',
                                         bgcolor='#fecaca')),
        ft.TextSpan('= 変更前、', ft.TextStyle(size=12, color='#6b7280')),
        ft.TextSpan(' 緑 ', ft.TextStyle(size=12, color='#1f2937',
                                         bgcolor='#bbf7d0')),
        ft.TextSpan('= 変更後・追加、',
                    ft.TextStyle(size=12, color='#6b7280')),
        ft.TextSpan(' 紺 ', ft.TextStyle(
            size=12, color='#1f2937', bgcolor='#d3ddee',
            decoration=ft.TextDecoration.LINE_THROUGH)),
        ft.TextSpan('= 削除された行。',
                    ft.TextStyle(size=12, color='#6b7280')),
        ft.TextSpan('\n※ 各ファイルの青枠の説明は提出時に自動生成された'
                    'ものです (その後の自動修正・統合は反映されません)。'
                    if model['has_notes'] else '',
                    ft.TextStyle(size=12, color='#6b7280')),
    ])
    add(ft.Container(padding=ft.Padding.symmetric(vertical=6,
                                                  horizontal=10),
                     content=legend), 56)

    add(_sec_header('<2> ファイル比較'), _H_SEC)
    for e in model['files']:
        offsets[e['path']] = y
        head = [_tag(e['status_jp'], e['tag_cls']),
                ft.Text(diffview._display_path(e['path']), size=13,
                        weight=ft.FontWeight.BOLD, color='#1f2937')]
        if e['kind'] != 'binary':
            head.append(_stat_text(e['adds'], e['dels']))
        # HTML 版と同じく説明は見出しと同じ行 (短ければ内容幅のまま)
        if e['note']:
            head.append(_fit_left(_note_box(e['note'])))
        else:
            head.append(ft.Container(expand=True))
        add(ft.Container(
            bgcolor='#f1f5f9', border_radius=6,
            margin=ft.Padding.only(top=10),
            padding=ft.Padding.symmetric(vertical=8, horizontal=10),
            content=ft.Row(head, spacing=8)), _H_FILEHEAD + 10)
        if e['kind'] == 'binary':
            add(_file_note_text('画像・バイナリ形式のため差分表示の'
                                '対象外です。'), _H_NOTE)
            continue
        if e['kind'] == 'big':
            add(_file_note_text('%d 行の変更があります。大きすぎるため'
                                '全体は省略しました。' % e['changed']),
                _H_NOTE)
            continue
        for kind, lno, lt, rno, rt in e['rows']:
            if kind == 'gap':
                add(_gap_row(lt), _H_GAP)
            else:
                row, n = _diff_row(kind, lno, lt, rno, rt, code_w)
                add(row, n * _H_ROW)
    return items, offsets


# ---------------- リスク確認 (HTML 版のモーダルと同じ 3 カード) ----------

_FIG_S = 1.5      # SVG (180x74) を 1.5 倍で描く


def _fig_text(x, y, value, size, color, weight=None, center=False):
    """SVG の <text> と同じ座標系 (y = ベースライン) で canvas に置く."""
    s = _FIG_S
    align = ft.Alignment(0, -1) if center else ft.Alignment(-1, -1)
    return cv.Text(x * s, (y - size * 0.9) * s, value,
                   style=ft.TextStyle(size=size * s, color=color,
                                      weight=weight,
                                      font_family=_font_family),
                   alignment=align)


def _fig_stroke(color, width, dash=None):
    s = _FIG_S
    return ft.Paint(color=color, stroke_width=width * s,
                    style=ft.PaintingStyle.STROKE,
                    stroke_cap=ft.StrokeCap.ROUND,
                    stroke_dash_pattern=([d * s for d in dash]
                                         if dash else None))


def _fig_fill(color):
    return ft.Paint(color=color, style=ft.PaintingStyle.FILL)


def _fig_line(x1, y1, x2, y2, color, width, dash=None):
    s = _FIG_S
    return cv.Line(x1 * s, y1 * s, x2 * s, y2 * s,
                   paint=_fig_stroke(color, width, dash))


def _fig_curve(pts, color, width, dash=None):
    """pts = (x0, y0, c1x, c1y, c2x, c2y, x1, y1) の 3 次曲線."""
    s = _FIG_S
    return cv.Path([
        cv.Path.MoveTo(pts[0] * s, pts[1] * s),
        cv.Path.CubicTo(pts[2] * s, pts[3] * s, pts[4] * s, pts[5] * s,
                        pts[6] * s, pts[7] * s),
    ], paint=_fig_stroke(color, width, dash))


def _fig_circle(x, y, r, color):
    s = _FIG_S
    return cv.Circle(x * s, y * s, r * s, paint=_fig_fill(color))


def _risk_fig(idx):
    """資料 4.2 の 3 つのリスク図 (diffview._RISK_CARDS の SVG と同じ絵).

    flutter_svg は <text> を描けないため、canvas で同じ座標のまま
    描き直している。座標・色は SVG (viewBox 180x74) と 1:1 対応。
    """
    if idx == 0:            # ① 基点の消滅
        shapes = [
            _fig_line(8, 52, 80, 52, NAVY, 2.5),
            _fig_curve((40, 50, 60, 32, 78, 28, 96, 28), '#e3c08c', 2,
                       dash=(4, 3)),
            _fig_circle(100, 28, 6, '#ecd2a8'),
            _fig_line(92, 20, 108, 36, '#dc2626', 2.5),
            _fig_line(108, 20, 92, 36, '#dc2626', 2.5),
            _fig_text(100, 12, 'A-1 が削除', 7.5, '#dc2626', center=True),
            _fig_curve((106, 26, 126, 22, 138, 26, 148, 30), '#94a3b8',
                       1.5, dash=(3, 3)),
            _fig_circle(155, 32, 6, '#059669'),
            _fig_text(155, 48, 'A-1-1', 7.5, '#475569', center=True),
            _fig_text(95, 70, '土台を失って宙に浮き、提出不能に', 7.4,
                      '#64748b', center=True),
        ]
    elif idx == 1:          # ② 親リリース後の縮退
        shapes = [
            _fig_line(8, 52, 172, 52, NAVY, 2.5),
            _fig_circle(120, 52, 6, '#0d9488'),
            _fig_text(118, 68, "A-1' (squash 済・別 SHA)", 7.2, '#475569',
                      center=True),
            _fig_curve((26, 50, 44, 30, 60, 24, 76, 24), '#d97706', 2),
            _fig_circle(78, 24, 5, '#d97706'),
            _fig_text(46, 14, 'A-1 (旧)', 7.4, '#92400e', center=True),
            _fig_circle(112, 20, 5, '#059669'),
            _fig_curve((83, 23, 93, 21, 100, 20, 107, 20), '#059669', 2),
            _fig_text(130, 14, 'A-1-1', 7.4, '#14532d', center=True),
            _fig_curve((117, 24, 132, 30, 142, 38, 148, 46), '#dc2626',
                       1.6, dash=(4, 3)),
            _fig_text(152, 36, '!', 8.5, '#dc2626',
                      weight=ft.FontWeight.BOLD),
        ]
    else:                   # ③ レビュー責任の曖昧化
        s = _FIG_S
        shapes = [
            cv.Rect(22 * s, 36 * s, 120 * s, 22 * s, border_radius=4 * s,
                    paint=_fig_fill('#fffbeb')),
            cv.Rect(22 * s, 36 * s, 120 * s, 22 * s, border_radius=4 * s,
                    paint=_fig_stroke('#b45309', 1.2)),
            _fig_text(82, 50, 'A-1 (親) — まだ未承認', 7.6, '#92400e',
                      center=True),
            cv.Rect(22 * s, 8 * s, 120 * s, 22 * s, border_radius=4 * s,
                    paint=_fig_fill('#f0fdf4')),
            cv.Rect(22 * s, 8 * s, 120 * s, 22 * s, border_radius=4 * s,
                    paint=_fig_stroke('#059669', 1.2)),
            _fig_text(82, 22, 'A-1-1 (子) — 承認した', 7.6, '#14532d',
                      center=True),
            cv.Path([cv.Path.MoveTo(150 * s, 12 * s),
                     cv.Path.LineTo(155 * s, 18 * s),
                     cv.Path.LineTo(164 * s, 8 * s)],
                    paint=_fig_stroke('#059669', 2.5)),
            _fig_text(156, 52, '?', 12, '#dc2626',
                      weight=ft.FontWeight.BOLD, center=True),
            _fig_text(88, 70, '親の分まで承認したことになる…?', 7.4,
                      '#64748b', center=True),
        ]
    return cv.Canvas(shapes=shapes, width=180 * _FIG_S,
                     height=74 * _FIG_S)


def _risk_card(idx, cls, title, text):
    bad = cls == 'bad'
    return ft.Container(
        border=ft.Border.all(1, '#fecaca' if bad else '#fde68a'),
        bgcolor='#fff7f7' if bad else '#fffcf0', border_radius=8,
        padding=ft.Padding.symmetric(vertical=8, horizontal=12),
        content=ft.Row([
            ft.Column([
                ft.Text(title, size=12.5, weight=ft.FontWeight.BOLD,
                        color='#b91c1c' if bad else '#b45309'),
                ft.Text(size=11.5, spans=_bold_spans(text, 11.5,
                                                     '#334155')),
            ], spacing=4, tight=True, expand=True),
            _risk_fig(idx),
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.START))


def show(page, model, workrepo, size, close_label='閉じる',
         on_open_browser=None):
    """差分ダイアログを開く (page.show_dialog)。

    size: (幅, 高さ) — 呼び出し側のウィンドウ 8 割ルールに従う。
    close_label: 履歴の詳細から開くときは「戻る」(閉じると背後に戻る)。
    on_open_browser(status_text): 同じ内容をブラウザで開く補助導線
    (印刷や大画面で見たい人向け。None なら出さない)。
    """
    global _font_family
    _font_family = getattr(page.theme, 'font_family', None) \
        if page.theme else None
    col_w, col_h = size
    status = ft.Text('', size=12, color='#6b7280', selectable=True)

    lv = ft.ListView(expand=True, spacing=0, padding=0)

    def _scroll(off):
        async def go():
            await lv.scroll_to(offset=off, duration=200)
        page.run_task(go)

    def on_jump(path):
        off = offsets.get(path)
        if off is not None:
            _scroll(off)

    code_w = (col_w - 2 * _LN_W) / 2 - 12
    items, offsets = build_items(model, on_jump, code_w=code_w)
    lv.controls = items

    save_btn = None

    def do_save(_):
        page.pop_dialog()       # リスク確認を閉じて差分に戻る
        # 押した瞬間に反応: 本体側の保存ボタンを無効化 (二度押し防止)
        if save_btn is not None:
            save_btn.disabled = True
        status.value = '確認用データを作成しています...'
        page.update()

        def work():
            try:
                data = diffview.build_review_zip(model, workrepo)
                dest_dir = os.path.join(os.path.expanduser('~'),
                                        'Downloads')
                os.makedirs(dest_dir, exist_ok=True)
                base = '#%d_確認用' % model['number']
                dest = os.path.join(dest_dir, base + '.zip')
                k = 2
                while os.path.exists(dest):    # 既存を黙って上書きしない
                    dest = os.path.join(dest_dir,
                                        '%s (%d).zip' % (base, k))
                    k += 1
                with open(dest, 'wb') as f:
                    f.write(data)
                status.value = '確認用データを保存しました: %s' % dest
            except Exception as e2:
                log.exception('確認用データの保存に失敗')
                status.value = ('保存できませんでした: %s' % e2)
            finally:
                if save_btn is not None:
                    save_btn.disabled = False
                page.update()
        threading.Thread(target=work, daemon=True).start()

    def confirm_save(_):
        cards = [_risk_card(i, cls, title, text)
                 for i, (cls, title, text, _svg)
                 in enumerate(diffview._RISK_CARDS)]
        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text('保存の前に【注意】'),
            content=ft.Column([
                ft.Text('このデータを保存して開発の土台にすることには、'
                        '資料「バージョン管理と同時開発のしくみ」4.2 に'
                        '示した 3 つのリスクがあります。動作チェックや'
                        '中身の確認に使うのは問題ありません。', size=13),
                *cards,
                ft.Text('ZIP には動作チェック用の「一式」と中身チェック用'
                        'の「変更のみ」が入っています。', size=12,
                        color='#475569'),
                # 高さは内容ぶん (約 540px) を上限に、小さいウィンドウ
                # ではスクロールに逃がす (はみ出してボタンが消えない
                # ように。死に空間はほぼ出ない)
            ], width=620, tight=True, spacing=8,
                scroll=ft.ScrollMode.AUTO, height=min(540, col_h)),
            actions=[
                ft.TextButton('キャンセル',
                              on_click=lambda _: (page.pop_dialog(),
                                                  page.update())),
                ft.FilledButton('リスクを理解した上で保存',
                                bgcolor=NAVY, color='#ffffff',
                                on_click=do_save),
            ]))
        page.update()

    beta = (' ・ β版 %s' % model['beta']) if model.get('beta') else ''
    title = ft.Column([
        ft.Text('#%d %s' % (model['number'], model['title']), size=16,
                weight=ft.FontWeight.BOLD, max_lines=2,
                overflow=ft.TextOverflow.ELLIPSIS),
        ft.Text('提出者: %s%s ・ 基点との比較 (追加 +%d 行 / 削除 -%d 行)'
                % (model['author'], beta, model['n_add'], model['n_del']),
                size=12, color='#6b7280'),
    ], spacing=2, tight=True)

    actions = []
    if on_open_browser is not None:
        actions.append(ft.TextButton(
            'ブラウザで開く', on_click=lambda _: on_open_browser(status)))
    if model['dl_files']:
        save_btn = ft.TextButton('確認用データを保存...',
                                 icon=ft.Icons.DOWNLOAD,
                                 on_click=confirm_save)
        actions.append(save_btn)
    actions.append(ft.TextButton(
        close_label, on_click=lambda _: (page.pop_dialog(),
                                         page.update())))

    # HTML 版の「▲ 先頭へ」(ファイル一覧へ戻る) を右下に重ねる
    top_btn = ft.Container(
        right=18, bottom=10, bgcolor=NAVY, border_radius=16,
        padding=ft.Padding.symmetric(vertical=5, horizontal=12),
        ink=True, on_click=lambda _: _scroll(0),
        content=ft.Text('▲ 先頭へ', size=11.5, color='#ffffff'))
    body = ft.Stack([ft.Container(content=lv, expand=True), top_btn],
                    expand=True)

    page.show_dialog(ft.AlertDialog(
        modal=True,
        title=title,
        content=ft.Column([body, status], width=col_w, height=col_h,
                          spacing=6, tight=True),
        actions=actions))
    page.update()
