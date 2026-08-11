# -*- coding: utf-8 -*-
"""Claude API の利用量・料金の記録と集計 (この PC のマネージャー経由分).

- 保存先: <install_root>/usage.json (settings.json と同じくローカルのみ。
  提出対象外で、GitHub には送らない)
- claude_helper が API 応答を受け取るたびに record() で日別に積算する
- 料金は config.json manager.api_pricing の単価 ($/MTok) から計算した
  「目安」。正確な請求額は Anthropic Console で確認する
- 単価は記録時点のもので日別に確定額 (usd) として保存するため、
  後から単価設定を変えても過去の記録は変わらない
"""
import datetime
import json
import logging
import os
import threading

from . import paths

log = logging.getLogger(__name__)

_LOCK = threading.Lock()

# claude-opus-5 の既定単価 ($/MTok)。config manager.api_pricing で上書き可
DEFAULT_PRICING = {'input_per_mtok': 5.0, 'output_per_mtok': 25.0}
DEFAULT_USD_JPY = 150.0


def usage_path(config=None):
    return os.path.join(paths.install_root(config), 'usage.json')


def _mgr(config):
    return (config or paths.load_config()).get('manager') or {}


def pricing(config=None):
    """$/MTok 単価。キャッシュ書き込みは入力の 1.25 倍、読み出しは 0.1 倍."""
    p = dict(DEFAULT_PRICING)
    p.update(_mgr(config).get('api_pricing') or {})
    return p


def usd_jpy_rate(config=None):
    try:
        return float(_mgr(config).get('usd_jpy_rate') or DEFAULT_USD_JPY)
    except (TypeError, ValueError):
        return DEFAULT_USD_JPY


def cost_usd(in_tok, out_tok, cache_create=0, cache_read=0, config=None):
    p = pricing(config)
    return ((in_tok + 1.25 * cache_create + 0.1 * cache_read)
            * p['input_per_mtok']
            + out_tok * p['output_per_mtok']) / 1e6


def _load(config=None):
    try:
        with open(usage_path(config), encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {'days': {}}
    if not isinstance(data, dict) or not isinstance(data.get('days'), dict):
        return {'days': {}}
    return data


def _tok(usage_obj, name):
    """SDK の usage オブジェクトでも dict でも読めるように."""
    if isinstance(usage_obj, dict):
        v = usage_obj.get(name)
    else:
        v = getattr(usage_obj, name, 0)
    return int(v or 0)


def record(usage_obj, config=None):
    """API 応答 1 回分の利用量を日別に積算する。決して例外を投げない."""
    try:
        in_tok = _tok(usage_obj, 'input_tokens')
        out_tok = _tok(usage_obj, 'output_tokens')
        cc = _tok(usage_obj, 'cache_creation_input_tokens')
        cr = _tok(usage_obj, 'cache_read_input_tokens')
        usd = cost_usd(in_tok, out_tok, cc, cr, config)
        today = datetime.date.today().isoformat()
        with _LOCK:
            data = _load(config)
            day = data['days'].setdefault(
                today, {'in': 0, 'out': 0, 'calls': 0, 'usd': 0.0})
            day['in'] += in_tok + cc + cr
            day['out'] += out_tok
            day['calls'] += 1
            day['usd'] = round(day['usd'] + usd, 6)
            path = usage_path(config)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
        return usd
    except Exception:
        log.debug('API 利用量の記録に失敗しました', exc_info=True)
        return None


def summary(config=None, n_days=30, today=None):
    """グラフ表示用の集計.

    戻り値: dict(
      days=[(date_iso, usd, in, out, calls)] × n_days (古→新、無い日は 0),
      month, month_usd, total_usd, total_in, total_out, total_calls)
    """
    data = _load(config)
    today = today or datetime.date.today()
    days = []
    for i in range(n_days - 1, -1, -1):
        d = (today - datetime.timedelta(days=i)).isoformat()
        rec = data['days'].get(d) or {}
        days.append((d, float(rec.get('usd') or 0.0),
                     int(rec.get('in') or 0), int(rec.get('out') or 0),
                     int(rec.get('calls') or 0)))
    month = today.strftime('%Y-%m')
    month_usd = sum(float((r or {}).get('usd') or 0.0)
                    for d, r in data['days'].items()
                    if d.startswith(month))
    return {
        'days': days,
        'month': month,
        'month_usd': month_usd,
        'total_usd': sum(float((r or {}).get('usd') or 0.0)
                         for r in data['days'].values()),
        'total_in': sum(int((r or {}).get('in') or 0)
                        for r in data['days'].values()),
        'total_out': sum(int((r or {}).get('out') or 0)
                         for r in data['days'].values()),
        'total_calls': sum(int((r or {}).get('calls') or 0)
                           for r in data['days'].values()),
    }
