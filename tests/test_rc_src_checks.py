"""Level 2 (tier 1): rc_check.py / src_check.py / alst.py の純粋関数回帰テスト。

ゴールデン値は現行実装の出力を記録したもの (characterization、2026-08 採取)。
"""
import numpy as np
import pytest

from mgtkit import alst, rc_check, src_check


class TestAlstRcAij:
    def test_fc21(self):
        np.testing.assert_allclose(
            rc_check.ALST_RC_AIJ(21),
            [[7.0, 0.0, 0.7, 1.4, 2.1],
             [14.0, 0.0, 1.05, 2.1, 3.15]])

    def test_fc30(self):
        np.testing.assert_allclose(
            rc_check.ALST_RC_AIJ(30),
            [[10.0, 0.0, 0.8, 1.7, 2.55],
             [20.0, 0.0, 1.2, 2.55, 3.825]])


class TestAlstSteelbarKJ:
    def test_sd345_small_diameter(self):
        np.testing.assert_allclose(
            rc_check.ALST_steelbar_KJ([22, 345]),
            [[215.0, 195.0], [345.0, 345.0]])

    def test_sd390_d29_or_larger(self):
        np.testing.assert_allclose(
            rc_check.ALST_steelbar_KJ([32, 390]),
            [[195.0, 195.0], [390.0, 390.0]])

    def test_rejects_sd295_d19_or_larger(self):
        with pytest.raises(ValueError):
            rc_check.ALST_steelbar_KJ([19, 295])


def test_e_rc_aij_fc24():
    E, n, gamma = rc_check.E_RC_AIJ(24)
    assert E == pytest.approx(22668.9459, rel=1e-6)
    assert n == pytest.approx(15.0)
    assert gamma == pytest.approx(24.0)


class TestBarTable:
    """data/bar_table.json (遅延ロード) 依存の関数。"""

    def test_area_steelbar_d19_x3(self, rc_flags_reset):
        assert rc_check.Area_steelbar(19, 3) == pytest.approx(860.0)

    def test_outf_steelbar_jis_d19(self, rc_flags_reset):
        assert rc_check.outf_steelbar_JIS(19) == pytest.approx(21.0)

    def test_next_diameter_of_d19(self, rc_flags_reset):
        assert rc_check.bar_table_next_diameter(19) == pytest.approx(22.0)


def test_alst_s_sn400_t13():
    # ALST_S([鋼種, 板厚]) -> 3x2 [[長期ft, 長期fs],[短期ft, 短期fs],[F, F/sqrt(3)]]
    out = alst.ALST_S([400., 13.])
    np.testing.assert_allclose(
        out,
        [[156.66666667, 90.45154217],
         [235.0, 135.67731326],
         [235.0, 119.84031929]], rtol=1e-8)


def test_alst_s_sn490_t40():
    out = alst.ALST_S([490., 40.])
    np.testing.assert_allclose(
        out,
        [[216.66666667, 125.09255832],
         [325.0, 187.63883749],
         [325.0, 101.90493307]], rtol=1e-8)


class TestSrcCheckPureMath:
    def test_car3d_cubic_roots(self):
        # x^3 - 6x^2 + 11x - 6 = 0 -> 根 1, 2, 3
        roots = np.sort_complex(src_check.car3d(1., -6., 11., -6.))
        np.testing.assert_allclose(roots.real, [1., 2., 3.], atol=1e-9)
        np.testing.assert_allclose(roots.imag, [0., 0., 0.], atol=1e-9)

    def test_sd_check_d19_is_sd345(self):
        assert src_check.SD_check(19) == 345
