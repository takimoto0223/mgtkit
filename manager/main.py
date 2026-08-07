# -*- coding: utf-8 -*-
"""mgtkit アプリマネージャー UI (Flet).

起動方法 (リポジトリルートで): python -m manager.main
Git / GitHub の用語はユーザーに見せず、平易な日本語のみ表示する。
"""
import asyncio
import logging
import threading

import flet as ft

from . import (autofix, conflicts, feedback, ghcli, launcher, paths,
               reviews, settings, submit, updater)
from .gitcli import GitError

UPDATE_POLL_SECONDS = 30 * 60  # 新しい安定版の定期チェック間隔

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

    def header():
        return ft.Container(
            bgcolor=NAVY,
            padding=ft.Padding.symmetric(vertical=10, horizontal=18),
            content=ft.Row([
                ft.Text('mgtkit', size=20, weight=ft.FontWeight.BOLD,
                        color='#ffffff'),
                ft.Text('アプリマネージャー', size=12, color='#ffffffcc'),
            ], spacing=10),
        )

    # ---------------- タブ1: 起動 ----------------

    t1_version = ft.Text('', size=16, weight=ft.FontWeight.BOLD)
    t1_status = status_text()
    t1_notice = ft.Text('', size=13, weight=ft.FontWeight.BOLD, color=AMBER)

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

    def on_launch_stable(_):
        t1_status.value = '起動しています...'
        page.update()

        def work():
            def progress(msg):
                t1_status.value = msg
                page.update()
            try:
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
                    updater.install_release(repo, latest, stable,
                                            on_progress=progress)
                    refresh_local_version()
                _, url = launcher.launch_app(
                    stable, paths.stable_port(config), channel='stable')
                t1_status.value = 'ブラウザで開きます: %s' % url
            except (ghcli.GhError, launcher.LaunchError, Exception) as e:
                t1_status.value = str(e) or '起動に失敗しました。'
            page.update()
        run_bg(work)

    tab_launch = ft.Container(padding=24, content=ft.Column([
        t1_notice,
        t1_version,
        ft.FilledButton('起動', icon=ft.Icons.PLAY_ARROW,
                        on_click=on_launch_stable,
                        bgcolor=NAVY, color='#ffffff'),
        t1_status,
    ], spacing=16))

    # ---------------- タブ2: 更新 ----------------

    t2_info = ft.Text('「更新を確認」を押してください', size=14)
    t2_notes = ft.Text('', size=13, selectable=True)
    t2_status = status_text()
    t2_update_btn = ft.FilledButton(
        '更新して起動', icon=ft.Icons.DOWNLOAD, disabled=True,
        bgcolor=NAVY, color='#ffffff')
    _latest = {'release': None}

    def on_check_update(_):
        t2_status.value = '確認中...'
        t2_update_btn.disabled = True
        page.update()

        def work():
            try:
                result = updater.check_update(repo, stable)
            except ghcli.GhError as e:
                t2_status.value = str(e)
                page.update()
                return
            local = result['local']
            latest = result['latest']
            local_v = (local or {}).get('version', '未取得')
            if latest is None:
                t2_info.value = ('配布された安定版はまだありません '
                                 '(手元: %s)' % local_v)
                t2_notes.value = ''
            elif result['has_update']:
                t2_info.value = '更新があります: %s → %s' % (
                    local_v, latest['tag'])
                t2_notes.value = ('【更新内容】\n%s' % (latest['notes']
                                  or '(リリースノートなし)'))
                _latest['release'] = latest
                t2_update_btn.disabled = False
            else:
                t2_info.value = '最新の安定版です (%s)' % local_v
                t2_notes.value = ''
            t2_status.value = ''
            page.update()
        run_bg(work)

    def on_do_update(_):
        rel = _latest['release']
        if rel is None:
            return
        t2_update_btn.disabled = True
        page.update()

        def work():
            def progress(msg):
                t2_status.value = msg
                page.update()
            try:
                updater.install_release(repo, rel, stable,
                                        on_progress=progress)
                refresh_local_version()
                _, url = launcher.launch_app(
                    stable, paths.stable_port(config), channel='stable')
                t2_status.value = '更新して起動しました: %s' % url
            except (ghcli.GhError, launcher.LaunchError, Exception) as e:
                t2_status.value = str(e) or '更新に失敗しました。'
            page.update()
        run_bg(work)

    t2_update_btn.on_click = on_do_update
    tab_update = ft.Container(padding=24, content=ft.Column([
        ft.Row([
            ft.OutlinedButton('更新を確認', icon=ft.Icons.REFRESH,
                              on_click=on_check_update),
            t2_update_btn,
        ], spacing=12),
        t2_info,
        ft.Container(content=t2_notes, bgcolor='#f5f7fa', padding=12,
                     border_radius=6),
        t2_status,
    ], spacing=16, scroll=ft.ScrollMode.AUTO))

    # ---------------- タブ3: 更新版を提出 ----------------

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
                '計算結果 (mgtkit_out) は自動で除外されます。',
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
    t5_beta_extra = ft.Column([], spacing=8)
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
                        updater.install_release(repo, release, beta,
                                                on_progress=progress)
                    _, url = launcher.launch_app(
                        beta, paths.beta_port(config), channel='beta')
                    t5_status.value = ('β版 %s を起動しました (安定版とは'
                                       '別画面・別データ): %s'
                                       % (release['tag'], url))
                except (ghcli.GhError, launcher.LaunchError,
                        Exception) as e:
                    t5_status.value = str(e) or 'β版の起動に失敗しました。'
                page.update()
            run_bg(work)
        return handler

    def on_feedback_dialog(pr, beta):
        """フィードバック一覧 (誰が・いつ・内容) + β版があれば投稿欄."""
        def handler(_):
            items = []
            for fb in pr.get('feedback') or []:
                items.append(ft.Container(
                    bgcolor='#f5f7fa', border_radius=6, padding=10,
                    content=ft.Column([
                        ft.Text('%s さん (%s / %s)'
                                % (fb['name'], fb['tag'], fb['date']),
                                size=12, weight=ft.FontWeight.BOLD,
                                color='#555555'),
                        ft.Text(fb['text'], size=13, selectable=True),
                    ], spacing=4)))
            if not items:
                items.append(ft.Text('フィードバックはまだありません。',
                                     size=13))
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

    def on_approve(pr):
        def handler(_):
            def work():
                try:
                    reviews.approve(pr['number'], config)
                    t5_status.value = '#%d を承認しました。' % pr['number']
                except (reviews.ReviewError, ghcli.GhError) as e:
                    t5_status.value = str(e)
                page.update()
                on_refresh_reviews(None)
            run_bg(work)
        return handler

    def on_reject(pr):
        def handler(_):
            reason = ft.TextField(label='却下の理由 (必須。提出者に伝わります)',
                                  multiline=True, min_lines=2, max_lines=4)
            err = ft.Text('', size=12, color='#b91c1c')

            def do_reject(_):
                def work():
                    try:
                        reviews.request_changes(pr['number'], reason.value,
                                                config)
                        page.pop_dialog()
                        t5_status.value = ('#%d を差し戻しました。'
                                           % pr['number'])
                        page.update()
                        on_refresh_reviews(None)
                    except (reviews.ReviewError, ghcli.GhError) as e:
                        err.value = str(e)
                        page.update()
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

    def on_show_diff(pr):
        def handler(_):
            _t5_progress('差分を取得しています...')

            def work():
                try:
                    d = reviews.classified_diff(pr['number'], config)
                except Exception as e:
                    log.exception('classified_diff failed')
                    t5_status.value = '差分を取得できませんでした: %s' % e
                    page.update()
                    return
                items = []
                if d['user_files']:
                    items.append(ft.Text('提出者の変更:', size=13,
                                         weight=ft.FontWeight.BOLD,
                                         color=NAVY))
                    items += [ft.Text('  ' + f, size=12, color=NAVY)
                              for f in d['user_files']]
                if d['autofix_files']:
                    items.append(ft.Text('自動修正 [auto-fix] による変更:',
                                         size=13,
                                         weight=ft.FontWeight.BOLD,
                                         color=AMBER))
                    items += [ft.Text('  ' + f, size=12, color=AMBER)
                              for f in d['autofix_files']]
                diff = d['diff_text']
                if len(diff) > 20000:
                    diff = diff[:20000] + '\n... (以降は GitHub で確認)'
                items.append(ft.Container(
                    bgcolor='#1e293b', border_radius=6, padding=10,
                    content=ft.Text(diff, size=11, color='#e2e8f0',
                                    font_family='monospace',
                                    selectable=True)))
                t5_status.value = ''
                page.show_dialog(ft.AlertDialog(
                    title=ft.Text('#%d の差分' % pr['number']),
                    content=ft.Column(items, width=640, height=420,
                                      scroll=ft.ScrollMode.AUTO),
                    actions=[ft.TextButton(
                        '閉じる', on_click=lambda _: page.pop_dialog())]))
                page.update()
            run_bg(work)
        return handler

    def on_release(pr):
        def handler(_):
            def work():
                try:
                    result = reviews.release(pr['number'], config,
                                             on_progress=_t5_progress)
                    t5_status.value = result['message']
                except (reviews.ReviewError, ghcli.GhError) as e:
                    t5_status.value = str(e)
                page.update()
                on_refresh_reviews(None)
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
                            t5_status.value = '統合しました: %s' % summary
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

    def _review_row(pr, me, beta=None):
        n_req = reviews.required_approvals(config)
        badges = [ft.Container(
            bgcolor='#fef08a', border_radius=4,
            padding=ft.Padding.symmetric(vertical=2, horizontal=8),
            content=ft.Text('承認 %d/%d' % (len(pr['approved']), n_req),
                            size=12, weight=ft.FontWeight.BOLD,
                            color='#713f12'))]
        if pr['rejected']:
            badges.append(ft.Container(
                bgcolor='#fecaca', border_radius=4,
                padding=ft.Padding.symmetric(vertical=2, horizontal=8),
                content=ft.Text('却下あり', size=12, color='#7f1d1d')))
        if pr['conflicting']:
            badges.append(ft.Container(
                bgcolor='#fde68a', border_radius=4,
                padding=ft.Padding.symmetric(vertical=2, horizontal=8),
                content=ft.Text('最新版と衝突', size=12, color='#78350f')))
        checks_label, checks_color = {
            'success': ('検証OK', '#15803d'),
            'failure': ('検証で問題あり', '#b91c1c'),
            'pending': ('検証中', '#b45309'),
        }[pr['checks']]

        lines = [
            ft.Row([ft.Text('#%d %s' % (pr['number'], pr['title']),
                            weight=ft.FontWeight.BOLD, size=13,
                            expand=True)]),
            ft.Row(badges + [ft.Text(checks_label, size=12,
                                     color=checks_color),
                             ft.Text('提出者: %s' % pr['author'], size=12,
                                     color='#555555')], spacing=8),
        ]
        if pr['approved']:
            lines.append(ft.Text('承認済み: %s' % '、'.join(pr['approved']),
                                 size=12, color='#15803d'))
        for rej in pr['rejected']:
            lines.append(ft.Text(
                '%s さんが差し戻し: %s' % (rej['name'],
                                           rej['comment'] or '(理由なし)'),
                size=12, color='#b91c1c'))

        buttons = []
        if beta is not None:
            buttons.append(ft.FilledButton(
                'β版 %s を試す' % beta['tag'], icon=ft.Icons.SCIENCE,
                on_click=try_beta(beta), bgcolor=AMBER, color='#ffffff'))
        fb = pr.get('feedback') or []
        buttons.append(ft.OutlinedButton(
            'フィードバック %d 件' % len(fb),
            disabled=(beta is None and not fb),
            on_click=on_feedback_dialog(pr, beta)))
        buttons.append(ft.OutlinedButton('差分',
                                         on_click=on_show_diff(pr)))
        if pr['author'] == me:
            if pr['conflicting']:
                buttons.append(ft.FilledButton(
                    '最新版と統合', on_click=on_resolve_conflict(pr),
                    bgcolor=AMBER, color='#ffffff'))
        else:
            already = me in pr['approved']
            buttons.append(ft.FilledButton(
                '承認済み' if already else '承認',
                disabled=already, on_click=on_approve(pr),
                bgcolor='#15803d', color='#ffffff'))
            buttons.append(ft.OutlinedButton('却下',
                                             on_click=on_reject(pr)))
        if reviews.can_release(pr, config, me):
            buttons.append(ft.FilledButton(
                'リリース', icon=ft.Icons.ROCKET_LAUNCH,
                on_click=on_release(pr), bgcolor=NAVY, color='#ffffff'))
        lines.append(ft.Row(buttons, spacing=8))
        return ft.Container(bgcolor='#f5f7fa', border_radius=6, padding=12,
                            content=ft.Column(lines, spacing=6))

    def _beta_for(pr_number, betas):
        """提出番号に対応するβ版 (リリースノートの #N で対応付け)."""
        for r in betas:
            if feedback.pr_number_from_release(r) == pr_number:
                return r
        return None

    def on_refresh_reviews(_):
        t5_status.value = '取得中...'
        page.update()

        def work():
            try:
                pending = reviews.list_pending(config)
                me = reviews.current_user()
                releases = ghcli.fetch_releases(repo)
            except (reviews.ReviewError, ghcli.GhError) as e:
                t5_status.value = str(e)
                page.update()
                return
            betas = ghcli.prereleases(releases)
            set_badge(review_badge, len(pending))
            t5_list.controls.clear()
            if not pending:
                t5_list.controls.append(
                    ft.Text('確認・承認待ちの提出はありません', size=14))
            used = set()
            for pr in pending:
                beta = _beta_for(pr['number'], betas)
                if beta is not None:
                    used.add(beta['tag'])
                t5_list.controls.append(_review_row(pr, me, beta))
            others = [b for b in betas if b['tag'] not in used]
            t5_beta_extra.controls.clear()
            if others:
                t5_beta_extra.controls.append(ft.Text(
                    'その他のβ版 (承認待ちの提出とは対応しません)',
                    size=13, weight=ft.FontWeight.BOLD))
                for r in others:
                    t5_beta_extra.controls.append(ft.Container(
                        bgcolor='#f5f7fa', border_radius=6, padding=12,
                        content=ft.Row([
                            ft.Column([
                                ft.Text('%s (%s)' % (r['tag'],
                                                     r['published_at']),
                                        weight=ft.FontWeight.BOLD),
                                ft.Text(r['name'], size=12,
                                        color='#555555'),
                            ], spacing=2, expand=True),
                            ft.FilledButton(
                                '試す', icon=ft.Icons.SCIENCE,
                                on_click=try_beta(r),
                                bgcolor=AMBER, color='#ffffff'),
                        ])))
            t5_status.value = ''
            page.update()
        run_bg(work)

    tab_beta_review = ft.Container(padding=24, content=ft.Column([
        ft.Text('提出された更新版は、検証を通過するとβ版として発行され'
                'ます。β版を試して問題なければ承認してください。%d 人の'
                '承認がそろうと正式版としてリリースできます。自分の提出は'
                '自分では承認できません。'
                % reviews.required_approvals(config),
                size=13, color='#555555'),
        ft.Text('β版は安定版とは別フォルダ・別データ・別画面で起動する'
                'ため、通常の作業には影響しません。', size=12,
                color='#555555'),
        ft.OutlinedButton('一覧を取得', icon=ft.Icons.REFRESH,
                          on_click=on_refresh_reviews),
        t5_list,
        t5_beta_extra,
        t5_status,
    ], spacing=16, scroll=ft.ScrollMode.AUTO))

    # ---------------- 組み立て ----------------

    update_label, update_badge = tab_label('更新')
    review_label, review_badge = tab_label('β版の確認と承認')

    page.add(
        header(),
        ft.Tabs(
            length=4, selected_index=0, animation_duration=150, expand=True,
            content=ft.Column([
                ft.TabBar(tabs=[
                    ft.Tab(label='起動'),
                    ft.Tab(label=update_label),
                    ft.Tab(label='更新版を提出'),
                    ft.Tab(label=review_label),
                ]),
                ft.TabBarView(expand=True, controls=[
                    tab_launch,
                    tab_update,
                    tab_submit,
                    tab_beta_review,
                ]),
            ], spacing=0, expand=True),
        ),
    )
    refresh_local_version()

    # ------- 通知の更新 (起動時 + 定期ポーリング): 更新バナーとタブバッジ -------

    def check_update_notice(reschedule=True):
        def work():
            try:
                result = updater.check_update(repo, stable)
            except Exception:
                log.info('更新チェックをスキップしました (オフライン等)')
                return
            if result['has_update'] and result['latest'] is not None:
                t1_notice.value = ('新しい安定版 %s があります。「更新」タブ'
                                   'から更新してください。'
                                   % result['latest']['tag'])
                set_badge(update_badge, 1)
            else:
                t1_notice.value = ''
                set_badge(update_badge, 0)
            try:
                set_badge(review_badge, reviews.count_pending(config))
            except Exception:
                log.info('承認待ち件数の取得をスキップしました')
            page.update()
        run_bg(work)
        if reschedule:
            timer = threading.Timer(UPDATE_POLL_SECONDS, check_update_notice)
            timer.daemon = True
            timer.start()

    check_update_notice()

    # ---------------- 初回セットアップ (名前と API キーの登録) ----------------

    def show_first_run_dialog():
        name_field = ft.TextField(label='名前 (例: 山田太郎)', autofocus=True)
        key_field = ft.TextField(
            label='Anthropic API キー (sk-ant- で始まる文字列)',
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
                        'Anthropic API キーの登録が必要です。', size=13),
                ft.Text('キーはこの PC の中にだけ保存され、提出時の説明文の'
                        '自動作成と、検証失敗時の自動修正に本人のキーとして'
                        '使われます。', size=12, color='#555555'),
                name_field,
                key_field,
                err_text,
            ], tight=True, width=480),
            actions=[ft.FilledButton('登録してはじめる', on_click=on_save,
                                     bgcolor=NAVY, color='#ffffff')]))

    if settings.load_settings(config) is None:
        show_first_run_dialog()


if __name__ == '__main__':
    ft.app(main)
