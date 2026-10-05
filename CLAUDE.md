# mgtkit 本体 開発メモ (Claude / 開発者向け)

このリポジトリはルートが mgtkit 本体 (Flask 製の構造設計効率化アプリ)、
`manager/` がアプリマネージャー (配布・提出・承認のツール)。
**アプリマネージャーの開発ルールは `manager/CLAUDE.md` を参照**すること。

## 作業の進め方 (管理者指示 2026-08)

- **複数の独立した作業は、指示がなくてもサブエージェントに分担させて
  並行で進める** (調査・レビュー・検証・別領域の実装など)。
  同じファイルを触る作業・前の結果を見てから決める作業・一貫性が要る
  一連の UI 変更は直列のまま。結果はメインセッションが取りまとめる

## 本体の約束事

- テスト: `python -m pytest` 全緑を確認してからコミットする
- 既知の不具合はテストで現状動作を固定してあり (characterization test)、
  修正は独立した変更として行う (基準値の書き換えは要レビュー)
- チェックアウト先のフォルダ名は `mgtkit` 固定
  (`from mgtkit.x import y` の import 構造とテストのパッケージ解決の要件)
- CI の required check は `test` と `safety` (job 名を変えるときは
  ブランチ保護側も更新)

## タブの足し方 (app.py / index.html / app.js は編集しない)

タブを足す PR どうしが同じ行を編集して衝突しないよう、つなぎ方を決めてある
(詳細は `tabreg.py` と `static/app.js` 冒頭の説明):

- API: 本体直下に `<x>/__init__.py` と `<x>/routes.py` を置き、routes.py に
  `make_blueprint(host)` を書く → 起動時に自動登録 (app.py に import・登録行を足さない)
- 画面: `templates/_tab_<x>.html` の**先頭行**に見出し
  `{# tab: id="x" label="表示名" order="150" out="x" #}` を書く → nav のボタン・
  本体の include・出力フォルダ案内文に載る。order は組み込みタブ
  (app.py の `BUILTIN_TABS`) と既存の見出しを見て、入れたい位置の間の値にする
  (省略すると右端。省略したタブどうしはファイル名順)。
  JS は `<script src="/static/<x>.js">` をそのテンプレートの末尾に書く
- 入力欄: 既存の応力ファイル欄と同期するなら `data-sync="beam"` など
  (beam / truss / plate / wall)、記憶と出力先推定の対象にするなら `data-persist`
- 共通欄の出来事は `document.addEventListener('mgtkit:mgt-loaded', e => ...)`
  (`e.detail` が /api/mgt_info の結果) / `'mgtkit:stress-changed'` で受け取る
