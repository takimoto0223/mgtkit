# -*- coding: utf-8 -*-
"""mgtkit アプリマネージャー UI (Flet).

起動方法 (リポジトリルートで): python -m manager.main
Git / GitHub の用語はユーザーに見せず、平易な日本語のみ表示する。
"""
import asyncio
import datetime
import json
import logging
import os
import threading
import time

import flet as ft

import webbrowser

from . import (autofix, conflicts, diffdialog, diffview, feedback, ghcli,
               history, historyview, launcher, localstate, paths,
               reviewcache, reviews, rocketfx, selfupdate, settings,
               submit, uiguard, updater, usage)
from .gitcli import GitError

UPDATE_POLL_SECONDS = 10 * 60  # 新しい安定版の定期チェック間隔

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

NAVY = '#2b4a6f'
AMBER = '#b45309'

# ---- 「β版の確認と承認」一覧の寸法 -----------------------------------------
#
# 発射・爆発の演出は page.overlay の上を画面の絶対座標で動くのに対し、
# Flet には描画後の位置を問い合わせる手段がない。カードごとの基点は
# 行の高さの積み上げでしか求められないため、一覧カードの行の高さは
# すべてここで固定してある。**ここを変えると演出の基点がずれる**。
#
# 唯一の実測値が _LIST_TOP (1 枚目のカードの上端)。ヘッダーとタブバーの
# 高さは Flet と OS のフォントで決まるため計算では出せない。
# 2 枚目以降はここからの積み上げで正確に出せる。
_TAB_TOP = 92          # ヘッダー + タブバーの高さ (✨ の到達点と同じ)
_TAB_PAD = 24          # タブの中身の余白
_INTRO_H = 119         # 一覧の上の説明文 2 段ぶん (最小幅 760 での高さ)
_INTRO_GAP = 16        # 説明文と一覧のあいだ
_CARD_GAP = 8          # カードとカードのすき間 (t5_list の spacing)
_CARD_PAD = 12         # カードの内側の余白
_LINE_GAP = 6          # カードの中の行間
_H_TITLE = rocketfx.ZONE_H   # タイトル行 (常駐ロケットの高さで決まる)
_H_BADGE = 24          # バッジの行
_H_TEXT = 18           # 「承認済み」の 1 行
_H_REJECT = 36         # 差し戻しの理由 (2 行まで。続きはマウスオーバー)
_H_NOTE = 42           # 統合待ちの案内 (2 行ぶん)
_H_BTNS = 40           # ボタンの行
# 一覧の 1 枚目のカードの上端 (画面上の絶対位置)
_LIST_TOP = _TAB_TOP + _TAB_PAD + _INTRO_H + _INTRO_GAP


def downloads_dir():
    """ダウンロードフォルダ (保存先の初期表示)."""
    return os.path.join(os.path.expanduser('~'), 'Downloads')


def unique_path(path):
    """すでにあるファイルを黙って上書きしないよう、空き名にずらす."""
    base, ext = os.path.splitext(path)
    k, out = 2, path
    while os.path.exists(out):
        out = '%s (%d)%s' % (base, k, ext)
        k += 1
    return out


async def save_path_dialog(picker, file_name,
                           dialog_title='保存先を選んでください'):
    """保存先を OS の画面でたずねて、そのパスを返す (取り消しなら None).

    ダウンロードの印を押したら、まず**どこに保存するか**を聞く
    (勝手にダウンロードフォルダへ置くと、あとで探せなくなるため。
    管理者指示 2026-08)。提出の「提出する ZIP を選択」と同じ、
    OS のファイル選択画面が出る。初期表示はダウンロードフォルダ。

    ブラウザ表示など OS の画面を出せない環境でだけ、ダウンロード
    フォルダの空き名を返して従来どおり保存する (ValueError はその
    合図。それ以外の失敗は握りつぶさず呼び出し側へ伝える)。
    """
    init_dir = downloads_dir()
    ext = file_name.rsplit('.', 1)[-1] if '.' in file_name else None
    try:
        return await picker.save_file(
            dialog_title=dialog_title, file_name=file_name,
            initial_directory=(init_dir if os.path.isdir(init_dir)
                               else None),
            file_type=(ft.FilePickerFileType.CUSTOM if ext
                       else ft.FilePickerFileType.ANY),
            allowed_extensions=([ext] if ext else None))
    except ValueError:
        # web / モバイルでは OS の画面を出せない (flet の仕様)
        log.info('保存先の画面を出せないため既定の置き場所を使います')
        os.makedirs(init_dir, exist_ok=True)
        return unique_path(os.path.join(init_dir, file_name))


def main(page: ft.Page):
    page.title = 'mgtkit アプリマネージャー'
    # OS のダークモード設定に追従させず、ガイドと同じ見た目に固定する
    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding = 0
    try:
        page.window.width = 760
        page.window.height = 640
    except AttributeError:
        pass

    config = paths.load_config()
    repo = paths.repo_slug(config)
    stable = paths.stable_dir(config)

    # ---------------- 共通部品 ----------------

    def status_text():
        return ft.Text('', size=13, color='#555555', selectable=True)

    def run_bg(fn):
        threading.Thread(target=fn, daemon=True).start()

    def run_ui(fn, on_error=None):
        """裏スレッドから画面を書き換えるときの共通入口 (必ずこれを通す).

        Flet は画面の更新を送信キューに積むだけで、積んだ側からは
        画面の処理ループを起こせない。裏スレッドから直接書き換えると、
        利用者が次に何か操作するまで実機 (デスクトップ) に届かない
        (2026-08 の実機報告「別の作業をすると表示される」の原因)。
        run_task はループを起こす経路なので、そこに載せて実行する。
        on_error(e) を渡すと、fn の失敗をそこに知らせる (別タスクで
        走るため、呼び出し側の try では捕まえられないため)。
        """
        async def apply():
            try:
                fn()
            except Exception as e:
                log.exception('画面の更新に失敗')
                if on_error is not None:
                    on_error(e)
        try:
            page.run_task(apply)
        except (RuntimeError, AttributeError) as e:
            # ループがまだ無い / セッションが閉じた等。直接実行に戻すが、
            # この経路では画面が届かないことがあるので記録は残す
            log.warning('画面更新をループに載せられません: %s', e)
            fn()

    def _page_loop():
        """この画面のイベントループ (取れないときは None)."""
        try:
            return page.session.connection.loop
        except AttributeError:
            return None

    # 画面に触る入口はどこから呼ばれてもループ上で行うようにしておく
    # (個々の run_bg の中の書き換えを 1 箇所でまとめて安全にする。
    # 新しく書くコードで run_ui を忘れても取りこぼさない)
    # (名前は他のラッパーと衝突させないこと。同じ名前を後から def
    #  すると、この中の参照までそちらに向いて無限再帰になる)
    _raw_update = page.update
    _raw_show_dialog = page.show_dialog
    _raw_pop_dialog = page.pop_dialog

    def _on_loop():
        """いま画面のループの上にいるか (= そのまま書き換えてよいか)."""
        try:
            here = asyncio.get_running_loop()
        except RuntimeError:
            return False
        # 別のループ (裏で asyncio.run するワーカー等) の上かもしれない
        theirs = _page_loop()
        return theirs is None or here is theirs

    def _update_on_loop(*controls, **kwargs):
        if _on_loop():
            _raw_update(*controls, **kwargs)
        else:
            run_ui(lambda: _raw_update(*controls, **kwargs))

    def _show_dialog_on_loop(dialog):
        if _on_loop():
            _raw_show_dialog(dialog)
        else:
            run_ui(lambda: _raw_show_dialog(dialog))

    def _pop_dialog_on_loop():
        if _on_loop():
            return _raw_pop_dialog()
        run_ui(_raw_pop_dialog)
        return None

    page.update = _update_on_loop
    page.show_dialog = _show_dialog_on_loop
    page.pop_dialog = _pop_dialog_on_loop

    # 押した瞬間に必ず画面を反応させるための共通部品。
    # 人は反応がないと何度もクリックするため、ボタンを押したら
    # 「無効化 + 進行中の文言」を先に表示し、時間のかかる処理は
    # すべて run_bg で裏に回す (このファイル全体の方針)。

    def dialog_busy(btn, note, msg):
        """ダイアログの確定ボタンを押した瞬間の反応 (二度押し防止つき).

        btn を無効化し、note (エラー表示欄) に灰色で進行中の文言を出す。
        失敗したら dialog_error で押せる状態に戻す。
        """
        btn.disabled = True
        note.color = '#555555'
        note.value = msg
        page.update()

    def dialog_error(btn, note, msg):
        """ダイアログ内の失敗表示。ボタンを押せる状態に戻す."""
        btn.disabled = False
        note.color = '#b91c1c'
        note.value = msg
        page.update()

    # ファイルの選択・保存先の選択に使う共通の部品 (画面に 1 つだけ持つ)
    file_picker = ft.FilePicker()
    page.services.append(file_picker)

    def ask_save_path(file_name, dialog_title='保存先を選んでください'):
        """保存先をたずねる (提出の「ZIP を選択」と同じ OS の画面)."""
        return save_path_dialog(file_picker, file_name, dialog_title)

    def tab_label(text):
        """タブ名 + 件数バッジ (黄色い丸に数字)。バッジ Container を返す."""
        badge = ft.Container(
            width=20, height=20, border_radius=10, bgcolor='#facc15',
            visible=False, alignment=ft.Alignment(0, 0),
            content=ft.Text('', size=11, weight=ft.FontWeight.BOLD,
                            color='#713f12'))
        return ft.Row([ft.Text(text), badge], spacing=6), badge

    def set_badge(badge, count):
        badge.content.value = '99+' if count > 99 else str(count)
        badge.visible = count > 0
        badge.bgcolor = '#facc15'
        badge.tooltip = None

    def on_show_usage(_):
        """API 利用量ダイアログ (月小計 + 30日グラフ + 日別 + 通算合計)."""
        s = usage.summary(config)
        rate = usage.usd_jpy_rate(config)
        p = usage.pricing(config)

        def usd(v):
            return '$%.2f' % v if (v >= 0.005 or v == 0) else '$%.3f' % v

        def jpy(v):
            return '約¥{:,}'.format(round(v * rate))

        def short(d):
            return d[5:].replace('-', '/')

        max_usd = max((u for _, u, _, _, _ in s['days']), default=0.0)
        bars = [ft.Container(
            width=13, border_radius=2,
            height=4 + (108 * u / max_usd if max_usd else 0),
            bgcolor=NAVY if u else '#d1d5db',
            tooltip='%s: %s (%d回)' % (short(d), usd(u), calls))
            for d, u, _, _, calls in s['days']]
        day_lines = [
            ft.Text('%s: %s (%s) ・ %d回 ・ 入力 %s / 出力 %s tok'
                    % (short(d), usd(u), jpy(u), calls,
                       format(tin, ','), format(tout, ',')),
                    size=12, color='#374151')
            for d, u, tin, tout, calls in reversed(s['days']) if calls]
        if not day_lines:
            day_lines = [ft.Text('この 30 日間の利用はありません。',
                                 size=12, color='#6b7280')]
        page.show_dialog(ft.AlertDialog(
            title=ft.Text('API 利用量 (この PC のマネージャー経由分)'),
            content=ft.Column([
                ft.Text('%s の小計: %s (%s)'
                        % (s['month'], usd(s['month_usd']),
                           jpy(s['month_usd'])),
                        size=16, weight=ft.FontWeight.BOLD, color=NAVY),
                ft.Container(
                    bgcolor='#f5f7fa', border_radius=6,
                    padding=ft.Padding.symmetric(vertical=8, horizontal=10),
                    content=ft.Column([
                        ft.Row(bars, spacing=3,
                               vertical_alignment=ft.CrossAxisAlignment.END),
                        ft.Row([
                            ft.Text('30日前', size=10, color='#9ca3af'),
                            ft.Container(expand=True),
                            ft.Text('今日', size=10, color='#9ca3af'),
                        ]),
                    ], spacing=2)),
                ft.Text('日ごとの利用料 (新しい順)', size=12,
                        weight=ft.FontWeight.BOLD),
                ft.Column(day_lines, spacing=2, height=140,
                          scroll=ft.ScrollMode.AUTO),
                ft.Divider(),
                ft.Text('通算合計: %s (%s) ・ %d回 ・ 入力 %s / 出力 %s tok'
                        % (usd(s['total_usd']), jpy(s['total_usd']),
                           s['total_calls'], format(s['total_in'], ','),
                           format(s['total_out'], ',')),
                        size=13, weight=ft.FontWeight.BOLD),
                ft.Text('※ 金額は単価設定 (入力 $%.2f / 出力 $%.2f '
                        'per 100万トークン) からの目安です。正確な請求額は '
                        'Claude Console で確認してください。'
                        '他の PC や他のアプリでの利用は含みません。'
                        % (p['input_per_mtok'], p['output_per_mtok']),
                        size=11, color='#6b7280'),
            ], tight=True, width=620),
            actions=[ft.TextButton('閉じる',
                                   on_click=lambda _: page.pop_dialog())]))

    def header():
        return ft.Container(
            bgcolor=NAVY,
            padding=ft.Padding.symmetric(vertical=10, horizontal=18),
            content=ft.Row([
                ft.Text('mgtkit', size=20, weight=ft.FontWeight.BOLD,
                        color='#ffffff'),
                ft.Text('アプリマネージャー', size=12, color='#ffffffcc'),
                ft.Container(expand=True),
                ft.IconButton(ft.Icons.BAR_CHART, icon_color='#ffffff',
                              icon_size=20, tooltip='API 利用量',
                              on_click=on_show_usage),
            ], spacing=10),
        )

    # ---------------- タブ1: 起動 ----------------

    t1_version = ft.Text('', size=16, weight=ft.FontWeight.BOLD)
    t1_meta = ft.Text('', size=12, color='#555555')   # 恒久情報 (配布日)
    t1_fresh = ft.Text('', size=12, color='#9ca3af')  # 確認状態の常時表示
    t1_status = status_text()                          # 進捗・エラー専用
    t1_notice = ft.Text('', size=13, weight=ft.FontWeight.BOLD, color=AMBER)
    join_notice = ft.Text('', size=12, color='#555555')
    # 自動更新の完了通知: 黄色いタグ (更新タブ廃止に伴う導線)
    t1_update_tag = ft.Container(
        visible=False, bgcolor='#fef08a', border_radius=6,
        padding=ft.Padding.symmetric(vertical=6, horizontal=12),
        content=ft.Text('', size=13, weight=ft.FontWeight.BOLD,
                        color='#713f12'))
    # リリース公開待ちの案内: バッジと同色の黄色タグ + 今すぐ確認ボタン
    # (ロケットが起動タブに届いてから、更新の取り込み完了まで表示)
    t1_preparing_text = ft.Text(
        '新しい正式版を準備中です (数分かかります)。公開されると自動で'
        '更新されます。', size=13, weight=ft.FontWeight.BOLD,
        color='#713f12', expand=True)
    t1_preparing_tag = ft.Container(
        visible=False, bgcolor='#fef08a', border_radius=6,
        padding=ft.Padding.symmetric(vertical=2, horizontal=12),
        content=ft.Row([
            t1_preparing_text,
            ft.IconButton(ft.Icons.REFRESH, icon_size=18,
                          icon_color='#713f12', tooltip='今すぐ確認',
                          on_click=lambda e: on_check_now(e)),
        ], spacing=4))
    # 現行版の更新内容 (常設。何が入っている版かを後から確認できる)
    t1_notes_head = ft.Text('', size=13, weight=ft.FontWeight.BOLD,
                            color='#374151')
    t1_notes_body = ft.Text('', size=13, selectable=True, color='#374151')
    # AI が自動で調整した箇所は琥珀色の枠で色分けして見せる
    t1_notes_ai_body = ft.Text('', size=12.5, selectable=True,
                               color='#78350f')
    t1_notes_ai_box = ft.Container(
        visible=False, bgcolor='#fffbeb', border_radius=8,
        border=ft.Border.all(1.5, '#f59e0b'),
        padding=ft.Padding.symmetric(vertical=10, horizontal=12),
        content=ft.Column([
            ft.Text('AI が自動で調整した箇所', size=12.5,
                    weight=ft.FontWeight.BOLD, color='#92400e'),
            t1_notes_ai_body,
        ], spacing=6, tight=True))
    t1_notes_box = ft.Container(
        visible=False, bgcolor='#f5f7fa', border_radius=6, padding=12,
        content=ft.Column([t1_notes_head, t1_notes_body, t1_notes_ai_box],
                          spacing=6))
    t1_launch_btn = ft.FilledButton('起動', icon=ft.Icons.PLAY_ARROW,
                                    bgcolor=NAVY, color='#ffffff')
    # installing: 自動更新の実行中 (二重実行と起動の衝突を防ぐ) /
    # pending: アプリ起動中などで取り込めなかった新しい正式版
    # (次回「起動」時に取り込む)
    _update_state = {'installing': False, 'pending': None}

    # 空の案内 Text が行として残ると余白がガタつくため、描画のたびに
    # 空なら行ごと畳む (起動タブの案内は設定箇所が多いため一括処理)
    _page_update_orig = page.update

    def _page_update(*args, **kwargs):
        for c in (t1_fresh, t1_status, t1_notice, join_notice):
            c.visible = bool(c.value)
        return _page_update_orig(*args, **kwargs)

    page.update = _page_update

    def refresh_local_version(latest=None):
        """版の見出しを更新する。latest=True のときだけ「(最新)」を添える
        (取り込み待ちで古い版のまま「最新」と出さないため)."""
        info = updater.local_version_info(stable)
        if info is None:
            t1_version.value = 'アプリはまだ取り込まれていません'
            t1_meta.value = ('初回はアプリの取り込みに数分かかります。'
                             '「起動」を押してそのままお待ちください。')
        else:
            t1_version.value = ('現行版 %s%s'
                                % (info.get('version', '?'),
                                   ' (最新)' if latest else ''))
            t1_meta.value = '配布日: %s' % info.get('distributed_at', '-')
        page.update()

    def _clean_notes(tag, notes):
        """更新内容の表示用整形: 版名と重複する先頭行を取り除く.

        リリースノートは「vX.Y リリース。」で始まる書式が多く、版名の
        見出しと並べると重複するため表示時だけ落とす (原文は変えない)。
        """
        dup = (tag, '%s リリース' % tag, '%sリリース' % tag,
               '%s リリースノート' % tag, 'mgtkit %s リリースノート' % tag)
        lines = (notes or '').strip().splitlines()
        while lines and (not lines[0].strip()
                         or lines[0].strip().lstrip('#').strip().rstrip('。.')
                         in dup):
            lines.pop(0)
        return '\n'.join(lines).strip()

    _window_fitted = {'done': False}

    def _fit_window_to_notes(notes):
        """起動時のウィンドウを更新内容が折り返し無しで読める大きさに.

        規定値 (760x640) は固定せず、現行版のリリースノートの最長行が
        折り返さない幅・全行が入る高さに合わせる (画面からあふれない
        よう上限つき)。起動時に 1 回だけ。手でのリサイズは妨げない。
        """
        if _window_fitted['done']:
            return
        lines = (notes or '').splitlines()
        if not lines:
            return
        widest = max(sum(13 if ord(ch) > 0x2500 else 7.2 for ch in line)
                     for line in lines)
        try:
            page.window.width = int(min(1240, max(760, widest + 150)))
            page.window.height = int(min(1000, max(640,
                                                   360 + len(lines) * 21)))
            _window_fitted['done'] = True
            page.update()
        except AttributeError:
            pass

    def _refresh_current_notes(releases=None):
        """「現行版の更新内容」の常設表示を最新化する."""
        if releases is None:
            cached = reviewcache.get() or reviewcache.load_from_disk(config)
            releases = (cached or {}).get('releases') or []
        local_v = (updater.local_version_info(stable) or {}).get('version')
        rel = next((r for r in releases
                    if not r['prerelease'] and r['tag'] == local_v), None)
        if rel is None:
            t1_notes_box.visible = False
        else:
            t1_notes_head.value = '現行版 (%s) の更新内容' % rel['tag']
            normal, ai = history.split_ai_note(
                _clean_notes(rel['tag'], rel.get('notes')))
            t1_notes_body.value = normal or '(更新内容の記載はありません)'
            t1_notes_ai_body.value = ai or ''
            t1_notes_ai_box.visible = bool(ai)
            t1_notes_box.visible = True
            _fit_window_to_notes(t1_notes_body.value)
        page.update()

    def _show_updated(release):
        """自動更新の完了を黄色いタグで知らせ、更新内容の常設欄も更新する.

        公開待ちの案内 (準備中タグ・起動タブのバッジ) はここで役目を
        終えるため消す。
        """
        t1_update_tag.content.value = ('アプリを %s に更新しました。'
                                       'このまま「起動」してお使い'
                                       'いただけます。' % release['tag'])
        t1_update_tag.visible = True
        t1_preparing_tag.visible = False
        set_badge(launch_badge, 0)
        t1_notice.value = ''
        _refresh_current_notes()
        page.update()

    def _auto_update(latest):
        """新しい正式版を自動で取り込む (更新タブ廃止に伴い手動操作なし).

        アプリが起動中 (Windows ではフォルダが使用中で置き換え不可) の
        ときは作業を邪魔しないため取り込まず、次回「起動」時に取り込む
        予約だけする。戻り値: 取り込んだら True。

        このとき「アプリを使用中です」とは書かない (管理者指示 2026-08)。
        ブラウザの画面を閉じてもアプリ本体はしばらく動いたままなので、
        本人は使っていないつもりなのに使用中と言われて戸惑う。言えるのは
        「更新版を開くには、もう一度「起動」を押す」という次の一手だけ。
        """
        if _update_state['installing'] or latest is None:
            return False
        if launcher.port_in_use(paths.stable_port(config)):
            _update_state['pending'] = latest
            t1_preparing_tag.visible = False  # 公開待ちは終わった
            set_badge(launch_badge, 1)        # 取り込みが済むまでは残す
            t1_notice.value = ('新しい版 %s があります。更新版を'
                               '使うときは、もう一度「起動」を押して'
                               'ください。' % latest['tag'])
            page.update()
            return False
        _update_state['installing'] = True
        t1_launch_btn.disabled = True
        page.update()
        try:
            def progress(msg):
                t1_status.value = ('新しい版 %s を取り込んでいます... %s'
                                   % (latest['tag'], msg))
                page.update()
            updater.install_release(repo, latest, stable,
                                    on_progress=progress)
            _update_state['pending'] = None
            t1_status.value = ''
            refresh_local_version(latest=True)
            _show_updated(latest)
            ok = True
        except Exception:
            log.exception('自動更新に失敗しました')
            t1_preparing_tag.visible = False
            t1_notice.value = ('自動更新に失敗しました。ネットワーク接続を'
                               '確認してください (次回の起動時にもう一度'
                               '試します)。')
            _update_state['pending'] = latest
            ok = False
        _update_state['installing'] = False
        t1_launch_btn.disabled = False
        page.update()
        return ok

    def _launch(update_first=True):
        """起動する。update_first=True なら取り込み待ちの版を先に取り込む."""
        if _update_state['installing']:
            t1_status.value = ('新しい版の取り込み中です。'
                               '完了までお待ちください。')
            page.update()
            return
        t1_update_tag.visible = False   # 更新完了の通知は一度見たら畳む
        t1_status.value = '起動しています...'
        t1_launch_btn.disabled = True   # 取り込み中の二度押しを防ぐ
        page.update()

        def work():
            def progress(msg):
                t1_status.value = msg
                page.update()
            try:
                # 取り込み待ちの新しい正式版があれば起動前に取り込む
                pending = _update_state['pending']
                if update_first and pending is not None:
                    progress('新しい版 %s に更新しています...'
                             % pending['tag'])
                    launcher.stop_app(paths.stable_port(config))
                    updater.install_release(repo, pending, stable,
                                            on_progress=progress)
                    _update_state['pending'] = None
                    refresh_local_version(latest=True)
                    _show_updated(pending)
                if updater.local_version_info(stable) is None:
                    # 初回: 最新の正式版を自動で取得してから起動する
                    progress('最新の版を確認しています...')
                    latest = ghcli.latest_stable(
                        ghcli.fetch_releases(repo))
                    if latest is None:
                        t1_status.value = ('配布された正式版がまだありません。'
                                           '管理者に確認してください。')
                        t1_launch_btn.disabled = False
                        page.update()
                        return
                    # 前回の残骸のサーバーが動いているとフォルダを
                    # 置き換えられないため、先に終了させる
                    launcher.stop_app(paths.stable_port(config))
                    updater.install_release(repo, latest, stable,
                                            on_progress=progress)
                    refresh_local_version(latest=True)
                    _refresh_current_notes()
                _, url = launcher.launch_app(
                    stable, paths.stable_port(config), channel='stable')
                t1_status.value = 'ブラウザで開きます: %s' % url
            except (ghcli.GhError, launcher.LaunchError, Exception) as e:
                log.exception('起動に失敗しました')
                t1_status.value = str(e) or '起動に失敗しました。'
            t1_launch_btn.disabled = False
            page.update()
        run_bg(work)

    def on_launch_stable(_):
        # 取り込み待ちの版があり、かつアプリを使用中なら、黙って終了させず
        # 確認してから更新する (入力中の内容を失わせないため)
        pending = _update_state['pending']
        if (pending is not None
                and launcher.port_in_use(paths.stable_port(config))):
            def do_update(_):
                page.pop_dialog()
                _launch(update_first=True)

            def later(_):
                page.pop_dialog()
                _launch(update_first=False)

            page.show_dialog(ft.AlertDialog(
                modal=True,
                title=ft.Text('%s に更新して開き直しますか?'
                              % pending['tag']),
                content=ft.Text('前に開いたアプリがまだ動いています。'
                                '更新するにはいったん終了する必要が'
                                'あります。ブラウザに入力中の内容が'
                                'あれば保存してから「更新して開く」を'
                                '押してください。', size=13, width=440),
                actions=[
                    ft.TextButton('あとにする (このまま開く)',
                                  on_click=later),
                    ft.FilledButton('更新して開く', on_click=do_update,
                                    bgcolor=NAVY, color='#ffffff'),
                ]))
            return
        _launch(update_first=True)

    t1_launch_btn.on_click = on_launch_stable

    def on_check_now(_):
        """公開待ちタグの「今すぐ確認」。時刻スタンプで動きを見せる."""
        t1_fresh.value = '新しい版がないか確認しています...'
        page.update()
        check_update_notice(reschedule=False)

    def _download_history_zip(rel, dlg_status):
        """過去の正式版の ZIP を保存する (まず保存先をたずねる)."""
        async def handler(e):
            btn = e.control
            dest = await ask_save_path('mgtkit-%s.zip' % rel['tag'],
                                       '%s の ZIP の保存先' % rel['tag'])
            if not dest:
                dlg_status.value = '保存を取り消しました。'
                page.update()
                return
            btn.disabled = True     # 反応が見えず二度押しされるのを防ぐ
            dlg_status.value = '%s を取得しています...' % rel['tag']
            page.update()

            def work():
                import shutil
                import tempfile
                tmp = tempfile.mkdtemp(prefix='mgtkit_hist_')
                try:
                    path, _src = ghcli.download_release(
                        repo, rel['tag'], tmp,
                        has_assets=bool(rel.get('assets')))
                    shutil.copyfile(path, dest)
                    dlg_status.value = 'ZIP を保存しました: %s' % dest
                except Exception as e2:
                    log.exception('過去の版の取得に失敗しました')
                    dlg_status.value = (str(e2)
                                        or 'ダウンロードに失敗しました。')
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                    btn.disabled = False
                    page.update()
            run_bg(work)
        return handler

    def _person_dot(color):
        return ft.Container(width=11, height=11, border_radius=6,
                            bgcolor=color)

    def _stable_summary(s):
        """一覧行の 1 行概要 (提出タイトル。無ければノートの先頭行)."""
        if s['pr']:
            return s['pr'].get('title') or ''
        note = _clean_notes(s['tag'],
                            (s['release'].get('notes') or '')) or ''
        first = note.strip().splitlines()
        return (first[0].lstrip('-· ').strip() if first else '') or '配布'

    # 過去の更新ログの「図へ戻る」用 (開いている図と一覧への参照)
    _history_ctx = {'body_col': None, 'fig': None}

    def _show_history_detail(kind, payload, tl):
        """帯・ノード・一覧の行から開く更新内容 (リリースノート) の画面.

        ZIP の保存ボタンはこの画面に置く (図の中には置かない)。
        """
        authors = tl['authors']
        chip_by_target = {c['target_tag']: c for c in tl['chips']
                          if not c['pending']}
        st = ft.Text('', size=12, color='#555555', selectable=True)

        is_pending = kind == 'chip' and payload['pending']
        if is_pending:
            c = payload
            color = history.person_color(c['author'], authors)
            meta = ('%s #%s · %s を基に作成 · %s 提出'
                    % (c['author'], c['number'], history.base_label(c),
                       history.fmt_date(c['start'], with_year=True)))
            content = [
                ft.Row([_person_dot(color),
                        ft.Text(meta, size=12, color='#475569',
                                expand=True)], spacing=8),
                ft.Text(c['title'], size=13, color='#374151',
                        selectable=True),
                ft.Container(
                    bgcolor='#fef3c7', border_radius=8,
                    padding=ft.Padding.symmetric(vertical=6, horizontal=10),
                    content=ft.Text('確認と承認の途中です。承認がそろって'
                                    '取り込まれると、正式版として公開'
                                    'されます。', size=12,
                                    color='#92400e')),
                st,
            ]
            title = '提出 #%s の内容' % c['number']
        else:
            if kind == 'chip':
                s = next((x for x in tl['stables']
                          if x['tag'] == payload['target_tag']), None)
                if s is None:
                    return
            else:
                s = payload
            c = chip_by_target.get(s['tag'])
            rel = s['release']
            title = '%s の更新内容' % s['tag']
            if c:
                color = history.person_color(c['author'], authors)
                meta = ('%s #%s · %s を基に作成 · %s 提出 → %s 公開'
                        % (c['author'], c['number'], history.base_label(c),
                           history.fmt_date(c['start'], with_year=True),
                           history.fmt_date(c['end'])))
            else:
                color = '#94a3b8'
                meta = '%s 公開' % history.fmt_date(s['date'],
                                                    with_year=True)
            normal, ai = history.split_ai_note(
                _clean_notes(s['tag'], rel.get('notes')))
            content = [
                ft.Row([_person_dot(color),
                        ft.Text(meta, size=12, color='#475569',
                                expand=True)], spacing=8),
                ft.Text(normal or '(更新内容の記載はありません)',
                        size=13, color='#374151', selectable=True),
            ]
            if ai:
                # AI が自動で調整した箇所は琥珀色の枠で色分けして残す
                content.append(ft.Container(
                    bgcolor='#fffbeb', border_radius=8,
                    border=ft.Border.all(1.5, '#f59e0b'),
                    padding=ft.Padding.symmetric(vertical=10, horizontal=12),
                    content=ft.Column([
                        ft.Text('AI が自動で調整した箇所', size=12.5,
                                weight=ft.FontWeight.BOLD,
                                color='#92400e'),
                        ft.Text(ai, size=12, color='#78350f',
                                selectable=True),
                    ], spacing=6, tight=True)))
            # ZIP の保存ボタンは一覧の行と重複するためここには置かない
            # (管理者指示)。案内だけ残す
            content.append(ft.Text(
                '提出済みの計算書を当時の版で再現したいときは、一覧の'
                'この版の「ZIP を保存」を使ってください。', size=11,
                color='#4b5563'))

        _history_ctx['open'] = False    # 詳細表示中は図を開き直さない

        def close_all(_):
            _history_ctx['open'] = False
            page.pop_dialog()   # この画面
            page.pop_dialog()   # 背後の過去の更新ログ
            page.update()

        def back_to_figure(_):
            _history_ctx['open'] = True
            page.pop_dialog()
            page.update()

            async def back_scroll():
                # 「図へ」の約束どおり、図 (現行版の位置) まで戻す
                await asyncio.sleep(0.45)
                try:
                    body = _history_ctx.get('body_col')
                    if body is not None:
                        await body.scroll_to(offset=0, duration=10)
                    fig = _history_ctx.get('fig')
                    if fig:
                        await fig['scroll_row'].scroll_to(
                            offset=fig['initial_offset'], duration=10)
                except Exception:
                    log.debug('図へ戻るスクロールに失敗 (表示には影響'
                              'なし)', exc_info=True)
            page.run_task(back_scroll)

        # 中身の量に高さを合わせる (長いノートだけスクロールにする)
        def _est_lines(text):
            return sum(max(1, len(line) // 40 + 1)
                       for line in (text or '').splitlines() or [''])

        def open_pending_diff(e):
            """「詳細」→ β版の差分をアプリ内ダイアログで開く."""
            btn = e.control
            btn.disabled = True
            st.value = '差分を準備しています...'
            page.update()

            def failed(e):
                st.value = '差分を開けませんでした: %s' % e
                btn.disabled = False
                page.update()

            def work():
                try:
                    data = (reviewcache.get()
                            or reviewcache.load_from_disk(config) or {})
                    pr = next((p for p in data.get('pending') or []
                               if p.get('number') == payload['number']),
                              None)
                    if pr is None:
                        st.value = ('この提出の情報が見つかりません'
                                    'でした。少し待ってからもう一度'
                                    'お試しください。')
                        return
                    betas = ghcli.prereleases(data.get('releases') or [])
                    beta = _beta_for(pr['number'], betas)
                    # 「戻る」で閉じると背後のこの詳細画面に戻る
                    _open_diff_dialog(pr,
                                      beta['tag'] if beta else None,
                                      close_label='戻る',
                                      on_error=failed)
                    st.value = ''
                except Exception as e2:
                    log.exception('diff viewer failed')
                    failed(e2)
                finally:
                    btn.disabled = False
                    page.update()
            run_bg(work)

        if is_pending:
            est_h = 230
            # 「図へ戻る」と「閉じる」はほぼ同じ動作なので、
            # 戻る (図へ) と 詳細 (β版の差分) の 2 つにする (管理者指示)
            actions = [ft.TextButton('戻る', on_click=back_to_figure),
                       ft.TextButton('詳細 (β版の差分)',
                                     on_click=open_pending_diff)]
        else:
            est_h = 90 + _est_lines(normal) * 19
            if ai:
                est_h += 62 + _est_lines(ai) * 17
            actions = [ft.TextButton('戻る', on_click=back_to_figure),
                       ft.TextButton('閉じる', on_click=close_all)]
        page.show_dialog(ft.AlertDialog(
            title=ft.Text(title),
            content=ft.Column(content, width=560, tight=True, spacing=10,
                              height=min(430, est_h),
                              scroll=ft.ScrollMode.AUTO),
            actions=actions))
        page.update()

    def _tl_rows(tl, local_v, dlg_status, open_detail):
        """図の下の一覧 (公開済み + 確認と承認の途中)."""
        authors = tl['authors']
        chip_by_target = {c['target_tag']: c for c in tl['chips']
                          if not c['pending']}
        rows = [ft.Text('公開済み (新しい順)', size=11.5,
                        weight=ft.FontWeight.BOLD, color='#4b5563')]

        def row(children, bgcolor, on_click=None, border=None):
            return ft.Container(
                bgcolor=bgcolor, border_radius=8, on_click=on_click,
                border=border or ft.Border.all(1, '#e5e7eb'),
                padding=ft.Padding.symmetric(vertical=6, horizontal=10),
                content=ft.Row(children, spacing=8))

        for s in reversed(tl['stables']):
            c = chip_by_target.get(s['tag'])
            color = (history.person_color(c['author'], authors)
                     if c else '#94a3b8')
            cells = [
                _person_dot(color),
                ft.Text(c['author'] if c else '—', size=12,
                        color='#374151', width=64, max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(s['tag'], size=12.5, weight=ft.FontWeight.BOLD,
                        color=NAVY, width=42),
                ft.Text('#%s' % c['number'] if c else '—', size=12,
                        color='#475569', width=38),
                ft.Text(_stable_summary(s), size=12.5, color='#374151',
                        expand=True, max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS),
            ]
            is_cur = s['tag'] == local_v
            if is_cur:
                cells.append(ft.Container(
                    bgcolor='#fef08a', border_radius=8,
                    padding=ft.Padding.symmetric(vertical=2, horizontal=7),
                    content=ft.Text('現行版', size=10.5,
                                    weight=ft.FontWeight.BOLD,
                                    color='#713f12')))
            if c:
                dates = '%s → %s 公開' % (
                    history.fmt_date(c['start'], with_year=True),
                    history.fmt_date(c['end']))
            else:
                dates = '%s 公開' % history.fmt_date(s['date'],
                                                     with_year=True)
            cells.append(ft.Text(dates, size=11, color='#475569'))
            cells.append(ft.OutlinedButton(
                'ZIP を保存', icon=ft.Icons.DOWNLOAD,
                on_click=_download_history_zip(s['release'], dlg_status)))
            side = ft.BorderSide(1, '#e5e7eb')
            cur_border = ft.Border(left=ft.BorderSide(4, '#facc15'),
                                   top=side, right=side, bottom=side)
            rows.append(row(
                cells, '#fefce8' if is_cur else '#ffffff',
                on_click=(lambda _, s=s: open_detail('stable', s)),
                border=(cur_border if is_cur else None)))

        pend = [c for c in tl['chips'] if c['pending']]
        if pend:
            rows.append(ft.Text('確認と承認の途中 (まだ配れません)',
                                size=11.5, weight=ft.FontWeight.BOLD,
                                color='#4b5563'))
        for c in pend:
            color = history.person_color(c['author'], authors)
            rows.append(row([
                _person_dot(color),
                ft.Text(c['author'], size=12, color='#374151', width=64,
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text('—', size=12.5, color='#9ca3af', width=42),
                ft.Text('#%s' % c['number'], size=12, color='#475569',
                        width=38),
                ft.Text(c['title'], size=12.5, color='#374151',
                        expand=True, max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS),
                ft.Container(
                    bgcolor='#fef3c7', border_radius=8,
                    padding=ft.Padding.symmetric(vertical=2, horizontal=7),
                    content=ft.Text('確認中', size=10.5,
                                    weight=ft.FontWeight.BOLD,
                                    color='#92400e')),
                # 確認中の行にはダウンロードの印を置かない (無効表示で
                # あっても「β版を保存できる」誤解につながるため =
                # 管理者指示。β版のデータは差分ページの注意付き導線のみ)
                ft.Text('%s 提出' % history.fmt_date(c['start'],
                                                     with_year=True),
                        size=11, color='#475569'),
            ], '#fafafa', on_click=(lambda _, c=c:
                                    open_detail('chip', c))))
        return rows

    def _history_fp(data):
        """図の見た目に効く部分の指紋 (裏の再取得で変わったときだけ
        開き直すための比較キー)."""
        rel = [(r.get('tag'), r.get('published_at_full'),
                r.get('published_at'), r.get('tag_sha'), r.get('pr_number'),
                bool(r.get('prerelease')), r.get('notes'))
               for r in (data.get('releases') or [])]
        pend = [(p.get('number'), p.get('fork_sha'),
                 p.get('base_version'), p.get('base_commit'),
                 p.get('created_at_full'), p.get('title'),
                 p.get('author'), bool(p.get('rejected_final')))
                for p in (data.get('pending') or [])]
        return json.dumps([rel, data.get('merged') or [], pend],
                          ensure_ascii=False, sort_keys=True)

    def on_show_history(_):
        """過去の更新ログ: 誰がどの版を基に作りどこで正式版になったかの
        時系列図 + 一覧。行・帯を押すと更新内容 (ZIP 保存はそこ)。

        手元のスナップショットを即表示しつつ、裏で必ず最新を取得して
        内容が変わっていたら開き直す (古い基点判定が見え続けないため)。
        """
        data = reviewcache.get() or reviewcache.load_from_disk(config)
        if data is not None and data.get('merged') is not None:
            _open_history_dialog(data)
        else:
            data = None
            body = ft.Column([ft.Text('過去の更新ログを読み込んで'
                                      'います...', size=13,
                                      color='#555555')],
                             width=640, height=120)
            _history_ctx['open'] = True

            def close_loading(_):
                _history_ctx['open'] = False
                page.pop_dialog()

            page.show_dialog(ft.AlertDialog(
                title=ft.Text('過去の更新ログ'), content=body,
                actions=[ft.TextButton('閉じる',
                                       on_click=close_loading)]))
            page.update()

        def work():
            try:
                fresh = _fetch_review_snapshot()
            except (reviews.ReviewError, ghcli.GhError) as e:
                if data is None:
                    body.controls = [ft.Text(str(e), size=13,
                                             color='#b91c1c')]
                    page.update()
                return
            fresh = fresh or reviewcache.get()
            if fresh is None:
                return
            if data is not None and (_history_fp(fresh)
                                     == _history_fp(data)):
                return          # 変化なし: 開いたままの画面を触らない
            if not _history_ctx.get('open'):
                return          # もう閉じている (or 詳細を見ている)
            _open_history_dialog(fresh)
        run_bg(work)

    def _open_history_dialog(data):
        today = datetime.date.today()
        local_v = (updater.local_version_info(stable) or {}).get('version')
        tl = history.build_timeline(data.get('releases') or [],
                                    data.get('merged') or [],
                                    data.get('pending') or [],
                                    today=today)
        dlg_status = ft.Text('', size=12, color='#555555', selectable=True)

        def open_detail(kind, payload):
            _show_history_detail(kind, payload, tl)

        # ダイアログの幅・高さはウィンドウの 8 割を目安にする (管理者
        # 指示)。図の下に公開済みの行が少なくとも見える高さは確保する
        if getattr(page, 'web', False):
            # Web 表示 (UI レビュー用) はブラウザの画面サイズを使う
            win_w = int(page.width or 760)
            win_h = int(page.height or 640)
        else:
            win_w = int(getattr(page.window, 'width', None) or page.width
                        or 760)
            win_h = int(getattr(page.window, 'height', None) or page.height
                        or 640)
        col_w = max(600, int(win_w * 0.8) - 48)
        fig = historyview.build_figure(
            tl, local_v, today, open_detail, viewport_w=col_w - 4,
            font_family=getattr(page.theme, 'font_family', None))
        controls = []
        if fig:
            controls.append(fig['control'])
            controls.append(historyview.build_legend())
        else:
            controls.append(ft.Text('過去の更新ログはまだありません。',
                                    size=13, color='#555555'))
        controls += _tl_rows(tl, local_v, dlg_status, open_detail)
        controls.append(ft.Text(
            '直近の 30 件までを表示しています。それより前の版が必要な'
            'ときは管理者に相談してください。', size=11, color='#6b7280'))
        controls.append(dlg_status)
        # ダイアログ全体 (タイトル + 内容 + ボタン ≈ 内容 + 165px) が
        # ウィンドウの約 8 割になるよう内容の高さを決める
        fig_h = fig['height'] if fig else 60
        col_h = min(win_h - 170,
                    max(int(win_h * 0.8) - 165, fig_h + 220))
        body_col = ft.Column(controls, width=col_w, height=col_h,
                             spacing=8, scroll=ft.ScrollMode.AUTO,
                             tight=True)
        _history_ctx['body_col'] = body_col
        _history_ctx['fig'] = fig
        _history_ctx['open'] = True

        def close(_):
            _history_ctx['open'] = False
            page.pop_dialog()

        page.show_dialog(ft.AlertDialog(
            title=ft.Text('過去の更新ログ'),
            content=body_col,
            actions=[ft.TextButton('閉じる', on_click=close)]))
        page.update()
        if fig:
            async def scroll_to_current():
                # 描画が終わってから現行版が見える位置へスクロールする
                # (scroll_to は async のためページのループで実行する)
                await asyncio.sleep(0.25)
                try:
                    await fig['scroll_row'].scroll_to(
                        offset=fig['initial_offset'], duration=100)
                except Exception:
                    log.debug('初期スクロールに失敗 (表示には影響なし)',
                              exc_info=True)
            page.run_task(scroll_to_current)

    # 並び: 版情報 → ボタン (位置固定) → 状態・通知 → 更新内容 (常設)。
    # 通知の有無で「起動」ボタンの位置が動かないようにする
    tab_launch = ft.Container(padding=24, content=ft.Column([
        t1_version,
        t1_meta,
        ft.Row([
            t1_launch_btn,
            ft.OutlinedButton(
                '過去の更新ログ', on_click=on_show_history,
                style=ft.ButtonStyle(color='#6b7280')),
        ], spacing=12),
        t1_fresh,
        t1_status,
        t1_update_tag,
        t1_preparing_tag,
        t1_notice,
        join_notice,
        t1_notes_box,
    ], spacing=12, scroll=ft.ScrollMode.AUTO))

    # ---------------- タブ2: 更新版を提出 ----------------
    # (旧・更新タブは廃止: 新しい正式版は自動で取り込み、起動タブの
    #  黄色いタグと「過去の更新ログ」で知らせる)

    t4_status = status_text()
    t4_result = ft.Text('', size=14, selectable=True)
    # 手書きの人でも様式 (更新内容 / 制限事項) が揃うよう、項目名の下に
    # 薄いグレーの例 (hint) を常時表示する。空欄なら従来どおり自動生成。
    # ラベルは TextField に持たせない (未フォーカス時に hint が隠れるため)。
    # hint は薄いグレーでも本文 (ほぼ黒) と十分差がつく #6b7280 (4.8:1)
    _t4_hint = ft.TextStyle(color='#6b7280', size=13)
    _t4_box = dict(border_color='#9ca3af', focused_border_color=NAVY,
                   width=600, multiline=True)
    t4_commit_msg = ft.TextField(
        hint_text='例:\n・二丁溝形鋼(2C)の断面算定に対応\n'
                  '・計算書の文章出力を改善',
        hint_style=_t4_hint, hint_max_lines=3,
        min_lines=3, max_lines=5, **_t4_box)
    t4_limits = ft.TextField(
        hint_text='例:\n・二丁山形鋼は等辺のみ対応です '
                  '(不等辺はエラーで停止します)',
        hint_style=_t4_hint, hint_max_lines=2,
        min_lines=2, max_lines=4, **_t4_box)

    def _t4_field(caption, note, field):
        """様式の 1 項目 (項目名 + 補足 + 入力欄)."""
        return ft.Column([
            ft.Row([ft.Text(caption, size=13.5, weight=ft.FontWeight.BOLD,
                            color='#374151'),
                    ft.Text(note, size=12, color='#6b7280')],
                   spacing=8, vertical_alignment=ft.CrossAxisAlignment.END),
            field,
        ], spacing=6)
    t4_submit_btn = ft.FilledButton('ZIP を選んで提出', icon=ft.Icons.UPLOAD,
                                    bgcolor=NAVY, color='#ffffff')

    def _submit_progress(msg):
        t4_status.value = msg
        page.update()

    def _do_finalize(prep, deletions, existing_branch=None, use_ai=False):
        # ダイアログを閉じた直後に反応を見せる (裏の処理は数十秒かかる)
        t4_status.value = '提出しています...'
        page.update()

        def work():
            try:
                result = submit.finalize_submission(
                    prep, deletions, t4_commit_msg.value or '',
                    config, on_progress=_submit_progress,
                    existing_branch=existing_branch,
                    limitations=t4_limits.value or '', use_ai=use_ai)
                t4_status.value = ''
                t4_result.value = (
                    '提出しました。検証を通過するとβ版として発行され、'
                    '「β版の確認と承認」タブに表示されます。\n'
                    '提出内容: %s' % result['pr_url'])
                t4_commit_msg.value = ''
                t4_limits.value = ''
            except (submit.SubmitError, ghcli.GhError, GitError) as e:
                t4_status.value = str(e)
            except Exception as e:
                log.exception('finalize_submission failed')
                t4_status.value = '提出に失敗しました: %s' % e
            t4_submit_btn.disabled = False
            page.update()
        run_bg(work)

    def _confirm_and_finalize(prep, my_prs=()):
        """削除ファイルの確認・警告表示ダイアログ → 確定."""
        ch = prep['changes']
        warnings = prep['safety']['warnings']
        del_checks = [ft.Checkbox(label=rel, value=False)
                      for rel in ch['deleted']]
        dest_dd = None
        items = [ft.Text('追加 %d 件 / 変更 %d 件のファイルを提出します。'
                         % (len(ch['added']), len(ch['modified']))),
                 ft.Text('変更のないファイルは送られません。個人設定 '
                         '(API キーなど) や計算結果は自動で除外済みです。',
                         size=12, color='#555555')]
        skipped = prep.get('skipped') or []
        if skipped:
            items.append(ft.Text(
                '提出対象外のため除外しました (エラーではありません): %s'
                % '、'.join(skipped), size=12, color='#555555'))
        if my_prs:
            # 差し戻し後の修正版は同一の提出に積める
            options = [ft.DropdownOption(key='', text='新しい提出として出す')]
            options += [ft.DropdownOption(
                key=p['branch'], text='#%d に修正版として積む (%s)'
                % (p['number'], p['title'][:30])) for p in my_prs]
            dest_dd = ft.Dropdown(label='提出先', options=options, value='')
            items.append(dest_dd)
        # 更新内容・制限事項とも空欄なら、説明の作り方を本人に選ばせる
        # (API 使用料がかかる自動生成を黙って実行しない)。キー未登録なら
        # 自動生成は使えないため選択肢を出さず空欄のまま提出する
        gen_rg = None
        blank = (not (t4_commit_msg.value or '').strip()
                 and not (t4_limits.value or '').strip())
        if blank and settings.api_key(config):
            # 既定は自動作成 (入力欄の案内「空欄の場合は AI で自動記述
            # します。(推奨)」と同じ扱いにそろえる)。ここで選び直せるので
            # 使用料のかかる処理を黙って実行することにはならない
            gen_rg = ft.RadioGroup(value='ai', content=ft.Column([
                ft.Radio(value='ai',
                         label='Claude で自動作成する (推奨。'
                               'API 使用料が数十円かかります)'),
                ft.Radio(value='blank',
                         label='空欄のまま提出する (無料)'),
            ], spacing=0))
            items.append(ft.Container(
                bgcolor='#ffffff', border_radius=8, padding=12,
                content=ft.Column([
                    ft.Text('「更新内容」と「制限事項」が空欄です。'
                            'どうしますか?',
                            size=13, weight=ft.FontWeight.BOLD),
                    gen_rg,
                    ft.Text('自動作成では、提出する変更内容から更新内容と'
                            '制限事項がまとめられます。空欄のまま提出すると、'
                            '正式版になったときの更新内容の表示も空欄に'
                            'なります。',
                            size=12, color='#4b5563'),
                ], spacing=6)))
        if del_checks:
            items.append(ft.Text(
                '基点にあったのに ZIP に無いファイルがあります。'
                '意図的に削除したものにチェックを入れてください '
                '(チェックなし = 入れ忘れとして元のまま維持):',
                size=13))
            items.extend(del_checks)
        for w in warnings:
            items.append(ft.Text('⚠ %s' % w, size=13, color=AMBER))
        if warnings:
            items.append(ft.Text('警告を確認のうえ続行できます。', size=12,
                                 color='#555555'))

        def close(_):
            # 先にダイアログを閉じて反応を見せ、作業ファイルの掃除は裏で
            page.pop_dialog()
            t4_submit_btn.disabled = False
            t4_status.value = '提出を取り消しました。'
            page.update()

            def tidy():
                try:
                    submit.cleanup(prep)
                except Exception:
                    log.exception('提出の後片付けに失敗しました')
            run_bg(tidy)

        def proceed(_):
            page.pop_dialog()
            deletions = [c.label for c in del_checks if c.value]
            existing = (dest_dd.value or None) if dest_dd else None
            use_ai = bool(gen_rg is not None and gen_rg.value == 'ai')
            _do_finalize(prep, deletions, existing, use_ai)

        page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text('提出内容の確認'),
            content=ft.Column(items, tight=True, width=560,
                              scroll=ft.ScrollMode.AUTO),
            actions=[ft.TextButton('キャンセル', on_click=close),
                     ft.FilledButton('提出する', on_click=proceed,
                                     bgcolor=NAVY, color='#ffffff')]))

    async def on_submit(_):
        files = await file_picker.pick_files(
            dialog_title='提出する ZIP を選択',
            allowed_extensions=['zip'])
        if not files or not files[0].path:
            return
        zip_path = files[0].path
        t4_submit_btn.disabled = True
        t4_result.value = ''
        page.update()
        try:
            prep = await asyncio.to_thread(
                submit.prepare_submission, zip_path, config,
                None, _submit_progress)
        except (submit.SubmitError, ghcli.GhError, GitError) as e:
            t4_status.value = str(e)
            t4_submit_btn.disabled = False
            page.update()
            return
        except Exception as e:
            log.exception('prepare_submission failed')
            t4_status.value = '提出の準備に失敗しました: %s' % e
            t4_submit_btn.disabled = False
            page.update()
            return
        blockers = prep['safety']['blockers']
        if blockers:
            submit.cleanup(prep)
            t4_status.value = '提出できません:\n- ' + '\n- '.join(blockers)
            t4_submit_btn.disabled = False
            page.update()
            return
        try:
            my_prs = await asyncio.to_thread(
                autofix.list_my_submissions, config)
        except Exception:
            my_prs = []
        t4_status.value = ''
        page.update()
        _confirm_and_finalize(prep, my_prs)

    t4_submit_btn.on_click = on_submit

    # --- 検証状況と自動修正 ---

    t4_pr_list = ft.Column([], spacing=8)
    t4_fix_status = status_text()

    def on_autofix(pr, btn):
        def handler(_):
            # 押した瞬間に反応 (二度押しで修正ループが二重に走らないように)
            btn.disabled = True
            t4_fix_status.value = '自動修正を開始します...'
            page.update()

            def work():
                def progress(msg):
                    t4_fix_status.value = '#%d: %s' % (pr['number'], msg)
                    page.update()
                try:
                    result = autofix.autofix_loop(pr['number'], config,
                                                  on_progress=progress)
                    t4_fix_status.value = '#%d: %s' % (pr['number'],
                                                       result['message'])
                except Exception as e:
                    log.exception('autofix failed')
                    t4_fix_status.value = '自動修正に失敗しました: %s' % e
                finally:
                    btn.disabled = False
                    page.update()
            run_bg(work)
        return handler

    _STATUS_LABELS = {
        'success': ('検証OK', '#15803d'),
        'failure': ('検証で問題あり', '#b91c1c'),
        'pending': ('検証中', '#b45309'),
    }

    def on_refresh_prs(_):
        # 押した瞬間の反応: ボタンを止めて進行中を出し、取得は裏で行う
        t4_refresh_btn.disabled = True
        t4_fix_status.value = '取得中...'
        page.update()

        def work():
            try:
                prs = autofix.list_my_submissions(config)
            except (autofix.AutofixError, ghcli.GhError) as e:
                t4_fix_status.value = str(e)
                t4_refresh_btn.disabled = False
                page.update()
                return
            t4_pr_list.controls.clear()
            if not prs:
                t4_pr_list.controls.append(
                    ft.Text('検証・承認待ちの提出はありません', size=13))
            for pr in prs:
                label, color = _STATUS_LABELS[pr['status']]
                row = [ft.Column([
                    ft.Text('#%d %s' % (pr['number'], pr['title']),
                            weight=ft.FontWeight.BOLD, size=13),
                    ft.Text(label, size=12, color=color),
                ], spacing=2, expand=True)]
                if pr['status'] == 'failure':
                    fix_btn = ft.FilledButton(
                        '自動修正を試す', icon=ft.Icons.BUILD,
                        bgcolor=AMBER, color='#ffffff')
                    fix_btn.on_click = on_autofix(pr, fix_btn)
                    row.append(fix_btn)
                t4_pr_list.controls.append(ft.Container(
                    bgcolor='#f5f7fa', border_radius=6, padding=12,
                    content=ft.Row(row)))
            t4_fix_status.value = ''
            t4_refresh_btn.disabled = False
            page.update()
        run_bg(work)

    t4_refresh_btn = ft.OutlinedButton('確認', on_click=on_refresh_prs)

    tab_submit = ft.Container(padding=24, content=ft.Column([
        ft.Text('作業した mgtkit のフォルダを ZIP にして提出すると、'
                '自動で検証されます。エラーなく本体に組み込める状態に'
                'なるとβ版として発行され、メンバーの確認と承認を経て'
                '正式版になります。マネージャーで取得した版 '
                '(version.json 入り) を基に作業してください。',
                size=13, color='#555555'),
        ft.Text('フォルダは丸ごと ZIP にして構いません。取得した版から'
                '変更したファイルだけが提出され、個人設定 (API キーなど)・'
                '計算結果 (mgtkit_out)・PDF や実行ファイル (.bat など) '
                'コード以外のファイルは自動で除外されます。',
                size=12, color='#555555'),
        _t4_field('更新内容', '空欄の場合は AI で自動記述します。(推奨)',
                  t4_commit_msg),
        _t4_field('ご利用にあたっての制限事項',
                  '使えない条件など。空欄の場合は AI で自動記述します。'
                  '(推奨)', t4_limits),
        ft.Container(t4_submit_btn, margin=ft.Margin(0, 16, 0, 0)),
        t4_status,
        t4_result,
        ft.Divider(),
        ft.Row([
            ft.Text('提出済みの検証状況', size=14,
                    weight=ft.FontWeight.BOLD),
            t4_refresh_btn,
        ], spacing=12),
        t4_pr_list,
        t4_fix_status,
    ], spacing=16, scroll=ft.ScrollMode.AUTO))

    # ------------- タブ4: β版の確認と承認 (β版の試用 + 承認を 1 画面に) -------------

    # spacing は演出の基点の積み上げ (_rocket_base) に使うため定数で結合
    t5_list = ft.Column([], spacing=_CARD_GAP)
    t5_status = status_text()

    def _t5_progress(msg):
        t5_status.value = msg
        page.update()

    def try_beta(pr, release):
        def handler(_):
            # 押した瞬間に反応 (二度押しで二重に取得・起動しないように)
            restore = _freeze_card(pr['number'])
            t5_status.value = '%s を準備しています...' % release['tag']
            page.update()

            def work():
                def progress(msg):
                    t5_status.value = '%s: %s' % (release['tag'], msg)
                    page.update()
                beta = paths.beta_dir(release['tag'], config)
                try:
                    if updater.local_version_info(beta) is None:
                        # 別のβ版が起動中だとフォルダの置き換えや
                        # 新しい版の起動ができないため先に終了する
                        launcher.stop_app(paths.beta_port(config))
                        updater.install_release(repo, release, beta,
                                                on_progress=progress)
                    _, url = launcher.launch_app(
                        beta, paths.beta_port(config), channel='beta')
                    t5_status.value = ('β版 %s を起動しました (安定版とは'
                                       '別画面・別データ): %s'
                                       % (release['tag'], url))
                except (ghcli.GhError, launcher.LaunchError,
                        Exception) as e:
                    log.exception('β版の起動に失敗しました')
                    t5_status.value = str(e) or 'β版の起動に失敗しました。'
                finally:
                    restore()
                    page.update()
            run_bg(work)
        return handler

    def _edit_feedback_dialog(fb):
        """自分のフィードバックの編集ダイアログ."""
        field = ft.TextField(label='フィードバックを編集', value=fb['text'],
                             multiline=True, min_lines=3, max_lines=6)
        err = ft.Text('', size=12, color='#b91c1c')

        def save(_):
            dialog_busy(save_btn, err, '保存しています...')

            def work():
                try:
                    feedback.update_feedback(fb['comment_id'], fb['tag'],
                                             field.value, config)
                    page.pop_dialog()
                    t5_status.value = 'フィードバックを更新しました。'
                    page.update()
                    on_refresh_reviews(None)
                except (feedback.FeedbackError, ghcli.GhError) as e:
                    dialog_error(save_btn, err, str(e))
            run_bg(work)

        save_btn = ft.FilledButton('保存', on_click=save,
                                   bgcolor=NAVY, color='#ffffff')
        page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text('フィードバックの編集'),
            content=ft.Column([field, err], tight=True, width=560),
            actions=[ft.TextButton('キャンセル',
                                   on_click=lambda _: page.pop_dialog()),
                     save_btn]))

    def _delete_feedback_dialog(fb):
        """自分のフィードバックの削除確認ダイアログ."""
        err = ft.Text('', size=12, color='#b91c1c')

        def do_delete(_):
            dialog_busy(del_btn, err, '削除しています...')

            def work():
                try:
                    feedback.delete_feedback(fb['comment_id'], config)
                    page.pop_dialog()
                    t5_status.value = 'フィードバックを削除しました。'
                    page.update()
                    on_refresh_reviews(None)
                except (feedback.FeedbackError, ghcli.GhError) as e:
                    dialog_error(del_btn, err, str(e))
            run_bg(work)

        del_btn = ft.FilledButton('削除する', on_click=do_delete,
                                  bgcolor='#b91c1c', color='#ffffff')
        page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text('フィードバックの削除'),
            content=ft.Column([
                ft.Text('このフィードバックを削除しますか?', size=13),
                ft.Text(fb['text'], size=12, color='#555555'),
                err,
            ], tight=True, width=560),
            actions=[ft.TextButton('キャンセル',
                                   on_click=lambda _: page.pop_dialog()),
                     del_btn]))

    def on_feedback_dialog(pr, beta, me):
        """フィードバック一覧 (誰が・いつ・内容) + β版があれば投稿欄.

        自分が書いたフィードバックには編集・削除ボタンを表示する。
        """
        def _open_edit(fb):
            def h(_):
                page.pop_dialog()
                _edit_feedback_dialog(fb)
            return h

        def _open_delete(fb):
            def h(_):
                page.pop_dialog()
                _delete_feedback_dialog(fb)
            return h

        def _fb_card(fb, muted=False):
            text_color = '#9ca3af' if muted else '#555555'
            header = [ft.Text('%s さん (%s / %s)'
                              % (fb['name'], fb['tag'], fb['date']),
                              size=12, weight=ft.FontWeight.BOLD,
                              color=text_color, expand=True)]
            if fb.get('author') == me and fb.get('comment_id'):
                header += [
                    ft.IconButton(ft.Icons.EDIT_OUTLINED,
                                  icon_size=16, tooltip='編集',
                                  on_click=_open_edit(fb)),
                    ft.IconButton(ft.Icons.DELETE_OUTLINE,
                                  icon_size=16, tooltip='削除',
                                  on_click=_open_delete(fb)),
                ]
            return ft.Container(
                bgcolor='#f5f7fa', border_radius=6, padding=10,
                content=ft.Column([
                    ft.Row(header, spacing=4),
                    ft.Text(fb['text'], size=13, selectable=True,
                            color='#9ca3af' if muted else None),
                ], spacing=4))

        def handler(_):
            fb_all = pr.get('feedback') or []
            # 現在のβ版宛てが「今回のフィードバック」。統合などで新しい
            # β版に切り替わった後は、旧版宛てを下に薄く残す
            if beta is not None:
                current = [f for f in fb_all if f['tag'] == beta['tag']]
                old = [f for f in fb_all if f['tag'] != beta['tag']]
            else:
                current, old = fb_all, []
            items = [_fb_card(f) for f in current]
            if not items:
                items.append(ft.Text('フィードバックはまだありません。',
                                     size=13))
            if old:
                items.append(ft.Text(
                    '以前の版へのフィードバック (統合前など。記録として'
                    '残しています)', size=12, color='#9ca3af'))
                items += [_fb_card(f, muted=True) for f in old]
            actions = [ft.TextButton(
                '閉じる', on_click=lambda _: page.pop_dialog())]
            if beta is not None:
                field = ft.TextField(
                    label='気づいたこと・不具合・感想 '
                          '(承認の判断材料になります)',
                    multiline=True, min_lines=3, max_lines=6)
                err = ft.Text('', size=12, color='#b91c1c')
                items += [ft.Divider(), field, err]

                def send(_):
                    dialog_busy(send_btn, err, '送信しています...')

                    def work():
                        try:
                            n = feedback.post_feedback(beta, field.value,
                                                       config)
                            page.pop_dialog()
                            t5_status.value = ('フィードバックを送信しました '
                                               '(提出 #%d に届きます)。' % n)
                            page.update()
                            on_refresh_reviews(None)
                        except (feedback.FeedbackError,
                                ghcli.GhError) as e:
                            dialog_error(send_btn, err, str(e))
                    run_bg(work)

                send_btn = ft.FilledButton('送信', on_click=send,
                                           bgcolor=NAVY, color='#ffffff')
                actions.append(send_btn)
            page.show_dialog(ft.AlertDialog(
                modal=True,
                title=ft.Text('#%d のフィードバック' % pr['number']),
                content=ft.Column(items, tight=True, width=560,
                                  scroll=ft.ScrollMode.AUTO),
                actions=actions))
        return handler

    def on_withdraw(pr):
        """提出者本人による取り下げ (確認ダイアログつき)."""
        def handler(_):
            reason = ft.TextField(label='取り下げの理由 (任意)',
                                  multiline=True, min_lines=2, max_lines=4)
            err = ft.Text('', size=12, color='#b91c1c')

            def do_withdraw(_):
                dialog_busy(wd_btn, err, '取り下げの処理をしています...')

                def work():
                    try:
                        reviews.withdraw(pr['number'], reason.value, config)
                        page.pop_dialog()
                        t5_status.value = ('#%d を取り下げました。'
                                           % pr['number'])
                        page.update()
                        on_refresh_reviews(None)
                    except (reviews.ReviewError, ghcli.GhError) as e:
                        dialog_error(wd_btn, err, str(e))
                run_bg(work)

            wd_btn = ft.FilledButton('取り下げる', on_click=do_withdraw,
                                     bgcolor='#b91c1c', color='#ffffff')
            page.show_dialog(ft.AlertDialog(
                modal=True, title=ft.Text('#%d を取り下げ' % pr['number']),
                content=ft.Column([
                    ft.Text('この提出を取り下げます。承認待ちから消え、'
                            '対応するβ版も削除されます。', size=13),
                    reason, err,
                ], tight=True, width=560),
                actions=[ft.TextButton('キャンセル',
                                       on_click=lambda _: page.pop_dialog()),
                         wd_btn]))
        return handler

    def on_approve(pr):
        def handler(_):
            # 連打の 2 回目以降は捨てる (承認が二重に飛ばないように)。
            # 黙って捨てると「効いていない」と見えるため一言出す
            if not _review_guard.begin(pr['number']):
                _t5_progress('#%d は前の操作を送信しています。'
                             '少し待ってください。' % pr['number'])
                return
            # 押す前から走っていた取得は、返ってきても採用しない
            # (押す前の内容で画面が描き直され、表示が戻ってしまうため)
            reviewcache.note_local_change()
            # 楽観的更新: 押した瞬間に承認済み表示 (バッジ・点火演出) へ
            # 切り替え、送信は裏で行う。失敗したら取得し直して表示が戻る
            data = reviewcache.get()
            me = (data or {}).get('me')
            optimistic = bool(data is not None and me
                              and me != pr['author'])
            restore = None
            if optimistic:
                cur = reviewcache.pending_of(pr['number']) or pr
                cur['approved'] = pr['approved'] = sorted(
                    set(cur['approved']) | {me})
                _render_reviews(data, stale=True, animate=True,
                                status='#%d を承認しました (送信中...)。'
                                       % pr['number'])
            else:
                # 手元に前回の取得結果がないときは、ボタンを止めて
                # 進行中の文言を出す (押した瞬間の反応をここで作る)
                restore = _freeze_card(pr['number'])
                _t5_progress('#%d の承認を送信しています...' % pr['number'])

            def work():
                released = False
                try:
                    # 提出者は一覧取得時に判明済み。渡して再取得を省く
                    reviews.approve(pr['number'], config,
                                    author=pr['author'])
                    t5_status.value = '#%d を承認しました。' % pr['number']
                    page.update()
                    # 必要数に達していたら自動リリース (ロケット発射)
                    released = _try_auto_release(pr['number'])
                except (reviews.ReviewError, ghcli.GhError) as e:
                    t5_status.value = str(e)
                    if restore is not None:
                        # 凍結経路の失敗は一覧が作り直されないため、
                        # ここで戻さないとボタンが無効のまま残る
                        restore()
                finally:
                    _review_guard.end(pr['number'])
                page.update()
                if not released:
                    # 発射したときは ✨ のあとに _try_auto_release 側で
                    # 一覧を更新する (演出前にカードが消えないように)
                    on_refresh_reviews(None)
            run_bg(work)
        return handler

    def _try_auto_release(pr_number):
        """承認が必要数そろい条件が整っていれば、その場でリリースする.

        検証NG・却下あり・衝突中・権限なしのときは何もしない。
        発射演出を始めたら True を返す。カードの削除 (一覧更新) と
        公開待ち・自動更新の案内は ✨ が光ったあとに行う。
        """
        try:
            _t5_progress('承認がそろったかどうか確認しています...')
            me = reviews.current_user()
            pending = reviews.list_pending(config)
        except (reviews.ReviewError, ghcli.GhError):
            return False
        pr = next((p for p in pending if p['number'] == pr_number), None)
        if pr is None or pr.get('rejected_final'):
            return False
        if not reviews.can_release(pr, config, me):
            return False
        # ブランチが最新の正式版より古い場合はここで取り込み直して検証を
        # 待つ (時間がかかる工程は発射演出の前に済ませる)
        try:
            reviews.ensure_branch_current(pr['number'], config,
                                          on_progress=_t5_progress)
        except (reviews.ReviewError, ghcli.GhError) as e:
            t5_status.value = str(e)
            page.update()
            return False
        _hide_zone(pr_number)     # 常駐機は overlay の機体と入れ替える
        state = {'anim': False, 'net': False, 'ok': False}

        def _finish(key):
            state[key] = True
            if not (state['anim'] and state['net']):
                return
            if state['ok']:
                # キラッのあと: 「準備中」の案内を出し、リリースの公開
                # (数分かかる) を裏で見張って公開されたら自動で取り込む
                _watch_new_release()
            on_refresh_reviews(None)

        _play_launch(pr_number, done=lambda: _finish('anim'))
        try:
            result = reviews.release(pr['number'], config,
                                     on_progress=_t5_progress)
            t5_status.value = ('承認が %d 人そろったため自動でリリース'
                               'しました。%s'
                               % (reviews.required_approvals(config),
                                  result['message']))
            state['ok'] = True
        except (reviews.ReviewError, ghcli.GhError) as e:
            t5_status.value = str(e)
        page.update()
        _finish('net')
        return True

    def _rocket_base(pr_number):
        """発射・爆発の基点 = そのカードの常駐ロケットの定位置 (機体中心).

        押したカードのロケットからそのまま飛ぶ・倒れるように、カードごと
        に基点を持つ。Flet には描画後の位置を問い合わせる手段がないため、
        一覧の行の高さをすべて固定 (_H_* / _CARD_* / _INTRO_H) にして
        積み上げで求めている。スクロール量は _list_scroll に控えてある。

        (2026-08 に一度は全カード共通の固定点にしたが、それだと 2 枚目
         以降のカードから押しても 1 枚目の位置から飛んでしまう。行の
         高さを固定して積み上げれば正確に出せるため戻した)
        """
        x = _TAB_PAD + _CARD_PAD + rocketfx.ZONE_CX
        y = (_card_tops.get(pr_number, _LIST_TOP) + _CARD_PAD
             + rocketfx.ZONE_BODY_H / 2)
        return x, y - _list_scroll['pixels']

    def _hide_zone(pr_number):
        """演出中: 常駐ロケットを消し、カードのボタンも無効化する
        (発射中の二度押し・飛行中の却下を防ぐ).

        場所は残したまま透明にする。visible=False にすると行が詰まって
        タイトルが左へ飛び、演出中のカードがガタつくため。
        戻すのは次の一覧描画 (_unfreeze_cards)。一覧の内容が前回と同じで
        作り直さない場合でも戻るようにしてある。
        """
        zone = _rocket_zones.get(pr_number)
        btns = list(_card_buttons.get(pr_number, []))
        before = [(b.disabled, getattr(b, 'bgcolor', None),
                   getattr(b, 'color', None)) for b in btns]

        def restore():
            if zone is not None:
                zone.opacity = 1.0
            for b, (disabled, bg, fg) in zip(btns, before):
                b.disabled = disabled
                if bg is not None:
                    b.bgcolor, b.color = bg, fg
        _frozen_cards.append(restore)
        if zone is not None:
            zone.opacity = 0.0
        for b in btns:
            b.disabled = True
            if getattr(b, 'bgcolor', None):
                b.bgcolor = '#e5e7eb'
                b.color = '#9ca3af'

    def _unfreeze_cards():
        """演出のために止めていたカードを元に戻す (演出が全部終わってから)."""
        if _fx_busy['n'] > 0:
            return                      # まだ飛んでいる・落ちている
        for restore in _frozen_cards:
            try:
                restore()
            except Exception:
                log.debug('カードの復帰に失敗', exc_info=True)
        _frozen_cards.clear()

    def _freeze_card(pr_number):
        """カードの操作ボタンを一時的に無効化する (二度押し防止).

        押した瞬間に呼び、処理が終わったら戻り値の関数で元の状態に戻す
        (統合待ちカードの「元から無効のボタン」を誤って有効化しないよう、
        個々の無効状態を覚えて復元する)。一覧を描き直す流れではボタンごと
        作り直されるため、復元を呼ばなくてもよい。
        """
        btns = list(_card_buttons.get(pr_number, []))
        states = [b.disabled for b in btns]
        for b in btns:
            b.disabled = True

        def restore():
            for b, d in zip(btns, states):
                b.disabled = d
        return restore

    def _fx_done(done):
        """演出の後始末 (演出中フラグを下ろしてから本来の続きへ)."""
        def finish():
            _fx_busy['n'] = max(0, _fx_busy['n'] - 1)
            if done is not None:
                done()
        return finish

    def _play_launch(pr_number, done=None):
        """自動リリース演出 (rocketfx): 点火 → 震え → 加速上昇 →
        弧を描いて「起動」タブへ → ✨ と弾けて消える."""
        sx, sy = _rocket_base(pr_number)
        _fx_busy['n'] += 1
        # 到達点はこの画面のタブバー付近 (rocketfx の既定に頼らず渡す)
        rocketfx.play_launch(page, sx, sy, target_y=_TAB_TOP,
                             done=_fx_done(done))

    def _play_crash(pr_number, done=None):
        """却下確定演出 (rocketfx): ぐらつき → 転倒 → 爆発と同時に
        パーツ分解 → 破片が放物線で散乱."""
        sx, sy = _rocket_base(pr_number)
        _fx_busy['n'] += 1
        rocketfx.play_crash(page, sx, sy, done=_fx_done(done))

    def on_cancel_review(pr):
        """自分の承認・却下の取り消し (2 回目のクリックでニュートラルへ)."""
        def handler(_):
            # 連打の 2 回目以降は捨てる (取り消しが二重に飛ばないように)
            if not _review_guard.begin(pr['number']):
                _t5_progress('#%d は前の操作を送信しています。'
                             '少し待ってください。' % pr['number'])
                return
            # 押す前から走っていた取得は、返ってきても採用しない
            # (押す前の内容で画面が描き直され、表示が戻ってしまうため)
            reviewcache.note_local_change()
            # 楽観的更新: 押した瞬間に取り消し後の表示 (バッジ・ボタン・
            # 常駐ロケット) へ切り替え、送信は裏で行う。失敗したら取得し
            # 直して表示が戻る (承認 on_approve と対称の作り)
            data = reviewcache.get()
            me = (data or {}).get('me')
            optimistic = bool(data is not None and me)
            restore = None
            if optimistic:
                cur = reviewcache.pending_of(pr['number']) or pr
                cur['approved'] = pr['approved'] = [
                    n for n in cur['approved'] if n != me]
                cur['rejected'] = pr['rejected'] = [
                    r for r in cur['rejected'] if r['name'] != me]
                n_req = reviews.required_approvals(config)
                if (cur.get('rejected_final')
                        and len(cur['rejected']) < n_req):
                    # 却下確定が解けた: 畳みから戻してすぐ再表示する
                    cur['rejected_final'] = pr['rejected_final'] = False
                    localstate.unhide_pr(pr['number'], config)
                    localstate.unmark_auto_folded(pr['number'], config)
                _render_reviews(data, stale=True, animate=True,
                                status='#%d への承認・却下を取り消しました '
                                       '(送信中...)。' % pr['number'])
            else:
                # 手元に前回の取得結果がないときだけ従来どおり
                # (ボタンを止めて送信を待つ)
                restore = _freeze_card(pr['number'])
                _t5_progress('#%d の取り消しを送信しています...'
                             % pr['number'])

            def work():
                try:
                    reviews.cancel_my_review(pr['number'], config)
                    # 却下確定が解けたときのため、非表示・自動畳みの記録を
                    # 掃除する (再び確定したらまた自動で畳める)
                    localstate.unhide_pr(pr['number'], config)
                    localstate.unmark_auto_folded(pr['number'], config)
                    t5_status.value = ('#%d への承認・却下を取り消しました。'
                                       % pr['number'])
                except (reviews.ReviewError, ghcli.GhError) as e:
                    t5_status.value = str(e)
                    if restore is not None:
                        # 凍結経路の失敗時は表示内容が変わらず一覧が
                        # 作り直されないため、ここで戻さないとボタンが
                        # 無効のまま残る (楽観的更新の失敗はこの後の
                        # 再取得で表示ごと戻る)
                        restore()
                finally:
                    _review_guard.end(pr['number'])
                page.update()
                on_refresh_reviews(None)
            run_bg(work)
        return handler

    def _rerender_local(status):
        """畳む・戻すなどこの PC 内だけの操作の即時反映.

        手元のスナップショットで一覧を描き直すだけで、ネットワークへは
        取りに行かない (取得を挟むと表示の反応が数秒遅れるため)。
        """
        data = reviewcache.get()
        if data is None:
            on_refresh_reviews(None)
            return
        _render_reviews(data, stale=True, animate=False, status=status)

    def on_hide_pr(pr):
        """却下確定した提出を一覧の下に畳む (自分の画面のみ)."""
        def handler(_):
            localstate.hide_pr(pr['number'], config)
            _rerender_local('#%d を一覧の下に畳みました (自分の画面のみ。'
                            '「一覧に戻す」で戻せます)。' % pr['number'])
        return handler

    def on_unhide_pr(pr):
        """畳んだ提出を一覧に戻す."""
        def handler(_):
            localstate.unhide_pr(pr['number'], config)
            _rerender_local('#%d を一覧に戻しました。' % pr['number'])
        return handler

    def on_reject(pr):
        def handler(_):
            reason = ft.TextField(label='却下の理由 (必須。提出者に伝わります)',
                                  multiline=True, min_lines=2, max_lines=4)
            err = ft.Text('', size=12, color='#b91c1c')

            def do_reject(_):
                comment = (reason.value or '').strip()
                if not comment:
                    err.value = '却下には理由の入力が必要です。'
                    page.update()
                    return
                # 連打の 2 回目以降は捨てる。ここが抜けていたため、
                # 同じ差し戻しが 2〜3 件 GitHub に登録されていた
                # (自動削除までの残り日数もそのたびに延びていた)
                if not _review_guard.begin(pr['number']):
                    err.value = ('前の操作を送信しています。'
                                 '少し待ってください。')
                    page.update()
                    return
                # 押した瞬間の反応: ボタンを止めてから閉じる
                rj_btn.disabled = True
                page.update()
                page.pop_dialog()
                # 押す前から走っていた取得は、返ってきても採用しない
                # (押す前の内容で画面が描き直され、表示が戻ってしまう)
                reviewcache.note_local_change()
                # 楽観的更新: 閉じた瞬間に却下表示 (バッジ・煙/爆発演出)
                # へ切り替え、送信は裏で行う。失敗したら表示が戻る
                data = reviewcache.get()
                me = (data or {}).get('me')
                optimistic = bool(data is not None and me)
                n_req = reviews.required_approvals(config)
                rejected, crashing = None, False
                if optimistic:
                    at = (datetime.datetime.now(datetime.timezone.utc)
                          .isoformat(timespec='seconds'))
                    cur = reviewcache.pending_of(pr['number']) or pr
                    rejected = (
                        [r for r in cur['rejected'] if r['name'] != me]
                        + [{'name': me, 'comment': comment, 'at': at}])
                    # 却下確定なら、演出が終わるまでカードは触らない
                    crashing = len(rejected) >= n_req
                if optimistic and not crashing:
                    cur['rejected'] = pr['rejected'] = rejected
                    _render_reviews(data, stale=True, animate=True,
                                    status='#%d を差し戻しました '
                                           '(送信中...)。' % pr['number'])
                # 却下確定の畳み込みは「爆発の終わり」と「送信の成功」の
                # 両方がそろってから (_try_auto_release の発射と同じ形)。
                # 演出中にカードが消えない・送信失敗なら爆発だけで戻る
                crash_state = {'anim': False, 'net': False}

                def _crash_finish(key):
                    crash_state[key] = True
                    if not (crash_state['anim'] and crash_state['net']):
                        return
                    live = reviewcache.pending_of(pr['number']) or pr
                    live['rejected'] = pr['rejected'] = rejected
                    live['rejected_final'] = pr['rejected_final'] = True
                    # その場で畳んで残骸の 1 行へ (次の取得を待たない)
                    localstate.hide_pr(pr['number'], config)
                    localstate.mark_auto_folded(pr['number'], config)
                    on_refresh_reviews(None)

                if crashing:
                    # 却下確定。カードはそのまま置いておき、破片が散り
                    # きってから畳む (押した瞬間の反応はボタンの無効化と、
                    # 常駐ロケットが倒れて爆発すること)
                    _hide_zone(pr['number'])
                    t5_status.value = ('#%d を差し戻しました (送信中...)。'
                                       % pr['number'])
                    page.update()
                    _play_crash(pr['number'],
                                done=lambda: _crash_finish('anim'))
                elif not optimistic:
                    # 手元に前回の取得結果がないとき: 楽観的な描き直しも
                    # 演出も無いため、ここで進行中の文言を出す
                    _t5_progress('#%d の却下を送信しています...'
                                 % pr['number'])

                def send():
                    try:
                        reviews.request_changes(pr['number'], comment,
                                                config)
                        t5_status.value = ('#%d を差し戻しました。'
                                           % pr['number'])
                        page.update()
                    except (reviews.ReviewError, ghcli.GhError) as e:
                        t5_status.value = str(e)
                        page.update()
                        on_refresh_reviews(None)  # 楽観的表示を元に戻す
                        return
                    if crashing:
                        _crash_finish('net')
                        return
                    # 自分の却下で必要数に達したら「転倒→爆発」演出。
                    # 非表示エリアへ畳むのは爆発が終わってから
                    if (not optimistic
                            and len(pr['rejected']) + 1 >= n_req):
                        _hide_zone(pr['number'])
                        _play_crash(pr['number'],
                                    done=lambda: on_refresh_reviews(None))
                    else:
                        on_refresh_reviews(None)

                def work():
                    try:
                        send()
                    finally:
                        _review_guard.end(pr['number'])
                run_bg(work)

            rj_btn = ft.FilledButton('却下する', on_click=do_reject,
                                     bgcolor='#b91c1c', color='#ffffff')
            page.show_dialog(ft.AlertDialog(
                modal=True, title=ft.Text('#%d を却下' % pr['number']),
                content=ft.Column([reason, err], tight=True, width=480),
                actions=[
                    ft.TextButton('キャンセル',
                                  on_click=lambda _: page.pop_dialog()),
                    rj_btn,
                ]))
        return handler

    def _dialog_size():
        """ダイアログの内容サイズ (ウィンドウの約 8 割ルール)."""
        if getattr(page, 'web', False):
            win_w = int(page.width or 760)
            win_h = int(page.height or 640)
        else:
            win_w = int(getattr(page.window, 'width', None) or page.width
                        or 760)
            win_h = int(getattr(page.window, 'height', None) or page.height
                        or 640)
        return (max(600, int(win_w * 0.8) - 48),
                min(win_h - 170, int(win_h * 0.8) - 120))

    def _open_diff_dialog(pr, beta_tag, close_label='閉じる',
                          on_error=None):
        """差分をアプリ内ダイアログで開く (バックグラウンド用).

        モデルの取得 (git fetch 等) は呼び出し側の bg スレッドで走る。
        表示後の「ブラウザで開く」は同じモデルから HTML を書き出すだけ
        (取得のやり直しなし)。
        """
        model, workrepo = diffview.build_model_cached(pr, config,
                                                      beta_tag=beta_tag)

        def open_browser(status):
            status.value = 'ブラウザで開いています...'
            page.update()

            def work():
                try:
                    path = diffview.write_html_from_model(model, workrepo)
                    webbrowser.open('file:///'
                                    + path.replace(os.sep, '/'), new=1)
                    status.value = 'ブラウザで開きました。'
                except Exception as e:
                    log.exception('diff browser open failed')
                    status.value = 'ブラウザで開けませんでした: %s' % e
                page.update()
            run_bg(work)

        # 重い組み立ては裏のまま (画面を止めない)、表示だけをループ上で
        # 行う。表示で失敗したときも呼び出し側と同じ場所に知らせる
        dlg = diffdialog.build_dialog(page, model, workrepo,
                                      _dialog_size(),
                                      close_label=close_label,
                                      on_open_browser=open_browser,
                                      run_ui=run_ui,
                                      ask_save_path=ask_save_path)

        def display():
            page.show_dialog(dlg)
            page.update()
        run_ui(display, on_error=on_error)

    def on_show_diff(pr, beta=None):
        """「差分」クリックで差分をアプリ内ダイアログで開く."""
        def handler(_):
            # 押した瞬間に反応 (二度押しで二重に生成・表示しないように)
            restore = _freeze_card(pr['number'])
            _t5_progress('差分を準備しています...')

            def failed(e):
                t5_status.value = '差分を開けませんでした: %s' % e
                page.update()

            def work():
                try:
                    _open_diff_dialog(pr, beta['tag'] if beta else None,
                                      on_error=failed)
                    t5_status.value = ''
                except Exception as e:
                    log.exception('diff viewer failed')
                    failed(e)
                finally:
                    restore()
                    page.update()
            run_bg(work)
        return handler

    def on_resolve_conflict(pr):
        def handler(_):
            # 押した瞬間に反応 (調査中の二度押しを防ぐ。ダイアログ表示
            # またはエラー表示の時点でボタンを元に戻す)
            restore = _freeze_card(pr['number'])
            _t5_progress('最新版との衝突を調べています...')

            def work():
                try:
                    analysis = conflicts.analyze(pr['branch'], config,
                                                 on_progress=_t5_progress)
                except (conflicts.ConflictError, GitError,
                        ghcli.GhError) as e:
                    t5_status.value = str(e)
                    restore()
                    page.update()
                    return

                policy = ft.RadioGroup(value='both', content=ft.Column([
                    ft.Radio(value='both',
                             label='両方の機能を残す (推奨)'),
                    ft.Radio(value='ours',
                             label='自分の機能を優先 (最新版側の該当部分を破棄)'),
                    ft.Radio(value='theirs',
                             label='最新版を優先 (自分の該当部分を破棄)'),
                ]))

                def cancel(_):
                    # 先にダイアログを閉じて反応を見せ、後片付けは裏で行う
                    page.pop_dialog()
                    _t5_progress('統合を取り消しています...')

                    def undo():
                        try:
                            conflicts.abort(analysis)
                            t5_status.value = '統合を取り消しました。'
                        except Exception as e:
                            log.exception('conflict abort failed')
                            t5_status.value = (str(e)
                                               or '統合の取り消しに'
                                                  '失敗しました。')
                        page.update()
                    run_bg(undo)

                def execute(_):
                    # 連打の 2 回目以降は捨てる (統合と承認リセットが
                    # 二重に走らないように)
                    if not _review_guard.begin(pr['number']):
                        _t5_progress('#%d は前の操作を送信しています。'
                                     '少し待ってください。' % pr['number'])
                        return
                    # 押した瞬間の反応: ボタンを止めてから閉じる
                    ex_btn.disabled = True
                    page.update()
                    page.pop_dialog()
                    _t5_progress('最新版との統合を進めています...')

                    def run_resolve():
                        try:
                            summary = conflicts.resolve(
                                analysis, policy.value, config,
                                on_progress=_t5_progress)
                            # 内容が変わったので全員の承認・却下を仕切り直す
                            try:
                                reviews.reset_all_reviews(pr['number'],
                                                          config)
                            except Exception:
                                log.exception('review reset failed')
                            t5_status.value = ('統合しました: %s '
                                               '(承認・却下はリセットされ、'
                                               '改めて確認をお願いする状態に'
                                               '戻りました)' % summary)
                        except (conflicts.ConflictError, GitError,
                                ghcli.GhError) as e:
                            t5_status.value = str(e)
                        finally:
                            _review_guard.end(pr['number'])
                        page.update()
                        on_refresh_reviews(None)
                    run_bg(run_resolve)

                ex_btn = ft.FilledButton('統合を実行', on_click=execute,
                                         bgcolor=NAVY, color='#ffffff')
                body = [ft.Text(analysis['explanation'], size=13)]
                if not analysis['merged_clean']:
                    body.append(policy)
                t5_status.value = ''
                page.show_dialog(ft.AlertDialog(
                    modal=True,
                    title=ft.Text('最新版との統合'),
                    content=ft.Column(body, tight=True, width=560,
                                      scroll=ft.ScrollMode.AUTO),
                    actions=[
                        ft.TextButton('キャンセル', on_click=cancel),
                        ex_btn,
                    ]))
                restore()
                page.update()
            run_bg(work)
        return handler

    def _days_until_cleanup(pr):
        """却下確定から自動削除までの残り日数 (0 = まもなく)。不明は None."""
        try:
            t = datetime.datetime.strptime(
                (pr.get('rejected_since') or '')[:19], '%Y-%m-%dT%H:%M:%S')
        except ValueError:
            return None
        passed = (datetime.datetime.utcnow() - t).days
        return max(0, reviews.rejected_cleanup_days(config) - max(0, passed))

    # 一覧描画後に一度だけ再生するロケット演出 (飾り。操作はできない)
    _rocket_anims = []
    # 表示中の常駐ロケット (発射・爆発の演出中は隠して二重表示を防ぐ)
    _rocket_zones = {}
    # カードの上端 (提出番号 -> 画面上の y)。描画のたびに積み上げ直す
    _card_tops = {}
    # 一覧のスクロール量 (基点は画面の絶対座標なので差し引く)
    _list_scroll = {'pixels': 0.0}
    # 演出のために止めているカードを戻す関数と、再生中の本数
    _frozen_cards = []
    _fx_busy = {'n': 0}
    # カードごとの操作ボタン (演出中に無効化するための控え)
    _card_buttons = {}
    # 承認・却下・取り消しなど「提出に対する操作」の二重実行防止の札。
    # ボタンの無効化・カードの描き直しは押した後に効くため、押した直後に
    # 届いた 2 回目のクリックはこれで捨てる (uiguard.ActionGuard 参照)
    _review_guard = uiguard.ActionGuard()
    # 開いたとき自動リリースを試した提出番号 (失敗時の連続再試行を防ぐ)
    _auto_release_tried = set()
    # リリース公開待ちの期限 (この間は定期チェックが更新バッジを消さない)
    _pending_release = {'until': 0.0}

    def _rocket_zone(pr, n_req):
        """ボタン列末尾の常駐ロケット。承認・却下の進み具合を演出で表す.

        却下確定 (一覧に戻した表示) は残骸の静止画のみ (演出はない)。
        却下が期日内に取り消されれば状態が変わり、煙付きで再表示される。
        """
        fired = len(pr['approved']) >= max(1, n_req - 1)
        if pr.get('rejected_final'):
            state = 'wreck'              # 却下確定: 散らばった残骸
        elif pr['rejected'] and fired:
            state = 'ignited_smoking'    # 承認も却下もある: 火も煙も
        elif pr['rejected']:
            state = 'smoking'            # 却下 1 つ: 煙
        elif fired:
            state = 'ignited'            # 承認 1 つ: 点火
        else:
            state = 'idle'
        control, anim = rocketfx.zone(state)
        if anim is not None:
            # 開いたときに一度だけ再生 (page.update を渡す)
            # 書き換えと送信をひとまとめにループ上で行う (演出は裏
            # スレッドで回るため、別々にすると送信中の書き換えになる)
            _rocket_anims.append(
                lambda fn=anim: fn(lambda: rocketfx.ui_sync(page,
                                                            page.update)))
        # 演出中に隠せるよう登録しておく (操作ボタンとは spacer で分離)
        wrapper = ft.Container(content=control)
        _rocket_zones[pr['number']] = wrapper
        return wrapper

    def _line(height, control):
        """高さを固定した 1 行 (演出の基点を積み上げで出すため)."""
        return ft.Container(height=height, content=control,
                            alignment=ft.Alignment(-1, 0))

    def _stack_height(rows):
        """並べた行の合計の高さ (行間ぶんを足す)."""
        return sum(r.height for r in rows) + _LINE_GAP * (len(rows) - 1)

    def _review_row(pr, me, beta=None):
        n_req = reviews.required_approvals(config)
        final = pr.get('rejected_final')
        # 最新版と衝突中は「統合待ち」: 提出者の統合・取り下げ以外を閉じる
        locked = pr['conflicting'] and not final
        rejected_names = [r['name'] for r in pr['rejected']]

        def _badge(text, bg, fg):
            return ft.Container(
                bgcolor=bg, border_radius=4,
                padding=ft.Padding.symmetric(vertical=2, horizontal=8),
                content=ft.Text(text, size=12, weight=ft.FontWeight.BOLD,
                                color=fg))

        badges = []
        if final:
            badges.append(_badge('却下 %d/%d' % (len(rejected_names), n_req),
                                 '#dc2626', '#ffffff'))
            left = _days_until_cleanup(pr)
            if left is not None:
                badges.append(ft.Text(
                    'あと %d 日で自動削除' % left if left
                    else 'まもなく自動削除', size=12,
                    weight=ft.FontWeight.BOLD, color='#b91c1c'))
        else:
            badges.append(_badge('承認 %d/%d' % (len(pr['approved']), n_req),
                                 '#fef08a', '#713f12'))
            if pr['rejected']:
                badges.append(ft.Container(
                    content=_badge('却下あり', '#fecaca',
                                   '#7f1d1d'),
                    tooltip='却下が 1 件あります (却下した本人の'
                            '取り消し、または修正版の再提出で'
                            '解消します)'))
            if pr['conflicting']:
                badges.append(_badge('最新版と衝突', '#fde68a', '#78350f'))
        checks_label, checks_color = {
            'success': ('検証OK', '#15803d'),
            'failure': ('検証で問題あり', '#b91c1c'),
            'pending': ('検証中', '#b45309'),
        }[pr['checks']]

        # 行はすべて _line() で高さを固定する。カードの高さはここで
        # 並べた行の合計から出し、演出の基点はその積み上げで決まる
        # (行を足しても引いても自動で追従する)
        lines = [
            _line(_H_TITLE, ft.Row(
                [_rocket_zone(pr, n_req),
                 ft.Text('#%d %s' % (pr['number'], pr['title']),
                         weight=ft.FontWeight.BOLD, size=13,
                         expand=True, max_lines=1,
                         overflow=ft.TextOverflow.ELLIPSIS)],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER)),
            _line(_H_BADGE, ft.Row(
                badges + [ft.Text(checks_label, size=12,
                                  color=checks_color),
                          ft.Text('提出者: %s' % pr['author'], size=12,
                                  color='#555555')], spacing=8)),
        ]
        if pr['approved'] and not final:
            lines.append(_line(_H_TEXT, ft.Text(
                '承認済み: %s' % '、'.join(pr['approved']),
                size=12, color='#15803d', max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS)))
        for rej in pr['rejected']:
            body = '%s さんが差し戻し: %s' % (
                rej['name'], rej['comment'] or '(理由なし)')
            # 長い理由は 2 行で切り、全文はマウスを乗せると出す
            # (行の高さが伸びると下のカードの演出の基点がずれるため)
            lines.append(_line(_H_REJECT, ft.Text(
                body, size=12, color='#b91c1c', max_lines=2,
                overflow=ft.TextOverflow.ELLIPSIS, tooltip=body)))

        buttons = []
        if beta is not None and not final:
            buttons.append(ft.FilledButton(
                'β版 %s を試す' % beta['tag'], icon=ft.Icons.SCIENCE,
                disabled=locked,
                on_click=try_beta(pr, beta),
                bgcolor='#e5e7eb' if locked else AMBER,
                color='#9ca3af' if locked else '#ffffff'))
        fb_all = pr.get('feedback') or []
        # 件数は現在のβ版宛てのみ (統合後は新β版基準で仕切り直し)
        fb = [f for f in fb_all
              if beta is None or f['tag'] == beta['tag']]
        buttons.append(ft.OutlinedButton(
            'フィードバック %d 件' % len(fb),
            disabled=locked or (beta is None and not fb_all),
            on_click=on_feedback_dialog(pr, beta, me)))
        buttons.append(ft.OutlinedButton('差分', disabled=locked,
                                         on_click=on_show_diff(pr, beta)))
        if locked:
            # 統合待ち: 提出者だけが動ける。他は操作不可 (押せない状態で表示)
            if pr['author'] == me:
                buttons.append(ft.FilledButton(
                    '最新版と統合', on_click=on_resolve_conflict(pr),
                    bgcolor=AMBER, color='#ffffff'))
                buttons.append(ft.OutlinedButton(
                    '取り下げ', on_click=on_withdraw(pr)))
            else:
                buttons.append(ft.FilledButton(
                    '承認', disabled=True,
                    bgcolor='#e5e7eb', color='#9ca3af'))
                buttons.append(ft.OutlinedButton('却下', disabled=True))
        elif final:
            # 却下確定: 却下した本人は取り消し可、提出者は取り下げ可。
            # 全員が自分の画面から非表示にできる
            if me in rejected_names:
                buttons.append(ft.OutlinedButton(
                    '却下を取り消す', on_click=on_cancel_review(pr)))
            if pr['author'] == me:
                buttons.append(ft.OutlinedButton(
                    '取り下げ', on_click=on_withdraw(pr)))
            buttons.append(ft.OutlinedButton(
                '非表示', on_click=on_hide_pr(pr)))
        elif pr['author'] == me:
            buttons.append(ft.OutlinedButton('取り下げ',
                                             on_click=on_withdraw(pr)))
        else:
            # 2 回目のクリックで自分の承認・却下を取り消せる (トグル)
            if me in pr['approved']:
                buttons.append(ft.FilledButton(
                    '承認を取り消す', on_click=on_cancel_review(pr),
                    bgcolor='#15803d', color='#ffffff'))
            else:
                buttons.append(ft.FilledButton(
                    '承認', on_click=on_approve(pr),
                    bgcolor='#15803d', color='#ffffff'))
            if me in rejected_names:
                buttons.append(ft.OutlinedButton(
                    '却下を取り消す', on_click=on_cancel_review(pr)))
            else:
                buttons.append(ft.OutlinedButton('却下',
                                                 on_click=on_reject(pr)))
        # 演出中の無効化用に操作ボタンを控えておく
        _card_buttons[pr['number']] = list(buttons)
        if locked:
            # カード情報は薄く、案内文だけ明るく表示する
            rows = [
                ft.Container(opacity=0.45, height=_stack_height(lines),
                             content=ft.Column(lines, spacing=_LINE_GAP)),
                _line(_H_NOTE, ft.Text(
                    '最新版と衝突したため、提出者による統合待ちです。'
                    '統合されると承認・却下はリセットされ、'
                    '改めて確認をお願いします。',
                    size=13, weight=ft.FontWeight.BOLD, color='#b45309',
                    max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)),
                _line(_H_BTNS, ft.Row(buttons, spacing=8)),
            ]
        else:
            lines.append(_line(_H_BTNS, ft.Row(buttons, spacing=8)))
            rows = lines
        # カードの高さは「実際に並べた行」から出す。演出の基点はこの
        # 高さの積み上げで決まるので、行を足したら自動で追従する
        return ft.Container(bgcolor='#f5f7fa', border_radius=6,
                            padding=_CARD_PAD,
                            height=_CARD_PAD * 2 + _stack_height(rows),
                            opacity=0.72 if final else 1.0,
                            content=ft.Column(rows, spacing=_LINE_GAP))

    def _beta_for(pr_number, betas):
        """提出番号に対応するβ版 (リリースノートの #N で対応付け)."""
        for r in betas:
            if feedback.pr_number_from_release(r) == pr_number:
                return r
        return None

    def _fetched_hm(data):
        """スナップショット取得時刻を「HH:MM」(この PC の時刻) で返す."""
        try:
            t = datetime.datetime.fromisoformat(
                data.get('fetched_at') or '')
        except (TypeError, ValueError):
            return ''
        if t.tzinfo is not None:
            t = t.astimezone()
        return t.strftime('%H:%M')

    # 描画は複数スレッド (ボタン操作・定期更新) から呼ばれるため直列化する
    _render_lock = threading.Lock()
    _prefetch_lock = threading.Lock()
    # 前回描画した内容の指紋 (同じ内容なら一覧を作り直さないための記録)
    _render_state = {'key': None}

    def _render_reviews(data, stale=False, animate=None, status=None):
        """取得済みスナップショットで一覧を描画する.

        stale=True は前回結果の即時表示 (裏の取得で差し替わる前提)。
        ローカル記録の掃除・自動畳みは最新データの描画 (stale=False) の
        ときだけ行う。animate はロケット演出の再生 (省略時は最新データの
        描画のみ再生。楽観的更新の即時描画では True を渡す)。
        status は状態行の文言の上書き (楽観的更新の「送信中...」など)。
        """
        if animate is None:
            animate = not stale
        with _render_lock:
            _render_reviews_locked(data, stale, animate, status)

    def _review_status_line(data, stale, status):
        """一覧下の状態行の文言を決めて反映する."""
        if status is not None:
            t5_status.value = status
        elif stale:
            age = reviewcache.age_minutes(data)
            t5_status.value = ('前回の内容を表示中%s。最新を確認して'
                               'います...'
                               % (' (%d 分前の取得)' % age if age else ''))
        else:
            hm = _fetched_hm(data)
            t5_status.value = ('最新の状態です'
                               + (' (%s 取得)' % hm if hm else ''))

    def _render_reviews_locked(data, stale, animate, status):
        # 演出のために止めていたカードを戻す。一覧を作り直さない経路
        # (内容が前回と同じとき) でも必ず通るよう、ここで行う
        _unfreeze_cards()
        pending, me = data['pending'], data['me']
        betas = ghcli.prereleases(data.get('releases') or [])
        if not stale:
            # 「非表示」にした却下確定の提出は自分の画面から除く
            # (クローズ済みの記録は掃除する)
            localstate.prune_hidden([p['number'] for p in pending], config)
            # 却下確定した提出は自動で一覧下の「非表示」へ畳む
            # (「一覧に戻す」で戻せる。戻したあとは再び自動では畳まない)
            auto_done = localstate.auto_folded(config)
            for p in pending:
                if p.get('rejected_final') and p['number'] not in auto_done:
                    localstate.hide_pr(p['number'], config)
                    localstate.mark_auto_folded(p['number'], config)
        hidden = localstate.hidden_prs(config)
        # 表示内容が前回の描画と同じなら一覧を作り直さない。
        # 作り直すとボタンが別のコントロールに差し替わり、差し替え直前の
        # クリックが取りこぼされる (裏の更新とクリックの競合防止)
        render_key = json.dumps(
            [pending, me, sorted(b['tag'] for b in betas), sorted(hidden)],
            ensure_ascii=False, sort_keys=True, default=str)
        if render_key == _render_state.get('key'):
            _review_status_line(data, stale, status)
            page.update()
            return
        _render_state['key'] = render_key
        folded = [p for p in pending
                  if p.get('rejected_final') and p['number'] in hidden]
        visible = [p for p in pending if p not in folded]
        set_badge(review_badge, len(visible))
        _rocket_anims.clear()
        _rocket_zones.clear()
        _card_buttons.clear()
        _card_tops.clear()
        t5_list.controls.clear()
        if not visible:
            t5_list.controls.append(
                ft.Text('確認・承認待ちの提出はありません', size=14))
        # カードの上端を積み上げながら控える (演出の基点に使う)
        top = _LIST_TOP
        for pr in visible:
            card = _review_row(pr, me, _beta_for(pr['number'], betas))
            _card_tops[pr['number']] = top
            top += card.height + _CARD_GAP
            t5_list.controls.append(card)
        if folded:
            # 非表示にした提出は一覧の一番下に 1 行で畳んでおく
            t5_list.controls.append(ft.Text(
                '却下が確定した提出 (自動で畳みました)', size=12,
                color='#9ca3af'))
            for pr in folded:
                t5_list.controls.append(ft.Row([
                    ft.Image(src=rocketfx.WRECK, width=30, height=13),
                    ft.Text('#%d %s' % (pr['number'], pr['title']),
                            size=12, color='#9ca3af', expand=True),
                    ft.TextButton('一覧に戻す',
                                  on_click=on_unhide_pr(pr)),
                ], spacing=8))
        # 提出に対応しないβ版はリリース・取り下げ・却下確定の時点で
        # 自動削除されるため、ここでは表示しない
        _review_status_line(data, stale, status)
        page.update()
        # 描画後にロケット演出 (点火・煙) を一度だけ再生
        anims = _rocket_anims[:]
        _rocket_anims.clear()
        if animate:
            for fn in anims:
                def play(fn=fn):
                    try:
                        fn()
                    except Exception:
                        log.debug('ロケット演出をスキップ', exc_info=True)
                run_bg(play)
        # 差分モデルを裏で先読みしておく (「差分」クリックを即表示に)
        _prefetch_diffs(visible)

    def _prefetch_diffs(pending):
        """一覧の提出の差分モデルを裏で組んでキャッシュしておく.

        組み立て済みのものは飛ばすので、一覧が描き直されるたびに
        呼んでも無駄な取得は起きない。クリックは提出ごとの鍵で動く
        ため、ここで別の提出を組んでいてもクリックは待たされない。
        """
        # 押せない提出 (畳んだもの) は呼び出し元によらず対象外にする。
        # キャッシュ上限を超える先読みは追い出し合いになるだけなので
        # 一覧の上から上限件数まで (それ以降はクリック時に組む)
        hidden = localstate.hidden_prs(config)
        targets = [p for p in pending
                   if diffview.model_key(p) and p['number'] not in hidden
                   ][:diffview._MODEL_CACHE_MAX]
        targets = [p for p in targets
                   if diffview.model_key(p) not in diffview._model_cache]
        if not targets:
            return

        def work():
            # 先読み同士は 1 本ずつ。すでに走っているときは何もしない
            # (待たせるとスレッドが溜まる。次の描画・取得で拾い直す)
            if not _prefetch_lock.acquire(blocking=False):
                return
            try:
                for pr in targets:
                    if diffview.model_key(pr) in diffview._model_cache:
                        continue
                    try:
                        diffview.build_model_cached(pr, config)
                    except Exception:
                        log.debug('差分の先読みに失敗 #%s',
                                  pr.get('number'), exc_info=True)
            finally:
                _prefetch_lock.release()
        run_bg(work)

    def _fetch_review_snapshot(on_progress=None):
        """一覧とリリース一覧を一括取得してスナップショットへ保存する.

        取得は reviews.fetch_snapshot (1 回の一括取得。失敗時は従来経路へ
        自動フォールバック)。on_progress で進行状況を画面に流せる。
        戻り値: 採用されたスナップショット。より新しい取得に追い越されて
        破棄されたときは None。取得失敗は ReviewError / GhError を送出。
        """
        seq = reviewcache.next_seq()
        prev = reviewcache.get() or reviewcache.load_from_disk(config)
        snap = reviews.fetch_snapshot(
            config, on_progress=on_progress,
            known_forks=reviews.fork_memo(prev))
        data = reviewcache.put(snap['pending'], snap['releases'],
                               snap['me'], seq=seq, config=config,
                               merged=snap.get('merged'))
        # 一覧の描画を待たず、届いた時点で差分の先読みを始める
        # (タブを開いてすぐ「差分」を押しても間に合わせるため)。
        # 追い越されて捨てられた取得結果では先読みしない
        if data is not None:
            _prefetch_diffs(data['pending'])
        return data

    def on_refresh_reviews(_):
        """一覧の再描画。手元にある前回の取得結果を即座に表示し、
        裏で最新を取得して差し替える (取得を待たせない)。"""
        cached = reviewcache.get() or reviewcache.load_from_disk(config)
        if cached is not None:
            _render_reviews(cached, stale=True)
        else:
            _t5_progress('最新の提出状況とβ版・リリースの一覧を確認して'
                         'います...')

        def work():
            try:
                data = _fetch_review_snapshot(on_progress=_t5_progress)
            except (reviews.ReviewError, ghcli.GhError) as e:
                t5_status.value = str(e)
                page.update()
                return
            if data is None:
                return  # より新しい取得に追い越された
            _render_reviews(data)
            # リリースボタン廃止に伴い、承認がそろったまま未リリースの
            # 提出 (2 人目の承認時に自動リリースできなかったもの) は
            # 最新の取得結果に基づき自動でリリースする (発射演出つき)。
            # 楽観的表示 (stale 描画) からは発火しない
            for p in data['pending']:
                if (p['number'] not in _auto_release_tried
                        and not p.get('rejected_final')
                        and reviews.can_release(p, config, data['me'])):
                    _auto_release_tried.add(p['number'])
                    _try_auto_release(p['number'])
                    break
        run_bg(work)

    def _on_list_scroll(e):
        """一覧のスクロール量を控える (演出の基点は画面の絶対座標のため)."""
        try:
            _list_scroll['pixels'] = float(e.pixels or 0.0)
        except (TypeError, ValueError):
            pass

    # 説明文は高さを固定する。ここが伸び縮みすると一覧の位置が動き、
    # カードごとの演出の基点 (_rocket_base) がずれるため
    tab_beta_review = ft.Container(padding=_TAB_PAD, content=ft.Column([
        ft.Container(height=_INTRO_H, content=ft.Column([
            ft.Text('提出された更新版は、検証を通過するとβ版として発行され'
                    'ます。β版を確認したら承認してください。%d 人の'
                    '承認がそろうと自動で正式版になり、みなさんの'
                    'マネージャーに自動で取り込まれます。'
                    '自分の提出は自分では承認できません。'
                    % reviews.required_approvals(config),
                    size=13, color='#555555'),
            ft.Text('β版は安定版とは別フォルダ・別データ・別画面で起動する'
                    'ため、通常の作業には影響しません。', size=12,
                    color='#555555'),
        ], spacing=8)),
        t5_list,
        t5_status,
    ], spacing=_INTRO_GAP, scroll=ft.ScrollMode.AUTO,
        on_scroll=_on_list_scroll))

    # ---------------- 組み立て ----------------

    launch_label, launch_badge = tab_label('起動')
    review_label, review_badge = tab_label('β版の確認と承認')

    def on_tab_change(e):
        # タブに来る = 最新の情報が見たいとき。手動の更新ボタンは置かず、
        # タブを開いた瞬間に手元の内容を即表示し、裏で最新へ更新する
        idx = getattr(e.control, 'selected_index', None)
        if idx == 0:
            check_update_notice(reschedule=False)
        elif idx == 2:
            on_refresh_reviews(None)

    page.add(
        header(),
        ft.Tabs(
            length=3, selected_index=0, animation_duration=150, expand=True,
            on_change=on_tab_change,
            content=ft.Column([
                ft.TabBar(tabs=[
                    ft.Tab(label=launch_label),
                    ft.Tab(label='更新版を提出'),
                    ft.Tab(label=review_label),
                ]),
                ft.TabBarView(expand=True, controls=[
                    tab_launch,
                    tab_submit,
                    tab_beta_review,
                ]),
            ], spacing=0, expand=True),
        ),
    )
    refresh_local_version()

    # ------- 通知の更新 (起動時 + 定期ポーリング): 更新バナーとタブバッジ -------

    def _watch_new_release():
        """リリース公開 (数分かかる) を見張り、公開されたら自動更新する.

        リリース直後は Releases の公開処理が終わっておらず即時チェック
        では「更新なし」になるため、「準備中」の案内をすぐ出し、
        公開を確認できたら自動で取り込んで黄色いタグに切り替える。
        最初の 1 分は 10 秒間隔・以降 30 秒間隔で最大 10 分見張る。
        """
        _pending_release['until'] = time.time() + 10 * 60
        # 起動タブへバッジ + 同色の黄色タグで「準備中」を知らせる
        # (どちらも更新の取り込みが完了するまで表示し続ける)
        t1_preparing_text.value = ('新しい正式版を準備中です (数分かかり'
                                   'ます)。公開されると自動で更新されます。')
        t1_preparing_tag.visible = True
        set_badge(launch_badge, 1)
        page.update()

        def work():
            start = time.time()
            while time.time() < _pending_release['until']:
                time.sleep(10 if time.time() - start < 60 else 30)
                try:
                    result = updater.check_update(repo, stable)
                except Exception:
                    result = None
                if (result and result['has_update']
                        and result['latest'] is not None):
                    _pending_release['until'] = 0.0
                    _auto_update(result['latest'])
                    return
            # 期限切れ: 黙って消さず、その後の動きを案内する。
            # 「まだ処理中」と「失敗して永久に来ない」は伝えることが
            # 違うため、リリース処理の結果を見て案内を分ける
            _pending_release['until'] = 0.0
            try:
                state = reviews.release_run_status(config)
            except Exception:
                log.exception('リリース処理の状態を確認できませんでした')
                state = None
            if state == 'failed':
                msg = ('正式版の作成に失敗しました。管理者に連絡して'
                       'ください (取り込みは行われません)。')
            elif state == 'running':
                msg = ('正式版を作成中です (時間がかかっています)。'
                       '公開されると自動で取り込んでお知らせします。')
            else:
                msg = ('リリースの公開確認ができませんでした。公開されると'
                       '自動で取り込んでお知らせします。')
            t1_preparing_text.value = msg
            t5_status.value = msg
            page.update()
        run_bg(work)

    def check_update_notice(reschedule=True):
        """起動時と定期的なバックグラウンド更新.

        承認待ち一覧 + リリース一覧を 1 回のスナップショット取得に
        まとめ、承認タブの表示・バッジを最新化し、新しい正式版が
        あれば自動で取り込む (更新タブ廃止に伴い手動操作なし)。
        """
        def work():
            try:
                data = _fetch_review_snapshot()
            except Exception:
                log.info('更新チェックをスキップしました (オフライン等)')
                t1_fresh.value = ('確認できませんでした。ネットワーク接続を'
                                  'ご確認ください。')
                page.update()
                return
            if data is None:
                return  # より新しい取得が反映済み
            # 承認タブの一覧・バッジを先に最新化する (自動更新の
            # ダウンロードで画面の反映を待たせない)
            _render_reviews(data, animate=False)
            _refresh_current_notes(data['releases'])
            try:
                result = updater.check_update(repo, stable,
                                              releases=data['releases'])
            except Exception:
                log.info('更新チェックをスキップしました (オフライン等)')
                result = None
            if result is None:
                return
            now_hm = datetime.datetime.now().strftime('%H:%M')
            if result['has_update'] and result['latest'] is not None:
                _pending_release['until'] = 0.0
                if _auto_update(result['latest']):
                    t1_fresh.value = '最新の状態です (%s 確認)' % now_hm
                else:
                    # 取り込み待ち中も「いつ確認したか」は残す
                    t1_fresh.value = '更新の確認: %s' % now_hm
                    refresh_local_version(latest=False)
            elif time.time() >= _pending_release['until']:
                # 最新の状態: 取り込み予約や古い案内が残っていれば消す
                _update_state['pending'] = None
                if t1_notice.value:
                    t1_notice.value = ''
                t1_fresh.value = '最新の状態です (%s 確認)' % now_hm
                refresh_local_version(latest=True)
            else:
                # 公開待ちの間も「いつ確認したか」を残す (手動確認の応答)
                t1_fresh.value = '更新の確認: %s (公開待ち)' % now_hm
            page.update()
        run_bg(work)
        if reschedule:
            timer = threading.Timer(UPDATE_POLL_SECONDS, check_update_notice)
            timer.daemon = True
            timer.start()

    # 起動時: 前回のスナップショットがあれば先に描画しておき、直後の
    # 定期更新が最新へ差し替える (初回からタブを開いた瞬間に表示される)
    _startup_snapshot = reviewcache.load_from_disk(config)
    if _startup_snapshot is not None:
        _render_reviews(_startup_snapshot, stale=True, animate=False)
        _refresh_current_notes(_startup_snapshot.get('releases'))

    check_update_notice()

    # ---- メンバー参加の自動処理 ----
    # 1. 招待が届いていれば自動承諾 (起動するだけで参加完了)
    # 2. まだ collaborator でなければ参加申請 Issue を自動作成
    #    (オーナーに通知メールが届き、「承認」の返信で自動招待される)

    def check_membership():
        def work():
            try:
                if ghcli.accept_repo_invitation(repo):
                    log.info('リポジトリへの招待を承諾しました')
                    return
                if ghcli.has_push_access(repo):
                    return
                if ghcli.find_my_join_request(repo) is None:
                    name = settings.user_name(config) or ''
                    ghcli.create_join_request(repo, name)
                    join_notice.value = ('参加申請を送信しました。管理者の'
                                         '承認後に提出・承認へ参加できます '
                                         '(起動・β版の試用は承認前でも'
                                         '使えます)。')
                else:
                    join_notice.value = ('参加申請は送信済みです。管理者の'
                                         '承認をお待ちください (起動・'
                                         'β版の試用はそのまま使えます)。')
                page.update()
            except Exception:
                log.info('参加状態の確認をスキップしました (オフライン等)')
        run_bg(work)

    check_membership()

    # キャッシュの事前取得は check_update_notice のスナップショット取得が
    # 兼ねる (ログイン名・メンバー一覧・一覧・リリース一覧が一度に温まる)

    # ---------------- 初回セットアップ (名前と API キーの登録) ----------------

    def show_first_run_dialog():
        name_field = ft.TextField(label='名前 (例: 山田太郎)', autofocus=True)
        key_field = ft.TextField(
            label='Claude API キー (sk-ant- で始まる文字列)',
            password=True, can_reveal_password=True)
        err_text = ft.Text('', size=12, color='#b91c1c')

        def on_save(_):
            # 押した瞬間にボタンを止めて進行中を出し、保存は裏で行う
            dialog_busy(save_btn, err_text, '登録しています...')

            def work():
                try:
                    settings.save_settings(name_field.value,
                                           key_field.value, config)
                except ValueError as e:
                    dialog_error(save_btn, err_text, str(e))
                    return
                page.pop_dialog()
                page.update()
            run_bg(work)

        save_btn = ft.FilledButton('登録してはじめる', on_click=on_save,
                                   bgcolor=NAVY, color='#ffffff')
        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text('はじめに登録してください'),
            content=ft.Column([
                ft.Text('mgtkit マネージャーの利用には、名前と本人の '
                        'Claude API キーの登録が必要です。', size=13),
                ft.Text('キーはこの PC の中にだけ保存され、提出時の説明文の'
                        '自動作成と、検証失敗時の自動修正に本人のキーとして'
                        '使われます。', size=12, color='#555555'),
                name_field,
                key_field,
                err_text,
            ], tight=True, width=480),
            actions=[save_btn]))

    # ---------------- 起動時の自動最新化 (直接変更の退避つき) ----------------

    def _tag(text, bg, fg):
        return ft.Container(
            bgcolor=bg, border_radius=4,
            padding=ft.Padding.symmetric(vertical=2, horizontal=8),
            content=ft.Text(text, size=11, weight=ft.FontWeight.BOLD,
                            color=fg))

    def check_selfupdate():
        def work():
            upd = selfupdate.auto_update()
            if not upd.get('stashed'):
                return

            def open_detail(_):
                webbrowser.open(
                    'file:///' + upd['diff_html'].replace(os.sep, '/'),
                    new=1)

            done = ('最新版に更新しました'
                    if upd.get('updated') else
                    '退避しました (更新はネットワーク接続後の次回起動時に'
                    '行われます)')
            page.show_dialog(ft.AlertDialog(
                modal=True,
                title=ft.Row([
                    _tag('注意', '#fef3c7', '#92400e'),
                    _tag('要確認', '#fee2e2', '#b91c1c'),
                    ft.Text('直接変更を退避しました', size=15,
                            weight=ft.FontWeight.BOLD),
                ], spacing=8),
                content=ft.Column([
                    ft.Text('自動バージョン更新されるフォルダ置き場所 (%s) '
                            '内のファイルに直接変更があったため、退避領域 '
                            '(%s) に移して%s。'
                            % (paths.REPO_ROOT, upd['stash_dir'], done),
                            size=13),
                    ft.Text('対象: %s' % '、'.join(upd['stashed'][:8])
                            + ('ほか' if len(upd['stashed']) > 8 else ''),
                            size=12, color='#555555'),
                    ft.Text('このフォルダ内のファイルは直接編集せず、'
                            '機能追加は取得した版のコピーで作業してください'
                            '(使い方ガイド 1 章)。', size=12,
                            color='#555555'),
                    ft.Text('直した内容がみんなにも役立ちそうなら、ぜひ'
                            '「更新版を提出」から共有してください '
                            '(小さな改良でも大歓迎です)。', size=12,
                            color='#047857'),
                ], tight=True, width=560),
                actions=[
                    ft.TextButton('詳細 (差分を見る)', on_click=open_detail),
                    ft.FilledButton('OK',
                                    on_click=lambda _: page.pop_dialog(),
                                    bgcolor=NAVY, color='#ffffff'),
                ]))
            page.update()
        run_bg(work)

    check_selfupdate()

    if settings.load_settings(config) is None:
        show_first_run_dialog()


if __name__ == '__main__':
    ft.app(main)
