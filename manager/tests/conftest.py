# -*- coding: utf-8 -*-
"""アプリマネージャーのテスト共通設定.

`from manager import ...` の解決のため、リポジトリルートとその親を
sys.path に載せる (ルート側 tests/conftest.py と同じ制約:
チェックアウト先のフォルダ名は mgtkit であること)。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # リポジトリルート
PARENT = ROOT.parent

if ROOT.name != 'mgtkit':
    raise RuntimeError(
        'テストはフォルダ名 mgtkit のチェックアウトで実行してください '
        '(現在: %s)。' % ROOT.name)

if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402  (sys.path 設定後に import する)


@pytest.fixture(autouse=True)
def _clear_reviews_cache():
    """reviews のセッションキャッシュ (ログイン名等) をテスト間で
    持ち越さない (各テストが run_gh を独自にモックするため)."""
    from manager import reviews
    reviews.clear_cache()
    yield
    reviews.clear_cache()
