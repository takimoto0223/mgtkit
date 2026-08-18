# -*- coding: utf-8 -*-
"""PC 内だけで完結するローカル状態 (リポジトリ・GitHub には送らない).

現在の用途: 却下が確定した提出を自分の画面から「非表示」にした記録。
非表示は本人の画面にだけ効き、他のメンバーには影響しない。
"""
import json
import logging
import os

from . import paths

log = logging.getLogger(__name__)


def _path(config=None):
    return os.path.join(paths.install_root(config), 'local_state.json')


def _load(config=None):
    try:
        with open(_path(config), encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data, config=None):
    os.makedirs(paths.install_root(config), exist_ok=True)
    with open(_path(config), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def hidden_prs(config=None):
    """非表示にした提出番号の集合."""
    try:
        return {int(n) for n in _load(config).get('hidden_prs', [])}
    except (TypeError, ValueError):
        return set()


def hide_pr(number, config=None):
    """提出を自分の画面から非表示にする."""
    data = _load(config)
    nums = hidden_prs(config)
    nums.add(int(number))
    data['hidden_prs'] = sorted(nums)
    _save(data, config)


def unhide_pr(number, config=None):
    """非表示を解除して一覧に戻す."""
    nums = hidden_prs(config)
    nums.discard(int(number))
    data = _load(config)
    data['hidden_prs'] = sorted(nums)
    _save(data, config)


def auto_folded(config=None):
    """却下確定時に自動で畳んだ記録 (「一覧に戻す」後の再自動畳みを防ぐ)."""
    try:
        return {int(n) for n in _load(config).get('auto_folded', [])}
    except (TypeError, ValueError):
        return set()


def mark_auto_folded(number, config=None):
    data = _load(config)
    nums = auto_folded(config)
    nums.add(int(number))
    data['auto_folded'] = sorted(nums)
    _save(data, config)


def unmark_auto_folded(number, config=None):
    """自動で畳んだ記録を消す (却下取り消しで確定が解けたとき用)."""
    nums = auto_folded(config)
    nums.discard(int(number))
    data = _load(config)
    data['auto_folded'] = sorted(nums)
    _save(data, config)


def prune_hidden(open_numbers, config=None):
    """クローズ済みの提出の非表示・自動畳み記録を掃除する."""
    open_set = {int(n) for n in open_numbers}
    data = _load(config)
    changed = False
    for key, cur in (('hidden_prs', hidden_prs(config)),
                     ('auto_folded', auto_folded(config))):
        kept = sorted(cur & open_set)
        if set(kept) != cur:
            data[key] = kept
            changed = True
    if changed:
        _save(data, config)
