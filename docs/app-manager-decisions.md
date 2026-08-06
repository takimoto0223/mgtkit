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

## Phase 3 の決定 (提出タブ)

- 提出フローは UI 非依存の `manager/submit.py` に実装し、
  「準備 (`prepare_submission`) → ユーザー確認 → 確定 (`finalize_submission`)」
  の 2 段階に分割。削除ファイルの意図確認と警告承諾をこの境界で行う。
- 基点特定は ZIP 同梱の version.json の commit SHA のみを使う (spec 1.4)。
  無い/壊れている/履歴に無い場合は平易な日本語でエラー中断。
- 差分計算は隠し作業クローン (`<install_root>/workrepo`) 上で行い、
  比較は git blob ハッシュ (改行変換の影響なし)。**配布 ZIP に含まれない
  開発用ファイル (tests/ docs/ scripts/ manager/ .github/ 等) は差分対象外**
  とし、提出で削除・変更されない (release.yml の除外リストと同期)。
- 安全チェック: 実行ファイル (.exe .bat .ps1 .sh 等) は即ブロック、
  拡張子ホワイトリスト (config `manager.allowed_extensions`)、
  サイズ/件数上限、簡易秘密情報スキャン (正規表現) は警告→確認の上続行可。
  gitleaks 等の本格スキャンは Phase 4 の CI 側で実施。
- コミットメッセージ (未入力時) と PR 本文は Anthropic API
  (`manager.claude_model`、既定 claude-opus-5、キーは環境変数
  ANTHROPIC_API_KEY) で自動生成。キー未設定・失敗時は定型文へ
  フォールバックし、提出自体は Claude なしでも完結する。
- 衝突時 (基点より main が先行し同一箇所が変更) の解決フローは
  仕様どおり Phase 5 で実装する。

## Phase 4 の決定 (安全チェック CI・自動修正ループ・β版自動リリース)

- **API キーの運用 (仕様からの変更点)**: 管理者キーを repo Secrets に置いて
  `claude-code-action` を使う方式はやめ、**提出者本人の API キー**を使う。
  - マネージャー初回起動時に「名前 + Anthropic API キー」の登録を必須化。
    保存先は各自の PC の `<install_root>/settings.json` のみ
    (GitHub には一切送らない。ANTHROPIC_API_KEY 環境変数はフォールバック)。
  - コミットメッセージ/PR 本文生成・自動修正ループはすべて本人キーで実行。
    費用も本人 (所属組織) のキーに紐づく。
- **自動修正ループは Actions ではなくマネージャー側で実行** (`manager/autofix.py`)。
  提出タブの「検証状況」から実行: 失敗ログ取得 (gh run view --log-failed) →
  Claude が原因分析・修正 (structured output) → `[auto-fix]` commit → push →
  再検証待ち。上限 `manager.auto_fix_max_attempts` (既定3) 回。
  上限到達時は平易な3行要約を PR コメントへ投稿。
- **ガード (spec 3.2 の4点)**: tests/ ほか保護領域 (docs/ scripts/ manager/
  .github/) の変更禁止をマネージャー側で二重チェックし、さらに CI 側でも
  `[auto-fix]` コミットに tests/ の diff があれば fail。`[auto-fix]`
  プレフィックスと「原因・対応」のコミットメッセージを必須化。
  禁止パターン (bare except / skip 追加 / アサーション緩和 / 仕様変更) は
  プロンプトに明記。
- **CI (`safety` ジョブ、PR のみ)**: gitleaks による秘密情報スキャン +
  requirements.txt 変更時の pip-audit (検出は warning とし承認時の判断材料に)
  + auto-fix ガード。
- **β版自動リリース (`beta-release` ジョブ)**: `feature/*` からの PR で
  test/safety が green になると、最新安定版 vX.Y の次 (vX.Y+1) に対する
  `-beta.N` を自動採番し、version.json 入り配布 ZIP をプレリリース登録。
  マネージャーのβ版タブに即座に現れる。

## Phase 5 の決定 (承認タブ・衝突解決)

- 承認タブ (`manager/reviews.py` + UI): open PR を承認待ち一覧として表示。
  承認 n/3 (黄色マーカー)・承認済みメンバー・却下理由 (平易表示)・
  検証状況・衝突有無を1行に集約。
  - 承認 = `gh pr review --approve`。**自己承認はマネージャー側で禁止**。
  - 却下 = 理由コメント必須 → `--request-changes`。1人でも却下があれば
    リリース不可 (ブロック表示)。
  - 差分表示は「提出者の変更 (紺)」「[auto-fix] による変更 (橙)」の
    色分けファイル一覧 + unified diff。判定はコミットメッセージの
    [auto-fix] プレフィックス。
  - リリースボタンは「必要数承認・却下なし・検証OK・衝突なし・管理者
    (config `manager.admins`、既定はリポジトリ owner)」で有効。
    実行内容 = squash merge → 次の正式版 vX.Y を採番 →
    release ワークフローを dispatch (リリースノートは Claude 生成、
    フォールバックあり)。
- 差し戻し後の再提出: 提出ダイアログに「既存の提出 #N に修正版として積む」
  の選択肢を追加 (同一 PR にコミットが積まれる)。
- 衝突解決 (`manager/conflicts.py`): merge 試行で衝突検出 → Claude が
  「機能レベルの説明」に翻訳 → 3択 (両方残す[推奨]/自分優先/最新版優先)。
  自分優先・最新版優先は git で機械的に解決し、**「両方残す」のみ Claude が
  コードレベル統合** (統合結果は衝突ファイル以外への変更・マーカー残りを
  検査して適用)。統合ボタンは提出者本人にのみ表示。
- セキュリティ強化: settings.json (個人設定) を提出対象から常に除外、
  API キー・トークン・秘密鍵の検出を「警告」から「即ブロック」へ格上げ。

## Phase 6 の決定 (通知・フィードバック・セットアップ)

- 更新通知: マネージャー起動時 + 30分ごとのポーリングで GitHub Releases を
  確認し、新しい安定版があれば起動タブに通知バナーを表示 (spec 2.2 タブ5
  「マージ完了後、次回起動時/ポーリングで通知」)。
- β版フィードバック (`manager/feedback.py`): β版タブの各リリースに
  フィードバック入力を用意し、リリースノート中の「提出 #N」から対応 PR を
  特定して `gh pr comment` で投稿。登録済みの名前を添えて承認判断の材料に
  する。対応 PR が特定できないリリース (手動作成等) は送信不可。
- `scripts/setup.bat` (メンバー配布用): winget で Git / gh / Python を導入
  → `gh auth login` (ブラウザ認証) → `%USERPROFILE%\mgtkit` へ clone →
  マネージャー起動まで。このファイル 1 つを渡すだけで新メンバーの環境が整う。
- README にアプリマネージャーの節と構成表の更新を追加。

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
