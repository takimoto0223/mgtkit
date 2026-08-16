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

## 撮影時の注意 (2026-08)

- 起動には「登録済み・取り込み済み」の状態が要る。無いと初回登録
  ダイアログが全画面を覆い、どのコマも同じ絵になる。`run_manager.py`
  が起動時に `settings.json` (名前 = 山田太郎、キーはダミー) と
  `stable/mgtkit/version.json` (v1.1) を用意する
- **ダイアログは Escape で閉じない** (modal=True のため)。`shoot_real.py`
  の後半 (提出確認 → β版タブ) は Escape 頼みなので、そのままでは
  ダイアログが被ったまま撮れる。連続撮影するときは「キャンセル」
  ボタンを座標クリックする形に直すこと。1 枚だけ撮り直すなら
  該当の分岐だけ実行するのが確実
- 生成物 (`real_*.png`) と `home/` は .gitignore 済み。採用する図だけ
  `manager/readme/src/img/` へコピーする
