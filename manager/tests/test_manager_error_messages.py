# -*- coding: utf-8 -*-
"""原因を名指しする案内が「何でも受ける except」から出ていないかの静的検査.

原因を決め打ちした文言 (「使用中」「ネットワーク」「再起動」
「setup.bat」) は、**原因が分かった分岐**でだけ書いてよい。
`except Exception:` のような何でも受ける受け口から出すと、まったく別の
原因のときにも同じ案内が出て、利用者に直らない操作をさせることになる
(引っ越し後のβ版が「フォルダが使用中です。再起動を」で止まった件、
自動更新の失敗が一律「ネットワーク接続を確認してください」だった件)。

この検査は「二度と同じ形を作らない」ための歯止め。新しく引っかかったら、
まず具体的な例外を先に受けて str(e) を出す形に直すこと。どうしても
必要なら _ALLOWED に理由つきで足す。
"""
import ast
import os

MANAGER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 原因を決め打ちしている言い回し
DECISIVE = ('使用中', 'ネットワーク', '再起動', 'setup.bat',
            'ログイン', '空き容量')

# 何でも受ける受け口
CATCH_ALL = ('Exception', 'BaseException')

# 例外つき (理由を書くこと)
_ALLOWED = {
    # git のタイムアウトは原因が「時間切れ」と確定しており、
    # 通信以外の原因が入り込まない
    ('gitcli.py', 'TimeoutExpired'),
}


def _module_files():
    for name in sorted(os.listdir(MANAGER_DIR)):
        if name.endswith('.py') and name != '__init__.py':
            yield name, os.path.join(MANAGER_DIR, name)


def _handled_names(handler):
    """except が受ける例外名の一覧 (ghcli.GhError なら 'GhError')."""
    node = handler.type
    if node is None:
        return ['(bare)']
    parts = node.elts if isinstance(node, ast.Tuple) else [node]
    names = []
    for part in parts:
        if isinstance(part, ast.Attribute):
            names.append(part.attr)
        elif isinstance(part, ast.Name):
            names.append(part.id)
    return names


def _strings_in(node):
    return [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _offenders(files=None):
    found = []
    for name, path in (files if files is not None else _module_files()):
        with open(path, encoding='utf-8') as f:
            tree = ast.parse(f.read(), path)
        for handler in (n for n in ast.walk(tree)
                        if isinstance(n, ast.ExceptHandler)):
            names = _handled_names(handler)
            if not any(n in CATCH_ALL or n == '(bare)' for n in names):
                continue
            for text in _strings_in(handler):
                for word in DECISIVE:
                    if word in text and not any(
                            a[0] == name and a[1] in names
                            for a in _ALLOWED):
                        found.append((name, handler.lineno, word,
                                      text[:40]))
    return found


def test_decisive_wording_is_not_emitted_from_catch_all_handlers():
    offenders = _offenders()
    assert offenders == [], (
        '原因を決め打ちした案内を「何でも受ける except」から出しています。'
        '具体的な例外を先に受けて str(e) を出す形に直してください: %s'
        % offenders)


def test_the_check_can_actually_catch_a_bad_pattern(tmp_path):
    """検査自体が効いていること (見逃す検査を残さない)."""
    bad = tmp_path / 'sample.py'
    bad.write_text(
        'try:\n    pass\nexcept Exception:\n'
        '    show("アプリのフォルダが使用中のため…")\n', encoding='utf-8')
    assert _offenders([('sample.py', str(bad))]) != []


def test_the_check_passes_a_correctly_written_handler(tmp_path):
    """正しい形 (具体例外で受けて str(e)) は引っかからないこと."""
    ok = tmp_path / 'sample.py'
    ok.write_text(
        'try:\n    pass\nexcept InstallError as e:\n'
        '    show("アプリのフォルダが使用中のため…")\n'
        'except Exception:\n    show("できませんでした (詳しくはログ)")\n',
        encoding='utf-8')
    assert _offenders([('sample.py', str(ok))]) == []
