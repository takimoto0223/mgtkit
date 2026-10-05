# -*- coding: utf-8 -*-
"""木造水平構面検定の Flask Blueprint.

loadmap / jointchk と同じ作法で、共通ヘルパ (_out_dir / _register_file /
_check_input_file / _error_response / _capture_notes / _PLOT_LOCK) は
**登録時に app.py 自身のモジュールを受け取って使う**。

    from mgtkit.diaphragm.routes import make_blueprint
    app.register_blueprint(make_blueprint(sys.modules[__name__]))
"""

import os

from flask import Blueprint, jsonify, request

from mgtkit.diaphragm.calc import read_targets, run_check, diaphragm_csv
from mgtkit.diaphragm.report import export_report

#: 出力先のサブフォルダ (mgtkit_out/diaphragm/)
OUT_SUB = 'diaphragm'


def make_blueprint(host):
    """host = app.py のモジュール。共通ヘルパをそこから借りる。"""
    bp = Blueprint('diaphragm', __name__)

    @bp.route('/api/diaphragm_read', methods=['POST'])
    def api_diaphragm_read():
        """対象読込: 床倍率グループと荷重ケースの既定種別を返す."""
        p = request.get_json(force=True)
        err = (host._check_input_file(p.get('mgt_path'), 'mgtファイル')
               or host._check_input_file(p.get('plate_stress_path'),
                                         'plate_stressファイル'))
        if err:
            return jsonify({'error': err}), 400
        try:
            notes = []
            with host._capture_notes(notes):
                out = read_targets(p['mgt_path'], p['plate_stress_path'])
            out['notes'] = notes
            return jsonify(out)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:                     # noqa: BLE001
            return host._error_response(e)

    @bp.route('/api/diaphragm_run', methods=['POST'])
    def api_diaphragm_run():
        """検定を実行し、結果一覧と検討書PDF・CSVを返す."""
        p = request.get_json(force=True)
        err = (host._check_input_file(p.get('mgt_path'), 'mgtファイル')
               or host._check_input_file(p.get('plate_stress_path'),
                                         'plate_stressファイル'))
        if err:
            return jsonify({'error': err}), 400
        if not p.get('cases'):
            return jsonify({'error': '検討対象の荷重ケースが選択されて'
                            'いません。「対象読込」でケースを選んで'
                            'ください。'}), 400
        try:
            notes = []
            with host._capture_notes(notes):
                betas = p.get('betas')
                res = run_check(p['mgt_path'], p['plate_stress_path'],
                                p['cases'],
                                qa_base=float(p.get('qa_base') or 1.96),
                                betas=(betas if betas else None))
                out_dir = host._out_dir(p, OUT_SUB)
                csv = diaphragm_csv(
                    res, os.path.join(out_dir, 'diaphragm_check.csv'))
                # 検討書PDF (matplotlib はスレッドセーフでないので直列化)
                with host._PLOT_LOCK:
                    pdf = export_report(
                        res, os.path.join(out_dir, 'diaphragm_report.pdf'))
            # 図データ (clusters) は大きいのでJSON応答から除く
            groups = [{k: v for k, v in g.items() if k != 'clusters'}
                      for g in res['groups']]
            return jsonify({
                'cases': res['cases'], 'groups': groups,
                'qa_base': res['qa_base'],
                'notes': notes, 'out_dir': out_dir,
                'csv': {'name': os.path.basename(csv),
                        'url': host._register_file(csv) + '&dl=1'},
                'pdf': {'name': os.path.basename(pdf),
                        'url': host._register_file(pdf),
                        'path': os.path.abspath(pdf)}})
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:                     # noqa: BLE001
            return host._error_response(e)

    return bp
