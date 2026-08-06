# アプリマネージャー 決定事項の記録

`docs/app-manager-spec.md` の「7. 未確定事項」に対する調査結果と決定を記録する。

## 7-1. スモークテスト対象ルート

既存アプリ (`app.py`) は全 27 ルート。fixture(実 mgt ファイル)なしで決定的に検証できる範囲を
Level 1 スモークテストとした (`tests/test_smoke_routes.py`):

| 対象 | 期待動作 |
|---|---|
| `GET /` | 200 (index.html 描画、`Cache-Control: no-store`) |
| `GET /api/file`(未許可パス) | 404 + JSON `error` |
| `POST /api/upload`(ファイルなし / ダミーファイル) | 400 / 200 + `path` |
| `POST /api/bundle`(不正 `mode`) | 400 |
| `_check_input_file` ガード付き POST 16 ルート(空 JSON) | 400 + JSON `error` |
| `qr_*` 6 ルート(空 JSON) | **500**(現状動作の固定。`_qr_load` が ValueError を送出するため。400 化は将来の改善候補) |

実出力(PDF/DXF/xlsx)を伴う経路は実 mgt が必要なため、合成テキストによる
パーサ・計算ロジックの回帰テスト (Level 2) でカバーする。

## 7-2. 承認者の範囲・承認必要数

- 全メンバー承認可(仕様どおり)。提出者本人の自己承認禁止は Phase 5 のマネージャー側で制御。
- 承認必要数は `config.json` の `branch_protection.required_approvals`(既定 3)。
  2 に変更して `scripts/setup_repo_protection.sh|bat` を再実行すれば反映される。
- **注意**: 本リポジトリは現在 個人アカウントの private リポジトリのため、
  ブランチ保護(required reviews の強制)には GitHub Pro 等の有料プラン、
  または public 化、または Organization への移管が必要。
  スクリプトは 403/422 応答時にこの旨を案内する。

## 7-3. β版のデータ分離

アプリ実装を調査した結果、**DB・設定ファイル・レジストリは一切使用していない**。
アプリが持つ状態は次の 3 つのみ:

1. アップロード一時ファイル: `<TEMP>/mgtkit_uploads/`(`app.py` の `_UPLOAD_DIR`)
2. 生成物: 入力 mgt ファイルと同階層の `mgtkit_out/`(入力の置き場所に従う)
3. 待ち受けポート: `MGTKIT_PORT` 環境変数(既定 8765)

したがって分離方針は:

- 安定版とβ版を**別フォルダに展開**し、β版は `MGTKIT_PORT=8766`(`config.json` の
  `app.port_beta`)で起動する。生成物は入力ファイル位置に従うため衝突しない。
- アップロード一時フォルダのみ両者で共有される。分離するには `MGTKIT_UPLOAD_DIR`
  環境変数対応の小改修が必要 → **Phase 2 で実施**(Phase 1 ではアプリ本体を変更しない)。
- β版バナー表示用の環境変数(例 `MGTKIT_CHANNEL=beta`)も Phase 2 で追加する。

## Phase 2 の決定 (マネージャー: 起動・更新・β版タブ)

- マネージャーは `manager/` パッケージとしてリポジトリに同梱し、
  `python -m manager.main`(または `manager/マネージャー起動.bat`)で起動する。
  UI は Flet (`manager/requirements.txt` で固定)。ロジック(バージョン比較・
  gh ラッパー・展開・起動)は UI 非依存のモジュールに分離し pytest で回帰する。
- インストール配置: `<install_root>/stable/mgtkit/` と
  `<install_root>/beta/<version>/mgtkit/`。**アプリ本体フォルダ名は mgtkit 固定**
  (app.py が親ディレクトリを sys.path に入れて `from mgtkit.x import y` する構造のため)。
  install_root は Windows で `%LOCALAPPDATA%\mgtkit`(config.json の
  `manager.install_root` で変更可)。
- リリース取得は `gh api repos/<repo>/releases` + `gh release download`。
  CI 添付の配布 ZIP(version.json 同梱)を優先し、無い場合(Phase 4 整備前)は
  ソースアーカイブを取得してマネージャーが version.json を補完する。
- アプリ側の小改修(Phase 1 で予告済み):
  - `MGTKIT_UPLOAD_DIR` でアップロード一時フォルダを分離(マネージャーが
    インスタンスごとの `uploads_tmp/` を指定)
  - `MGTKIT_CHANNEL=beta` で画面上部にβ版バナーを表示
- β版フィードバック → PR コメント投稿は仕様どおり Phase 6 で対応(Phase 2 は読み取り系のみ)。

## リリース手順 (Phase 4 自動化までの暫定運用)

- `.github/workflows/release.yml`(手動実行)で安定版/β版を Releases に登録する。
  GitHub の Actions タブ → release → Run workflow でバージョン(vX.Y または
  vX.Y-beta.N)とリリースノートを入力する。
- version.json の生成と配布 ZIP(開発用ファイル除外)の添付はワークフローが行う。
  vX.Y-beta.N はプレリリースフラグ付きで登録される。
- Phase 4 で「PR マージ → 自動タグ付け・自動リリース」に置き換える際も
  この ZIP 作成手順を流用する。

## Phase 1 のその他の決定

- 既知の不具合はテストで**現状動作を固定**し、修正しない(qr_* の 500、
  `secprops.section_ability(11000)` の NameError 等)。修正は独立した PR で行う。
- ゴールデン値(回帰基準値)は現行実装の実行結果を記録したもの(characterization test)。
- CI ジョブ名 `test` がブランチ保護の required status check 名。変更時は両方更新すること。
- 依存は `requirements.txt` に完全固定(`==`)。開発用は `requirements-dev.txt`。
