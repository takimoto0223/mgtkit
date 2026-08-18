# -*- coding: utf-8 -*-
"""バージョン表記 (vX.Y / vX.Y-beta.N) の解析・比較と version.json の読み書き.

提出の「基点」(提出者がマネージャーで取得した版) の記録もここに置く。
配布 ZIP の version.json に書いてある版がその答えで、提出時に PR 本文へ
書き写しておき、過去の更新ログの図はそれを読む
(git や日時からの推定は取り違えるため。manager/docs/decisions.md)。
"""
import json
import os
import re

_VER_RE = re.compile(
    r'^v(?P<major>\d+)\.(?P<minor>\d+)(?:-beta\.(?P<beta>\d+))?$')

# 提出の基点を PR 本文に残す印。GitHub の画面には出ない HTML コメント。
# 印を入れる前の提出のために、fallback_pr_body の定型文からも拾えるようにする
_BASE_MARKER = re.compile(
    r'<!--\s*mgtkit-base\s+version=(?P<version>\S+)'
    r'(?:\s+commit=(?P<commit>[0-9a-fA-F]*))?\s*-->')
_BASE_LEGACY = re.compile(
    r'マネージャー経由の提出です\s*[（(]\s*基点:\s*(?P<version>[^)）\s]+)')


def base_marker(version, commit=''):
    """提出の基点 (取得した版) を PR 本文へ残す印。本文の先頭行に置く."""
    return '<!-- mgtkit-base version=%s commit=%s -->' % (
        version or '?', commit or '')


def base_from_body(body):
    """PR 本文から提出の基点を読む。戻り値: dict(version, commit) / None.

    1) マネージャーが入れた印 (提出時の記録そのもの)
    2) 印が無い古い提出は、本文の定型文「(基点: vX.Y)」から拾う
    版が分からなかった提出 ('?') は記録が無いものとして None を返す。
    """
    for pat in (_BASE_MARKER, _BASE_LEGACY):
        m = pat.search(body or '')
        if m is None:
            continue
        version = (m.group('version') or '').strip()
        if not version or version == '?':
            continue
        groups = m.groupdict()
        commit = (groups.get('commit') or '').strip() \
            if 'commit' in groups else ''
        return {'version': version, 'commit': commit}
    return None


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
