"""Level 1 スモークテスト。

実 mgt ファイルなしで決定的に検証できる範囲のみを対象とする。
ステータスコードと JSON 形式(error キー等)を固定し、ルーティング・
バリデーション層の退行を検出する。
"""
import io

import pytest

# _check_input_file による入力パス検証で空 JSON を 400 で弾くルート
GUARDED_ROUTES = [
    '/api/mgt_info', '/api/model3d', '/api/plot_model', '/api/load_cases',
    '/api/plot_stress', '/api/check_cases', '/api/steel_check',
    '/api/struct_info', '/api/struct_preview', '/api/struct_dxf',
    '/api/plywood_check', '/api/plot_ratio', '/api/export_tex',
    '/api/ratio_table_tex', '/api/ratio_detail_tex', '/api/export_dxf',
]

# _qr_load が ValueError を送出し _error_response 経由で 500 になるルート
QR_ROUTES = ['/api/qr_info', '/api/qr_reaction', '/api/qr_drift',
             '/api/qr_center', '/api/qr_shear', '/api/qr_cb']


def test_index_returns_html(client):
    r = client.get('/')
    assert r.status_code == 200
    assert 'text/html' in r.content_type
    assert 'no-store' in r.headers.get('Cache-Control', '')


def test_file_rejects_non_whitelisted_path(client):
    r = client.get('/api/file', query_string={'path': '/no/such/file.pdf'})
    assert r.status_code == 404
    assert 'error' in r.get_json()


def test_upload_without_file_is_400(client):
    r = client.post('/api/upload')
    assert r.status_code == 400
    assert 'error' in r.get_json()


def test_upload_roundtrip(client):
    data = {'file': (io.BytesIO('ダミー'.encode('cp932')), 'smoke.mgt')}
    r = client.post('/api/upload', data=data)
    assert r.status_code == 200
    body = r.get_json()
    assert 'path' in body
    assert body['path'].endswith('smoke.mgt')


def test_bundle_rejects_bad_mode(client):
    r = client.post('/api/bundle', json={'mode': 'bad'})
    assert r.status_code == 400
    assert 'error' in r.get_json()


def test_bundle_rejects_empty_files(client):
    r = client.post('/api/bundle',
                    json={'mode': 'zip', 'orient': 'mixed', 'files': []})
    assert r.status_code == 400


def test_open_folder_rejects_unknown_path(client):
    r = client.post('/api/open_folder', json={'path': '/no/such/dir'})
    assert r.status_code != 200


@pytest.mark.parametrize('route', GUARDED_ROUTES)
def test_guarded_routes_reject_empty_json(client, route):
    r = client.post(route, json={})
    assert r.status_code == 400
    assert 'error' in r.get_json()


@pytest.mark.parametrize('route', QR_ROUTES)
def test_qr_routes_reject_empty_json_with_500(client, route):
    # 現状動作の固定 (characterization): _qr_load は空入力で ValueError を
    # 送出し、_error_response 経由で 500 になる。他ルートと同様の 400 が
    # 本来は望ましいが、Phase 1 ではアプリ本体を変更せず現状を回帰基準とする。
    r = client.post(route, json={})
    assert r.status_code == 500
    assert 'error' in r.get_json()
