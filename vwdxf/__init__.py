# -*- coding: utf-8 -*-
"""DXF(VW10J) タブ: 改良版の構造図 DXF (伏図・軸組図・部材リスト).

既存の DXF タブ (export_dxf.py / dxf_struct.py) とは独立。構成は tools/struct_cad_py と同じ層構造:
  read/ (mgt・mgtx → Model IR) → members.py (部材の組み立て) → draw/ (Drawing IR) → render/ (DXF・プレビュー)
詳しくは README.md。
"""
