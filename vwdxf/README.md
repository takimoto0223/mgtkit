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

## 組み込み（既存ファイルへの追加はこれだけ）

- `app.py`: `from mgtkit.vwdxf.routes import make_blueprint as _vwdxf_bp` と
  `app.register_blueprint(_vwdxf_bp(sys.modules[__name__]))`
- `templates/index.html`: nav に `<button data-tab="vwdxf">DXF(VW10J)</button>`、
  本体に `{% include '_tab_vwdxf.html' %}`

外すときはこの 4 行と、`vwdxf/`・`_tab_vwdxf.html`・`vwdxf.js` を消せば stable と同じに戻る。

作図の仕様（改良の一覧）は `works/mgtkit-dxf-kaizen/IMPROVEMENTS.md`。
