# vwdxf — DXF(VW10J) タブ

mgtkit の既存「DXF」タブ（`export_dxf.py`・`dxf_struct.py`）とは独立した、構造図 DXF の改良版。
Vectorworks 10J で読める書式（R2000・Shift-JIS・文字スタイル MS UI Gothic）で、
伏図・軸組図・部材リストを書き出す。mgtkit の共通パーサ（`mgt.py`・`section.py` 等）は使わない。

## 層構造（struct_cad_py と同じ考え方）

| 層 | ファイル | 中身 |
|---|---|---|
| 読み込み | `read/` | mgt / mgtx を読む（BOM 付き UTF-8 → UTF-8 → CP932 の順に判定）。断面・グループ・材料・*MEMBER・*FRAME-RLS・配筋・板厚。struct_cad_py から移植 |
| モデル | `model.py` | Node / Element / Section / Material / Group / Rebar / Model（m 単位） |
| 断面 | `sections.py` | せい・幅・符号・断面記号の形（struct_cad の sections / section_shape を移植） |
| 結合 | `merge.py` | *MEMBER をピンで区切って結合（struct_cad の merge を移植） |
| 部材 | `members.py` | 部材の組み立て・判定・端部処理（end_extension）、構面と床の候補（struct_cad を移植）。`build_struct_model()` |
| 作図 | `draw/` | 伏図・軸組図（`plan.py`・`elevation.py`、struct_cad の floor_plan / axis_elevation と同じ規則）、通り芯・レベル線（`figure.py`）、部材リスト（`parts_list.py`）。結果は `primitives.py` の Figure（Drawing IR） |
| 出力 | `render/dxf.py`・`render/preview.py` | 同じ Figure から DXF と用紙プレビュー PNG を描く |
| 入口 | `api.py` | `load`・`info`・`build_figures`・`export`・`preview`（読み込み結果はファイル時刻ごとに保持） |
| 画面 | `routes.py`、`templates/_tab_vwdxf.html`、`static/vwdxf.js` | Blueprint。出力先は `mgtkit_out/dxf_vw10j/` |

書式・レイヤ名・紙面寸法の定数は `style.py` にまとめてある。作図の決まりは struct_cad_py と同じ（2026-09-19 に構造図β由来の規則を置き換え、6 モデルの全部の図で struct_cad の出力と一致を確認）。レイヤは VW10J のクラス（事務所のペン）。出力は図の種類ごとに 1 ファイル（`<元名>_伏図 / _軸組図 / _リスト.dxf`）。

## 組み込み（既存ファイルへの追加は無し）

- `routes.py` の `make_blueprint` は起動時に自動で登録される (`tabreg.py`)
- nav のボタンと本体の include は `_tab_vwdxf.html` 先頭行の見出し
  `{# tab: id="vwdxf" label="DXF(VW10J)" order="80" #}` から作られる
  (order=80 で「DXF」と「数量集計」の間に並ぶ)

外すときは `vwdxf/`・`_tab_vwdxf.html`・`vwdxf.js` を消せば stable と同じに戻る。

作図の仕様（改良の一覧）は `works/mgtkit-dxf-kaizen/IMPROVEMENTS.md`。

## 部材 ID と付属 JSON（2026-10-13 追加、structural-cad 向け）

DXF の見た目・レイヤ・座標は変えずに、次の 2 つを足す。structural-cad（ブラウザ CAD）が
符号の一括変更と、出し直したときの差分反映に使う。

### XDATA（図形ごとの印）

アプリ名 `STRUCTURAL_CAD`、グループコード 1000 の `KEY=VALUE`（DXF 本体と同じ Shift-JIS）。
作図で各図形の `tag`（`draw/primitives.py`）に入れ、`render/dxf.py` の `set_xdata` が書く。
ブロック参照は INSERT に付け、ブロック定義の中には付けない。VW10J などは XDATA を無視する。

| キー | 値 |
|---|---|
| `MID` | 部材 ID = MIDAS の要素番号（結合した部材は代表、壁は面要素の番号） |
| `EL` | 結合した要素番号（2 つ以上のとき。255 バイトを超えるときは付けず JSON の `elements` で引く） |
| `KIND` | `column` / `beam` / `brace` / `truss`（トラス要素） / `wall`（面要素） |
| `CODE` / `SEC` | 符号 / 断面名 |
| `SECNO` | 断面番号（部材リストの列。部材とはこれで結ぶ。1 列 = 1 断面で部材が何本もあるため MID は付けない） |
| `ROLE` | `outline` `section` `code` `grid` `grid-mark` `level` `level-mark` `title` `list-frame` `list-cell` `hatch` `other` |
| `FIG` | 図の ID（`plan:<床>` / 木造 2 枚は `plan:<床>:beam`・`plan:<床>:column` / `axis:<通り>` / `list:<題名>`、複数ページは `list:<題名>:<頁>`） |
| `FL` / `AX` | 床名（伏図の部材・レベル線・レベル名・見出し、軸組図の部材は `members.member_floors` = 梁は床グループ・柱は下端が載る床。床グループに無い部材は無し） / 通り名（通り芯・丸・通り名・軸組図の部材・見出し） |

図 ID は `primitives.figure_id()` だけで作る（XDATA の FIG と JSON の `figures[].id` を一致させるため）。

### 付属 JSON

DXF と同名の `.json`（UTF-8、`render/sidecar.py`）。座標・寸法は mm、図の位置は 1 ファイルに並べたあとの DXF 座標。
名前・符号は XDATA と同じ文字の置き換え（Shift-JIS に無い字）を通す。JSON が書けなくても DXF は出す（注意を出す）。

- `format: "mgtkit-dxf"`, `version: 1`, `source {mgt, exported, mgtkit}`, `paper`, `scale`, `text_mm`, `grid_mm`
- `figures[]`: `id` `kind` `name` `variant`（木造の伏図 `beam`/`column`/`full`） `title`（DXF に描いた見出しの文字） `scale`
  `bbox`（作図の見積もりと書いた図形の範囲を合わせたもの） `members`（MID の list）
  `offset`（作図の座標 → DXF 座標のずらし。伏図はモデルの X・Y [mm] + offset、軸組図は (u, Z) + offset で
  u = X·`frame_dir[0]` + Y·`frame_dir[1]`）、伏図・軸組図は
  `origin` `origin_from` `grids` `levels`（伏図は自分の床の高さ `z` も）、部材リストは `sections` `page` `pages`
  - `origin`: 伏図 = いちばん左の X 系の通り × いちばん下の Y 系の通りの交点、軸組図 = いちばん左の通り芯 ×
    いちばん下のレベル。決められないとき（通り芯を選んでいない・通りが全部斜め）は図の範囲の左下で、
    `origin_from` が `"extent"`（決められたときは `"grid"`）
  - `grids`: `X` / `Y` はほぼ真っすぐな通り（傾き約 0.57° 未満。`coord` = DXF 座標、わずかに傾いていれば `dir` も）。
    それより傾いた通りは `other`（`point` `dir` `family`）。
    軸組図は縦線の位置を `X` に入れる
  - `levels`: 画面の「レベル線にするフロア」で選んだ床（名前・高さ = 梁天）を低い順に。伏図・軸組図とも同じ一覧で、
    伏図は自分の床と同じ高さの行が無ければ足す（同じ高さは 2 行にしない。自分の床は `name` と `z`）。
    同じ高さの床が複数あるときの名前は、画面で先に選んだ方（DXF の ▽ と同じ）
- `members[]`: `id` `elements` `kind` `code` `section` `section_no` `material` `floor` `floors` `axis` `axes`
  `story` `story_name` `story_from` `n1` `n2` `b` `d` `truss` `pin1` `pin2`。壁は `section_no: null` と `thickness_no` `t` `nodes`
  - `floor` / `axis` = 主な図（下柱の×・面外の梁の断面記号だけ、ではない図）の先頭。`floors` / `axes` は出た図すべて（主な図が先）。
    並びは画面で選んだ順によらない: 伏図はモデルの床の順（低い方から）、軸組図は部材が乗っている構面
    （構面の線からの距離が小さい順）→ X 通りが先 → モデルの通りの順。交点の柱は X 通りが `axis` になる
  - `story`: `*STORY`（層設定）があれば合う層を下から 1 始まりで（`story_from: "STORY"`、`story_name` は層の名前。
    mgtkit の N 値・壁量と同じ約束。柱・斜材・壁は下端、梁は高さで、許容 0.05m）。無ければ床の名前の先頭
    `<数字>F` から（`2F1`・`2F2` = 2 階の段差の床。`story_from: "name"`）。候補の床は部材が入っている床グループ
    （低い順）→ 主な図の床（下柱の×で出た上の床は入れない）→ 高さが合う床。`*STORY` の同じレベルの行は 1 つにまとめる。
    どれでも決まらなければ null（屋根 `RF`・勾配の部材・名前に階の無いモデルなど）
  - 部材リストの JSON は、載っている断面を使う部材を全部入れる

確認: 試験モデルを全部の図で書き出し、XDATA を除いた図形・座標・ブロック・レイヤが追加前と一致すること、DXF の FIG・MID・SECNO と JSON の figures / members / sections が食い違わないこと、bbox が図形を含み origin が通り芯の上にあることを見た（6 モデル、2026-10-13）。
