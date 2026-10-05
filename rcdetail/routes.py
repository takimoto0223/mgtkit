# -*- coding: utf-8 -*-
"""RC定着・付着・柱梁接合部検定の Flask Blueprint.

loadmap / jointchk / diaphragm と同じ作法で、共通ヘルパは登録時に
app.py 自身のモジュールを受け取って使う。

    from mgtkit.rcdetail.routes import make_blueprint
    app.register_blueprint(make_blueprint(sys.modules[__name__]))
"""

import os

from flask import Blueprint, jsonify, request

from mgtkit.rcdetail.calc import read_targets, run_check
from mgtkit.rcdetail.tex import export_tex, compile_preview

#: 出力先のサブフォルダ (mgtkit_out/rcdetail/)
OUT_SUB = 'rcdetail'


def make_blueprint(host):
    """host = app.py のモジュール。共通ヘルパをそこから借りる。"""
    bp = Blueprint('rcdetail', __name__)

    @bp.route('/api/rcdetail_read', methods=['POST'])
    def api_rcdetail_read():
        """対象読込: RC梁断面・接合部の一覧と荷重ケース既定値を返す."""
        p = request.get_json(force=True)
        err = host._check_input_file(p.get('mgt_path'), 'mgtファイル')
        if err:
            return jsonify({'error': err}), 400
        try:
            notes = []
            with host._capture_notes(notes):
                out = read_targets(p['mgt_path'],
                                   wall_prefix=(p.get('wall_prefix')
                                                or 'EW'),
                                   notes=notes)
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
                    if bs.shape[1] < 2:
                        return jsonify({'error': 'beam_stressファイルの'
                                        '列数が不足しています (8列必要)。'
                                        }), 400
                    out['cases'] = default_case_types(np.unique(bs[:, 1]))
            out['notes'] = notes
            return jsonify(out)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:                     # noqa: BLE001
            return host._error_response(e)

    @bp.route('/api/rcdetail_run', methods=['POST'])
    def api_rcdetail_run():
        """検定を実行し、結果一覧と検討書PDFを返す."""
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
                                p['case_types'], params=p.get('params'),
                                form_secs=p.get('form_secs'),
                                check_secs=p.get('check_secs'),
                                wall_cols=p.get('wall_cols'),
                                wall_open=p.get('wall_open'),
                                notes=notes)
                out_dir = host._out_dir(p, OUT_SUB)
                tex_files = export_tex(res, out_dir)
                pdf, pngs = compile_preview(tex_files, out_dir,
                                            notes=notes)
            return jsonify({
                'summary': res['summary'], 'cases': res['cases'],
                'anchor': res['anchor'], 'through': res['through'],
                'bond': res['bond'], 'joints': res['joints'],
                'walls': res['walls'], 'openings': res['openings'],
                'params': res['params'],
                'notes': notes, 'out_dir': out_dir,
                'tex_files': [{'name': os.path.basename(f),
                               'url': host._register_file(f),
                               'path': os.path.abspath(f)}
                              for f in tex_files],
                'pdf': ({'name': os.path.basename(pdf),
                         'url': host._register_file(pdf),
                         'path': os.path.abspath(pdf)}
                        if pdf else None),
                'preview_pngs': [host._register_file(f) for f in pngs]})
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:                     # noqa: BLE001
            return host._error_response(e)

    return bp
