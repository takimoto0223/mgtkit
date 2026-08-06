# mgtkit

構造設計の業務効率化ツール。midas gen の mgt ファイルを読み込み、断面算定・応力図・QR 図・DXF/TeX 出力などをブラウザ UI から実行する Flask アプリ。

## 起動

`起動.bat` をダブルクリックすると、必要ライブラリの確認・インストールを行ったうえで `app.py` が起動し、ブラウザが開きます。

手動で起動する場合:

```
python app.py
```

### 必要環境

- Python 3
- flask / numpy / matplotlib / openpyxl / pypdf / ezdxf

```
pip install flask numpy matplotlib openpyxl pypdf ezdxf
```

## 使い方

`readme/mgtkit操作マニュアル_v1.5.pdf` を参照してください。

## アプリマネージャー

バージョン管理・機能追加の提出・承認を Git / GitHub を意識せずに行うための
デスクトップアプリを同梱しています。

- 新しいメンバー: `scripts/setup.bat` を実行するだけで環境構築からマネージャー起動まで完了します
- 既にリポジトリがある場合: `manager\マネージャー起動.bat` で起動します
- 初回起動時に名前と本人の Anthropic API キーを登録します (この PC 内にのみ保存)
- タブ構成: 起動 / 更新 / β版 / 提出 / 承認
  - **提出**: 作業フォルダを ZIP にして選ぶだけで、検証 → β版配布 → 承認の流れに乗ります
  - **承認**: 3人の承認がそろえば正式版としてリリースできます（特定メンバーに
    限定したい場合は `config.json` の `manager.admins` に名前を設定）
  - 更新 / β版 / 承認タブには未対応の件数が黄色バッジで表示されます
- 設計判断・運用ルールの詳細は `docs/app-manager-decisions.md` を参照してください

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
| `readme/` | 操作マニュアル |
| `manager/` | アプリマネージャー（Flet 製。起動・更新・β版・提出・承認） |
| `tests/` | 回帰テスト（pytest。CI で自動実行） |
| `docs/` | アプリマネージャーの設計・運用の記録 |
| `scripts/` | セットアップ用スクリプト（setup.bat / ブランチ保護） |

出力は入力 mgt ファイルと同じ階層の `mgtkit_out/` に生成されます（git 管理対象外）。

## 開発

```
git clone https://github.com/takimoto0223/mgtkit.git
cd mgtkit
git switch -c <作業ブランチ名>
```

`main` に直接 push せず、ブランチを切って Pull Request でレビューしてからマージしてください。
