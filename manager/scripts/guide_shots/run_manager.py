# -*- coding: utf-8 -*-
"""本物のマネージャー UI を架空データで起動する (ガイド撮影用).

- gh は同梱の偽コマンド (bin/gh) に差し替え、GitHub には一切つながない
- 提出確認ダイアログ用に prepare_submission だけ canned データを返す
- UI コード (manager/main.py) には手を加えない
"""
import json
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

# 「Claude で自動作成する」の確認ダイアログ用。git 操作も API 呼び出しも
# せず、確認画面 (on_review) だけ本物の UI に通す
_FAKE_AI_TEXT = (
    '- 二丁山形鋼(2L)の断面算定に対応しました\n'
    '- 柱脚の検定比一覧に軸力の欄を追加しました',
    '- 二丁山形鋼は等辺のみ対応です (不等辺はエラーで停止します)')


def _fake_finalize(prep, deletions, commit_message='', config=None,
                   on_progress=None, existing_branch=None, limitations='',
                   use_ai=False, on_review=None):
    if use_ai and on_review and on_review(*_FAKE_AI_TEXT) is None:
        raise submit.SubmitCancelled('提出を取り消しました。')
    return {'pr_url': 'https://github.com/o/r/pull/42',
            'branch': 'feature/yamada-taro-20260817-1',
            'commit_message': commit_message or '提出'}


submit.finalize_submission = _fake_finalize

import flet as ft  # noqa: E402

# Web モードでは pick_files がパスを返さないため、選択結果を差し込む
class _FakeFile:
    path = 'C:/Users/yamada/Desktop/mgtkit.zip'
    name = 'mgtkit.zip'


async def _fake_pick_files(self, *a, **k):
    return [_FakeFile()]

ft.FilePicker.pick_files = _fake_pick_files

from manager import paths, versions  # noqa: E402


def seed_state():
    """撮影に必要な「登録済み・取り込み済み」の状態を用意する.

    未登録だと初回登録ダイアログが全画面を覆い、取り込み前だと
    「アプリはまだ取り込まれていません」になって撮影できないため。
    値はすべて架空 (API キーはダミー文字列で、通信はしない)。
    """
    root = paths.install_root()
    os.makedirs(root, exist_ok=True)
    sp = os.path.join(root, 'settings.json')
    if not os.path.exists(sp):
        with open(sp, 'w', encoding='utf-8') as f:
            # キー名は settings.load_settings が見る 'name'。API キーは
            # 使わないのでダミー (本物らしい文字列は置かない)
            json.dump({'name': '山田太郎',
                       'anthropic_api_key': 'dummy-not-a-real-key'},
                      f, ensure_ascii=False, indent=2)
    appd = paths.app_dir(paths.stable_dir())
    if not os.path.exists(os.path.join(appd, 'version.json')):
        os.makedirs(appd, exist_ok=True)
        versions.write_version_json(appd, 'v1.1', 'b2c3d4' + '0' * 34,
                                    '2026-08-07')


seed_state()

from manager import main as manager_main  # noqa: E402

ft.app(manager_main.main, view=None, port=8571)
