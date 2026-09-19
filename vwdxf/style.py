# -*- coding: utf-8 -*-
"""レイヤ・線種・用紙・縮尺・DXF の書式など、作図と出力で共通の決まり.

値は mgtkit の構造図β (dxf_struct.py) と、試作版で決めた改良 (通り芯・木造・VW10J 向け書式) のもの。
寸法の「紙面 mm」は縮尺を掛けて実寸にする。図形の座標は原寸 mm。
"""
import re as _re_mod

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

# レイヤ = Vectorworks 10J のクラス (事務所のペン S-<色><番号>[破線|芯線])。
# 割り当ては struct_cad_py の style.py と同じ (2026-09-19 ユーザー指定)。材質では分けない。
#   柱=S-Cyan20 / 梁・ブレース=S-Yellow20 / 柱断面=S-Green30 / 梁断面=S-Magenda30 /
#   下柱×=S-Cyan20 / 文字・符号・罫線・鉄筋=S-Red09 / 通り芯=S-Gray09芯線 / 壁=S-Orange40
LAYER_MEMBER = {   # 部材の外形 (矩形・線). キーは layer_role()
    'column': ('S-Cyan20', 4),
    'beam': ('S-Yellow20', 2),
}
LAYER_SECTION = {  # 断面記号 (ブロック INSERT)・部材リストの断面外形
    'column': ('S-Green30', 3),
    'beam': ('S-Magenda30', 6),
}
LAYER_DOWN_COLUMN = ('S-Cyan20', 4)   # 伏図の下柱 (×印)
LAYER_TRUSS = ('S-Gray09', 8)         # トラス系の要素 (TRUSS / TENSTR / COMPTR / CABLE)
LAYER_TEXT = ('S-Red09', 1)     # 符号・文字・リストの罫線・鉄筋
LAYER_TITLE = ('S-Red09', 1)


def layer_role(m):
    """部材 → レイヤの役割 'column' / 'beam' (梁・ブレースは 'beam'. struct_cad と同じ)."""
    return 'column' if m.get('kind') == 'column' else 'beam'


# DXF の書式 (Vectorworks 10J で文字が潰れないように。2026-09-19)
#   R2010 + Standard/txt (ezdxf 既定) では VW10J で文字が重なって潰れた。
#   VW10J 自身が書き出す DXF と同じ R2000 + Shift-JIS (ANSI_932) + フォント欄に表示名、にそろえる。
#   試験ファイルは works/mgtkit-dxf-kaizen/font_test/ (A〜F)。ここは E 案
DXF_VERSION = 'R2000'
DXF_CODEPAGE = 'ANSI_932'          # None なら ezdxf 既定 (日本語は \U+XXXX で書かれる)
TEXT_STYLE = ('MSUIGothic', 'MS UI Gothic')   # (スタイル名 [半角], フォント欄 code 3)
TEXT_STYLE_FONT_FILE = None        # 'msgothic.ttc' にするとファイル名 + XDATA の書体名で書く (F 案)
LAYER_GRID = ('S-Gray09芯線', 8)  # 通り芯 (一点鎖線・グレー)。丸記号と通り名は LAYER_TEXT
LAYER_WALL = ('S-Orange40', 30)   # 木造柱伏図の立上り壁 (struct_cad と同じ)
LAYER_WALL_ELEV = ('S-Gray09', 8)  # 軸組図の壁・面要素 (struct_cad と同じ)
DASHED_SUFFIX = '破線'            # 破線のクラス: レイヤ名 + '破線' (木造柱伏図の梁・細いブレース = S-Yellow20破線)
DASHED_PAPER_MM = (3.0, -1.5)  # 破線 (線, 空き) 紙面mm。木造柱伏図の梁・細い引張ブレース

# 木造の伏図2枚 (struct_cad の mode=wood と同じ規則)
#   最上階以外: 梁伏図 (梁=実線+梁符号 / 柱=断面のみ) と 柱伏図 (梁=破線・符号なし /
#               柱=符号つき / その床から立ち上がる壁) の2枚
#   最上階   : 伏図1枚 (梁符号・柱符号とも)
#   柱は「その床から上へ伸びる柱」。下の柱は、ピンで分かれるとき・上に柱が無いときだけ×印
WALL_INCLINE_DEG = 60.0   # これ以上傾いた面要素を壁とみなす
WALL_HATCH_PAPER_MM = 2.5  # 木造軸組図の壁ハッチ (ANSI31) の線間隔 (紙面mm)
ANSI31_SPACING = 3.175     # ezdxf の ANSI31 の線間隔 (尺度1のとき)

# 通り芯 (寸法はすべて紙面mm。縮尺を掛けて実寸にする)
GRID_BUBBLE_R_RATIO = 4.0 / 3.0  # 丸記号の半径 = 通り名の文字高さ × これ (struct_cad と同じ)
GRID_DASHDOT_PAPER_MM = (8.0, -1.0, 0.0, -1.0)  # 一点鎖線 (線, 空き, 点, 空き)
# 軸組図のレベル線 (通り芯と同じレイヤ・線種。▽記号とレベル名はまだ描かない)
LEVEL_FLAT_TOL = 0.3      # 節点Zの広がりがこれ未満のフロアを「水平な床」とみなす [m]

# 文字の大きさは紙面ポイント (VW10J の文字は pt)。既定は事務所の実図面の実測値
# (通り名・見出しは kb guides/vw10j-vectorscript-guide.md の実測 18pt、符号はユーザー指定の 12pt)
PT_MM = 25.4 / 72.0          # 1pt [紙面 mm]
TEXT_PT_DEFAULT = 12.0       # 部材符号・部材リストの文字 (ユーザー指定 2026-09-19。実図面の実測は 14pt)
GRID_PT_DEFAULT = 18.0       # 通り名・図の見出し

SCALE_SERIES = [20, 25, 30, 40, 50, 60, 75, 100, 150, 200, 250, 300,
                400, 500, 600, 750, 1000]

PAPER_MM = {'A1': (841.0, 594.0), 'A2': (594.0, 420.0),
            'A3': (420.0, 297.0), 'A4': (297.0, 210.0)}
PAPER_MARGIN_MM = 15.0

_NAME_DIM_RE = _re_mod.compile(r'(\d+)\s*[xX×]\s*(\d+)')


LAYER_HOOP = ('S-Cyan20', 4)      # 部材リストの帯筋・あばら筋
DEFAULT_LIMIT_SEC_NO = 10000       # これ以上の断面番号はダミー (描かない)

__all__ = [
    'LAYER_MEMBER',
    'LAYER_SECTION',
    'LAYER_DOWN_COLUMN',
    'LAYER_TRUSS',
    'LAYER_WALL_ELEV',
    'layer_role',
    'LAYER_TEXT',
    'LAYER_TITLE',
    'DXF_VERSION',
    'DXF_CODEPAGE',
    'TEXT_STYLE',
    'TEXT_STYLE_FONT_FILE',
    'LAYER_GRID',
    'LAYER_WALL',
    'DASHED_SUFFIX',
    'DASHED_PAPER_MM',
    'WALL_INCLINE_DEG',
    'WALL_HATCH_PAPER_MM',
    'ANSI31_SPACING',
    'GRID_BUBBLE_R_RATIO',
    'PT_MM',
    'TEXT_PT_DEFAULT',
    'GRID_PT_DEFAULT',
    'GRID_DASHDOT_PAPER_MM',
    'LEVEL_FLAT_TOL',
    'SCALE_SERIES',
    'PAPER_MM',
    'PAPER_MARGIN_MM',
    '_NAME_DIM_RE',
    'LAYER_HOOP',
    'DEFAULT_LIMIT_SEC_NO',
]
