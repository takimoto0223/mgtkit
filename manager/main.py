# -*- coding: utf-8 -*-
"""mgtkit アプリマネージャー UI (Flet).

起動方法 (リポジトリルートで): python -m manager.main
Git / GitHub の用語はユーザーに見せず、平易な日本語のみ表示する。
"""
import logging
import threading

import flet as ft

from . import ghcli, launcher, paths, updater

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
            bgcolor=NAVY, padding=ft.padding.symmetric(10, 18),
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
                        style=ft.ButtonStyle(bgcolor=NAVY, color='#ffffff')),
        t1_status,
    ], spacing=16))

    # ---------------- タブ2: 更新 ----------------

    t2_info = ft.Text('「更新を確認」を押してください', size=14)
    t2_notes = ft.Text('', size=13, selectable=True)
    t2_status = status_text()
    t2_update_btn = ft.FilledButton(
        '更新して起動', icon=ft.Icons.DOWNLOAD, disabled=True,
        style=ft.ButtonStyle(bgcolor=NAVY, color='#ffffff'))
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
                            style=ft.ButtonStyle(bgcolor=AMBER,
                                                 color='#ffffff')),
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

    # ---------------- 組み立て ----------------

    page.add(
        header(),
        ft.Tabs(selected_index=0, animation_duration=150, expand=True,
                tabs=[
                    ft.Tab(text='起動', content=tab_launch),
                    ft.Tab(text='更新', content=tab_update),
                    ft.Tab(text='β版', content=tab_beta),
                ]),
    )
    refresh_local_version()


if __name__ == '__main__':
    ft.app(main)
