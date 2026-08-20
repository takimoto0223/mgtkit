# -*- coding: utf-8 -*-
"""荷重分布図 (load map) — 荷重ケースごとの立体図を PDF で出力する.

MIDAS Gen NX の荷重入力画面をキャプチャする代わりに、mgt / mgtx から
同じ内容を作図する。構造計算書の「エリアごとの単位荷重の荷重分布図」用。

    ・床荷重 (*FLOORLOAD)  … 領域を塗り、"(3)-RF2(One)" のラベルを引き出す
    ・面荷重 (*PRESSURE)   … 対象プレート要素を塗り、要素ごとに矢印を立てる
    ・節点荷重 (*CONLOAD)  … 矢印と値

**既存モジュールは変更していない。** このパッケージだけで完結し、
app.py への追加も Blueprint の登録2行のみ。

  mgt_loads.py  荷重ブロックのパーサ (mgt.py に無いため新規)
  draw.py       matplotlib での作図 (draw_model.py の作法に合わせる)
  routes.py     Flask Blueprint (/api/loadmap_cases, /api/plot_loadmap)
"""
