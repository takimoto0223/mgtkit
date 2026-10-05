# -*- coding: utf-8 -*-
"""梁接合部検定の Flask Blueprint.

loadmap / wallqty と同じ作法で、共通ヘルパ (_out_dir / _register_file /
_check_input_file / _error_response / _capture_notes / _PLOT_LOCK) は
**登録時に app.py 自身のモジュールを受け取って使う**。

    from mgtkit.jointchk.routes import make_blueprint
    app.register_blueprint(make_blueprint(sys.modules[__name__]))
"""

import os

from flask import Blueprint, jsonify, request

from mgtkit.jointchk.calc import (read_targets, run_check, load_fittings_db,
                                  read_groups)
from mgtkit.jointchk.report import export_report

#: 出力先のサブフォルダ (mgtkit_out/jointchk/)
OUT_SUB = 'jointchk'


def make_blueprint(host):
    """host = app.py のモジュール。共通ヘルパをそこから借りる。"""
    bp = Blueprint('jointchk', __name__)

    @bp.route('/api/jointchk_read', methods=['POST'])
    def api_jointchk_read():
        """対象読込: ピン端をもつ梁要素と断面一覧、荷重ケース既定値を返す."""
        p = request.get_json(force=True)
        err = host._check_input_file(p.get('mgt_path'), 'mgtファイル')
        if err:
            return jsonify({'error': err}), 400
        try:
            notes = []
            with host._capture_notes(notes):
                members, sections, splices = read_targets(p['mgt_path'],
                                                          notes=notes)
                out = {'members': members, 'sections': sections,
                       'splices': splices,
                       'fittings_db': load_fittings_db(),
                       'groups': read_groups(p['mgt_path'], members,
                                             splices)}
                # 応力ファイルがあれば荷重ケースの既定種別も返す (断面算定と同じ)
                bs_path = (p.get('beam_stress_path') or '').strip()
                if bs_path:
                    err = host._check_input_file(bs_path, 'beam_stressファイル')
                    if err:
                        return jsonify({'error': err}), 400
                    import numpy as np
                    from mgtkit.util import load_beam_stress_table
                    from mgtkit.ratio_pipeline import default_case_types
                    bs = np.atleast_2d(load_beam_stress_table(bs_path))
                    if bs.shape[1] < 2:
                        return jsonify({'error': 'beam_stressファイルの列数が'
                                        '不足しています (8列必要)。'}), 400
                    out['cases'] = default_case_types(np.unique(bs[:, 1]))
            out['notes'] = notes
            return jsonify(out)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:                     # noqa: BLE001
            return host._error_response(e)

    @bp.route('/api/jointchk_run', methods=['POST'])
    def api_jointchk_run():
        """検定を実行し、結果一覧と検討書PDFを返す."""
        p = request.get_json(force=True)
        err = (host._check_input_file(p.get('mgt_path'), 'mgtファイル')
               or host._check_input_file(p.get('beam_stress_path'),
                                         'beam_stressファイル'))
        if err:
            return jsonify({'error': err}), 400
        if not p.get('sections'):
            return jsonify({'error': '検討する断面が選択されていません。'
                            '「対象読込」で断面を選んでください。'}), 400
        if not p.get('case_types'):
            return jsonify({'error': '荷重ケースが読み込まれていません。'
                            '「対象読込」を押してケース種別を確認して'
                            'ください。'}), 400
        try:
            notes = []
            with host._capture_notes(notes):
                res = run_check(p['mgt_path'], p['beam_stress_path'],
                                p['sections'], p.get('fittings') or {},
                                p['case_types'],
                                exclude_groups=p.get('exclude_groups') or [],
                                fit_groups=p.get('fit_groups') or [],
                                unit_labels=p.get('unit_labels') or {},
                                check_compression=bool(
                                    p.get('check_compression')),
                                hide_jtype=bool(p.get('hide_jtype')),
                                notes=notes)
                out_dir = host._out_dir(p, OUT_SUB)
                # 検討書PDF (matplotlib はスレッドセーフでないので直列化)
                with host._PLOT_LOCK:
                    pdf = export_report(
                        res, os.path.join(out_dir, 'jointchk_report.pdf'),
                        mgt_path=p['mgt_path'],
                        project=str(p.get('project') or ''))
            # 接合種別の非表示時は、画面の総括には統合版を渡す
            # (種別別の 'summary' はPDFの金物表用)
            return jsonify({
                'cases': res['cases'],
                'summary': res['summary_disp'] or res['summary'],
                'check_compression': res['check_compression'],
                'hide_jtype': res['hide_jtype'],
                'rows': res['rows'],
                'splice_summary': res['splice_summary'],
                'n_members': len(res['members']),
                'notes': notes, 'out_dir': out_dir,
                'pdf': {'name': os.path.basename(pdf),
                        'url': host._register_file(pdf),
                        'path': os.path.abspath(pdf)}})
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:                     # noqa: BLE001
            return host._error_response(e)

    return bp
