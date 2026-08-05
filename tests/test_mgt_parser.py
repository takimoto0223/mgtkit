"""Level 2 (tier 3): mgt.py パーサの回帰テスト (合成テキスト使用)。

実 mgt ファイルはリポジトリに含めない方針のため、IO 境界の mgt._lines を
monkeypatch して合成テキストでパーサを駆動する。

パーサ仕様 (MATLAB 移植) の注意点:
- セクションは「マーカー行 + offset」から始まり、次に '*' を含む行の
  2 行手前で終わる。つまり次のマーカー直前の 1 行は必ず捨てられるため、
  fixture では各セクション末尾に空行を 1 行入れること。
- *NODE は offset 2 (ヘッダ 1 行)、*ELEMENT は offset 5 (ヘッダ 4 行)。
"""
import numpy as np
import pytest

from mgtkit import mgt


def _patch_lines(monkeypatch, text):
    lines = [l + '\n' for l in text.splitlines()]
    monkeypatch.setattr(mgt, '_lines', lambda filename: lines)


MGT_TEXT = """*UNIT
; kN, m
*NODE    ; Nodes
; iNO, X, Y, Z
   1, 0, 0, 0
   2, 6, 0, 0
   3, 6, 6, 3.5

*ELEMENT    ; Elements
; iEL, TYPE, iMAT, iPRO, iN1, iN2, ANGLE, iSUB
; header2
; header3
; header4
   1, BEAM, 1, 2, 1, 2, 0, 0
   2, BEAM, 1, 3, 2, 3, 90, 0
   3, WALL, 1, 1, 1, 2, 0, 0

*ENDDATA
"""


class TestMgtopenNode:
    def test_parses_nodes(self, monkeypatch):
        _patch_lines(monkeypatch, MGT_TEXT)
        node = mgt.mgtopen_node('dummy.mgt')
        np.testing.assert_allclose(node, [
            [1., 0., 0., 0.],
            [2., 6., 0., 0.],
            [3., 6., 6., 3.5],
        ])

    def test_missing_marker_raises_japanese_error(self, monkeypatch):
        _patch_lines(monkeypatch, '*UNIT\n; kN, m\n*ENDDATA\n')
        with pytest.raises(ValueError, match='ENDDATA'):
            mgt.mgtopen_node('dummy.mgt')


class TestMgtopenElement:
    def test_parses_beam_elements_and_skips_wall(self, monkeypatch):
        _patch_lines(monkeypatch, MGT_TEXT)
        element = mgt.mgtopen_element('dummy.mgt')
        # 列: [要素番号, 材料番号, 断面番号, 節点i, 節点j, β角度]
        # WALL 要素 (3) は除外される
        np.testing.assert_allclose(element, [
            [1., 1., 2., 1., 2., 0.],
            [2., 1., 3., 2., 3., 90.],
        ])


def test_mgtopen_thickness_absent_returns_empty(monkeypatch):
    # *THICKNESS が無い場合は guard (*ENDDATA) 到達で空配列を返す仕様
    _patch_lines(monkeypatch, '*NODE\n; h\n 1, 0, 0, 0\n\n*ENDDATA\n')
    out = mgt.mgtopen_thickness('dummy.mgt')
    assert isinstance(out, np.ndarray)
    assert out.size == 0
