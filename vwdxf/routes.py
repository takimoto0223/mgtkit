# -*- coding: utf-8 -*-
"""DXF(VW10J) タブの Flask Blueprint.

app.py への追加を最小にするため、共通ヘルパ (_check_input_file / _out_dir /
_register_file / _error_response / _capture_notes / _PLOT_LOCK) は登録時に
app.py 自身のモジュールを受け取って使う (loadmap と同じ)。

    from mgtkit.vwdxf.routes import make_blueprint as _vwdxf_bp
    app.register_blueprint(_vwdxf_bp(sys.modules[__name__]))

app.py を import し返すと、`python app.py` 起動時に app が __main__ と mgtkit.app の
2 つ読み込まれ、配信許可リストが二重になって生成物をダウンロードできなくなるため。
"""
import os

from flask import Blueprint, jsonify, request

from mgtkit.vwdxf import api

#: 出力先のサブフォルダ (mgtkit_out/dxf_vw10j/)
OUT_SUB = 'dxf_vw10j'


def make_blueprint(host):
    """host = app.py のモジュール。共通ヘルパをそこから借りる。"""
    bp = Blueprint('vwdxf', __name__)

    def _limit(p):
        return float(p.get('limit_sec_no') or api.DEFAULT_LIMIT_SEC_NO)

    @bp.route('/api/vwdxf_info', methods=['POST'])
    def api_vwdxf_info():
        """読込: 通り・フロアの候補、部材リストの区分、部材集計."""
        p = request.get_json(force=True)
        err = host._check_input_file(p.get('mgt_path'), 'mgtファイル')
        if err:
            return jsonify({'error': err}), 400
        try:
            notes = []
            with host._capture_notes(notes):
                res = api.info(api.load(p['mgt_path'], _limit(p)))
            res['notes'] = notes
            return jsonify(res)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:  # noqa: BLE001
            return host._error_response(e)

    @bp.route('/api/vwdxf_preview', methods=['POST'])
    def api_vwdxf_preview():
        """用紙プレビュー PNG (DXF と同じ図形)."""
        p = request.get_json(force=True)
        err = host._check_input_file(p.get('mgt_path'), 'mgtファイル')
        if err:
            return jsonify({'error': err}), 400
        try:
            notes = []
            with host._capture_notes(notes):
                out_dir = host._out_dir(p, OUT_SUB)
            out_path = os.path.join(out_dir, '_vwdxf_preview.png')
            with host._PLOT_LOCK, host._capture_notes(notes):
                res = api.preview(p['mgt_path'], out_path, str(p.get('kind') or 'axis'),
                                  p.get('key'), api.options(p), _limit(p),
                                  sheet=p.get('sheet') or None, page=int(p.get('page') or 1))
            res['png_url'] = (host._register_file(out_path)
                              + '&t=%d' % int(os.path.getmtime(out_path)))
            res['notes'] = notes
            return jsonify(res)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:  # noqa: BLE001
            return host._error_response(e)

    @bp.route('/api/vwdxf_dxf', methods=['POST'])
    def api_vwdxf_dxf():
        """DXF 生成."""
        p = request.get_json(force=True)
        err = host._check_input_file(p.get('mgt_path'), 'mgtファイル')
        if err:
            return jsonify({'error': err}), 400
        try:
            notes = []
            with host._capture_notes(notes):
                out_dir = host._out_dir(p, OUT_SUB)
                made, info = api.export(p['mgt_path'], out_dir, p.get('axes') or [],
                                        list(p.get('levels') or []), api.options(p), _limit(p))
            files = [{'name': os.path.basename(f), 'url': host._register_file(f) + '&dl=1'}
                     for f in made]
            return jsonify({'files': files, 'info': info, 'out_dir': out_dir, 'notes': notes})
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:  # noqa: BLE001
            return host._error_response(e)

    return bp
