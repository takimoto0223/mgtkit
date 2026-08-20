# -*- coding: utf-8 -*-
"""荷重分布図の Flask Blueprint.

app.py への追加を最小にするため、共通ヘルパ (_out_dir / _register_file /
_check_input_file / _error_response / _capture_notes / _PLOT_LOCK) は
**登録時に app.py 自身のモジュールを受け取って使う**。

    from mgtkit.loadmap.routes import make_blueprint
    app.register_blueprint(make_blueprint(sys.modules[__name__]))

app.py を import し返すと、`python app.py` 起動時に app が __main__ と
mgtkit.app の2つ読み込まれ、配信許可リスト (_ALLOWED_FILES) が二重になって
生成物をダウンロードできなくなる。それを避けるための受け渡し。
"""

import os

from flask import Blueprint, jsonify, request

from mgtkit.loadmap.draw import (list_load_cases, plot_loadmap, plot_view_sheet,
                                 preview_page)

#: 出力先のサブフォルダ (mgtkit_out/loadmap/)
OUT_SUB = 'loadmap'


def make_blueprint(host):
    """host = app.py のモジュール。共通ヘルパをそこから借りる。"""
    bp = Blueprint('loadmap', __name__)

    def _files_response(paths, notes, out_dir):
        return jsonify({'pdfs': [{'name': os.path.basename(f),
                                  'url': host._register_file(f),
                                  'path': os.path.abspath(f)}
                                 for f in paths],
                        'notes': notes, 'out_dir': out_dir})

    @bp.route('/api/loadmap_cases', methods=['POST'])
    def api_loadmap_cases():
        """mgt を読んで、荷重が付いている荷重ケースの一覧を返す."""
        p = request.get_json(force=True)
        err = host._check_input_file(p.get('mgt_path'), 'mgtファイル')
        if err:
            return jsonify({'error': err}), 400
        try:
            return jsonify({'cases': list_load_cases(p['mgt_path'])})
        except Exception as e:  # noqa: BLE001
            return host._error_response(e)

    @bp.route('/api/plot_loadmap', methods=['POST'])
    def api_plot_loadmap():
        """荷重ケースごとの荷重分布図 PDF を作る."""
        p = request.get_json(force=True)
        err = host._check_input_file(p.get('mgt_path'), 'mgtファイル')
        if err:
            return jsonify({'error': err}), 400
        cases = [str(c) for c in (p.get('cases') or [])]
        if not cases:
            return jsonify({'error': '荷重ケースを1つ以上選択してください。'}), 400
        try:
            notes = []
            with host._PLOT_LOCK, host._capture_notes(notes):
                out_dir = host._out_dir(p, OUT_SUB)
                made = plot_loadmap(
                    p['mgt_path'], out_dir,
                    cases=cases,
                    per_page=int(p.get('per_page', 4)),
                    cols=(int(p['cols']) if p.get('cols') else None),
                    rows=(int(p['rows']) if p.get('rows') else None),
                    paper_size=int(p.get('paper_size', 4)),
                    paper_orient=('landscape' if p.get('landscape') else None),
                    margin=float(p.get('margin', 8.0)),
                    gap=float(p.get('gap', 4.0)),
                    azimuth=float(p.get('azimuth', -40.0)),
                    elevation=float(p.get('elevation', 24.0)),
                    show_arrows=bool(p.get('arrows', True)),
                    fig_format=str(p.get('fig_format') or 'pdf'))
            if not made:
                return jsonify({'error': '出力対象の荷重がありません。'
                                         '荷重ケースの選択を確認してください。',
                                'notes': notes}), 400
            return _files_response(made, notes, out_dir)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:  # noqa: BLE001
            return host._error_response(e)

    @bp.route('/api/loadmap_preview', methods=['POST'])
    def api_loadmap_preview():
        """PDF を作る前の確認用に、1ページぶんを PNG で返す."""
        p = request.get_json(force=True)
        err = host._check_input_file(p.get('mgt_path'), 'mgtファイル')
        if err:
            return jsonify({'error': err}), 400
        cases = [str(c) for c in (p.get('cases') or [])]
        if not cases:
            return jsonify({'error': '荷重ケースを1つ以上選択してください。'}), 400
        try:
            notes = []
            with host._PLOT_LOCK, host._capture_notes(notes):
                out_dir = host._out_dir(p, OUT_SUB)
                path, page, pages, on_page = preview_page(
                    p['mgt_path'], out_dir,
                    cases=cases,
                    page=int(p.get('page', 1)),
                    per_page=int(p.get('per_page', 4)),
                    cols=(int(p['cols']) if p.get('cols') else None),
                    rows=(int(p['rows']) if p.get('rows') else None),
                    paper_size=int(p.get('paper_size', 4)),
                    paper_orient=('landscape' if p.get('landscape') else None),
                    margin=float(p.get('margin', 8.0)),
                    gap=float(p.get('gap', 4.0)),
                    azimuth=float(p.get('azimuth', -40.0)),
                    elevation=float(p.get('elevation', 24.0)),
                    show_arrows=bool(p.get('arrows', True)))
            if not path:
                return jsonify({'error': '選択したケースに荷重がありません。',
                                'notes': notes}), 400
            return jsonify({'url': host._register_file(path), 'page': page,
                            'pages': pages, 'cases': on_page, 'notes': notes,
                            'out_dir': out_dir})
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:  # noqa: BLE001
            return host._error_response(e)

    @bp.route('/api/loadmap_view_sheet', methods=['POST'])
    def api_loadmap_view_sheet():
        """視点を選ぶための比較シート (1ケースを角度違いで並べる)."""
        p = request.get_json(force=True)
        err = host._check_input_file(p.get('mgt_path'), 'mgtファイル')
        if err:
            return jsonify({'error': err}), 400
        cases = [str(c) for c in (p.get('cases') or [])]
        if not cases:
            return jsonify({'error': '比較に使う荷重ケースを1つ選択して'
                                     'ください。'}), 400
        try:
            notes = []
            with host._PLOT_LOCK, host._capture_notes(notes):
                out_dir = host._out_dir(p, OUT_SUB)
                made = plot_view_sheet(p['mgt_path'], out_dir, cases[0])
            return _files_response(made, notes, out_dir)
        except Exception as e:  # noqa: BLE001
            return host._error_response(e)

    return bp
