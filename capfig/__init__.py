# -*- coding: utf-8 -*-
"""断面符号図 (MIDAS風) — 軸組図・伏図を MIDAS の画面キャプチャ風に出力する.

審査機関から「MIDAS 上で断面名を表示してキャプチャした伏図・軸組図」を
求められたときの提出用。モデル図タブ (draw_model.py) の断面符号図とは
別ツールで、次の点を MIDAS の表示に寄せている。

    ・部材の色は mgt の *SECT-COLOR / *THIK-COLOR (MIDAS の断面色) を使う
    ・部材は断面のせい・幅で塗る (MIDAS の隠線表示。オフセット記号・β角を反映)
    ・断面名は部材の横に水平に書く (梁は上、柱・縦向きの部材は右)
    ・節点を点で描き、通り芯を一点鎖線、階レベルを破線で描く
    ・左下に座標軸 (X/Y/Z)、右下に作図情報、右側に断面一覧 (凡例)

**既存モジュールは変更していない。** app.py への追加は Blueprint 登録のみ。

  draw.py    作図 (matplotlib)
  routes.py  Flask Blueprint (/api/capfig_plot, /api/capfig_preview)
"""
