# -*- coding: utf-8 -*-
"""バージョン表記 (vX.Y / vX.Y-beta.N) の解析・比較と version.json の読み書き."""
import json
import os
import re

_VER_RE = re.compile(
    r'^v(?P<major>\d+)\.(?P<minor>\d+)(?:-beta\.(?P<beta>\d+))?$')


def parse_version(tag):
    """'v1.2' / 'v1.3-beta.4' を (major, minor, beta) に分解する.

    beta は正式版なら None。解析できない表記は ValueError。
    """
    m = _VER_RE.match(str(tag).strip())
    if not m:
        raise ValueError('バージョン表記を解釈できません: %r' % (tag,))
    beta = m.group('beta')
    return (int(m.group('major')), int(m.group('minor')),
            int(beta) if beta is not None else None)


def is_valid_version(tag):
    try:
        parse_version(tag)
        return True
    except ValueError:
        return False


def is_prerelease_tag(tag):
    return parse_version(tag)[2] is not None


def compare_versions(a, b):
    """バージョン比較。a<b: -1, a==b: 0, a>b: 1.

    同一 X.Y では 正式版 > β版、β版同士は N で比較する。
    """
    ma, na, ba = parse_version(a)
    mb, nb, bb = parse_version(b)
    ka = (ma, na, 1 if ba is None else 0, ba or 0)
    kb = (mb, nb, 1 if bb is None else 0, bb or 0)
    return (ka > kb) - (ka < kb)


def read_version_json(install_dir):
    """インストール先の version.json を読む。無い/壊れている場合は None."""
    path = os.path.join(install_dir, 'version.json')
    try:
        with open(path, encoding='utf-8') as f:
            info = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(info, dict) or 'version' not in info:
        return None
    return info


def write_version_json(install_dir, version, commit, distributed_at):
    """version.json を書き込む (CI 添付が無い配布物への補完用)."""
    path = os.path.join(install_dir, 'version.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'version': version, 'commit': commit,
                   'distributed_at': distributed_at},
                  f, ensure_ascii=False, indent=2)
    return path
