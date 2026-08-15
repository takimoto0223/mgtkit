# -*- coding: utf-8 -*-
"""本物のマネージャー UI を架空データで起動する (ガイド撮影用).

- gh は同梱の偽コマンド (bin/gh) に差し替え、GitHub には一切つながない
- 提出確認ダイアログ用に prepare_submission だけ canned データを返す
- UI コード (manager/main.py) には手を加えない
"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.join(BASE, 'home')
os.environ['HOME'] = HOME
os.environ['PATH'] = os.path.join(BASE, 'bin') + os.pathsep + os.environ['PATH']

sys.path.insert(0, '/home/user/mgtkit')  # manager パッケージの親

from manager import autofix, launcher, selfupdate, submit  # noqa: E402

# 起動・自己更新はデモでは動かさない
selfupdate.auto_update = lambda *a, **k: {'stashed': False}
launcher.launch_stable = lambda *a, **k: None
launcher.launch_beta = lambda *a, **k: None

# 提出確認ダイアログ用の canned 準備結果
_FAKE_PREP = {
    'zip_path': 'C:/Users/yamada/Desktop/mgtkit.zip',
    'workrepo': '/tmp/none', 'extract_dir': '/tmp/none',
    'base_version': 'v1.1', 'base_commit': 'b2c3d4' + '0' * 34,
    'changes': {'added': ['csv_out.py'],
                'modified': ['app.py', 's_check.py'],
                'deleted': []},
    'skipped': [],
    'safety': {'warnings': [], 'blockers': []},
}
submit.prepare_submission = lambda *a, **k: dict(_FAKE_PREP)
submit.cleanup = lambda prep: None
autofix.list_my_submissions = lambda *a, **k: []

import flet as ft  # noqa: E402

# Web モードでは pick_files がパスを返さないため、選択結果を差し込む
class _FakeFile:
    path = 'C:/Users/yamada/Desktop/mgtkit.zip'
    name = 'mgtkit.zip'


async def _fake_pick_files(self, *a, **k):
    return [_FakeFile()]

ft.FilePicker.pick_files = _fake_pick_files

from manager import main as manager_main  # noqa: E402

ft.app(manager_main.main, view=None, port=8571)
