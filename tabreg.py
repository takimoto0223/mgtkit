# -*- coding: utf-8 -*-
"""タブの自動登録 (app.py から使う).

新しいタブは app.py を編集せずに足せる: 本体直下のサブパッケージ <x>/ に
__init__.py と routes.py を置き、routes.py に make_blueprint(host) を書くと、
起動時に自動で登録される。host は app.py のモジュールで、共通ヘルパは
host._out_dir(...) のように借りる (routes.py から mgtkit.app を import し返すと
`python app.py` 起動時に app が 2 つ読み込まれるため。loadmap/README.md 参照)。
"""
import importlib
import logging
import os

from flask import Flask

log = logging.getLogger('mgtkit.tabs')

# routes.py を置いてもタブとしては読まないフォルダ (マネージャーは別アプリ)
NOT_TABS = frozenset({'manager', 'tests', 'docs', 'data', 'static',
                      'templates', 'readme'})


class TabFlask(Flask):
    """同じタブの Blueprint の二重登録を飛ばす Flask.

    自動登録のあとに、古い書き方 (app.py に import と register_blueprint を
    1 行ずつ足す) の提出が来ても、起動が ValueError で落ちないようにする。
    名前が同じでも中身が別のパッケージなら本当の衝突なので、Flask 本来どおり
    ValueError にする (自動登録中ならそのタブの読み込み失敗として記録される)。
    """

    def register_blueprint(self, blueprint, **options):
        name = options.get('name', blueprint.name)
        prev = self.blueprints.get(name)
        if prev is not None and prev.import_name == blueprint.import_name:
            log.warning('タブ %s は登録済みのため、二度目の登録を飛ばしました '
                        '(app.py の register_blueprint の行は不要です)', name)
            return
        super().register_blueprint(blueprint, **options)


def discover_blueprints(app, host, base_dir, package='mgtkit'):
    """base_dir 直下のタブ (サブパッケージ) を探して app に登録する.

    名前順に読み込む。1 つのタブの読み込みに失敗しても他のタブの登録は続け、
    失敗はトレースバック付きでログに出し、戻り値の errors にも残す
    (黙って握りつぶさない。画面の警告表示とテストがこれを見る)。

    戻り値: (登録したパッケージ名のリスト, [{'name': ..., 'error': ...}, ...])
    """
    loaded, errors = [], []
    for name in sorted(os.listdir(base_dir)):
        d = os.path.join(base_dir, name)
        if (name in NOT_TABS or name.startswith(('.', '_'))
                or not os.path.isfile(os.path.join(d, '__init__.py'))
                or not os.path.isfile(os.path.join(d, 'routes.py'))):
            continue
        try:
            mod = importlib.import_module('%s.%s.routes' % (package, name))
            make = getattr(mod, 'make_blueprint', None)
            if not callable(make):
                continue
            app.register_blueprint(make(host))
        except Exception as e:  # noqa: BLE001  1 タブの失敗で全体を止めない
            log.exception('タブ %s を読み込めませんでした', name)
            errors.append({'name': name,
                           'error': '%s: %s' % (type(e).__name__, e)})
            continue
        loaded.append(name)
    return loaded, errors
