# -*- coding: utf-8 -*-
"""断面符号図 (MIDAS風) の Flask Blueprint.

loadmap/routes.py と同じく、共通ヘルパ (_out_dir / _register_file /
_check_input_file / _error_response / _capture_notes / _PLOT_LOCK) は
登録時に app.py のモジュールを受け取って使う (app.py を import し返さない)。
"""

import os

from flask import Blueprint, jsonify, request

from mgtkit.capfig.draw import (Style, list_sections, plot_capfig,
                                preview_png)

#: 出力先のサブフォルダ (mgtkit_out/capfig/)
OUT_SUB = 'capfig'


def _args(p):
    style = Style(
        color_mode=('mono' if p.get('mono') else 'midas'),
        short_name=bool(p.get('short_name', True)),
        merge=bool(p.get('merge', True)),
        show_nodes=bool(p.get('nodes', False)),
        solid=bool(p.get('solid', True)),
        show_legend=bool(p.get('legend', True)),
        show_grid=bool(p.get('grid', True)),
        label_pt=float(p.get('label_pt', 6.0)),
        limit_sec_no=float(p.get('limit_sec_no', 9000)),
        paper_size=int(p.get('paper_size', 3)),
        orient=str(p.get('orient') or 'landscape'),
        full_secs=[int(v) for v in (p.get('full_secs') or [])],
        show_footer=bool(p.get('footer', False)),
        hplate_label=not bool(p.get('hplate_nolabel', False)),
        level_names=(None if p.get('level_names') is None
                     else [str(v) for v in p['level_names']]))
    return dict(floors=[str(v) for v in (p.get('floors') or [])],
                axes=[str(v) for v in (p.get('axes') or [])],
                grids=[str(v) for v in (p.get('grids') or [])],
                show_columns=bool(p.get('columns', True)),
                show_hplates=bool(p.get('hplates', True)),
                show_levels=bool(p.get('levels', True)),
                style=style,
                file_label=os.path.basename(str(p.get('mgt_path') or '')))


def make_blueprint(host):
    """host = app.py のモジュール。共通ヘルパをそこから借りる。"""
    bp = Blueprint('capfig', __name__)

    def _check(p):
        err = host._check_input_file(p.get('mgt_path'), 'mgtファイル')
        if err:
            return err
        if not (p.get('floors') or p.get('axes')):
            return '伏図または軸組図のグループを1つ以上選択してください。'
        return None

    @bp.route('/api/capfig_sections', methods=['POST'])
    def api_capfig_sections():
        """断面一覧 (「_」以降も書く断面を選ぶ表用)。"""
        p = request.get_json(force=True)
        err = host._check_input_file(p.get('mgt_path'), 'mgtファイル')
        if err:
            return jsonify({'error': err}), 400
        try:
            return jsonify({'sections': list_sections(p['mgt_path'])})
        except Exception as e:  # noqa: BLE001
            return host._error_response(e)

    @bp.route('/api/capfig_plot', methods=['POST'])
    def api_capfig_plot():
        p = request.get_json(force=True)
        err = _check(p)
        if err:
            return jsonify({'error': err}), 400
        try:
            notes = []
            with host._PLOT_LOCK, host._capture_notes(notes):
                out_dir = host._out_dir(p, OUT_SUB)
                made, n2 = plot_capfig(p['mgt_path'], out_dir, **_args(p))
            notes += n2
            if not made:
                return jsonify({'error': '出力できる図がありません。',
                                'notes': notes}), 400
            return jsonify({'pdfs': [{'name': os.path.basename(f),
                                      'url': host._register_file(f),
                                      'path': os.path.abspath(f)}
                                     for f in made],
                            'notes': notes, 'out_dir': out_dir})
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:  # noqa: BLE001
            return host._error_response(e)

    @bp.route('/api/capfig_preview', methods=['POST'])
    def api_capfig_preview():
        p = request.get_json(force=True)
        err = _check(p)
        if err:
            return jsonify({'error': err}), 400
        try:
            notes = []
            with host._PLOT_LOCK, host._capture_notes(notes):
                out_dir = host._out_dir(p, OUT_SUB)
                path, page, pages, title, n2 = preview_png(
                    p['mgt_path'], out_dir, page=int(p.get('page', 1)),
                    **_args(p))
            notes += n2
            if not path:
                return jsonify({'error': '出力できる図がありません。',
                                'notes': notes}), 400
            return jsonify({'url': host._register_file(path), 'page': page,
                            'pages': pages, 'title': title, 'notes': notes,
                            'out_dir': out_dir})
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:  # noqa: BLE001
            return host._error_response(e)

    return bp
