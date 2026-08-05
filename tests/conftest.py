"""テスト共通フィクスチャ。

リポジトリのルート自体が mgtkit パッケージ (`__init__.py` がルートにある) であり、
app.py も「親ディレクトリを sys.path に入れて from mgtkit.x import y する」構造の
ため、チェックアウト先のフォルダ名は `mgtkit` である必要がある (アプリ実行時と
同じ制約)。CI では actions/checkout の path: mgtkit でこれを保証している。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # リポジトリルート
PARENT = ROOT.parent

if ROOT.name != 'mgtkit':
    raise RuntimeError(
        'テストはフォルダ名 mgtkit のチェックアウトで実行してください '
        '(現在: %s)。app.py の import 構造 (from mgtkit.x import y) と '
        'pytest のパッケージ解決の両方がフォルダ名に依存します。' % ROOT.name)

if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # `import app` 用

import pytest


@pytest.fixture()
def client():
    """Flask test_client。モジュールグローバルの状態を毎回初期値へ戻す。

    app import はフィクスチャ内で行い、純粋ロジックのテストが
    Flask/matplotlib の import コストを払わないようにする。
    """
    import app as app_module
    app_module._ALLOWED_FILES.clear()
    app_module._CHECK_CACHE.update({'key': None, 'result': None})
    app_module.app.config['TESTING'] = True
    with app_module.app.test_client() as c:
        yield c
    app_module._ALLOWED_FILES.clear()
    app_module._CHECK_CACHE.update({'key': None, 'result': None})


@pytest.fixture()
def rc_flags_reset():
    """rc_check のモジュール可変フラグを退避し、テスト後に復元する。"""
    from mgtkit import rc_check
    saved_yose = dict(rc_check.RC4_YOSE_FIX)
    saved_nmz = dict(rc_check.RCSR_NMZ_FIX)
    saved_warn = dict(rc_check._WARN_CTX)
    yield rc_check
    rc_check.RC4_YOSE_FIX.clear()
    rc_check.RC4_YOSE_FIX.update(saved_yose)
    rc_check.RCSR_NMZ_FIX.clear()
    rc_check.RCSR_NMZ_FIX.update(saved_nmz)
    rc_check._WARN_CTX.clear()
    rc_check._WARN_CTX.update(saved_warn)


@pytest.fixture()
def secprops_mod():
    """secprops の C_DATA/L_DATA (import 時に data/*.json からロード) を退避/復元。"""
    from mgtkit import secprops
    saved = (secprops.C_DATA, secprops.L_DATA)
    yield secprops
    secprops.C_DATA, secprops.L_DATA = saved
