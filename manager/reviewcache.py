# -*- coding: utf-8 -*-
"""承認タブ表示データのスナップショット (前回結果の即時表示 + 裏で更新用).

一次はプロセスメモリ、二次は install_root のスナップショットファイル。
内容は PR 一覧・レビュー状態・リリース一覧などチームで共有済みの情報のみで、
トークンや秘密情報は含まない (「トークンを保存しない」方針はそのまま)。

原則 (manager/docs/decisions.md):
- 表示は古いデータでよい (即時表示し、裏で取得した最新に差し替える)
- 承認・リリースの判定は GitHub を真実とし、操作時点の取得結果で行う
  (reviews.py 側でサーバの最新状態により再検証される)
"""
import datetime
import json
import logging
import os
import threading

from . import paths

log = logging.getLogger(__name__)

_FILE = 'reviews_cache.json'
_lock = threading.Lock()
# data: dict(pending, releases, me, fetched_at) / seq: 取得の通し番号 /
# applied_seq: 最後に採用した取得の番号 (応答の追い越し検出用)
_mem = {'data': None, 'seq': 0, 'applied_seq': 0}


def _path(config=None):
    return os.path.join(paths.install_root(config), _FILE)


def next_seq():
    """取得開始時に採番する通し番号。古い応答による上書きを防ぐ."""
    with _lock:
        _mem['seq'] += 1
        return _mem['seq']


def get():
    """メモリ上の最新スナップショット (無ければ None)."""
    with _lock:
        return _mem['data']


def put(pending, releases, me, seq=None, config=None):
    """スナップショットを保存する.

    seq: next_seq() で採番した番号。より新しい取得が既に採用済みなら
    保存せず None を返す (追い越された応答)。採用したら dict を返す。
    ディスクへの保存失敗は表示に影響しないため警告ログのみ。
    """
    data = {
        'pending': pending,
        'releases': releases,
        'me': me,
        'fetched_at': datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec='seconds'),
    }
    with _lock:
        if seq is not None:
            if seq < _mem['applied_seq']:
                return None
            _mem['applied_seq'] = seq
        _mem['data'] = data
    try:
        os.makedirs(paths.install_root(config), exist_ok=True)
        with open(_path(config), 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
    except OSError:
        log.warning('一覧スナップショットの保存に失敗 (表示には影響なし)',
                    exc_info=True)
    return data


def load_from_disk(config=None):
    """起動直後の初回表示用にディスクのスナップショットを読み込む.

    既にメモリに新しいデータがあればそちらを優先する。壊れたファイルは
    無視する。戻り値: 使えるスナップショット (無ければ None)。
    """
    with _lock:
        if _mem['data'] is not None:
            return _mem['data']
    try:
        with open(_path(config), encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get('pending'),
                                                    list):
        return None
    with _lock:
        if _mem['data'] is None:
            _mem['data'] = data
        return _mem['data']


def age_minutes(data, now=None):
    """スナップショット取得からの経過分数 (判定できなければ None)."""
    try:
        t = datetime.datetime.fromisoformat(data.get('fetched_at') or '')
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return max(0, int((now - t).total_seconds() // 60))


def clear():
    """メモリ状態を破棄する (テスト・仕切り直し用。ディスクは消さない)."""
    with _lock:
        _mem['data'] = None
        _mem['seq'] = 0
        _mem['applied_seq'] = 0
