# mgtkit 本体 開発メモ (Claude / 開発者向け)

このリポジトリはルートが mgtkit 本体 (Flask 製の構造設計効率化アプリ)、
`manager/` がアプリマネージャー (配布・提出・承認のツール)。
**アプリマネージャーの開発ルールは `manager/CLAUDE.md` を参照**すること。

## 本体の約束事

- テスト: `python -m pytest` 全緑を確認してからコミットする
- 既知の不具合はテストで現状動作を固定してあり (characterization test)、
  修正は独立した変更として行う (基準値の書き換えは要レビュー)
- チェックアウト先のフォルダ名は `mgtkit` 固定
  (`from mgtkit.x import y` の import 構造とテストのパッケージ解決の要件)
- CI の required check は `test` と `safety` (job 名を変えるときは
  ブランチ保護側も更新)
