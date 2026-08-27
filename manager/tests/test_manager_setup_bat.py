# -*- coding: utf-8 -*-
"""配布用バッチ (setup.bat / 起動 bat) の作りを固定するテスト。

これらは Windows でしか動かせず CI で実行できないため、実際に踏んだ失敗を
書き戻さないための検査だけを置く。検査対象は次の 3 つ:

1. 導入判定を `where` (在るか) ではなく実行 (動くか) で行うこと。
   Windows 10/11 は既定で Microsoft Store への誘導スタブ python.exe を
   PATH に持つため、`where python` は未導入を「導入済み」と誤判定する。
2. winget の結果を必ず確認すること。素通りすると後段が「コマンドが無い」で
   落ち、原因と無関係な案内が出る。
3. cmd のパーサの落とし穴を避けること。括弧ブロック内の rem / %errorlevel%
   に加え、**文字コード**もここに含む。UTF-8 + chcp 65001 のバッチは cmd が
   読み取り位置を見失い、コメントや echo の断片をコマンドとして実行する
   (実機で発生)。日本語を含むバッチは Shift_JIS + chcp 932 に固定する。
"""
import os
import re

import pytest

from manager import paths

SETUP = os.path.join(paths.REPO_ROOT, 'manager', 'scripts', 'setup.bat')
SETUP_TEST = os.path.join(paths.REPO_ROOT, 'manager', 'scripts',
                          'setup-test.bat')
MANAGER_LAUNCH = os.path.join(paths.REPO_ROOT, 'manager', 'マネージャー起動.bat')
APP_LAUNCH = os.path.join(paths.REPO_ROOT, '起動.bat')
ALL_BATS = [SETUP, SETUP_TEST, MANAGER_LAUNCH, APP_LAUNCH]
# Python を自分で探すバッチ (setup-test.bat は setup.bat を呼ぶだけ)
PY_DETECT_BATS = [SETUP, MANAGER_LAUNCH, APP_LAUNCH]


def read_text(path):
    # 日本語を含むバッチは Shift_JIS 固定 (ASCII のみのファイルも読める)
    with open(path, encoding='cp932') as f:
        return f.read()


def lines_of(path):
    return read_text(path).replace('\r\n', '\n').split('\n')


def blocks_of(path):
    """括弧ブロックの中身にいる行を (行番号, 行) で返す。

    echo / rem の中の括弧は文字としての括弧なので数に入れない。
    """
    inside, depth = [], 0
    for no, line in enumerate(lines_of(path), 1):
        text = line.strip()
        low = text.lower()
        if depth > 0:
            inside.append((no, text))
        if not low.startswith('echo') and not low.startswith('rem'):
            depth += text.count('(') - text.count(')')
            depth = max(depth, 0)
    return inside


@pytest.mark.parametrize('path', ALL_BATS)
def test_encoding_matches_declared_codepage(path):
    # 日本語を含むバッチを UTF-8 + chcp 65001 で置くと、cmd が読み取り位置を
    # 見失って rem や echo の断片をコマンドとして実行する。日本語を増やす
    # ほど悪化するので、Shift_JIS + chcp 932 に固定して再発を止める
    with open(path, 'rb') as f:
        raw = f.read()
    assert not raw.startswith(b'\xef\xbb\xbf'), 'BOM 付きにしない'
    assert b'\r\n' in raw
    assert b'\n' not in raw.replace(b'\r\n', b'')
    if all(b < 0x80 for b in raw):
        return                      # ASCII のみ = コードページに依存しない
    raw.decode('cp932')             # Shift_JIS として読めること
    with pytest.raises(UnicodeDecodeError):
        raw.decode('utf-8')         # UTF-8 で保存し直されていないこと
    assert raw.split(b'\r\n')[1].startswith(b'chcp 932'), \
        '日本語を含むバッチは 2 行目で chcp 932 を宣言する'


@pytest.mark.parametrize('path', ALL_BATS)
def test_ascii_only_after_switching_to_utf8(path):
    # 途中で chcp 65001 に切り替えると、そこから先は UTF-8 として読まれる。
    # 読み取り位置がずれないよう、以降は ASCII だけにしておく
    with open(path, 'rb') as f:
        raw = f.read()
    # 解説文中の言及ではなく、実際に切り替えている行を探す
    offset = 0
    at = -1
    for raw_line in raw.split(b'\r\n'):
        if raw_line.strip().startswith(b'chcp 65001'):
            at = offset
            break
        offset += len(raw_line) + 2
    if at < 0:
        return
    assert all(b < 0x80 for b in raw[at:]), \
        'chcp 65001 より後ろに非 ASCII を置かない'



@pytest.mark.parametrize('path', ALL_BATS)
def test_no_bat_uses_where_to_find_python(path):
    # where python はストア誘導スタブ (Microsoft Store への誘導) を拾って
    # しまい、未導入を「導入済み」と誤判定する
    assert not re.search(r'where\s+py(thon)?\b', read_text(path), re.I), \
        'Python の判定に where を使わない (ストア誘導スタブを誤検出する)'


@pytest.mark.parametrize('path', PY_DETECT_BATS)
def test_python_is_detected_by_running_it(path):
    # 実行してみる (py -3 --version / python -c) 方式であること
    text = read_text(path)
    assert 'py -3 --version' in text
    assert 'python -c' in text


def test_setup_checks_every_winget_result():
    # winget の戻り値を捨てると、導入に失敗したまま次の段へ進んでしまう
    lines = [ln.strip() for ln in lines_of(SETUP)]
    installs = [i for i, ln in enumerate(lines)
                if ln.lower().startswith('winget install')]
    assert installs, 'winget install が見つからない'
    for i in installs:
        assert lines[i + 1].lower() == 'set "rc=%errorlevel%"', \
            'winget install の直後に戻り値を控えること: %s' % lines[i]
    # 控えた値は文字列比較で見る。winget は失敗時に負の値を返すことがあり、
    # if errorlevel 1 (= 1 以上か) では失敗を取りこぼす
    assert 'if not "%RC%"=="0"' in read_text(SETUP)


def test_setup_goto_targets_are_defined():
    text = read_text(SETUP)
    labels = set(re.findall(r'(?m)^:(\w+)', text))
    targets = set(re.findall(r'(?:goto|call) :(\w+)', text))
    assert targets - labels == set(), '飛び先のラベルが無い'
    assert labels - targets == set(), '使われていないラベルが残っている'


@pytest.mark.parametrize('path', ALL_BATS)
def test_no_rem_or_errorlevel_inside_parenthesised_blocks(path):
    # 括弧ブロックの中の rem は括弧を巻き込んでブロックを壊すことがある。
    # %errorlevel% はブロックに入った時点の値で固定されるため中では使えない
    for no, text in blocks_of(path):
        low = text.lower()
        assert not (low == 'rem' or low.startswith('rem ')), \
            '%s:%d ブロック内に rem を置かない' % (path, no)
        assert '%errorlevel%' not in low, \
            '%s:%d ブロック内で %%errorlevel%% を読まない' % (path, no)


def test_manager_launch_runs_pip_in_utf8_mode():
    # 古い pip (25.0 以前) は BOM の無い requirements.txt を CP932 で読み、
    # UTF-8 の日本語コメントで UnicodeDecodeError になる (v1.5 取り込みの
    # 実機障害と同型)。pip を呼ぶ前に PYTHONUTF8=1 を宣言しておくこと
    lines = [ln.strip().lower() for ln in lines_of(MANAGER_LAUNCH)]
    pip_at = next(i for i, ln in enumerate(lines) if '-m pip install' in ln)
    assert 'set "pythonutf8=1"' in lines[:pip_at], \
        'pip install より前に set "PYTHONUTF8=1" を置く'


@pytest.mark.parametrize('path', [SETUP, MANAGER_LAUNCH])
def test_pull_is_ff_only(path):
    # --ff-only が無いと、履歴が分かれたときにマージのメッセージ入力が
    # 始まってバッチが無言で止まる
    for line in lines_of(path):
        if re.search(r'\bgit\b.*\bpull\b', line):
            assert '--ff-only' in line, '取り込みは --ff-only にする: %s' % line


def test_setup_pull_failure_is_not_ignored():
    lines = [ln.strip() for ln in lines_of(SETUP)]
    pulls = [i for i, ln in enumerate(lines)
             if re.search(r'\bgit\b.*\bpull\b', ln)]
    assert pulls
    for i in pulls:
        assert lines[i + 1].lower().startswith('if errorlevel 1'), \
            '最新化の失敗を黙って素通りさせない'


def test_setup_path_fixup_covers_both_install_scopes():
    # winget は管理者権限の有無で machine / user どちらにも入れるため、
    # PATH の暫定補完は両方の導入先を見る必要がある
    text = read_text(SETUP)
    for machine, user in [
            (r'%ProgramFiles%\Git\cmd', r'%LocalAppData%\Programs\Git\cmd'),
            (r'%ProgramFiles%\GitHub CLI',
             r'%LocalAppData%\Programs\GitHub CLI'),
            (r'%ProgramFiles%\Python311',
             r'%LocalAppData%\Programs\Python\Python311')]:
        assert machine in text, machine
        assert user in text, user
