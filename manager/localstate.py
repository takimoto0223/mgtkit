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


def prune_hidden(open_numbers, config=None):
    """クローズ済みの提出の非表示記録を掃除する."""
    nums = hidden_prs(config)
    kept = sorted(nums & {int(n) for n in open_numbers})
    if set(kept) != nums:
        data = _load(config)
        data['hidden_prs'] = kept
        _save(data, config)
