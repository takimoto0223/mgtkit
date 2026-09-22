# -*- coding: utf-8 -*-
"""DXF(VW10J) の入口: 読み込み → 図 (Figure) → DXF / プレビュー.

routes.py (画面) からはここの関数だけを呼ぶ。読み込み結果はファイルの更新時刻と
ダミー材断面番号下限ごとに保持し、同じモデルでプレビューを続けても読み直さない。
"""
import os
import threading

from .members import (auto_frames, build_struct_model, plan_key_is_flat, plan_key_z, plan_keys,
                      plan_levels, wood_plan_sheets, wood_top_z)
from .style import DEFAULT_LIMIT_SEC_NO, GRID_PT_DEFAULT, PT_MM, TEXT_PT_DEFAULT
from .draw.elevation import build_elevation
from .draw.parts_list import LIST_SCALE_DEFAULT, build_list_figures, list_categories
from .draw.plan import build_plan
from .render import dxf as _dxf
from .render import preview as _preview

_CACHE = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 4


def load(path, limit_sec_no=DEFAULT_LIMIT_SEC_NO):
    """mgt / mgtx → StructModel (部材の組み立てまで). 同じファイル・同じ下限なら使い回す."""
    key = (os.path.abspath(path), os.path.getmtime(path), float(limit_sec_no))
    with _CACHE_LOCK:
        M = _CACHE.get(key)
    if M is None:
        M = build_struct_model(path, limit_sec_no=limit_sec_no)
        with _CACHE_LOCK:
            if len(_CACHE) >= _CACHE_MAX:
                _CACHE.pop(next(iter(_CACHE)))
            _CACHE[key] = M
    return M


def info(M):
    """画面の「読込」: 通り・フロア (レベル・最上階・水平か)・部材リストの区分・部材集計."""
    frames = auto_frames(M)
    plans = plan_keys(M)
    top_z = wood_top_z(M)
    for pl in plans:
        z = plan_key_z(M, pl['key'])
        pl['z'] = float(z)
        pl['top'] = bool(top_z is not None and z >= top_z - 1e-9)
        pl['flat'] = plan_key_is_flat(M, pl['key'])
    kinds = {'column': 0, 'beam': 0, 'brace': 0}
    for m in M.members:
        kinds[m['kind']] = kinds.get(m['kind'], 0) + 1
    return {'levels': [float(v) for v in plan_levels(M)], 'plans': plans,
            'frames': [{'key': f['key'], 'label': f['label'], 'n_col': f['n_col']} for f in frames],
            'n_members': len(M.members), 'kinds': kinds,
            'n_pin': sum(1 for m in M.members if m['pin1'] or m['pin2']),
            'list_categories': list_categories(M)}


def axis_figure(M, key, o):
    return build_elevation(M, key, o['scale'], o['paper'], o['text_mm'], grids=o['grids'],
                           levels=o['level_lines'], grid_paper_mm=o['grid_mm'])


def plan_figures(M, key, o, top_z=None, sheet=None):
    """伏図 (木造では最上階以外が梁伏図・柱伏図の 2 枚). sheet を指定するとその 1 枚だけ."""
    sheets = wood_plan_sheets(M, key, top_z) if o['wood'] else [None]
    if sheet:
        sheets = [sheet if sheet in sheets else sheets[0]]
    return [build_plan(M, key, o['scale'], o['paper'], o['text_mm'], grids=o['grids'],
                       wood_sheet=sh, grid_paper_mm=o['grid_mm']) for sh in sheets]


def options(p):
    """画面の設定 (dict) → 作図の設定. 画面の項目名はそのまま.

    縮尺: 伏図・軸組図は画面で選ぶ (既定 1/100)、部材リストは別の欄 (既定 1/60)。
    文字: 紙面ポイントで受け取り、紙面 mm にして持つ (text_mm=符号・リスト、grid_mm=通り名・見出し)。
    """
    return {
        'paper': str(p.get('paper') or 'A3'),
        'scale': int(p.get('scale') or 100),
        'list_scale': int(p.get('list_scale') or LIST_SCALE_DEFAULT),
        'text_mm': float(p.get('text_pt') or TEXT_PT_DEFAULT) * PT_MM,
        'grid_mm': float(p.get('grid_pt') or GRID_PT_DEFAULT) * PT_MM,
        'grids': list(p.get('grids') or []),
        'level_lines': list(p.get('level_lines') or []),
        'wood': bool(p.get('wood')),
        'list_categories': list(p.get('list_categories') or []),
    }


def list_figures(M, o, categories):
    return [f for _cat, f in build_list_figures(M, o['list_scale'], o['paper'], o['text_mm'],
                                                categories=categories,
                                                title_paper_mm=o['grid_mm'])]


def build_figures(M, axes, levels, o):
    """選んだ通り・フロア・部材リストの区分 → {'伏図': [...], '軸組図': [...], 'リスト': [...]}."""
    out = {'伏図': [], '軸組図': [], 'リスト': []}
    top_z = wood_top_z(M) if o['wood'] else None
    for key in levels or []:
        try:
            out['伏図'] += plan_figures(M, key, o, top_z)
        except ValueError as e:
            print('注意: %s' % e)
    for key in axes or []:
        try:
            out['軸組図'].append(axis_figure(M, key, o))
        except ValueError as e:
            print('注意: %s' % e)
    if o['list_categories']:
        out['リスト'] = list_figures(M, o, o['list_categories'])
    return out


def export(path, out_dir, axes, levels, o, limit_sec_no=DEFAULT_LIMIT_SEC_NO):
    """DXF を書き出す (図の種類ごとに 1 ファイル: <元名>_伏図 / _軸組図 / _リスト、struct_cad と同じ)

    → (ファイルの list, [{title, scale}])。
    """
    M = load(path, limit_sec_no)
    figs = build_figures(M, axes, levels, o)
    if not any(figs.values()):
        raise ValueError('描画できる図がありません (通り・フロアの選択や'
                         '部材リストの区分を確認してください)')
    base = os.path.splitext(os.path.basename(path))[0]
    made, info_ = [], []
    for name, part in figs.items():
        if part:
            f, i = _dxf.export(part, out_dir, '%s_%s' % (base, name))
            made.append(f)
            info_ += i
    return made, info_


def preview(path, out_path, kind, key, o, limit_sec_no=DEFAULT_LIMIT_SEC_NO, sheet=None, page=1):
    """用紙プレビュー PNG → {'scale', 'pages', 'page'}. 部材リストは page 枚目."""
    M = load(path, limit_sec_no)
    pages, page_no = None, None
    if kind == 'list':
        figs = list_figures(M, o, [str(key)])
        if not figs:
            raise ValueError('部材リストの %s に載せる断面がありません' % key)
        page_no = min(max(int(page or 1), 1), len(figs))
        pages = len(figs)
        fig = figs[page_no - 1]
    elif kind == 'axis':
        fig = axis_figure(M, key, o)
    else:
        fig = plan_figures(M, key, o, sheet=sheet)[0]
    _p, n = _preview.preview_png(fig, out_path, o['paper'])
    return {'scale': n, 'pages': pages, 'page': page_no}
