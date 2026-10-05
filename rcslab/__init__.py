# -*- coding: utf-8 -*-
"""RCスラブの検討書 — mgt と板要素断面力テーブル (txt) から応力図と検定を作る.

MIDAS の画面キャプチャを使わずに、次の2図を検討対象 (厚さID または
グループ) ごとに作図し、断面検定のページと合わせて検討書 (A4 PDF) にする。

    曲げモーメント : 板要素断面力の Moment-Vector (主曲げ Mmax/Mmin の矢印)
    せん断力       : 板要素断面力の Vmax (要素中心値のコンター)

  fromfile.py mgt と txt の読み込み (検討対象の候補も)
  data.py    作図データの保存・読込
  draw.py    応力図と検討書 PDF (matplotlib)
  calc.py    断面検定 (RC規準2010)
  sheet.py   検定ページ
  routes.py  Flask Blueprint

**既存モジュールは変更していない。** app.py への追加は Blueprint 登録のみ。
"""
