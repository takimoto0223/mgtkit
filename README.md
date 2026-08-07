# mgtkit

構造設計の業務効率化ツール。midas gen の mgt ファイルを読み込み、断面算定・応力図・QR 図・DXF/TeX 出力などをブラウザ UI から実行する Flask アプリ。

## はじめかた（メンバー向け）

配布された `setup.bat` をダブルクリックするだけで、必要ツール（Git / GitHub CLI / Python）の導入 → mgtkit の取得 → アプリマネージャーの起動まで自動で行われます。

- mgtkit 本体は `C:\Users\(自分)\mgtkit` に取得されます。ここがバージョン管理の
  置き場所になるため、フォルダを移動・改名しないでください
- 初回はマネージャーで名前と本人の Anthropic API キーを登録します（この PC 内にのみ保存）
- 「起動」ボタンを押すと、最新の安定版を自動で取得してブラウザで開きます
- 2 回目からは `C:\Users\(自分)\mgtkit\manager\マネージャー起動.bat` をダブルクリックするだけです
- 画面つきの手順は `readme/アプリマネージャー使い方ガイド.pdf` を参照してください

## アプリの使い方

`readme/mgtkit操作マニュアル_v1.5.pdf` を参照してください。

## アプリマネージャー

バージョン管理・機能追加の提出・承認を Git / GitHub を意識せずに行うための
デスクトップアプリを同梱しています。

- タブ構成: 起動 / 更新 / 更新版を提出 / β版の確認と承認
  - **起動**: mgtkit を起動します（安定版が未取得なら自動で取得してから開きます）
  - **更新**: 新しい正式版の確認と、更新して起動
  - **更新版を提出**: 作業フォルダを丸ごと ZIP にして選ぶだけで自動検証され、
    エラーなく組み込める状態になるとβ版として発行されます。取得した版からの
    差分だけが提出され、個人設定 (API キー) や計算結果 (mgtkit_out) は
    自動で除外されます
  - **β版の確認と承認**: β版の試用・フィードバック・承認を 1 画面で行います。
    各提出の「フィードバック n 件」からメンバーの感想・不具合報告を一覧でき、
    2人の承認がそろえば正式版としてリリースできます（必要数は `config.json` の
    `branch_protection.required_approvals`。特定メンバーに
    限定したい場合は `config.json` の `manager.admins` に名前を設定）。
    承認・却下は push 権限を持つ collaborator のものだけがカウントされます
    (public リポジトリで第三者がレビューしても影響しません)
  - 更新 / β版の確認と承認タブには未対応の件数が黄色バッジで表示されます
  - 提出・承認への参加は自動申請制です: 新メンバーがマネージャーを起動すると
    参加申請 (Issue) が自動作成され、オーナーに通知メールが届きます。
    オーナーがメールに「承認」と返信 (Issue へのコメント) すると自動で
    collaborator 招待が送られ、メンバーは次回起動時に自動承諾されます
    (.github/workflows/join-request.yml。Secrets に COLLAB_INVITE_TOKEN の
    登録が必要)
- メンバーへの配布は `setup.bat` と `readme/アプリマネージャー使い方ガイド.pdf` の
  2 ファイルだけで完結します
- 設計判断・運用ルールの詳細は `docs/app-manager-decisions.md` を参照してください

## マネージャーを使わず直接起動する場合（非常用・開発用）

`起動.bat` をダブルクリックすると、必要ライブラリの確認・インストールを行ったうえで `app.py` が起動し、ブラウザが開きます。

手動で起動する場合:

```
python app.py
```

### 必要環境

- Python 3
- アプリ本体: flask / numpy / matplotlib / openpyxl / pypdf / ezdxf
- アプリマネージャー: flet / anthropic（`manager/requirements.txt`）

```
pip install flask numpy matplotlib openpyxl pypdf ezdxf
```

## 構成

| パス | 内容 |
| --- | --- |
| `app.py` | Flask アプリ本体（ルーティング・API） |
| `mgt.py` | mgt ファイルのパーサ |
| `*_check.py` | 各種断面算定（rc / s / src / cft / rm / pc / w / wall / plate / pile など） |
| `draw_*.py` | 図の描画（モデル図・応力図・QR 図・検定比図） |
| `export_dxf.py` / `export_tex.py` | DXF / TeX 出力 |
| `qr_*.py` | QR（保有水平耐力）関連の算定 |
| `secprops.py` / `section.py` | 断面性能 |
| `data/` | 基準データ（材料・鉄筋・PC 情報など JSON） |
| `templates/` / `static/` | フロントエンド（index.html / app.js） |
| `readme/` | 操作マニュアル・アプリマネージャー使い方ガイド |
| `manager/` | アプリマネージャー（Flet 製。起動・更新・更新版を提出・β版の確認と承認） |
| `tests/` | 回帰テスト（pytest。CI で自動実行） |
| `docs/` | アプリマネージャーの設計・運用の記録 |
| `scripts/` | セットアップ用スクリプト（setup.bat / ブランチ保護） |

出力は入力 mgt ファイルと同じ階層の `mgtkit_out/` に生成されます（git 管理対象外）。

## 開発

メンバーの機能追加は、アプリマネージャーの「更新版を提出」経由が基本です（Git 操作不要）。
Git で直接開発する場合:

```
git clone https://github.com/takimoto0223/mgtkit.git
cd mgtkit
git switch -c <作業ブランチ名>
```

- `main` に直接 push せず、ブランチを切って Pull Request でレビューしてからマージしてください
- checkout したフォルダの名前は `mgtkit` のまま使ってください（パッケージ構造の要件。
  変更するとアプリ・テストが動きません）
