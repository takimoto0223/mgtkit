# -*- coding: utf-8 -*-
"""mgtkit アプリマネージャー UI (Flet).

起動方法 (リポジトリルートで): python -m manager.main
Git / GitHub の用語はユーザーに見せず、平易な日本語のみ表示する。
"""
import asyncio
import logging
import threading

import flet as ft

from . import autofix, ghcli, launcher, paths, settings, submit, updater
from .gitcli import GitError

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

NAVY = '#2b4a6f'
AMBER = '#b45309'


def main(page: ft.Page):
    page.title = 'mgtkit アプリマネージャー'
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

    def refresh_local_version():
        info = updater.local_version_info(stable)
        if info is None:
            t1_version.value = '安定版は未取得です'
            t1_status.value = '「更新」タブから最新の安定版を取得してください。'
        else:
            t1_version.value = '安定版 %s' % info.get('version', '?')
            t1_status.value = '配布日: %s' % info.get('distributed_at', '-')
        page.update()

    def on_launch_stable(_):
        t1_status.value = '起動しています...'
        page.update()

        def work():
            try:
                _, url = launcher.launch_app(
                    stable, paths.stable_port(config), channel='stable')
                t1_status.value = 'ブラウザで開きます: %s' % url
            except launcher.LaunchError as e:
                t1_status.value = str(e)
            page.update()
        run_bg(work)

    tab_launch = ft.Container(padding=24, content=ft.Column([
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

    # ---------------- タブ3: β版 ----------------

    t3_list = ft.Column([], spacing=8)
    t3_status = status_text()

    def try_beta(release):
        def handler(_):
            t3_status.value = '%s を準備しています...' % release['tag']
            page.update()

            def work():
                def progress(msg):
                    t3_status.value = '%s: %s' % (release['tag'], msg)
                    page.update()
                beta = paths.beta_dir(release['tag'], config)
                try:
                    if updater.local_version_info(beta) is None:
                        updater.install_release(repo, release, beta,
                                                on_progress=progress)
                    _, url = launcher.launch_app(
                        beta, paths.beta_port(config), channel='beta')
                    t3_status.value = ('β版 %s を起動しました (安定版とは'
                                       '別画面・別データ): %s'
                                       % (release['tag'], url))
                except (ghcli.GhError, launcher.LaunchError,
                        Exception) as e:
                    t3_status.value = str(e) or 'β版の起動に失敗しました。'
                page.update()
            run_bg(work)
        return handler

    def on_refresh_betas(_):
        t3_status.value = '取得中...'
        page.update()

        def work():
            try:
                releases = ghcli.fetch_releases(repo)
            except ghcli.GhError as e:
                t3_status.value = str(e)
                page.update()
                return
            betas = ghcli.prereleases(releases)
            t3_list.controls.clear()
            if not betas:
                t3_list.controls.append(
                    ft.Text('試用できるβ版はいまありません', size=14))
            for r in betas:
                t3_list.controls.append(ft.Container(
                    bgcolor='#f5f7fa', border_radius=6, padding=12,
                    content=ft.Row([
                        ft.Column([
                            ft.Text('%s (%s)' % (r['tag'],
                                                 r['published_at']),
                                    weight=ft.FontWeight.BOLD),
                            ft.Text(r['name'], size=12, color='#555555'),
                        ], spacing=2, expand=True),
                        ft.FilledButton(
                            '試す', icon=ft.Icons.SCIENCE,
                            on_click=try_beta(r),
                            bgcolor=AMBER, color='#ffffff'),
                    ])))
            t3_status.value = ''
            page.update()
        run_bg(work)

    tab_beta = ft.Container(padding=24, content=ft.Column([
        ft.Text('β版は安定版とは別フォルダ・別データ・別ポートで起動する'
                'ため、通常の作業に影響しません。', size=13, color='#555555'),
        ft.OutlinedButton('β版一覧を取得', icon=ft.Icons.REFRESH,
                          on_click=on_refresh_betas),
        t3_list,
        t3_status,
    ], spacing=16, scroll=ft.ScrollMode.AUTO))

    # ---------------- タブ4: 提出 ----------------

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

    def _do_finalize(prep, deletions):
        def work():
            try:
                result = submit.finalize_submission(
                    prep, deletions, t4_commit_msg.value or '',
                    config, on_progress=_submit_progress)
                t4_status.value = ''
                t4_result.value = (
                    '提出しました。検証と承認が済むと配布されます。\n'
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

    def _confirm_and_finalize(prep):
        """削除ファイルの確認・警告表示ダイアログ → 確定."""
        ch = prep['changes']
        warnings = prep['safety']['warnings']
        del_checks = [ft.Checkbox(label=rel, value=False)
                      for rel in ch['deleted']]
        items = [ft.Text('追加 %d 件 / 変更 %d 件のファイルを提出します。'
                         % (len(ch['added']), len(ch['modified'])))]
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
            _do_finalize(prep, deletions)

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
        t4_status.value = ''
        page.update()
        _confirm_and_finalize(prep)

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
                '検証と承認ののち正式版として配布されます。'
                'マネージャーで取得した版 (version.json 入り) を基に'
                '作業してください。', size=13, color='#555555'),
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

    # ---------------- 組み立て ----------------

    page.add(
        header(),
        ft.Tabs(
            length=4, selected_index=0, animation_duration=150, expand=True,
            content=ft.Column([
                ft.TabBar(tabs=[
                    ft.Tab(label='起動'),
                    ft.Tab(label='更新'),
                    ft.Tab(label='β版'),
                    ft.Tab(label='提出'),
                ]),
                ft.TabBarView(expand=True, controls=[
                    tab_launch,
                    tab_update,
                    tab_beta,
                    tab_submit,
                ]),
            ], spacing=0, expand=True),
        ),
    )
    refresh_local_version()

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
