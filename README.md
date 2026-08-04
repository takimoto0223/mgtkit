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

出力は入力 mgt ファイルと同じ階層の `mgtkit_out/` に生成されます（git 管理対象外）。

## 開発

```
git clone https://github.com/takimoto0223/mgtkit.git
cd mgtkit
git switch -c <作業ブランチ名>
```

`main` に直接 push せず、ブランチを切って Pull Request でレビューしてからマージしてください。
