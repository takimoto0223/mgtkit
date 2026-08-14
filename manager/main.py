# -*- coding: utf-8 -*-
"""mgtkit アプリマネージャー UI (Flet).

起動方法 (リポジトリルートで): python -m manager.main
Git / GitHub の用語はユーザーに見せず、平易な日本語のみ表示する。
"""
import asyncio
import datetime
import logging
import os
import threading
import time

import flet as ft

import webbrowser

from . import (autofix, conflicts, diffview, feedback, ghcli, launcher,
               localstate, paths, reviewcache, reviews, rocketfx,
               selfupdate, settings, submit, updater, usage)
from .gitcli import GitError

UPDATE_POLL_SECONDS = 10 * 60  # 新しい安定版の定期チェック間隔

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

NAVY = '#2b4a6f'
AMBER = '#b45309'


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
    t1_status = status_text()
    t1_notice = ft.Text('', size=13, weight=ft.FontWeight.BOLD, color=AMBER)
    join_notice = ft.Text('', size=12, color='#555555')
    # 自動更新の完了通知: 黄色いタグ + 更新内容 (更新タブ廃止に伴う導線)
    t1_update_tag = ft.Container(
        visible=False, bgcolor='#fef08a', border_radius=6,
        padding=ft.Padding.symmetric(vertical=6, horizontal=12),
        content=ft.Text('', size=13, weight=ft.FontWeight.BOLD,
                        color='#713f12'))
    t1_update_notes = ft.Container(
        visible=False, bgcolor='#f5f7fa', border_radius=6, padding=12,
        content=ft.Text('', size=13, selectable=True, color='#374151'))
    t1_launch_btn = ft.FilledButton('起動', icon=ft.Icons.PLAY_ARROW,
                                    bgcolor=NAVY, color='#ffffff')
    # installing: 自動更新の実行中 (二重実行と起動の衝突を防ぐ) /
    # pending: アプリ起動中などで取り込めなかった新しい正式版
    # (次回「起動」時に取り込む)
    _update_state = {'installing': False, 'pending': None}

    def refresh_local_version():
        info = updater.local_version_info(stable)
        if info is None:
            t1_version.value = '安定版は未取得です'
            t1_status.value = ('「起動」を押すと最新の安定版を自動で取得して'
                               '開きます。')
        else:
            t1_version.value = '安定版 %s' % info.get('version', '?')
            t1_status.value = '配布日: %s' % info.get('distributed_at', '-')
        page.update()

    def _show_updated(release):
        """自動更新の完了を黄色いタグ + 更新内容で知らせる."""
        t1_update_tag.content.value = ('バージョンが更新されました (%s)。'
                                       % release['tag'])
        t1_update_tag.visible = True
        t1_update_notes.content.value = (release.get('notes')
                                         or '(更新内容の記載はありません)')
        t1_update_notes.visible = True
        t1_notice.value = ''
        page.update()

    def _auto_update(latest):
        """新しい正式版を自動で取り込む (更新タブ廃止に伴い手動操作なし).

        アプリが起動中 (Windows ではフォルダが使用中で置き換え不可) の
        ときは作業を邪魔しないため取り込まず、次回「起動」時に取り込む
        予約だけする。戻り値: 取り込んだら True。
        """
        if _update_state['installing'] or latest is None:
            return False
        if launcher.port_in_use(paths.stable_port(config)):
            _update_state['pending'] = latest
            t1_notice.value = ('新しい安定版 %s があります。次回の「起動」で'
                               '自動的に更新されます。' % latest['tag'])
            page.update()
            return False
        _update_state['installing'] = True
        t1_launch_btn.disabled = True
        page.update()
        try:
            def progress(msg):
                t1_status.value = ('新しい安定版 %s を取り込んでいます... %s'
                                   % (latest['tag'], msg))
                page.update()
            updater.install_release(repo, latest, stable,
                                    on_progress=progress)
            _update_state['pending'] = None
            refresh_local_version()
            _show_updated(latest)
            ok = True
        except Exception:
            log.exception('自動更新に失敗しました')
            t1_notice.value = ('自動更新に失敗しました。ネットワーク接続を'
                               '確認してください (次回の起動時にもう一度'
                               '試します)。')
            _update_state['pending'] = latest
            ok = False
        _update_state['installing'] = False
        t1_launch_btn.disabled = False
        page.update()
        return ok

    def on_launch_stable(_):
        t1_status.value = '起動しています...'
        page.update()

        def work():
            def progress(msg):
                t1_status.value = msg
                page.update()
            try:
                # 取り込み待ちの新しい正式版があれば起動前に取り込む
                pending = _update_state['pending']
                if pending is not None and not _update_state['installing']:
                    progress('新しい安定版 %s に更新しています...'
                             % pending['tag'])
                    launcher.stop_app(paths.stable_port(config))
                    updater.install_release(repo, pending, stable,
                                            on_progress=progress)
                    _update_state['pending'] = None
                    refresh_local_version()
                    _show_updated(pending)
                if updater.local_version_info(stable) is None:
                    # 初回: 最新の安定版を自動で取得してから起動する
                    progress('最新の安定版を確認しています...')
                    latest = ghcli.latest_stable(
                        ghcli.fetch_releases(repo))
                    if latest is None:
                        t1_status.value = ('配布された安定版がまだありません。'
                                           '管理者に確認してください。')
                        page.update()
                        return
                    # 前回の残骸のサーバーが動いているとフォルダを
                    # 置き換えられないため、先に終了させる
                    launcher.stop_app(paths.stable_port(config))
                    updater.install_release(repo, latest, stable,
                                            on_progress=progress)
                    refresh_local_version()
                _, url = launcher.launch_app(
                    stable, paths.stable_port(config), channel='stable')
                t1_status.value = 'ブラウザで開きます: %s' % url
            except (ghcli.GhError, launcher.LaunchError, Exception) as e:
                log.exception('起動に失敗しました')
                t1_status.value = str(e) or '起動に失敗しました。'
            page.update()
        run_bg(work)

    t1_launch_btn.on_click = on_launch_stable

    def _download_history_zip(rel, dlg_status):
        """過去の正式版の ZIP をダウンロードフォルダへ保存する."""
        def handler(_):
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
                    dest_dir = os.path.join(os.path.expanduser('~'),
                                            'Downloads')
                    os.makedirs(dest_dir, exist_ok=True)
                    dest = os.path.join(dest_dir,
                                        'mgtkit-%s.zip' % rel['tag'])
                    shutil.copyfile(path, dest)
                    dlg_status.value = 'ZIP を保存しました: %s' % dest
                except Exception as e:
                    log.exception('過去の版の取得に失敗しました')
                    dlg_status.value = (str(e)
                                        or 'ダウンロードに失敗しました。')
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                page.update()
            run_bg(work)
        return handler

    def on_show_history(_):
        """過去の更新ログ (正式版ごとのリリースノート + ZIP 保存)."""
        cached = reviewcache.get() or reviewcache.load_from_disk(config)
        stables = [r for r in (cached or {}).get('releases') or []
                   if not r['prerelease']]
        dlg_status = ft.Text('', size=12, color='#555555', selectable=True)
        local_v = (updater.local_version_info(stable) or {}).get('version')
        rows = []
        for r in stables:
            head = [ft.Text(r['tag'], size=14, weight=ft.FontWeight.BOLD,
                            color=NAVY),
                    ft.Text(r.get('published_at') or '', size=12,
                            color='#9ca3af')]
            if r['tag'] == local_v:
                head.append(ft.Text('いま使っている版', size=11,
                                    color='#15803d'))
            head.append(ft.Container(expand=True))
            head.append(ft.OutlinedButton(
                'ZIP を保存', icon=ft.Icons.DOWNLOAD,
                on_click=_download_history_zip(r, dlg_status)))
            rows.append(ft.Container(
                bgcolor='#f5f7fa', border_radius=6, padding=12,
                content=ft.Column([
                    ft.Row(head, spacing=10),
                    ft.Text(r.get('notes')
                            or '(更新内容の記載はありません)',
                            size=12, color='#555555', selectable=True),
                ], spacing=6)))
        if not rows:
            rows = [ft.Text('過去の更新ログはまだありません。', size=13,
                            color='#555555')]
        page.show_dialog(ft.AlertDialog(
            title=ft.Text('過去の更新ログ'),
            content=ft.Column(rows + [dlg_status], width=560, height=380,
                              scroll=ft.ScrollMode.AUTO, tight=True),
            actions=[ft.TextButton('閉じる',
                                   on_click=lambda _: page.pop_dialog())]))

    tab_launch = ft.Container(padding=24, content=ft.Column([
        t1_update_tag,
        t1_update_notes,
        t1_notice,
        join_notice,
        t1_version,
        ft.Row([
            t1_launch_btn,
            ft.OutlinedButton(
                '過去の更新ログ', icon=ft.Icons.HISTORY,
                on_click=on_show_history,
                style=ft.ButtonStyle(color='#6b7280')),
        ], spacing=12),
        t1_status,
    ], spacing=16, scroll=ft.ScrollMode.AUTO))

    # ---------------- タブ2: 更新版を提出 ----------------
    # (旧・更新タブは廃止: 新しい正式版は自動で取り込み、起動タブの
    #  黄色いタグと「過去の更新ログ」で知らせる)

    file_picker = ft.FilePicker()
    page.services.append(file_picker)

    t4_status = status_text()
    t4_result = ft.Text('', size=14, selectable=True)
    t4_commit_msg = ft.TextField(
        label='変更内容のメモ (空欄なら自動で作成されます)',
        multiline=True, min_lines=2, max_lines=4)
    t4_submit_btn = ft.FilledButton('ZIP を選んで提出', icon=ft.Icons.UPLOAD,
                                    bgcolor=NAVY, color='#ffffff')

    def _submit_progress(msg):
        t4_status.value = msg
        page.update()

    def _do_finalize(prep, deletions, existing_branch=None):
        def work():
            try:
                result = submit.finalize_submission(
                    prep, deletions, t4_commit_msg.value or '',
                    config, on_progress=_submit_progress,
                    existing_branch=existing_branch)
                t4_status.value = ''
                t4_result.value = (
                    '提出しました。検証を通過するとβ版として発行され、'
                    '「β版の確認と承認」タブに表示されます。\n'
                    '提出内容: %s' % result['pr_url'])
                t4_commit_msg.value = ''
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
            page.pop_dialog()
            submit.cleanup(prep)
            t4_submit_btn.disabled = False
            t4_status.value = '提出を取り消しました。'
            page.update()

        def proceed(_):
            page.pop_dialog()
            deletions = [c.label for c in del_checks if c.value]
            existing = (dest_dd.value or None) if dest_dd else None
            _do_finalize(prep, deletions, existing)

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

    def on_autofix(pr):
        def handler(_):
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
                page.update()
            run_bg(work)
        return handler

    _STATUS_LABELS = {
        'success': ('検証OK', '#15803d'),
        'failure': ('検証で問題あり', '#b91c1c'),
        'pending': ('検証中', '#b45309'),
    }

    def on_refresh_prs(_):
        t4_fix_status.value = '取得中...'
        page.update()

        def work():
            try:
                prs = autofix.list_my_submissions(config)
            except (autofix.AutofixError, ghcli.GhError) as e:
                t4_fix_status.value = str(e)
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
                    row.append(ft.FilledButton(
                        '自動修正を試す', icon=ft.Icons.BUILD,
                        on_click=on_autofix(pr),
                        bgcolor=AMBER, color='#ffffff'))
                t4_pr_list.controls.append(ft.Container(
                    bgcolor='#f5f7fa', border_radius=6, padding=12,
                    content=ft.Row(row)))
            t4_fix_status.value = ''
            page.update()
        run_bg(work)

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
        t4_commit_msg,
        t4_submit_btn,
        t4_status,
        t4_result,
        ft.Divider(),
        ft.Row([
            ft.Text('提出済みの検証状況', size=14,
                    weight=ft.FontWeight.BOLD),
            ft.OutlinedButton('確認', icon=ft.Icons.REFRESH,
                              on_click=on_refresh_prs),
        ], spacing=12),
        t4_pr_list,
        t4_fix_status,
    ], spacing=16, scroll=ft.ScrollMode.AUTO))

    # ------------- タブ4: β版の確認と承認 (β版の試用 + 承認を 1 画面に) -------------

    t5_list = ft.Column([], spacing=8)
    t5_status = status_text()

    def _t5_progress(msg):
        t5_status.value = msg
        page.update()

    def try_beta(release):
        def handler(_):
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
                page.update()
            run_bg(work)
        return handler

    def _edit_feedback_dialog(fb):
        """自分のフィードバックの編集ダイアログ."""
        field = ft.TextField(label='フィードバックを編集', value=fb['text'],
                             multiline=True, min_lines=3, max_lines=6)
        err = ft.Text('', size=12, color='#b91c1c')

        def save(_):
            def work():
                try:
                    feedback.update_feedback(fb['comment_id'], fb['tag'],
                                             field.value, config)
                    page.pop_dialog()
                    t5_status.value = 'フィードバックを更新しました。'
                    page.update()
                    on_refresh_reviews(None)
                except (feedback.FeedbackError, ghcli.GhError) as e:
                    err.value = str(e)
                    page.update()
            run_bg(work)

        page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text('フィードバックの編集'),
            content=ft.Column([field, err], tight=True, width=560),
            actions=[ft.TextButton('キャンセル',
                                   on_click=lambda _: page.pop_dialog()),
                     ft.FilledButton('保存', on_click=save,
                                     bgcolor=NAVY, color='#ffffff')]))

    def _delete_feedback_dialog(fb):
        """自分のフィードバックの削除確認ダイアログ."""
        err = ft.Text('', size=12, color='#b91c1c')

        def do_delete(_):
            def work():
                try:
                    feedback.delete_feedback(fb['comment_id'], config)
                    page.pop_dialog()
                    t5_status.value = 'フィードバックを削除しました。'
                    page.update()
                    on_refresh_reviews(None)
                except (feedback.FeedbackError, ghcli.GhError) as e:
                    err.value = str(e)
                    page.update()
            run_bg(work)

        page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text('フィードバックの削除'),
            content=ft.Column([
                ft.Text('このフィードバックを削除しますか?', size=13),
                ft.Text(fb['text'], size=12, color='#555555'),
                err,
            ], tight=True, width=560),
            actions=[ft.TextButton('キャンセル',
                                   on_click=lambda _: page.pop_dialog()),
                     ft.FilledButton('削除する', on_click=do_delete,
                                     bgcolor='#b91c1c', color='#ffffff')]))

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
                            err.value = str(e)
                            page.update()
                    run_bg(work)

                actions.append(ft.FilledButton('送信', on_click=send,
                                               bgcolor=NAVY,
                                               color='#ffffff'))
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
                def work():
                    try:
                        reviews.withdraw(pr['number'], reason.value, config)
                        page.pop_dialog()
                        t5_status.value = ('#%d を取り下げました。'
                                           % pr['number'])
                        page.update()
                        on_refresh_reviews(None)
                    except (reviews.ReviewError, ghcli.GhError) as e:
                        err.value = str(e)
                        page.update()
                run_bg(work)

            page.show_dialog(ft.AlertDialog(
                modal=True, title=ft.Text('#%d を取り下げ' % pr['number']),
                content=ft.Column([
                    ft.Text('この提出を取り下げます。承認待ちから消え、'
                            '対応するβ版も削除されます。', size=13),
                    reason, err,
                ], tight=True, width=560),
                actions=[ft.TextButton('キャンセル',
                                       on_click=lambda _: page.pop_dialog()),
                         ft.FilledButton('取り下げる', on_click=do_withdraw,
                                         bgcolor='#b91c1c',
                                         color='#ffffff')]))
        return handler

    def on_approve(pr):
        def handler(_):
            # 楽観的更新: 押した瞬間に承認済み表示 (バッジ・点火演出) へ
            # 切り替え、送信は裏で行う。失敗したら取得し直して表示が戻る
            data = reviewcache.get()
            me = (data or {}).get('me')
            optimistic = bool(data is not None and me
                              and me != pr['author'])
            if optimistic:
                pr['approved'] = sorted(set(pr['approved']) | {me})
                _render_reviews(data, stale=True, animate=True,
                                status='#%d を承認しました (送信中...)。'
                                       % pr['number'])

            def work():
                released = False
                try:
                    if not optimistic:
                        _t5_progress('#%d の承認を送信しています...'
                                     % pr['number'])
                    # 提出者は一覧取得時に判明済み。渡して再取得を省く
                    reviews.approve(pr['number'], config,
                                    author=pr['author'])
                    t5_status.value = '#%d を承認しました。' % pr['number']
                    page.update()
                    # 必要数に達していたら自動リリース (ロケット発射)
                    released = _try_auto_release(pr['number'])
                except (reviews.ReviewError, ghcli.GhError) as e:
                    t5_status.value = str(e)
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
        """発射・爆発の基点 = そのカードのボタン行右端のロケット位置.

        一覧描画時にカードごとの推定 y を _rocket_pos に入れてある。
        x はロケットが右寄せなのでウィンドウ幅から確定する。
        """
        w = int(page.width or 760)
        return _rocket_pos.get(pr_number, (w - 49, 410))

    def _hide_zone(pr_number):
        """演出中: 常駐ロケットを隠し、カードのボタンも無効化する
        (発射中の二度押し・飛行中の却下を防ぐ。次の一覧更新で戻る)."""
        zone = _rocket_zones.get(pr_number)
        if zone is not None:
            zone.visible = False
        for b in _card_buttons.get(pr_number, []):
            b.disabled = True

    def _play_launch(pr_number, done=None):
        """自動リリース演出 (rocketfx): 点火 → 震え → 加速上昇 →
        弧を描いて「起動」タブへ → ✨ と弾けて消える."""
        sx, sy = _rocket_base(pr_number)
        rocketfx.play_launch(page, sx, sy, done=done)

    def _play_crash(pr_number, done=None):
        """却下確定演出 (rocketfx): ぐらつき → 転倒 → 爆発と同時に
        パーツ分解 → 破片が放物線で散乱."""
        sx, sy = _rocket_base(pr_number)
        rocketfx.play_crash(page, sx, sy, done=done)

    def on_cancel_review(pr):
        """自分の承認・却下の取り消し (2 回目のクリックでニュートラルへ)."""
        def handler(_):
            def work():
                try:
                    _t5_progress('#%d の取り消しを送信しています...'
                                 % pr['number'])
                    reviews.cancel_my_review(pr['number'], config)
                    # 却下確定が解けたときのため、非表示・自動畳みの記録を
                    # 掃除する (再び確定したらまた自動で畳める)
                    localstate.unhide_pr(pr['number'], config)
                    localstate.unmark_auto_folded(pr['number'], config)
                    t5_status.value = ('#%d への承認・却下を取り消しました。'
                                       % pr['number'])
                except (reviews.ReviewError, ghcli.GhError) as e:
                    t5_status.value = str(e)
                page.update()
                on_refresh_reviews(None)
            run_bg(work)
        return handler

    def on_hide_pr(pr):
        """却下確定した提出を一覧の下に畳む (自分の画面のみ)."""
        def handler(_):
            localstate.hide_pr(pr['number'], config)
            t5_status.value = ('#%d を一覧の下に畳みました (自分の画面のみ。'
                               '「一覧に戻す」で戻せます)。' % pr['number'])
            page.update()
            on_refresh_reviews(None)
        return handler

    def on_unhide_pr(pr):
        """畳んだ提出を一覧に戻す."""
        def handler(_):
            localstate.unhide_pr(pr['number'], config)
            t5_status.value = '#%d を一覧に戻しました。' % pr['number']
            page.update()
            on_refresh_reviews(None)
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
                page.pop_dialog()
                # 楽観的更新: 閉じた瞬間に却下表示 (バッジ・煙/爆発演出)
                # へ切り替え、送信は裏で行う。失敗したら表示が戻る
                data = reviewcache.get()
                me = (data or {}).get('me')
                optimistic = bool(data is not None and me)
                n_req = reviews.required_approvals(config)
                if optimistic:
                    at = (datetime.datetime.now(datetime.timezone.utc)
                          .isoformat(timespec='seconds'))
                    pr['rejected'] = (
                        [r for r in pr['rejected'] if r['name'] != me]
                        + [{'name': me, 'comment': comment, 'at': at}])
                    pr['rejected_final'] = len(pr['rejected']) >= n_req
                    _render_reviews(data, stale=True, animate=True,
                                    status='#%d を差し戻しました '
                                           '(送信中...)。' % pr['number'])
                    if pr['rejected_final']:
                        # 押した瞬間に爆発を再生 (畳むのは送信後の更新で)
                        _hide_zone(pr['number'])
                        _play_crash(pr['number'])

                def work():
                    try:
                        if not optimistic:
                            _t5_progress('#%d の却下を送信しています...'
                                         % pr['number'])
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
                    # 自分の却下で必要数に達したら「転倒→爆発」演出。
                    # 非表示エリアへ畳むのは爆発が終わってから
                    # (楽観的更新のときは押した瞬間に再生済み)
                    if (not optimistic
                            and len(pr['rejected']) + 1 >= n_req):
                        _hide_zone(pr['number'])
                        _play_crash(pr['number'],
                                    done=lambda: on_refresh_reviews(None))
                    else:
                        on_refresh_reviews(None)
                run_bg(work)

            page.show_dialog(ft.AlertDialog(
                modal=True, title=ft.Text('#%d を却下' % pr['number']),
                content=ft.Column([reason, err], tight=True, width=480),
                actions=[
                    ft.TextButton('キャンセル',
                                  on_click=lambda _: page.pop_dialog()),
                    ft.FilledButton('却下する', on_click=do_reject,
                                    bgcolor='#b91c1c', color='#ffffff'),
                ]))
        return handler

    def on_show_diff(pr, beta=None):
        """「差分」クリックで差分ビューワ (HTML) を既定ブラウザで開く."""
        def handler(_):
            _t5_progress('差分ビューワを準備しています...')

            def work():
                try:
                    path = diffview.write_diff_html(
                        pr, config,
                        beta_tag=beta['tag'] if beta else None)
                    # new=1: 可能なら新しいウィンドウで開く (ブラウザ依存)
                    webbrowser.open('file:///' + path.replace(os.sep, '/'),
                                    new=1)
                    t5_status.value = ('#%d の差分をブラウザで開きました。'
                                       % pr['number'])
                except Exception as e:
                    log.exception('diff viewer failed')
                    t5_status.value = '差分を開けませんでした: %s' % e
                page.update()
            run_bg(work)
        return handler

    def on_resolve_conflict(pr):
        def handler(_):
            _t5_progress('最新版との衝突を調べています...')

            def work():
                try:
                    analysis = conflicts.analyze(pr['branch'], config,
                                                 on_progress=_t5_progress)
                except (conflicts.ConflictError, GitError,
                        ghcli.GhError) as e:
                    t5_status.value = str(e)
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
                    conflicts.abort(analysis)
                    page.pop_dialog()
                    t5_status.value = '統合を取り消しました。'
                    page.update()

                def execute(_):
                    page.pop_dialog()
                    page.update()

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
                        page.update()
                        on_refresh_reviews(None)
                    run_bg(run_resolve)

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
                        ft.FilledButton('統合を実行', on_click=execute,
                                        bgcolor=NAVY, color='#ffffff'),
                    ]))
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
    # カードごとの操作ボタン (演出中に無効化するための控え)
    _card_buttons = {}
    # 開いたとき自動リリースを試した提出番号 (失敗時の連続再試行を防ぐ)
    _auto_release_tried = set()
    # 発射・爆発の基点 (一覧描画時にカードごとの推定位置を入れる)
    _rocket_pos = {}
    # リリース公開待ちの期限 (この間は定期チェックが更新バッジを消さない)
    _pending_release = {'until': 0.0}

    def _rocket_zone(pr, n_req):
        """ボタン列末尾の常駐ロケット。承認・却下の進み具合を演出で表す.

        却下確定 (一覧に戻した表示) は残骸の静止画のみ (演出はない)。
        却下が期日内に取り消されれば状態が変わり、煙付きで再表示される。
        """
        if pr.get('rejected_final'):
            state = 'wreck'          # 却下確定: 散らばった残骸
        elif pr['rejected']:
            state = 'smoking'        # 却下 1 つ: 煙
        elif len(pr['approved']) >= max(1, n_req - 1):
            state = 'ignited'        # 承認 1 つ: 点火
        else:
            state = 'idle'
        control, anim = rocketfx.zone(state)
        if anim is not None:
            # 開いたときに一度だけ再生 (page.update を渡す)
            _rocket_anims.append(lambda fn=anim: fn(page.update))
        # 演出中に隠せるよう登録しておく (操作ボタンとは spacer で分離)
        wrapper = ft.Container(content=control)
        _rocket_zones[pr['number']] = wrapper
        return wrapper

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
                badges.append(_badge('却下あり', '#fecaca', '#7f1d1d'))
            if pr['conflicting']:
                badges.append(_badge('最新版と衝突', '#fde68a', '#78350f'))
        checks_label, checks_color = {
            'success': ('検証OK', '#15803d'),
            'failure': ('検証で問題あり', '#b91c1c'),
            'pending': ('検証中', '#b45309'),
        }[pr['checks']]

        lines = [
            ft.Text('#%d %s' % (pr['number'], pr['title']),
                    weight=ft.FontWeight.BOLD, size=13),
            ft.Row(badges + [ft.Text(checks_label, size=12,
                                     color=checks_color),
                             ft.Text('提出者: %s' % pr['author'], size=12,
                                     color='#555555')], spacing=8),
        ]
        if pr['approved'] and not final:
            lines.append(ft.Text('承認済み: %s' % '、'.join(pr['approved']),
                                 size=12, color='#15803d'))
        for rej in pr['rejected']:
            lines.append(ft.Text(
                '%s さんが差し戻し: %s' % (rej['name'],
                                           rej['comment'] or '(理由なし)'),
                size=12, color='#b91c1c'))

        buttons = []
        if beta is not None and not final:
            buttons.append(ft.FilledButton(
                'β版 %s を試す' % beta['tag'], icon=ft.Icons.SCIENCE,
                disabled=locked,
                on_click=try_beta(beta),
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
                '非表示', icon=ft.Icons.VISIBILITY_OFF,
                on_click=on_hide_pr(pr)))
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
        # 演出中の無効化用に操作ボタンを控えておく (spacer・ロケットは除く)
        _card_buttons[pr['number']] = list(buttons)
        # ロケットはボタン行の右端に常駐 (かつてのリリースボタン位置。
        # リリースは承認がそろった時点で自動実行されるためボタンは廃止。
        # 右寄せにすることで発射・爆発の基点座標が幅から確定する)
        buttons.append(ft.Container(expand=True))
        buttons.append(_rocket_zone(pr, n_req))
        if locked:
            # カード情報は薄く、案内文だけ明るく表示する
            content = ft.Column([
                ft.Container(opacity=0.45,
                             content=ft.Column(lines, spacing=6)),
                ft.Text('最新版と衝突したため、提出者による統合待ちです。'
                        '統合されると承認・却下はリセットされ、'
                        '改めて確認をお願いします。',
                        size=13, weight=ft.FontWeight.BOLD,
                        color='#b45309'),
                ft.Row(buttons, spacing=8),
            ], spacing=6)
        else:
            lines.append(ft.Row(buttons, spacing=8))
            content = ft.Column(lines, spacing=6)
        return ft.Container(bgcolor='#f5f7fa', border_radius=6, padding=12,
                            opacity=0.72 if final else 1.0,
                            content=content)

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

    def _render_reviews_locked(data, stale, animate, status):
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
        folded = [p for p in pending
                  if p.get('rejected_final') and p['number'] in hidden]
        visible = [p for p in pending if p not in folded]
        set_badge(review_badge, len(visible))
        _rocket_anims.clear()
        _rocket_zones.clear()
        _card_buttons.clear()
        t5_list.controls.clear()
        if not visible:
            t5_list.controls.append(
                ft.Text('確認・承認待ちの提出はありません', size=14))
        # カードごとの発射・爆発基点を推定 (行数からの概算で十分)
        _rocket_pos.clear()
        w = int(page.width or 760)
        # 一覧先頭の y。タブ上部の説明文の行数に依存するため、
        # 説明文の文言を変えたらこの値も見直すこと
        y = 283 + (22 if w < 900 else 0)
        for pr in visible:
            extra = 0
            if pr['approved'] and not pr.get('rejected_final'):
                extra += 1                  # 承認済み: ... の行
            extra += len(pr['rejected'])    # 差し戻しコメントの行
            if pr['conflicting'] and not pr.get('rejected_final'):
                extra += 2                  # 統合待ちの案内文
            h = 104 + 24 * extra            # カード実測からの係数
            # スクロールで画面外になっても、せめて画面内から発射する
            by = min(y + h - 33, int(page.height or 640) - 90)
            _rocket_pos[pr['number']] = (w - 49, by)
            y += h + 10
            t5_list.controls.append(
                _review_row(pr, me, _beta_for(pr['number'], betas)))
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

    def _fetch_review_snapshot(on_progress=None):
        """一覧とリリース一覧を一括取得してスナップショットへ保存する.

        取得は reviews.fetch_snapshot (1 回の一括取得。失敗時は従来経路へ
        自動フォールバック)。on_progress で進行状況を画面に流せる。
        戻り値: 採用されたスナップショット。より新しい取得に追い越されて
        破棄されたときは None。取得失敗は ReviewError / GhError を送出。
        """
        seq = reviewcache.next_seq()
        snap = reviews.fetch_snapshot(config, on_progress=on_progress)
        return reviewcache.put(snap['pending'], snap['releases'],
                               snap['me'], seq=seq, config=config)

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

    tab_beta_review = ft.Container(padding=24, content=ft.Column([
        ft.Text('提出された更新版は、検証を通過するとβ版として発行され'
                'ます。β版を確認したら、問題なければ承認してください。%d 人の'
                '承認がそろうと自動で正式版としてリリースされます 🚀。'
                '自分の提出は自分では承認できません。'
                % reviews.required_approvals(config),
                size=13, color='#555555'),
        ft.Text('β版は安定版とは別フォルダ・別データ・別画面で起動する'
                'ため、通常の作業には影響しません。', size=12,
                color='#555555'),
        ft.OutlinedButton('最新の状態に更新', icon=ft.Icons.REFRESH,
                          on_click=on_refresh_reviews),
        t5_list,
        t5_status,
    ], spacing=16, scroll=ft.ScrollMode.AUTO))

    # ---------------- 組み立て ----------------

    review_label, review_badge = tab_label('β版の確認と承認')

    def on_tab_change(e):
        # β版タブを開いた瞬間: 手元の内容を即表示し、裏で最新へ更新する
        if getattr(e.control, 'selected_index', None) == 2:
            on_refresh_reviews(None)

    page.add(
        header(),
        ft.Tabs(
            length=3, selected_index=0, animation_duration=150, expand=True,
            on_change=on_tab_change,
            content=ft.Column([
                ft.TabBar(tabs=[
                    ft.Tab(label='起動'),
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
        t1_notice.value = ('新しい正式版を準備中です (数分かかります)。'
                           '公開されると自動で更新されます。')
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
            # 期限切れ: 黙って消さず、その後の動きを案内する
            _pending_release['until'] = 0.0
            msg = ('リリースの公開確認ができませんでした。公開されると'
                   '自動で取り込んでお知らせします。')
            t1_notice.value = msg
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
                return
            if data is None:
                return  # より新しい取得が反映済み
            # 承認タブの一覧・バッジを先に最新化する (自動更新の
            # ダウンロードで画面の反映を待たせない)
            _render_reviews(data, animate=False)
            try:
                result = updater.check_update(repo, stable,
                                              releases=data['releases'])
            except Exception:
                log.info('更新チェックをスキップしました (オフライン等)')
                result = None
            if result is not None:
                if result['has_update'] and result['latest'] is not None:
                    _pending_release['until'] = 0.0
                    _auto_update(result['latest'])
                elif time.time() >= _pending_release['until']:
                    # 最新の状態: 取り込み予約や古い案内が残っていれば消す
                    _update_state['pending'] = None
                    if t1_notice.value:
                        t1_notice.value = ''
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
            try:
                settings.save_settings(name_field.value, key_field.value,
                                       config)
            except ValueError as e:
                err_text.value = str(e)
                page.update()
                return
            page.pop_dialog()
            page.update()

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
            actions=[ft.FilledButton('登録してはじめる', on_click=on_save,
                                     bgcolor=NAVY, color='#ffffff')]))

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
