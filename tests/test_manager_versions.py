"""manager/versions.py のテスト。"""
import pytest

from manager import versions


class TestParseVersion:
    def test_stable(self):
        assert versions.parse_version('v1.2') == (1, 2, None)

    def test_beta(self):
        assert versions.parse_version('v1.3-beta.4') == (1, 3, 4)

    @pytest.mark.parametrize('bad', ['1.2', 'v1', 'v1.2.3', 'v1.2-rc.1',
                                     'v1.2-beta', '', 'main'])
    def test_invalid(self, bad):
        with pytest.raises(ValueError):
            versions.parse_version(bad)
        assert not versions.is_valid_version(bad)


def test_is_prerelease_tag():
    assert versions.is_prerelease_tag('v1.3-beta.1')
    assert not versions.is_prerelease_tag('v1.3')


class TestCompareVersions:
    @pytest.mark.parametrize('a,b,expected', [
        ('v1.2', 'v1.2', 0),
        ('v1.3', 'v1.2', 1),
        ('v1.2', 'v1.10', -1),          # 数値比較 (辞書順でない)
        ('v2.0', 'v1.9', 1),
        ('v1.3', 'v1.3-beta.5', 1),     # 正式版 > β版 (同一 X.Y)
        ('v1.3-beta.1', 'v1.3-beta.2', -1),
        ('v1.3-beta.1', 'v1.2', 1),     # 次版のβは前の正式版より新しい
    ])
    def test_ordering(self, a, b, expected):
        assert versions.compare_versions(a, b) == expected
        assert versions.compare_versions(b, a) == -expected


class TestVersionJson:
    def test_roundtrip(self, tmp_path):
        versions.write_version_json(str(tmp_path), 'v1.2', 'abc123',
                                    '2026-08-06')
        info = versions.read_version_json(str(tmp_path))
        assert info == {'version': 'v1.2', 'commit': 'abc123',
                        'distributed_at': '2026-08-06'}

    def test_missing_returns_none(self, tmp_path):
        assert versions.read_version_json(str(tmp_path)) is None

    def test_broken_returns_none(self, tmp_path):
        (tmp_path / 'version.json').write_text('{oops', encoding='utf-8')
        assert versions.read_version_json(str(tmp_path)) is None

    def test_wrong_shape_returns_none(self, tmp_path):
        (tmp_path / 'version.json').write_text('[1,2]', encoding='utf-8')
        assert versions.read_version_json(str(tmp_path)) is None
