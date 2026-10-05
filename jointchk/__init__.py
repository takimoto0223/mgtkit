# -*- coding: utf-8 -*-
"""木造梁接合部の検定 — ピン接合端の軸力・せん断力を接合金物の耐力で検定する.

mgt の *FRAME-RLS (梁端解放) に '1' を含む端部 (=ピン接合) を持つ梁要素を
検討対象として拾い、beam_stress.txt のピン端の軸力 Fx とせん断力 Fz を、
画面から手入力した接合金物の許容耐力 (引張・せん断、長期・短期) で検定する。

  ・対象部材  … 画面のチェックボックスで選んだ断面 (符号) に属し、
                 i端/j端の解放フラグに '1' を含む梁要素のピン端
  ・応力      … beam_stress.txt (断面算定と同じもの) の i端/j端の行。
                 荷重ケースは断面算定タブと同じ種別指定
                 (L=長期 / H=水平のみ→長期と±組合せ / S=短期組合せ済み)
  ・検定      … 引張: N/Ta (N>0 の引張時のみ。圧縮はめり込み・接触で
                 伝達されるものとし金物検定の対象外)
                 せん断: |Q|/Qa
  ・計算書    … matplotlib による A4 縦の PDF (wallqty と同じ作法)

**既存モジュールは変更していない。** このパッケージだけで完結し、
app.py への追加は Blueprint の登録のみ (loadmap / wallqty と同じ作法)。

  calc.py     対象部材の抽出・検定ケースの構成・検定
  report.py   検定書 PDF (wallqty/report.py の Page/table を流用)
  routes.py   Flask Blueprint (/api/jointchk_read, /api/jointchk_run)
"""
