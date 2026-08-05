"""data/ 配下の基準データとコード内定数の整合性テスト。

pc_check.py は data/pc_info.json と同内容の PC 鋼材テーブルをモジュール定数
として持つ (JSON は現状未参照)。どちらか一方だけを更新する事故を検出する。
"""
import json
from pathlib import Path

import numpy as np

from mgtkit import pc_check

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'


def test_pc_info_json_matches_pc_check_constants():
    info = json.loads((DATA_DIR / 'pc_info.json').read_text(encoding='utf-8'))
    assert [str(n).strip() for n in pc_check.cable_name] == \
        [str(n).strip() for n in info['cable_name']]
    np.testing.assert_allclose(
        np.asarray(pc_check.cable_data, dtype=float),
        np.asarray(info['cable_data'], dtype=float))


def test_reference_json_files_are_valid():
    for name in ('W_AL.json', 'bar_table.json', 'c_data.json',
                 'l_data.json', 'pc_info.json'):
        json.loads((DATA_DIR / name).read_text(encoding='utf-8'))
