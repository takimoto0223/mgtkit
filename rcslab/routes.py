# -*- coding: utf-8 -*-
"""RCスラブの検討書の Flask Blueprint (loadmap / colbase と同じ作法).

    from mgtkit.rcslab.routes import make_blueprint
    app.register_blueprint(make_blueprint(sys.modules[__name__]))

入力は mgt (画面上部の欄) と、MIDAS の「板要素断面力」テーブルを保存した
txt。読み込んだデータは <出力先>\\mgtkit_out\\rcslab\\rcslab_data.json に
保存し、PDF の作り直しではそれを使う。
"""

import json
import os
import time

from flask import Blueprint, jsonify, request

from mgtkit.rcslab import data as rsdata
from mgtkit.rcslab.draw import (DEFAULT_POST, DEFAULT_LONG_ONLY_PRE,
                                build_report, figure_png, run_checks,
                                section_stats)
from mgtkit.rcslab.fromfile import candidates, load as load_file

OUT_SUB = 'rcslab'
DATA_JSON = 'rcslab_data.json'
SPEC_JSON = 'rcslab_spec.json'


def _out_dir(host, p):
    err = host._check_input_file(p.get('mgt_path'), 'mgtファイル')
    if err:
        raise ValueError(err)
    return host._out_dir(p, OUT_SUB)


def _summary(data):
    """画面に出す読込結果 (対象ごとの板厚・要素数)."""
    out = []
    for key, t in data['targets'].items():
        out.append({'key': key, 'kind': t['kind'], 'id': t['id'],
                    'name': t['name'], 't': t['t'], 'n': len(t['elems'])})
    return {'model': data.get('model') or '', 'source': data.get('source'),
            'fetched': data.get('fetched'), 'cases': data['cases'],
            'targets': out}


def _read_spec(out_dir):
    try:
        with open(os.path.join(out_dir, SPEC_JSON), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_spec(out_dir, spec):
    old = _read_spec(out_dir)
    old.update(spec)
    with open(os.path.join(out_dir, SPEC_JSON), 'w', encoding='utf-8') as f:
        json.dump(old, f, ensure_ascii=False, indent=1)


def _pick(p):
    """選んだ対象 {'kind','ids'} を取り出す."""
    kind = 'group' if p.get('kind') == 'group' else 'thick'
    ids = [str(v) for v in (p.get('ids') or []) if str(v).strip()]
    if kind == 'thick':
        ids = [int(v) for v in ids]
    return kind, ids


def make_blueprint(host):
    bp = Blueprint('rcslab', __name__)
    cache = {}   # path -> (mtime, data)

    def _load(out_dir):
        path = os.path.join(out_dir, DATA_JSON)
        if not os.path.isfile(path):
            raise ValueError('読み込み済みのデータがありません。先に「txtから'
                             '読み込み」を押してください。')
        mt = os.path.getmtime(path)
        if path not in cache or cache[path][0] != mt:
            cache[path] = (mt, rsdata.load_data(path))
        return cache[path][1]

    def _respond(out_dir, data, notes=None):
        return jsonify({'summary': _summary(data), 'spec': _read_spec(out_dir),
                        'out_dir': out_dir, 'notes': notes or [],
                        'defaults': {'post': DEFAULT_POST,
                                     'long_only_pre': DEFAULT_LONG_ONLY_PRE}})

    def _fail(e):
        if isinstance(e, (ValueError, RuntimeError, KeyError)):
            return jsonify({'error': str(e)}), 400
        return host._error_response(e)

    @bp.route('/api/rcslab_spec', methods=['POST'])
    def api_spec():
        """前回の入力 (荷重ケース・txt・対象) を返す."""
        p = request.get_json(force=True)
        try:
            return jsonify({'spec': _read_spec(_out_dir(host, p))})
        except Exception:  # noqa: BLE001
            return jsonify({'spec': {}})

    @bp.route('/api/rcslab_candidates', methods=['POST'])
    def api_candidates():
        """検討対象の候補 (厚さID またはグループ) の一覧."""
        p = request.get_json(force=True)
        err = host._check_input_file(p.get('mgt_path'), 'mgtファイル')
        if err:
            return jsonify({'error': err}), 400
        try:
            kind = 'group' if p.get('kind') == 'group' else 'thick'
            return jsonify({'kind': kind,
                            'items': candidates(p['mgt_path'], kind)})
        except Exception as e:  # noqa: BLE001
            return _fail(e)

    @bp.route('/api/rcslab_file', methods=['POST'])
    def api_file():
        """mgt + 板要素断面力テーブル txt を読んで保存する."""
        p = request.get_json(force=True)
        txt = str(p.get('txt_path') or '').strip()
        err = host._check_input_file(txt, '板要素断面力のtxt')
        if err:
            return jsonify({'error': err}), 400
        cases = [c for c in (p.get('cases') or [])
                 if str(c.get('name', '')).strip()]
        if not cases:
            return jsonify({'error': '荷重ケースを入力してください。'}), 400
        try:
            out_dir = _out_dir(host, p)
            kind, ids = _pick(p)
            notes = []
            with host._capture_notes(notes):
                data = load_file(p['mgt_path'], txt, cases, kind, ids)
            rsdata.save_data(data, os.path.join(out_dir, DATA_JSON))
            _write_spec(out_dir, {'cases': cases, 'txt_path': txt,
                                  'kind': kind, 'ids': ids})
            return _respond(out_dir, data, notes)
        except Exception as e:  # noqa: BLE001
            return _fail(e)

    @bp.route('/api/rcslab_load', methods=['POST'])
    def api_load():
        p = request.get_json(force=True)
        try:
            out_dir = _out_dir(host, p)
            return _respond(out_dir, _load(out_dir))
        except Exception as e:  # noqa: BLE001
            return _fail(e)

    @bp.route('/api/rcslab_preview', methods=['POST'])
    def api_preview():
        p = request.get_json(force=True)
        try:
            out_dir = _out_dir(host, p)
            data = _load(out_dir)
            key, case, kind = str(p['key']), str(p['case']), str(p['kind'])
            d = os.path.join(out_dir, 'preview')
            os.makedirs(d, exist_ok=True)
            safe = ''.join(ch if ch.isalnum() else '_' for ch in key + case)
            path = os.path.join(d, 'pv_%s_%s.png' % (safe, kind))
            with host._PLOT_LOCK:
                figure_png(data, key, case, kind, path)
            return jsonify({'url': host._register_file(path)
                            + '&t=%d' % int(time.time() * 1000)})
        except Exception as e:  # noqa: BLE001
            return _fail(e)

    def _secs(p, data):
        secs = [s for s in (p.get('sections') or []) if s.get('use', True)]
        for s in secs:
            if s.get('key') not in data['targets']:
                raise ValueError('%s は読み込み済みのデータにありません。'
                                 '読み込み直してください。' % s.get('key'))
        return secs

    @bp.route('/api/rcslab_check', methods=['POST'])
    def api_check():
        """PDF を作らずに検定比だけ返す (画面の一覧表用)."""
        p = request.get_json(force=True)
        try:
            out_dir = _out_dir(host, p)
            data = _load(out_dir)
            rows = []
            for r in run_checks(data, _secs(p, data)):
                def _one(c):
                    if not c:
                        return None
                    return {'M+': c['pos']['M'], 'M-': c['neg']['M'],
                            'Q': c['shear']['Q'],
                            'r_pos': c['pos']['ratio'],
                            'r_neg': c['neg']['ratio'],
                            'r_q': c['shear']['ratio'],
                            'rules_ok': all(x['ok'] for x in c['rules']),
                            'rules_ng': [x['name'] for x in c['rules']
                                         if not x['ok']],
                            'ok': c['ok']}
                rows.append({'key': r['key'], 'name': r['name'],
                             'long': _one(r['long']),
                             'short': _one(r['short'])})
            _write_spec(out_dir, {'sections': p.get('sections')})
            return jsonify({'rows': rows})
        except Exception as e:  # noqa: BLE001
            return _fail(e)

    @bp.route('/api/rcslab_pdf', methods=['POST'])
    def api_pdf():
        p = request.get_json(force=True)
        try:
            out_dir = _out_dir(host, p)
            data = _load(out_dir)
            secs = _secs(p, data)
            if not secs:
                return jsonify({'error': '検討書に載せる対象を1つ以上選択'
                                         'してください。'}), 400
            name = str(p.get('file_name') or 'RCスラブの検討').strip()
            name = ''.join(ch for ch in name if ch not in '\\/:*?"<>|')
            path = os.path.join(out_dir, name + '.pdf')
            notes = []
            with host._PLOT_LOCK, host._capture_notes(notes):
                build_report(data, secs, path,
                             font=str(p.get('font') or 'Yu Gothic'),
                             title=str(p.get('title') or 'RCスラブの検討'))
            _write_spec(out_dir, {'sections': p.get('sections'),
                                  'title': p.get('title'),
                                  'file_name': p.get('file_name'),
                                  'font': p.get('font')})
            return jsonify({'pdfs': [{'name': os.path.basename(path),
                                      'url': host._register_file(path),
                                      'path': os.path.abspath(path)}],
                            'notes': notes, 'out_dir': out_dir})
        except PermissionError:
            return jsonify({'error': 'PDF を書き込めません。同名の PDF を'
                                     '開いていたら閉じてください。'}), 400
        except Exception as e:  # noqa: BLE001
            return _fail(e)

    return bp
