"""Level 2 (tier 1): secprops.py 断面性能関数の回帰テスト。

ゴールデン値は現行実装の出力を記録したもの (characterization、2026-08 採取)。
入力は mm 単位、出力は cm 系。index: 1:a 2:Ix 3:Iy 4:ix 5:iy 6:Zx 7:Zy
8:ib 9:eta 10:Af 11:Aw。
"""
import numpy as np
import pytest

from mgtkit import secprops

H400 = [400., 200., 8., 13., 200., 13., 0.]  # H-400x200x8x13


class TestBH:
    # (index, 期待値) H-400x200x8x13
    GOLDEN = [
        (1, 81.92), (2, 23000.0), (3, 1730.0), (4, 16.7), (5, 4.6),
        (6, 1150.0), (7, 173.0), (8, 5.35), (9, 8.23), (10, 26.0),
        (11, 30.96),
    ]

    @pytest.mark.parametrize('index,expected', GOLDEN)
    def test_h400(self, index, expected):
        assert secprops.BH(H400, index) == pytest.approx(expected, rel=1e-9)

    def test_rejects_out_of_range_size(self):
        # 単位系チェック (10 <= H <= 10000 mm)
        with pytest.raises(RuntimeError):
            secprops.BH([4., 2., 0.08, 0.13, 2., 0.13, 0.], 1)


def test_bbox_400x400x12():
    box = [400., 400., 12., 12.]
    assert secprops.BBOX(box, 1) == pytest.approx(186.2, rel=1e-9)
    assert secprops.BBOX(box, 2) == pytest.approx(46800.0, rel=1e-9)
    assert secprops.BBOX(box, 6) == pytest.approx(2340.0, rel=1e-9)


def test_bp_pipe_355_6x9_5():
    p = [355.6, 9.5]
    assert secprops.BP(p, 1) == pytest.approx(103.3, rel=1e-9)
    assert secprops.BP(p, 2) == pytest.approx(15500.0, rel=1e-9)
    assert secprops.BP(p, 4) == pytest.approx(12.2, rel=1e-9)
    assert secprops.BP(p, 6) == pytest.approx(871.0, rel=1e-9)


def test_bct_cut_t():
    ct = [200., 200., 8., 12., 13.]
    assert secprops.BCT(ct, 1) == pytest.approx(39.77, rel=1e-9)
    assert secprops.BCT(ct, 2) == pytest.approx(1380.0, rel=1e-9)
    assert secprops.BCT(ct, 3) == pytest.approx(801.0, rel=1e-9)


def test_bsb_flatbar():
    fb = [100., 50.]
    assert secprops.BSB(fb, 1) == pytest.approx(50.0, rel=1e-9)
    assert secprops.BSB(fb, 2) == pytest.approx(417.0, rel=1e-9)
    assert secprops.BSB(fb, 6) == pytest.approx(83.3, rel=1e-9)


def test_bsr_roundbar():
    assert secprops.BSR([25.], 1) == pytest.approx(4.909, rel=1e-9)
    assert secprops.BSR([25.], 2) == pytest.approx(1.92, rel=1e-9)
    assert secprops.BSR([25.], 6) == pytest.approx(1.53, rel=1e-9)


class TestJisTables:
    """data/c_data.json, l_data.json (import 時ロード) 依存の関数。"""

    def test_tables_loaded_from_json(self, secprops_mod):
        assert secprops_mod.C_DATA is not None
        assert secprops_mod.L_DATA is not None

    def test_bc_channel_200x80(self, secprops_mod):
        assert secprops_mod.BC([200., 80., 7.5, 11.], 1) == pytest.approx(
            31.33, rel=1e-9)
        assert secprops_mod.BC([200., 80., 7.5, 11.], 2) == pytest.approx(
            1950.0, rel=1e-9)

    def test_bl_angle_100x100x10(self, secprops_mod):
        assert secprops_mod.BL([100., 100., 10.], 1) == pytest.approx(
            19.0, rel=1e-9)
        assert secprops_mod.BL([100., 100., 10.], 6) == pytest.approx(
            24.4, rel=1e-9)

    def test_bl_rejects_unsupported_index(self, secprops_mod):
        with pytest.raises(RuntimeError):
            secprops_mod.BL([100., 100., 10.], 2)


def test_steelform_dispatches_h():
    assert secprops.steelForm([1.] + H400, 1) == pytest.approx(
        secprops.BH(H400, 1))


def test_section_ability_code_11000_is_broken():
    # 既知の不具合の固定: code 11 (CT?) は未定義関数 BHT を参照し NameError に
    # なる (secprops.py 末尾)。MATLAB 原典と同じ挙動として保存されているため、
    # 修正した場合はこのテストを更新すること。
    sec = np.array([0.4, 0.2, 0.008, 0.013, 0.2, 0.013, 0., 11., 0.])
    with pytest.raises(NameError):
        secprops.section_ability(sec, 1)
