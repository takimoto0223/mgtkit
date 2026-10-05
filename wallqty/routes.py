# -*- coding: utf-8 -*-
"""木造壁量計算の Flask Blueprint.

loadmap と同じ作法で、共通ヘルパ (_out_dir / _register_file /
_check_input_file / _error_response / _capture_notes) は **登録時に app.py
自身のモジュールを受け取って使う** (app.py を import し返すと二重読み込みに
なるため)。

    from mgtkit.wallqty.routes import make_blueprint
    app.register_blueprint(make_blueprint(sys.modules[__name__]))
"""

import os

from flask import Blueprint, jsonify, request

from mgtkit.wallqty.mgt_walls import (read_walls, DEFAULT_KEYWORD,
                                      DEFAULT_SLOPE_MIN, DEFAULT_MIN_LEN,
                                      DEFAULT_MAX_ASPECT, DEFAULT_BETA_CAP,
                                      DEFAULT_MAX_GAP)
from mgtkit.wallqty.calc import (run_check, wind_area_estimate,
                                 export_summary_csv, export_walls_csv,
                                 export_quarter_csv)
from mgtkit.wallqty.report import export_report

#: 出力先のサブフォルダ (mgtkit_out/wallqty/)
OUT_SUB = 'wallqty'


def _read_params(p):
    """リクエストから read_walls の引数を取り出す."""
    return {
        'keyword': (str(p.get('keyword') or '').strip() or DEFAULT_KEYWORD),
        'diag_split': bool(p.get('diag_split')),
        'slope_min': float(p.get('slope_min') or DEFAULT_SLOPE_MIN),
        # 令46条の最小長さ規定。チェックを外すと 0 が来て判定しない
        'min_len': (DEFAULT_MIN_LEN if p.get('min_len') is None
                    else float(p['min_len'])),
        'max_aspect': (DEFAULT_MAX_ASPECT if p.get('max_aspect') is None
                       else float(p['max_aspect'])),
        'beta_cap': (DEFAULT_BETA_CAP if p.get('beta_cap') is None
                     else float(p['beta_cap'])),
        'max_gap': (DEFAULT_MAX_GAP if p.get('max_gap') is None
                    else float(p['max_gap'])),
        # 画面の階定義。省略/空なら mgt の *STORY から作る
        'stories_def': (p.get('stories_def') or None),
    }


def _read_payload(res):
    """存在壁量の読み取り結果を JSON 化する (検定の有無によらず共通)."""
    return {'unit': res['unit'], 'stories': res['stories'],
            'walls': res['walls'], 'exist': res['exist'],
            'model': res['model'], 'keyword': res['keyword'],
            'qa_unit': res['qa_unit'], 'materials': res['materials'],
            'story_src': res['story_src'], 'story_def': res['story_def'],
            'slope_min': res['slope_min'], 'axes': res['axes'],
            'min_len': res['min_len'], 'max_aspect': res['max_aspect'],
            'beta_cap': res['beta_cap'], 'max_gap': res['max_gap'],
            'wind_ref': wind_area_estimate(res)}


def make_blueprint(host):
    """host = app.py のモジュール。共通ヘルパをそこから借りる。"""
    bp = Blueprint('wallqty', __name__)

    @bp.route('/api/wallqty_read', methods=['POST'])
    def api_wallqty_read():
        """mgt を読んで、階ごと・方向ごとの存在壁量と壁の一覧を返す."""
        p = request.get_json(force=True)
        err = host._check_input_file(p.get('mgt_path'), 'mgtファイル')
        if err:
            return jsonify({'error': err}), 400
        try:
            notes = []
            with host._capture_notes(notes):
                res = read_walls(p['mgt_path'], notes=notes, **_read_params(p))
            out = _read_payload(res)
            out['notes'] = notes
            return jsonify(out)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:                     # noqa: BLE001
            return host._error_response(e)

    @bp.route('/api/wallqty_check', methods=['POST'])
    def api_wallqty_check():
        """層重量・見付面積から必要壁量を出し、壁量を検定して CSV も作る."""
        p = request.get_json(force=True)
        err = host._check_input_file(p.get('mgt_path'), 'mgtファイル')
        if err:
            return jsonify({'error': err}), 400
        try:
            notes = []
            with host._capture_notes(notes):
                res = read_walls(p['mgt_path'], notes=notes, **_read_params(p))
                chk = run_check(
                    res, p.get('stories') or [],
                    z=float(p.get('z', 1.0)),
                    tc=float(p.get('tc', 0.6)),
                    c0=float(p.get('c0', 0.2)),
                    qa=float(p.get('qa', res['qa_unit'])),
                    wind_k=float(p.get('wind_k', 50.0)),
                    height=(float(p['height']) if p.get('height') else None),
                    alpha=float(p.get('alpha', 1.0)),
                    imp=float(p.get('imp', 1.0)),
                    notes=notes)
                out_dir = host._out_dir(p, OUT_SUB)
                csv1 = export_summary_csv(
                    res, chk, os.path.join(out_dir, 'wallqty_summary.csv'))
                csv2 = export_quarter_csv(
                    res, chk, os.path.join(out_dir, 'wallqty_quarter.csv'))
                csv3 = export_walls_csv(
                    res, os.path.join(out_dir, 'wallqty_walls.csv'))
                # 検討書PDF (matplotlib はスレッドセーフでないので直列化)
                with host._PLOT_LOCK:
                    rep = export_report(
                        res, chk, os.path.join(out_dir, 'wallqty_report.pdf'),
                        mgt_path=p['mgt_path'],
                        project=str(p.get('project') or ''))
            out = _read_payload(res)
            out.update({'rows': chk['rows'], 'params': chk['params'],
                        'ng': chk['ng'], 'ng4': chk['ng4'],
                        'notes': notes, 'out_dir': out_dir,
                        'pdf': {'name': os.path.basename(rep),
                                'url': host._register_file(rep),
                                'path': os.path.abspath(rep)},
                        'csv': [{'name': os.path.basename(f),
                                 'url': host._register_file(f) + '&dl=1'}
                                for f in (csv1, csv2, csv3)]})
            return jsonify(out)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:                     # noqa: BLE001
            return host._error_response(e)

    return bp
