# -*- coding: utf-8 -*-
"""鉄骨造露出柱脚の検定の Flask Blueprint (rcdetail と同じ作法).

    from mgtkit.colbase.routes import make_blueprint
    app.register_blueprint(make_blueprint(sys.modules[__name__]))
"""

import csv
import io
import os

from flask import Blueprint, jsonify, request

from mgtkit.colbase.calc import (read_targets, run_check, SPEC_FIELDS,
                                 AB_TYPES, AB_STRESS_AREA)
from mgtkit.colbase.tex import export_tex, compile_preview
from mgtkit.colbase.figure import draw_groups, group_reps

#: 出力先のサブフォルダ (mgtkit_out/colbase/)
OUT_SUB = 'colbase'

#: 仕様の保存ファイル名 (出力先フォルダに置く。対象読込時に自動で読む)
SPEC_CSV = 'colbase_spec.csv'


def spec_to_csv(specs):
    """仕様 list → CSV 文字列 (1行目キー、2行目見出し、以降グループ)."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\n')
    w.writerow([k for k, _l, _t in SPEC_FIELDS])
    w.writerow(['#' + l for _k, l, _t in SPEC_FIELDS])
    for sp in specs:
        w.writerow(['' if sp.get(k) is None else sp.get(k)
                    for k, _l, _t in SPEC_FIELDS])
    return buf.getvalue()


def csv_to_spec(text):
    """CSV 文字列 → 仕様 list ('#' で始まる行は見出しとして読み飛ばす)."""
    rows = [r for r in csv.reader(io.StringIO(text))
            if r and any(c.strip() for c in r)]
    if not rows:
        return []
    keys = [c.strip().lstrip('﻿') for c in rows[0]]
    if 'group' not in keys:
        raise ValueError('柱脚仕様CSVの1行目に項目キー (group, bp_D, ...) '
                         'がありません。')
    out = []
    for r in rows[1:]:
        if r[0].strip().startswith('#'):
            continue
        out.append({k: (r[i].strip() if i < len(r) else '')
                    for i, k in enumerate(keys)})
    return out


def _read_text(path):
    with open(path, 'rb') as f:
        raw = f.read()
    for enc in ('utf-8-sig', 'cp932'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def _plain(o):
    """numpy の数値・真偽値を JSON 化できる Python 型にそろえる."""
    import numpy as np
    if isinstance(o, dict):
        return {k: _plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_plain(v) for v in o]
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    return o


def make_blueprint(host):
    """host = app.py のモジュール。共通ヘルパをそこから借りる。"""
    bp = Blueprint('colbase', __name__)

    @bp.route('/api/colbase_read', methods=['POST'])
    def api_colbase_read():
        """対象読込: 柱脚を含むグループ・ケース・保存済み仕様を返す."""
        p = request.get_json(force=True)
        err = host._check_input_file(p.get('mgt_path'), 'mgtファイル')
        if err:
            return jsonify({'error': err}), 400
        try:
            notes = []
            with host._capture_notes(notes):
                out = read_targets(p['mgt_path'], notes=notes)
                bs_path = (p.get('beam_stress_path') or '').strip()
                if bs_path:
                    err = host._check_input_file(bs_path,
                                                 'beam_stressファイル')
                    if err:
                        return jsonify({'error': err}), 400
                    import numpy as np
                    from mgtkit.util import load_beam_stress_table
                    from mgtkit.ratio_pipeline import default_case_types
                    bs = np.atleast_2d(load_beam_stress_table(bs_path))
                    out['cases'] = default_case_types(np.unique(bs[:, 1]))
                # 出力先に保存済みの仕様があれば読み込む
                out['saved'] = None
                spath = os.path.join(host._out_dir(p, OUT_SUB), SPEC_CSV)
                if os.path.isfile(spath):
                    out['saved'] = csv_to_spec(_read_text(spath))
                    out['saved_path'] = spath
            out['notes'] = notes
            out['ab_types'] = {k: {'F': v[0], 'ductile': v[1],
                                   'label': v[2]}
                               for k, v in AB_TYPES.items()}
            out['ab_areas'] = AB_STRESS_AREA
            return jsonify(_plain(out))
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:                     # noqa: BLE001
            return host._error_response(e)

    @bp.route('/api/colbase_spec_parse', methods=['POST'])
    def api_colbase_spec_parse():
        """CSV テキスト (ブラウザで選んだファイル) → 仕様 list."""
        p = request.get_json(force=True)
        try:
            return jsonify({'specs': csv_to_spec(p.get('text') or '')})
        except ValueError as e:
            return jsonify({'error': str(e)}), 400

    @bp.route('/api/colbase_spec_save', methods=['POST'])
    def api_colbase_spec_save():
        """仕様を出力先フォルダの CSV に保存する."""
        p = request.get_json(force=True)
        err = host._check_input_file(p.get('mgt_path'), 'mgtファイル')
        if err:
            return jsonify({'error': err}), 400
        try:
            path = os.path.join(host._out_dir(p, OUT_SUB), SPEC_CSV)
            with open(path, 'w', encoding='utf-8-sig', newline='') as f:
                f.write(spec_to_csv(p.get('specs') or []))
            return jsonify({'path': path,
                            'url': host._register_file(path) + '&dl=1'})
        except Exception as e:                     # noqa: BLE001
            return host._error_response(e)

    @bp.route('/api/colbase_figure', methods=['POST'])
    def api_colbase_figure():
        """チェックしたグループの仕様図 (PNG) を描いて返す."""
        p = request.get_json(force=True)
        err = host._check_input_file(p.get('mgt_path'), 'mgtファイル')
        if err:
            return jsonify({'error': err}), 400
        if not p.get('specs'):
            return jsonify({'error': '①で仕様図を描くグループを選んで'
                            'ください。'}), 400
        try:
            notes = []
            with host._capture_notes(notes):
                out_dir = os.path.join(host._out_dir(p, OUT_SUB), 'fig')
                figs = draw_groups(p['mgt_path'], p['specs'], out_dir,
                                   notes=notes, prefix='spec')
            return jsonify({'notes': notes, 'figs': [
                {'group': f['group'], 'col': f['col'],
                 'url': host._register_file(f['png'])}
                for f in figs]})
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:                     # noqa: BLE001
            return host._error_response(e)

    @bp.route('/api/colbase_run', methods=['POST'])
    def api_colbase_run():
        """検定を実行し、結果一覧とTeX・プレビューを返す."""
        p = request.get_json(force=True)
        err = (host._check_input_file(p.get('mgt_path'), 'mgtファイル')
               or host._check_input_file(p.get('beam_stress_path'),
                                         'beam_stressファイル'))
        if err:
            return jsonify({'error': err}), 400
        if not p.get('case_types'):
            return jsonify({'error': '荷重ケースが読み込まれていません。'
                            '「対象読込」を押してケース種別を確認して'
                            'ください。'}), 400
        try:
            notes = []
            with host._capture_notes(notes):
                res = run_check(p['mgt_path'], p['beam_stress_path'],
                                p['case_types'], p.get('specs'),
                                params=p.get('params'), notes=notes)
                out_dir = host._out_dir(p, OUT_SUB)
                # 実行時の仕様も保存しておく (次回の対象読込で復元)
                spath = os.path.join(out_dir, SPEC_CSV)
                with open(spath, 'w', encoding='utf-8-sig',
                          newline='') as f:
                    f.write(spec_to_csv(p.get('specs') or []))
                res['project'] = p.get('project') or ''
                # 仕様図 (検討書には TikZ で直接描く)
                res['figs'] = group_reps(p['mgt_path'], p.get('specs'),
                                         notes=notes)
                tex_files = export_tex(res, out_dir)
                pdf, pngs = compile_preview(tex_files, out_dir,
                                            notes=notes)
            return jsonify(_plain({
                'summary': res['summary'], 'rows': res['rows'],
                'kokuji': res['kokuji'], 'params': res['params'],
                'notes': notes, 'out_dir': out_dir,
                'spec_csv': {'name': SPEC_CSV,
                             'url': host._register_file(spath) + '&dl=1'},
                'tex_files': [{'name': os.path.basename(f),
                               'url': host._register_file(f),
                               'path': os.path.abspath(f)}
                              for f in tex_files],
                'pdf': ({'name': os.path.basename(pdf),
                         'url': host._register_file(pdf),
                         'path': os.path.abspath(pdf)}
                        if pdf else None),
                'preview_pngs': [host._register_file(f) for f in pngs]}))
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:                     # noqa: BLE001
            return host._error_response(e)

    return bp
