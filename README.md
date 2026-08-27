# mgtkit

構造設計の業務効率化ツール。midas gen の mgt ファイルを読み込み、断面算定・応力図・QR 図・DXF/TeX 出力などをブラウザ UI から実行する Flask アプリ。

このリポジトリ (mgtkit フォルダ) には、mgtkit 本体と、バージョン管理・提出・承認を
画面から行う**アプリマネージャー** (`manager/`) が入っています。

## はじめかた（メンバー向け）

配布された `setup.bat` をダブルクリックするだけで、必要ツール（Git / GitHub CLI / Python）の導入 → mgtkit の取得 → アプリマネージャーの起動まで自動で行われます。

- mgtkit 関係のファイルは `C:\mgtkit_appmanager`（C ドライブの直下）にまとまります。
  **このフォルダを移動・改名しないでください**（マネージャーの起動・更新・提出は
  すべてこの場所を前提に動きます）
- 初回はマネージャーで名前と本人の Claude API キーを登録します（この PC 内にのみ保存）
- 「起動」ボタンを押すと、最新の安定版を自動で取得してブラウザで開きます
- 2 回目からは `C:\mgtkit_appmanager` の中の**「mgtkit アプリマネージャー」**を
  ダブルクリックするだけです（このショートカットはデスクトップにコピーしても使えます）
- 画面つきの手順は、同じフォルダの **`アプリマネージャー使い方ガイド.pdf`** を
  参照してください（リポジトリ上は `manager/readme/アプリマネージャー使い方ガイド.pdf`）

## アプリの使い方

`readme/mgtkit操作マニュアル_v1.5.pdf` を参照してください。

## アプリマネージャー

機能・使い方・設計記録は `manager/README.md` を参照してください
（タブ構成、提出・承認の流れ、参加申請、配布方法など）。

## マネージャーを使わず直接起動する場合（非常用・開発用）

`C:\mgtkit_appmanager\stable\mgtkit\起動.bat` をダブルクリックすると、必要ライブラリの
確認・インストールを行ったうえで `app.py` が起動し、ブラウザが開きます
（いま入っているアプリを直接起動します）。

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
| `wallqty/` | 木造壁量計算タブ（存在壁量の読み取り・壁量検定） |
| `templates/` / `static/` | フロントエンド（index.html / app.js） |
| `readme/` | mgtkit 本体の操作マニュアル |
| `tests/` | 本体の回帰テスト（pytest。CI で自動実行） |
| `manager/` | アプリマネージャー一式（プログラム・テスト・資料・記録・スクリプト） |
| `config.json` | 共通の運用設定（ポート・承認必要数・提出上限・API 単価など） |

出力は入力 mgt ファイルと同じ階層の `mgtkit_out/` に生成されます（git 管理対象外）。

### メンバーの PC での置かれ方

上の表はリポジトリの中身です。メンバーの PC では `C:\mgtkit_appmanager` の下に
次のように置かれます（くわしくは使い方ガイドの付録 B）。

| パス | 内容 |
| --- | --- |
| `mgtkit アプリマネージャー` | 起動用のショートカット（デスクトップにコピー可） |
| `アプリマネージャー使い方ガイド.pdf` | 画面つきの手順書 |
| `stable\mgtkit\` | アプリ本体。改造するときはこれをフォルダの外へコピーする |
| `.manager\` | マネージャーの持ち物（通常は非表示）。このリポジトリの複製・設定・ログなど |

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
