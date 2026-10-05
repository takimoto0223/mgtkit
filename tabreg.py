# -*- coding: utf-8 -*-
"""タブの自動登録と見出し (app.py から使う).

新しいタブは app.py / templates/index.html を編集せずに足せる:

1. API: 本体直下のサブパッケージ <x>/ に __init__.py と routes.py を置き、
   routes.py に make_blueprint(host) を書くと、起動時に自動で登録される。
   host は app.py のモジュールで、共通ヘルパは host._out_dir(...) のように
   借りる (routes.py から mgtkit.app を import し返すと `python app.py`
   起動時に app が 2 つ読み込まれるため。loadmap/README.md 参照)。
2. 画面: templates/_tab_<x>.html の先頭行に見出しを書くと、nav のボタン・
   本体の include・出力フォルダの案内文に載る::

       {# tab: id="x" label="表示名" order="150" out="x" #}

   id    nav の data-tab と本体の section id (tab-<id>)
   label nav のボタンの表示名
   order 並び順 (小さいほど左。組み込みタブの値は app.py の BUILTIN_TABS)
   out   mgtkit_out/ の下に作る出力フォルダ名 (案内文に載せる。無ければ省略)
"""
import importlib
import logging
import os
import re

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


# ---- 見出し (templates/_tab_<x>.html の先頭行) -------------------------------

_TAB_FILE = re.compile(r'_tab_(\w+)\.html')
_TAG = re.compile(r'\{#\s*tab:(.*?)#\}')
_ATTR = re.compile(r'(\w+)="([^"]*)"')
_TAG_KEYS = ('id', 'label', 'order', 'out')


def read_tab_tag(path):
    """タブのテンプレートの先頭行の見出しを dict で返す (見出しが無ければ None).

    見出しはあるが書き方が誤っているときは ValueError (理由つき)。
    """
    with open(path, encoding='utf-8-sig') as f:
        first = f.readline().strip()
    m = _TAG.fullmatch(first)
    if not m:
        if first.startswith('{#') and 'tab:' in first:
            raise ValueError('見出しの書き方が違います: %s' % first)
        return None
    body = m.group(1)
    attrs = dict(_ATTR.findall(body))
    rest = _ATTR.sub('', body).strip()
    if rest:
        raise ValueError('見出しに読めない部分があります: %r' % rest)
    unknown = sorted(set(attrs) - set(_TAG_KEYS))
    if unknown:
        raise ValueError('見出しに知らない項目があります: %s' % ', '.join(unknown))
    missing = [k for k in ('id', 'label', 'order') if not attrs.get(k)]
    if missing:
        raise ValueError('見出しに %s がありません' % ' / '.join(missing))
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', attrs['id']):
        raise ValueError('見出しの id は英字で始まる英数字・_・- で書いてください: %r'
                         % attrs['id'])
    try:
        order = float(attrs['order'])
    except ValueError:
        raise ValueError('見出しの order は数値で書いてください: %r'
                         % attrs['order']) from None
    return {'id': attrs['id'], 'label': attrs['label'], 'order': order,
            'out': attrs.get('out', '')}


def collect_tabs(templates_dir, builtin=(), skip=(), handwritten=None):
    """組み込みタブと見出しのタブを並び順 (order) に並べた一覧を返す.

    builtin: index.html に本体が直書きのタブ [{'id', 'label', 'order', ...}]
    skip:    読み込みに失敗したタブのパッケージ名 (その見出しは出さない。
             API の無いタブを見せない)
    handwritten: index.html に手書きされたボタン・include
             ({'ids': ..., 'templates': ...}。find_handwritten の戻り値)。
             古い書き方の提出が残っても 2 重にならないよう、手書きのある id は
             nav=False、手書きで include 済みのテンプレートは include=False にする
    同じ id が 2 度出てきたら先のもの (組み込み → ファイル名順) だけを残し、
    後のものはエラーにする (nav に同じタブが 2 つ並ばないように)。
    order が同じなら組み込み → ファイル名順。

    戻り値: (tabs, errors)。tabs の各要素は id / label / order / out /
    template (組み込みタブは None) / nav / include を持つ。
    """
    tabs, errors, seen = [], [], {}
    for t in builtin:
        e = dict(t)
        e.setdefault('out', '')
        e['template'] = None
        seen[e['id']] = '組み込みタブ'
        tabs.append(e)
    for fn in sorted(os.listdir(templates_dir)):
        m = _TAB_FILE.fullmatch(fn)
        if not m or m.group(1) in skip:
            continue
        try:
            tag = read_tab_tag(os.path.join(templates_dir, fn))
        except (OSError, ValueError) as e:
            log.error('%s: %s', fn, e)
            errors.append({'name': fn, 'error': str(e)})
            continue
        if tag is None:
            log.warning('%s の先頭行に見出し ({# tab: ... #}) が無いため、'
                        'タブとして表示しません', fn)
            continue
        if tag['id'] in seen:
            msg = ('タブ id "%s" が %s と %s で重複しているため、%s は表示しません'
                   % (tag['id'], seen[tag['id']], fn, fn))
            log.error(msg)
            errors.append({'name': fn, 'error': msg})
            continue
        seen[tag['id']] = fn
        tag['template'] = fn
        tabs.append(tag)
    hw = handwritten or {}
    for t in tabs:
        t['nav'] = t['id'] not in hw.get('ids', ())
        t['include'] = bool(t['template']) and t['template'] not in hw.get('templates', ())
    tabs.sort(key=lambda t: t['order'])
    return tabs, errors


_HAND_BUTTON = re.compile(r'<button data-tab="([^"{]+)"')
_HAND_INCLUDE = re.compile(r"\{%-?\s*include\s+['\"](_tab_\w+\.html)['\"]")


def find_handwritten(index_path):
    """index.html に手書きされたタブのボタン (data-tab) と include を探す.

    いまの index.html には無い (見出しから作る)。古い書き方の提出がマージされて
    手書きの行が残ったときに、見出しの分と 2 重にならないようにするための保険。
    見つかったらログに警告を出す (tests/test_tab_nav.py でも検出する)。
    """
    with open(index_path, encoding='utf-8') as f:
        src = f.read()
    hw = {'ids': set(_HAND_BUTTON.findall(src)),
          'templates': set(_HAND_INCLUDE.findall(src))}
    if hw['ids'] or hw['templates']:
        log.warning('index.html に手書きのタブ (%s) があります。見出しのタブと 2 重に'
                    'ならないよう自動の分を省きます。手書きの行は消してください',
                    ', '.join(sorted(hw['ids'] | hw['templates'])))
    return hw
