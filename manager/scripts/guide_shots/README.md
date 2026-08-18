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

## 撮り方

```
python manager/scripts/guide_shots/run_manager.py &     # 架空データで起動
python manager/scripts/guide_shots/shoot_real.py <モード>
```

モードは **1 モード = 1 ページ**で、開いたダイアログは閉じずに終わる
(ダイアログが Escape で閉じないため、続きは別モードで開き直す):

| モード | 撮れる図 |
| --- | --- |
| `launch` | `real_launch` / `real_history` / `real_usage` |
| `submit` | `real_submit` / `real_submit_dialog` / `real_submit_manual` |
| `review` | `real_submit_review` (自動作成の確認画面) |
| `beta` | `real_beta` / `real_feedback` |
| `firstrun` | `real_firstrun` (`GUIDE_SHOTS_FIRSTRUN=1` を付けて起動する) |

撮影は 1180x860 で行い、`shoot_real.py` の `CROP` にある図は下の余白を
落とす (Pillow が要る。無ければ切り抜きだけ飛ばす)。図を差し替えたら
`python manager/scripts/build_guide_pdf.py guide` で PDF を作り直す
(目次のページ番号が変わっていないかも確認すること)。

必要な物: `pip install flet flet-web playwright pillow`

## 撮影時の注意 (2026-08)

- 起動には「登録済み・取り込み済み」の状態が要る。無いと初回登録
  ダイアログが全画面を覆い、どのコマも同じ絵になる。`run_manager.py`
  が起動時に `settings.json` (名前 = 山田太郎、キーはダミー) と
  `stable/mgtkit/version.json` (v1.1) を用意する
- **ダイアログは Escape で閉じない** (modal=True のため)。以前は 1 回の
  実行で全部撮ろうとしてダイアログが被ったまま撮れていたので、
  ダイアログを開くコマはモードを分けた (上の表)。座標クリックのため、
  UI の位置が変わったらモードの座標も直すこと
- 生成物 (`real_*.png`) と `home/` は .gitignore 済み。採用する図だけ
  `manager/readme/src/img/` へコピーする

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
