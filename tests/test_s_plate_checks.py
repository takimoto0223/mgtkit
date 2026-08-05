"""Level 2 (tier 2): s_check.py / plate_check.py の末端検定関数の回帰テスト。

ゴールデン値は現行実装の出力を記録したもの (characterization、2026-08 採取)。
応力は kN・m 系、列 = [N, Fy, Fz, My, Mz]、3 行 = i端/中央/j端。
戻り値 ratio は n×4 [組合せ応力度比, 強軸せん断比, 弱軸せん断比, 予備]。
"""
import numpy as np
import pytest

from mgtkit import plate_check, s_check

H400 = [400., 200., 8., 13., 200., 13., 0.]
LENGTH = [4., 4., 4.]  # [lky, lkz, lb] (m)
STRESS = np.array([
    [50., 10., 80., 120., 5.],
    [50., 10., 20., 60., 2.],
    [50., 10., 80., -100., -5.],
])


class TestHAnalysis:
    def test_long_term_sn400(self):
        ratio = s_check.H_analysis(H400, LENGTH, STRESS, 1, [1., 400.],
                                   0, [0., 0.], 1)
        np.testing.assert_allclose(ratio, [
            [0.92821535, 0.28567554, 0.02126085, 0.0],
            [0.46513906, 0.07141889, 0.02126085, 0.0],
            [0.81075242, 0.28567554, 0.02126085, 0.0],
        ], rtol=1e-6)

    def test_short_term_sn400(self):
        ratio = s_check.H_analysis(H400, LENGTH, STRESS, 10, [1., 400.],
                                   0, [0., 0.], 1)
        np.testing.assert_allclose(ratio, [
            [0.61881023, 0.19045036, 0.0141739, 0.0],
            [0.31009271, 0.04761259, 0.0141739, 0.0],
            [0.54050161, 0.19045036, 0.0141739, 0.0],
        ], rtol=1e-6)

    def test_long_term_f_value_direct(self):
        # SN=[2, F] は F 値直接指定 (BCP/BCR 系)
        ratio = s_check.H_analysis(H400, LENGTH, STRESS, 1, [2., 295.],
                                   0, [0., 0.], 1)
        np.testing.assert_allclose(ratio, [
            [0.74788619, 0.22757204, 0.01693661, 0.0],
            [0.37476471, 0.05689301, 0.01693661, 0.0],
            [0.65290396, 0.22757204, 0.01693661, 0.0],
        ], rtol=1e-6)

    def test_result_is_finite(self):
        # _matlabenv により 0 除算は例外でなく Inf/NaN になる設計。
        # 正常入力では有限値のみとなることを固定する。
        ratio = s_check.H_analysis(H400, LENGTH, STRESS, 1, [1., 400.],
                                   0, [0., 0.], 1)
        assert np.isfinite(ratio).all()


class TestSPlateRatioAnalysis:
    def test_long_term(self):
        ratio, maxratios, _ = plate_check.S_plate_ratio_analysis(
            12., 100., 1, [1., 400.], 1, [0., 0.], [], ['L'], 5)
        assert ratio == pytest.approx(0.0921303621, rel=1e-8)
        assert maxratios[0] == 5
        assert maxratios[1] == pytest.approx(ratio)

    def test_short_term_uses_1_5x_allowable(self):
        ratio, _, _ = plate_check.S_plate_ratio_analysis(
            12., 100., 10, [1., 400.], 1, [0., 0.], [], ['S'], 5)
        assert ratio == pytest.approx(0.0614202414, rel=1e-8)

    def test_rejects_bad_timecase(self):
        with pytest.raises(ValueError):
            plate_check.S_plate_ratio_analysis(
                12., 100., 5, [1., 400.], 1, [0., 0.], [], ['L'], 5)


class TestSCheckHelpers:
    def test_num2str_default_format(self):
        assert s_check._num2str(1.23456) == '1.2346'
        assert s_check._num2str(100.0) == '100'

    def test_maxratio_index(self):
        # 現行実装の出力を固定 (1-based 行番号を返す)
        ratio = np.array([[0.1, 0.2, 0.3, 0.15],
                          [0.5, 0.1, 0.2, 0.4]])
        assert s_check._maxratio_index(ratio) == 2
