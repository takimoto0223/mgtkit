# 荷重分布図 (loadmap)

MIDAS Gen NX の**荷重入力画面をキャプチャする代わりに**、mgt / mgtx から
荷重分布図を作って PDF に出す。荷重ケース1つにつき1図。
構造計算書の「エリアごとの単位荷重の荷重分布図」用。

| 荷重 | mgt のブロック | 図での表し方 |
| --- | --- | --- |
| 床荷重 | `*FLOORLOAD` + `*FLOADTYPE` | 領域を塗り、`(3)-RF2(One)` を引き出す |
| 面荷重 | `*PRESSURE` | 対象プレート要素を塗り、要素ごとに矢印を立てる |
| 部材荷重 | `*BEAMLOAD` | 荷重図（等分布・台形は値に比例した高さ、集中は矢印1本）と値 |
| 節点荷重 | `*CONLOAD` | 矢印と値 |

## 構成

| ファイル | 内容 |
| --- | --- |
| `mgt_loads.py` | 荷重ブロックのパーサ (`mgt.py` に無いため新規) |
| `draw.py` | matplotlib での作図。`plot_loadmap()` / `plot_view_sheet()` |
| `routes.py` | Flask Blueprint (`/api/loadmap_cases` / `/api/loadmap_preview` / `/api/plot_loadmap` / `/api/loadmap_view_sheet`) |
| `../templates/_tab_loadmap.html` | タブの markup (index.html から include) |
| `../static/loadmap.js` | タブの JS (app.js は無変更) |
| `../tests/test_loadmap.py` | 回帰テスト 15 件 |

## 既存ファイルへの追加は4行だけ

**`app.py`** — import 1行と、登録1行 (コメント含めて3行):

```python
from mgtkit.loadmap.routes import make_blueprint as _loadmap_bp   # import 群の末尾へ

# 荷重分布図タブ (mgtkit/loadmap/)。共通ヘルパを渡して登録する
app.register_blueprint(_loadmap_bp(sys.modules[__name__]))        # 最初の @app.route の直前へ
```

**`templates/index.html`** — nav に1行と、最後の `</section>` の後ろに1行:

```html
  <button data-tab="loadmap">荷重分布図</button>

{% include '_tab_loadmap.html' %}
```

`mgt.py` / `util.py` / `draw_model.py` / `app.js` などは**変更していない**
(番号リストの展開は `util.get_byto`、日本語フォント設定は
`draw_model._setup_japanese_font` をそのまま呼んでいる)。

## 設計メモ (ハマりどころ)

**共通ヘルパはモジュールを受け取って使う。**
`routes.py` から `mgtkit.app` を import し返すと、`python app.py` 起動時に
app が `__main__` と `mgtkit.app` の2つ読み込まれ、配信許可リスト
(`_ALLOWED_FILES`) が二重になって生成物をダウンロードできなくなる。
そのため `make_blueprint(sys.modules[__name__])` で app.py 自身を渡し、
`host._out_dir(...)` のように借りている。

**床荷重の所属ケースは `*USE-STLD` では決まらない。**
`*FLOORLOAD` はファイル末尾にまとめて置かれ、直前の `*USE-STLD` は無関係。
所属は床荷重タイプ (`*FLOADTYPE` の2行目 LCNAME) が持っている。
`*PRESSURE` と `*CONLOAD` は逆に `*USE-STLD` で決まる。

**`*BEAMLOAD` は列構成が2通りある。**
方向を `GZ` のような記号で指定する形と、方向ベクトル (`VX, VY, VZ`) を伴う形。
3列目が方向記号かどうかで見分ける。値は **ECCEN 5列のあとの 8列**
(`D1, P1, D2, P2, D3, P3, D4, P4`) で、位置は材長比 0〜1。

**mgt.py の `mgtopen_*` はコメント行の数を前提にしている。**
`_scan_section` はデータ開始行を「見出し行 + offset」で決める
(`*NODE` は +2、`*ELEMENT` は +5)。実ファイルは見出しの下に `; iNO, X, Y, Z`
のようなコメント行が並ぶ前提。テスト用のサンプル mgt をコメント行なしで
書くと **要素が0件でも図は出てしまい気づけない**ので、
`test_geometry_is_read_through_mgtopen` で見張っている。

**ラベルの通し番号は `*FLOADTYPE` の定義順でグループ化し、その中は出現順。**
これで MIDAS 画面の `(1)`〜`(34)` と一致する (一新亭 34面で照合済み)。

**作図はセル内 mm 座標で行う。**
Axes を用紙上の実寸で置き `aspect=equal` にしてあるので、データ座標 = 紙面 mm。
文字高や引き出し線の間隔を図の縮尺と無関係に指定できる。
ラベルはアンカーの高さから始め、重なるぶんだけ下へ押して解く。図の実際の
外形の外側に置く (枠いっぱいを基準にすると細長い建物で引き出し線が伸びる)。

**塗りと外形線は別の PolyCollection にする。**
1つにまとめると `alpha` が外形線にもかかり、領域の切れ目が読めなくなる。

## プレビュー

PDF を作る前に「プレビュー」で 1 ページぶんを PNG で確認できる
(`preview_page()` → `/api/loadmap_preview`)。複数ページある場合は前後に送れる。

**紙面の計算は PDF と同じ `page_layout()` を通す。**
プレビューと出力で用紙・枠・文字高がずれると確認の意味が無くなるため、
用紙寸法・列×行・枠寸法を出すのはこの関数1箇所だけにしてある。

## 視点

既定は**方位角 −40° / 見下ろし角 24°**。見下ろし角が浅いと床の領域が線に
潰れ、深いと壁の領域が重なる。その両立点として一新亭 (7.3×3.0×11.2m) で
決めた。建物の細長さで変わるので、案件ごとに画面の「視点の比較シート」
(1ケースを18通りの角度で並べた A3) で選び直す。

## 関連

同じ作図を単体ツールとしても持っている
(`X:\kanazawa\tools\struct_cad_py`、`python -m struct_cad.load_cli`)。
そちらは DXF/VectorScript と共通の Drawing IR に載せた実装で、mgtkit とは
独立。パーサ (`mgt_loads.py`) とラベル配置の考え方は共通。
