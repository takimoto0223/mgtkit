# -*- coding: utf-8 -*-
"""単独で開くファイル (manager/standalone.py) のテスト.

単独で動く計算ツールの HTML は、β版を起動しても mgtkit の画面からは
開けない (提出 #204 のフィードバック)。β版のカードにどのファイルを
「単独の〇〇を開く」として出すかを固定する。
"""
import os

import pytest

from manager import reviews, standalone

PR204 = ['loadtex/loadtex.py', 'snow/snow_rain_factor.html',
         'wood_joint/build_species.py', 'wood_joint/hikibolt_cross_calc.html',
         'wood_joint/homeconnector_calc.html',
         'wood_joint/unit_fastener_calc.html',
         'wood_joint/wood_joint_calc.html']


class TestFind:
    def test_pr204_lists_html_then_scripts(self):
        files = standalone.find(PR204)
        assert [f['kind'] for f in files] == ['HTML'] * 5 + ['スクリプト'] * 2
        assert files[0] == {'path': 'snow/snow_rain_factor.html',
                            'kind': 'HTML', 'how': 'browser'}
        assert {f['how'] for f in files[5:]} == {'folder'}

    def test_app_files_are_not_standalone(self):
        # 本体直下・共通フォルダ・タブの画面は mgtkit の画面から開ける
        paths = ['app.py', 'README.md', 'templates/_tab_x.html',
                 'static/x.js', 'data/W_AL.json', 'readme/manual.md',
                 'manager/main.py', '.github/workflows/test.yml']
        assert standalone.find(paths) == []

    def test_tab_folder_in_the_submission_is_not_standalone(self):
        # 同じ提出に routes.py がある = 新しいタブのフォルダ
        paths = ['rcslab/__init__.py', 'rcslab/routes.py',
                 'rcslab/calc.py', 'rcslab/help.html']
        assert standalone.find(paths) == []

    def test_existing_tab_folder_is_not_standalone(self, tmp_path):
        # 正式版にすでにあるタブ (提出では routes.py を触っていない)
        (tmp_path / 'loadmap').mkdir()
        (tmp_path / 'loadmap' / 'routes.py').write_text('')
        paths = ['loadmap/calc.py', 'snow/snow_rain_factor.html']
        assert [f['path'] for f in standalone.find(paths, str(tmp_path))] \
            == ['snow/snow_rain_factor.html']

    def test_parts_of_a_tool_are_not_listed(self):
        # 単独のツールの部品 (読み込まれる側) と requirements.txt は
        # 人が開くものではない
        paths = ['tool/app.js', 'tool/style.css', 'tool/table.json',
                 'tool/sample.csv', 'tool/requirements.txt',
                 'tool/index.html', 'tool/README.md']
        assert [(f['path'], f['kind']) for f in standalone.find(paths)] == [
            ('tool/index.html', 'HTML'), ('tool/README.md', 'メモ')]

    def test_changing_only_parts_lists_the_html_beside_them(self, tmp_path):
        # 部品だけ変えても HTML の動きは変わる。同じフォルダの HTML を
        # 手元のアプリから拾う
        (tmp_path / 'wood_joint').mkdir()
        for name in ('calc.html', 'species.json', 'notes.md'):
            (tmp_path / 'wood_joint' / name).write_text('')
        files = standalone.find(['wood_joint/species.json'], str(tmp_path))
        assert [f['path'] for f in files] == ['wood_joint/calc.html']

    def test_windows_separators(self):
        assert standalone.find(['snow\\a.html'])[0]['path'] == 'snow/a.html'

    def test_app_dirs_cover_the_folders_that_are_never_tabs(self):
        # tabreg.NOT_TABS (タブとして読まないフォルダ) とずれないこと
        from mgtkit import tabreg
        assert tabreg.NOT_TABS <= standalone.APP_DIRS


class TestButtons:
    def test_pr204_gets_one_button_per_kind(self):
        # 種類ごとのボタン。#204 は「単独の HTML を開く」が主役になる
        # (種類を混ぜて「ファイル」とぼかさない = UI レビュー)
        labels = [standalone.button_label(k, h, len(g))
                  for k, h, g in standalone.groups(standalone.find(PR204))]
        assert labels == ['単独の HTML を開く (5)',
                          '単独のスクリプトの場所を開く (2)']

    @pytest.mark.parametrize('kind, how, n, label', [
        ('HTML', 'browser', 1, '単独の HTML を開く'),
        ('メモ', 'app', 3, '単独のメモを開く (3)'),
        ('スクリプト', 'folder', 1, '単独のスクリプトの場所を開く'),
    ])
    def test_label(self, kind, how, n, label):
        assert standalone.button_label(kind, how, n) == label

    def test_folders(self):
        assert standalone.folders(standalone.find(PR204)) == [
            'snow/', 'wood_joint/', 'loadtex/']


class TestOpen:
    def test_html_opens_in_the_browser(self, tmp_path, monkeypatch):
        f = tmp_path / 'snow' / 'a.html'
        f.parent.mkdir()
        f.write_text('<html></html>')
        opened = []
        monkeypatch.setattr(standalone.webbrowser, 'open',
                            lambda url, new=0: opened.append(url) or True)
        assert standalone.open_file(str(f), 'browser') == 'opened'
        assert opened == [f.resolve().as_uri()]

    def test_browser_refusal_is_a_failure(self, tmp_path, monkeypatch):
        f = tmp_path / 'a.html'
        f.write_text('')
        monkeypatch.setattr(standalone.webbrowser, 'open',
                            lambda url, new=0: False)
        with pytest.raises(OSError):
            standalone.open_file(str(f), 'browser')

    def test_script_is_shown_not_run(self, tmp_path, monkeypatch):
        # スクリプトは開くと実行されてしまうので、場所を開いて選んで見せる
        f = tmp_path / 'tool.py'
        f.write_text('print(1)')
        shown, ran = [], []
        monkeypatch.setattr(standalone, 'reveal', shown.append)
        monkeypatch.setattr(standalone, '_open_with_app', ran.append)
        assert standalone.open_file(str(f), 'folder') == 'revealed'
        assert shown == [str(f)] and ran == []

    def test_no_app_for_the_type_shows_the_place(self, tmp_path,
                                                 monkeypatch):
        # 関連付けが無い (Windows の WinError 1155) ときは場所を開く
        f = tmp_path / 'memo.md'
        f.write_text('')
        shown = []

        def no_app(path):
            raise OSError(1155, 'no association')
        monkeypatch.setattr(standalone, '_open_with_app', no_app)
        monkeypatch.setattr(standalone, 'reveal', shown.append)
        assert standalone.open_file(str(f), 'app') == 'revealed'
        assert shown == [str(f)]

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            standalone.open_file(str(tmp_path / 'none.html'), 'browser')

    def test_local_path(self):
        assert standalone.local_path(os.path.join('b', 'mgtkit'),
                                     'snow/a.html') == os.path.join(
            'b', 'mgtkit', 'snow', 'a.html')


class TestPendingCarriesFiles:
    def test_pr_list_asks_for_files(self, monkeypatch):
        import json
        calls = []

        def fake(args, timeout=60):
            calls.append(args)
            if args[:2] == ['pr', 'list']:
                return json.dumps([
                    {'number': 204, 'title': 't', 'url': 'u',
                     'author': {'login': 'takimoto0223'},
                     'headRefName': 'feature/x-1', 'reviews': [],
                     'statusCheckRollup': [], 'mergeable': 'MERGEABLE',
                     'comments': [],
                     'files': [{'path': p, 'additions': 1, 'deletions': 0}
                               for p in PR204]}])
            return '[]'

        monkeypatch.setattr(reviews.ghcli, 'run_gh', fake)
        pending = reviews.list_pending({'repo': 'o/r'})
        assert 'files' in calls[0][calls[0].index('--json') + 1].split(',')
        assert pending[0]['files'] == PR204

    def test_graphql_node_carries_files(self):
        pr = reviews._pr_from_graphql({
            'number': 1, 'files': {'nodes': [{'path': 'snow/a.html'}]}})
        assert pr['files'] == [{'path': 'snow/a.html'}]
        assert 'files(first: 100) { nodes { path changeType } }' in \
            reviews._SNAPSHOT_QUERY

    def test_deleted_files_are_dropped(self, monkeypatch):
        # 提出で消したファイルは開けないので、ボタンにも件数にも出さない
        monkeypatch.setattr(reviews, 'collaborators', lambda config=None: [])
        pending = reviews._build_pending([{
            'number': 7, 'title': 't', 'url': 'u',
            'author': {'login': 'a'}, 'headRefName': 'feature/a-1',
            'reviews': [], 'statusCheckRollup': [], 'comments': [],
            'files': [{'path': 'snow/old.html', 'changeType': 'DELETED'},
                      {'path': 'snow/new.html', 'changeType': 'ADDED'}]}],
            {'repo': 'o/r'})
        assert pending[0]['files'] == ['snow/new.html']

    def test_old_cache_without_files_shows_nothing(self):
        # 変更ファイルを持たない古いキャッシュでもボタンを出さないだけ
        assert standalone.find(None) == []
