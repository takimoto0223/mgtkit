"""Level 2 (tier 0): util.py の純粋関数の回帰テスト。

期待値は手計算で導出可能なもの + 現行実装の出力を記録したもの
(characterization、2026-08 採取)。MATLAB からの移植仕様
(1-based → 0-based / 未検出 -1 等) を固定する。
"""
import numpy as np
import pytest

from mgtkit import util


class TestFindIndex:
    def test_scalar_hit_returns_zero_based(self):
        assert util.find_index(np.array([10., 20., 30.]), 20.) == 1

    def test_scalar_miss_returns_minus_one(self):
        assert util.find_index(np.array([10., 20., 30.]), 99.) == -1

    def test_vector_search(self):
        out = util.find_index(np.array([10., 20., 30.]),
                              np.array([30., 10.]))
        np.testing.assert_array_equal(out, [2, 0])


def test_find_zero_index():
    np.testing.assert_array_equal(
        util.find_zero_index(np.array([1., 0., 2., 0.])), [1, 3])


def test_find_index_rough_nearest():
    assert util.find_index_rough(np.array([1., 5., 9.]), 6.) == 1


class TestGetByto:
    def test_range_with_step(self):
        np.testing.assert_array_equal(util.get_byto('1to9by2'),
                                      [1., 3., 5., 7., 9.])

    def test_plain_number(self):
        np.testing.assert_array_equal(util.get_byto('5'), [5.])


def test_matlab_round_half_away_from_zero():
    # Python 組込み round (偶数丸め) と異なることを固定する
    assert util._matlab_round(0.5) == 1.0
    assert util._matlab_round(-0.5) == -1.0
    assert util._matlab_round(2.5) == 3.0
    assert util._matlab_round(-2.5) == -3.0
    assert util._matlab_round(1.4) == 1.0


def test_excelround_significant_digits():
    assert util.excelround(123.456, 2) == pytest.approx(120.0)
    assert util.excelround(0.0012345, 3) == pytest.approx(0.00123)


class TestStringHelpers:
    def test_space_erace_removes_all_spaces(self):
        assert util.space_erace(' a b ') == 'ab'

    def test_space_erace_passes_non_string(self):
        assert util.space_erace(123) == 123

    def test_skip_erace_collapses_spaces(self):
        assert util.skip_erace('a   b  c') == 'a b c'

    def test_deblank(self):
        assert util._deblank('ab  ') == 'ab'

    def test_pick_text(self):
        assert util.pick_text('abc, def, ghi', ',', 1) == ' def, ghi'


def test_str2double():
    assert util._str2double('1.5') == 1.5
    assert np.isnan(util._str2double('x'))


def test_str2num():
    assert util._str2num(' 1 2 3 ') == [1.0, 2.0, 3.0]
    assert util._str2num('abc') is None


def test_colon():
    np.testing.assert_array_equal(util._colon(1, 2, 7), [1., 3., 5., 7.])
    assert util._colon(1, 0, 5).size == 0


def test_doublecheck_dedup_sort():
    np.testing.assert_array_equal(
        util.doublecheck(np.array([3., 1., 3., 2., 1.])), [1., 2., 3.])


def test_doublecheck2_rowwise_dedup():
    out = util.doublecheck2(np.array([[1., 2.], [1., 2.], [0., 5.]]))
    np.testing.assert_array_equal(out, [[0., 5.], [1., 2.]])
