"""manager/usage.py (API 利用量の記録・集計) のテスト。"""
import datetime
import json
from types import SimpleNamespace

from manager import usage


def _config(tmp_path, **mgr):
    m = {'install_root': str(tmp_path)}
    m.update(mgr)
    return {'manager': m}


class TestPricing:
    def test_default(self, tmp_path):
        p = usage.pricing(_config(tmp_path))
        assert p == {'input_per_mtok': 5.0, 'output_per_mtok': 25.0}

    def test_config_override(self, tmp_path):
        cfg = _config(tmp_path, api_pricing={'input_per_mtok': 3.0,
                                             'output_per_mtok': 15.0})
        p = usage.pricing(cfg)
        assert p['input_per_mtok'] == 3.0
        assert p['output_per_mtok'] == 15.0

    def test_usd_jpy_rate(self, tmp_path):
        assert usage.usd_jpy_rate(_config(tmp_path)) == 150.0
        assert usage.usd_jpy_rate(
            _config(tmp_path, usd_jpy_rate=145)) == 145.0
        assert usage.usd_jpy_rate(
            _config(tmp_path, usd_jpy_rate='abc')) == 150.0

    def test_cost_usd(self, tmp_path):
        cfg = _config(tmp_path)
        # 100万トークン入力 = $5、100万トークン出力 = $25
        assert usage.cost_usd(1_000_000, 0, config=cfg) == 5.0
        assert usage.cost_usd(0, 1_000_000, config=cfg) == 25.0
        # キャッシュ書き込みは入力の 1.25 倍、読み出しは 0.1 倍
        assert usage.cost_usd(0, 0, cache_create=1_000_000,
                              config=cfg) == 6.25
        assert usage.cost_usd(0, 0, cache_read=1_000_000,
                              config=cfg) == 0.5


class TestRecord:
    def test_accumulates_same_day(self, tmp_path):
        cfg = _config(tmp_path)
        u = SimpleNamespace(input_tokens=100_000, output_tokens=10_000)
        usd = usage.record(u, cfg)
        assert usd == 100_000 * 5 / 1e6 + 10_000 * 25 / 1e6
        usage.record({'input_tokens': 50_000, 'output_tokens': 5_000}, cfg)

        with open(usage.usage_path(cfg), encoding='utf-8') as f:
            data = json.load(f)
        today = datetime.date.today().isoformat()
        day = data['days'][today]
        assert day['in'] == 150_000
        assert day['out'] == 15_000
        assert day['calls'] == 2

    def test_cache_tokens_counted(self, tmp_path):
        cfg = _config(tmp_path)
        u = SimpleNamespace(input_tokens=0, output_tokens=0,
                            cache_creation_input_tokens=1_000_000,
                            cache_read_input_tokens=0)
        assert usage.record(u, cfg) == 6.25

    def test_never_raises(self, tmp_path, monkeypatch):
        def boom(config=None):
            raise OSError('disk')
        monkeypatch.setattr(usage, 'usage_path', boom)
        assert usage.record({'input_tokens': 1}, _config(tmp_path)) is None


class TestSummary:
    def test_empty(self, tmp_path):
        s = usage.summary(_config(tmp_path))
        assert len(s['days']) == 30
        assert s['month_usd'] == 0.0
        assert s['total_usd'] == 0.0
        assert s['total_calls'] == 0

    def test_month_and_grand_total(self, tmp_path):
        cfg = _config(tmp_path)
        data = {'days': {
            '2026-08-11': {'in': 1, 'out': 1, 'calls': 2, 'usd': 0.30},
            '2026-08-01': {'in': 1, 'out': 1, 'calls': 1, 'usd': 0.20},
            '2026-07-05': {'in': 1, 'out': 1, 'calls': 3, 'usd': 1.00},
        }}
        with open(usage.usage_path(cfg), 'w', encoding='utf-8') as f:
            json.dump(data, f)
        s = usage.summary(cfg, today=datetime.date(2026, 8, 11))
        assert s['month'] == '2026-08'
        assert abs(s['month_usd'] - 0.50) < 1e-9
        # 通算は 30 日窓の外 (7/5) も含む
        assert abs(s['total_usd'] - 1.50) < 1e-9
        assert s['total_calls'] == 6
        # days は古→新 30 件、当日が末尾
        assert len(s['days']) == 30
        assert s['days'][-1][0] == '2026-08-11'
        assert s['days'][-1][1] == 0.30
        # 記録のない日は 0 で埋まる
        assert s['days'][-2] == ('2026-08-10', 0.0, 0, 0, 0)

    def test_broken_file(self, tmp_path):
        cfg = _config(tmp_path)
        (tmp_path / 'usage.json').write_text('not json', encoding='utf-8')
        s = usage.summary(cfg)
        assert s['total_usd'] == 0.0
