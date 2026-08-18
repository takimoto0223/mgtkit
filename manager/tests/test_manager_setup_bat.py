# -*- coding: utf-8 -*-
"""配布用バッチ (setup.bat / 起動 bat) の作りを固定するテスト。

これらは Windows でしか動かせず CI で実行できないため、実際に踏んだ失敗を
書き戻さないための検査だけを置く。検査対象は次の 3 つ:

1. 導入判定を `where` (在るか) ではなく実行 (動くか) で行うこと。
   Windows 10/11 は既定で Microsoft Store への誘導スタブ python.exe を
   PATH に持つため、`where python` は未導入を「導入済み」と誤判定する。
2. winget の結果を必ず確認すること。素通りすると後段が「コマンドが無い」で
   落ち、原因と無関係な案内が出る。
3. cmd のパーサの落とし穴 (括弧ブロック内の rem / %errorlevel%) を避けること。
"""
import os
import re

import pytest

from manager import paths

SETUP = os.path.join(paths.REPO_ROOT, 'manager', 'scripts', 'setup.bat')
MANAGER_LAUNCH = os.path.join(paths.REPO_ROOT, 'manager', 'マネージャー起動.bat')
APP_LAUNCH = os.path.join(paths.REPO_ROOT, '起動.bat')
ALL_BATS = [SETUP, MANAGER_LAUNCH, APP_LAUNCH]


def read_text(path):
    with open(path, encoding='utf-8') as f:
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
def test_utf8_crlf_without_bom(path):
    # chcp 65001 を前提に日本語を書いているので UTF-8。BOM があると cmd が
    # 1 行目 (@echo off) を解釈できず、コマンドが画面に出てしまう
    with open(path, 'rb') as f:
        raw = f.read()
    assert not raw.startswith(b'\xef\xbb\xbf'), 'BOM 付きにしない'
    raw.decode('utf-8')
    assert b'\r\n' in raw
    assert b'\n' not in raw.replace(b'\r\n', b'')


@pytest.mark.parametrize('path', ALL_BATS)
def test_python_is_detected_by_running_it(path):
    # where python はストア誘導スタブを拾ってしまうため使わない。
    # 実行してみる (py -3 --version / python -c) 方式であること
    text = read_text(path)
    assert not re.search(r'where\s+py(thon)?\b', text, re.I), \
        'Python の判定に where を使わない (ストア誘導スタブを誤検出する)'
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
