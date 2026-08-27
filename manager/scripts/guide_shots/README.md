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
  fonts.gstatic の要求に応えると実機に近い見た目になる。
  撮影後、下の余白 (地の色だけの行) は自動で切り詰める

パス (rive・フォントの置き場所) は撮影環境に合わせて調整すること。

## 使い方

```
python run_manager.py &                  # 架空データで UI を起動 (:8571)
python shoot_real.py                     # 既定の一式を撮る
python shoot_real.py submit submit_dialog   # 1 枚ずつ撮り直す
```

撮影名は `launch` `history` `usage` `submit` `submit_dialog`
`submit_manual` `submit_review` `beta` `feedback` `firstrun`。

**`firstrun` だけは未登録の状態が要る**ので、`GUIDE_SHOTS_FIRSTRUN=1`
を付けて `run_manager.py` を起動し直してから撮る (この環境変数が
あるときは `settings.json` を消してから起動する)。

## 撮影時の注意 (2026-08)

- 起動には「登録済み・取り込み済み」の状態が要る。無いと初回登録
  ダイアログが全画面を覆い、どのコマも同じ絵になる。`run_manager.py`
  が起動時に `settings.json` (名前 = 山田太郎、キーはダミー) と
  `stable/mgtkit/version.json` (v1.1) を用意する
- **ダイアログは Escape で閉じない** (modal=True のため)。このため
  `shoot_real.py` は**1 枚ごとに新しいページを開いて撮る**
  (前の画面を閉じて回らない)。撮影は遅くなるが、ダイアログが被ったまま
  撮れる事故が起きない
- **UI のタブの並びを変えたら、座標定数 (`TAB_*` ほか) と図の撮り直しを
  同じ PR でやること**。図だけ古いタブ順で残ると、章ごとに並びが違う
  ガイドになる (2026-08 の #134 で実際に起きた)
- 生成物 (`real_*.png`) と `home/` は .gitignore 済み。採用する図だけ
  `manager/readme/src/img/` へコピーする
- `real_diff.png` (差分ビューワ) はブラウザで開く別画面なのでこの
  ハーネスでは撮らない

## 資産の置き場所 (環境変数で上書き)

canvaskit・rive・フォントの場所は撮影環境で違うため、既定値を
環境変数で上書きできる:

- `GUIDE_SHOTS_CK` — canvaskit のディレクトリ
  (例: `<venv>/lib/python3.11/site-packages/flet_web/web/canvaskit`)。
  flet 0.86 では canvaskit は本体に同梱されず **`flet-web` パッケージが
  別途必要** (`pip install flet-web`)。入れないと画面が真っ白になる
- `GUIDE_SHOTS_RIVE` — rive の package ディレクトリ
- `GUIDE_SHOTS_FONT400` / `GUIDE_SHOTS_FONT700` — Noto Sans JP の TTF。
  無い環境では標準の日本語書体 (IPA ゴシック) で代用する
