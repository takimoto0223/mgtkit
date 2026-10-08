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
        # 単独のツールの部品 (読み込まれる側) は人が開くものではない
        paths = ['tool/app.js', 'tool/style.css', 'tool/table.json',
                 'tool/index.html', 'tool/README.md', 'tool/sample.csv']
        assert [(f['path'], f['kind']) for f in standalone.find(paths)] == [
            ('tool/index.html', 'HTML'), ('tool/README.md', 'メモ'),
            ('tool/sample.csv', 'CSV')]

    def test_windows_separators(self):
        assert standalone.find(['snow\\a.html'])[0]['path'] == 'snow/a.html'


class TestButtonLabel:
    @pytest.mark.parametrize('paths, label', [
        (['snow/a.html'], '単独の HTML を開く'),
        (['snow/a.html', 'wood/b.html'], '単独の HTML を開く'),
        (['tool/a.py'], '単独のスクリプトを開く'),
        (PR204, '単独のファイルを開く'),
    ])
    def test_label(self, paths, label):
        assert standalone.button_label(standalone.find(paths)) == label


class TestOpen:
    def test_html_opens_in_the_browser(self, tmp_path, monkeypatch):
        f = tmp_path / 'snow' / 'a.html'
        f.parent.mkdir()
        f.write_text('<html></html>')
        opened = []
        monkeypatch.setattr(standalone.webbrowser, 'open',
                            lambda url, new=0: opened.append(url))
        standalone.open_file(str(f), 'browser')
        assert opened == [f.resolve().as_uri()]

    def test_script_is_shown_not_run(self, tmp_path, monkeypatch):
        # スクリプトは開くと実行されてしまうので、フォルダで選んで見せる
        f = tmp_path / 'tool.py'
        f.write_text('print(1)')
        shown, ran = [], []
        monkeypatch.setattr(standalone, 'reveal', shown.append)
        monkeypatch.setattr(standalone, '_open_with_app', ran.append)
        standalone.open_file(str(f), 'folder')
        assert shown == [str(f)] and ran == []

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
        assert 'files(first: 100) { nodes { path } }' in \
            reviews._SNAPSHOT_QUERY

    def test_old_cache_without_files_shows_nothing(self):
        # 変更ファイルを持たない古いキャッシュでもボタンを出さないだけ
        assert standalone.find(None) == []
