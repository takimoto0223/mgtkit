# 使い方ガイドの実画面撮影ハーネス

使い方ガイド (`manager/readme/`) の図は、**本物のマネージャー UI** を
架空データ (偽名: yamada-taro / suzuki-ichiro / sato-jiro / tanaka-hanako)
で起動して撮影した実画面。GitHub には一切つながない。

- `bin/gh` — 偽 gh コマンド。承認待ち PR・リリース・メンバー等の
  架空データを返す (PATH の先頭に入れて使う)
- `run_manager.py` — 偽 gh + 架空の install_root で manager/main.py を
  そのまま flet Web モードで起動する (UI コードは無改変)。
  提出確認ダイアログ用に prepare_submission だけ canned 値を返す
- `shoot_real.py` — Playwright で各画面を撮影する。flutter は canvas
  描画のため座標クリック。CDN (canvaskit / rive / フォント) は
  ローカル資産で差し替える。フォントは Noto Sans JP TTF を用意して
  fonts.gstatic の要求に応えると実機に近い見た目になる

パス (rive・フォントの置き場所) は撮影環境に合わせて調整すること。
