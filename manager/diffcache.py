# -*- coding: utf-8 -*-
"""組み立て済みの差分モデルをディスクに保存する (再起動をまたぐ先読み).

アプリを開き直すたびに差分を組み直すと、初回クリックだけ待たされる。
提出ごとに「番号-先端-分岐点」で保存しておけば、起動直後のクリックも
待ち時間ゼロで出せる (管理者指示 2026-08「限りなく早く」)。

内容が変わればキーが変わるので、古い内容を出し続けることはない。
壊れたファイル・容量オーバーは黙って捨てる (組み直せば済むため)。
"""
import json
import logging
import os

from . import paths

log = logging.getLogger(__name__)

_DIR = 'diffcache'
# 保存形式の版。モデルの形を変えたら上げる (古い版は読まずに捨てる)
_FORMAT = 'v1'
_MAX_FILES = 8              # 残す提出の数 (古いものから消す)
_MAX_BYTES = 8 * 1024 * 1024   # 1 件の上限 (大きすぎる差分は保存しない)


def _dir(config=None):
    return os.path.join(paths.install_root(config), _DIR)


def _path(key, config=None):
    return os.path.join(_dir(config), '%s-%s.json' % (_FORMAT, key))


def load(key, config=None):
    """保存済みモデルを返す (無い・壊れているときは None)."""
    path = _path(key, config)
    try:
        with open(path, encoding='utf-8') as f:
            model = json.load(f)
        if not isinstance(model, dict):
            return None
        # JSON にすると行の組が配列になるため、元のタプルに戻す
        for entry in model.get('files') or []:
            if entry.get('rows') is not None:
                entry['rows'] = [tuple(r) for r in entry['rows']]
    except (OSError, ValueError, AttributeError, TypeError):
        log.debug('差分キャッシュを読めないので組み直します', exc_info=True)
        return None
    try:            # 押された提出を新しい扱いにする (古い順に消すため)
        os.utime(path, None)
    except OSError:
        pass
    return model


def save(key, model, config=None):
    """モデルを保存する (失敗しても表示には影響しないので握りつぶす)."""
    try:
        data = json.dumps(model, ensure_ascii=False).encode('utf-8')
        if len(data) > _MAX_BYTES:
            return
        os.makedirs(_dir(config), exist_ok=True)
        # 2 窓開いていても交錯しないようプロセスごとの一時名にする
        tmp = '%s.%d.tmp' % (_path(key, config), os.getpid())
        with open(tmp, 'wb') as f:
            f.write(data)
        os.replace(tmp, _path(key, config))
        _prune(config)
    except (OSError, TypeError, ValueError):
        log.debug('差分キャッシュの保存に失敗 (組み直しで代替)',
                  exc_info=True)


def _prune(config=None):
    """古いものから _MAX_FILES 件まで減らす."""
    d = _dir(config)
    try:
        names = [n for n in os.listdir(d) if n.endswith('.json')]
    except OSError:
        return
    if len(names) <= _MAX_FILES:
        return
    stamped = []
    for n in names:
        try:
            stamped.append((os.path.getmtime(os.path.join(d, n)), n))
        except OSError:
            continue
    for _, n in sorted(stamped)[:len(stamped) - _MAX_FILES]:
        try:
            os.remove(os.path.join(d, n))
        except OSError:
            pass


def clear(config=None):
    """保存済みを全部消す (テスト・障害時の手当て用)."""
    d = _dir(config)
    try:
        names = os.listdir(d)
    except OSError:
        return
    for n in names:
        try:
            os.remove(os.path.join(d, n))
        except OSError:
            pass
