# -*- coding: utf-8 -*-
"""DXF に添える付属 JSON (図の一覧と部材の一覧). structural-cad 向け (2026-10-13 追加).

DXF と同じ名前で .json を同じフォルダに書く (<元名>_伏図.dxf → <元名>_伏図.json、UTF-8)。
figures[].id は DXF の XDATA の FIG と同じ文字列 (draw/primitives.figure_id → dxf_text)。
座標・寸法は mm。図の範囲・基準点・通り芯は、1 ファイルに並べたあとの DXF 座標
(render/dxf.layout の置き場所を足したもの)。仕様は vwdxf/README.md「部材 ID と付属 JSON」。
"""
import datetime
import json
import os
import re

from ..draw.primitives import member_kind
from ..members import auto_frames, member_floor_groups, plan_key_z, plan_keys
from .dxf import dxf_text, layout

FORMAT = 'mgtkit-dxf'
VERSION = 1


def _mgtkit_version():
    """mgtkit/version.json の版 (先頭の v は外す). 読めなければ空."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), 'version.json')
    try:
        with open(path, encoding='utf-8') as f:
            return str(json.load(f).get('version', '')).lstrip('v')
    except (OSError, ValueError):
        return ''


def _r(v):
    return round(float(v), 3) + 0.0      # + 0.0: -0.0 を 0.0 にする


def _d(v):
    return round(float(v), 6) + 0.0      # 向き (単位ベクトル)


def _t(v):
    """名前・符号は DXF の XDATA と同じ文字に (Shift-JIS に無い字の置き換え)."""
    return None if v is None else dxf_text(v)


def _xyz(p):
    return [_r(p[0] * 1000.0), _r(p[1] * 1000.0), _r(p[2] * 1000.0)]


STORY_TOL = 0.05     # *STORY のレベルに合わせる許容 [m] (wallqty の STORY_TOL と同じ)
_FLOOR_NAME_RE = re.compile(r'^(\d+)F')


def _story(M, z, floor_names):
    """階番号 → (story, story_name, story_from).

    *STORY (層設定) があれば、高さ z [m] が合う層を下から 1 始まりで (n_value・wallqty と同じ約束。
    柱・斜材・壁は下端、梁は高さ)。*STORY が無ければ床の名前の先頭 <数字>F から
    (2F1・2F2 = 2 階の段差の床、2026-10-13 ユーザー確認)。floor_names は候補の床の名前を
    優先順に (同じ高さに壁用などのグループがあるモデルもあるので、名前が合う最初のもの)。
    候補に「下柱の×」として出た上の床は入れない (柱は下端で決める約束のため)。
    どちらでも決まらなければ None。
    """
    if M.ir.stories:
        stories = []                      # 同じレベルの行は 1 つに (先の名前。wallqty と同じ)
        for name, lz in M.ir.stories:
            if not stories or lz - stories[-1][1] > 1e-4:
                stories.append((name, lz))
        best = None
        for k, (name, lz) in enumerate(stories):
            if abs(lz - z) <= STORY_TOL and (best is None or
                                             abs(lz - z) < abs(stories[best][1] - z)):
                best = k
        if best is None:
            return None, None, None
        return best + 1, _t(stories[best][0]), 'STORY'
    for name in floor_names:
        mm = _FLOOR_NAME_RE.match(name or '')
        if mm:
            return int(mm.group(1)), '%sF' % mm.group(1), 'name'
    return None, None, None


def _figure_entry(fig, origin):
    ox, oy = origin
    meta = fig.meta or {}
    # bbox: 作図時の見積もり (文字は高さ×文字数の目安) と、書いた DXF の図形の範囲を合わせたもの
    x0, y0, x1, y1 = fig.bounds
    x0, y0, x1, y1 = x0 + ox, y0 + oy, x1 + ox, y1 + oy
    if 'dxf_extents' in meta:
        a0, b0, a1, b1 = meta['dxf_extents']
        x0, y0, x1, y1 = min(x0, a0), min(y0, b0), max(x1, a1), max(y1, b1)
    # title = DXF に描いた見出しの文字 (木造の伏図でも「(梁伏図)」は付かない)
    title = next((p.text for p in fig.prims if p.tag and p.tag.get('ROLE') == 'title'), fig.title)
    ent = {'id': dxf_text(fig.fig_id), 'kind': fig.kind, 'name': _t(meta.get('name', fig.key)),
           'variant': fig.sheet if fig.kind == 'plan' else None,
           'title': _t(title), 'scale': fig.scale,
           'bbox': [_r(x0), _r(y0), _r(x1), _r(y1)],
           # 作図の座標 (伏図 = モデルの X・Y、軸組図 = u・Z。mm) → DXF 座標のずらし量
           'offset': [_r(ox), _r(oy)]}
    if 'frame_dir' in meta:
        # 軸組図の横の座標 u = X·dir[0] + Y·dir[1] (モデル座標 mm)。縦は Z
        ent['frame_dir'] = [_d(meta['frame_dir'][0]), _d(meta['frame_dir'][1])]
    if 'origin' in meta:
        ent['origin'] = [_r(meta['origin'][0] + ox), _r(meta['origin'][1] + oy)]
        ent['origin_from'] = meta.get('origin_from')
    if 'grids' in meta:
        g = meta['grids']
        def axis_grid(v, off):
            out = {'name': _t(v['name']), 'coord': _r(v['coord'] + off)}
            if 'dir' in v:
                out['dir'] = [_d(v['dir'][0]), _d(v['dir'][1])]
            return out
        ent['grids'] = {'X': [axis_grid(v, ox) for v in g['X']],
                        'Y': [axis_grid(v, oy) for v in g['Y']]}
        if g.get('other'):
            ent['grids']['other'] = [
                {'name': _t(v['name']), 'family': v.get('family'),
                 'point': [_r(v['point'][0] + ox), _r(v['point'][1] + oy)],
                 'dir': [_d(v['dir'][0]), _d(v['dir'][1])]} for v in g['other']]
    if 'z' in meta:
        ent['z'] = _r(meta['z'])          # 伏図: この床のレベル (mm)
    if 'levels' in meta:
        ent['levels'] = [{'name': _t(v['name']), 'z': _r(v['z'])} for v in meta['levels']]
    if fig.kind == 'list':
        ent['sections'] = list(meta.get('sections', []))
        ent['page'] = meta.get('page')
        ent['pages'] = meta.get('pages')
    return ent


def _line_record(M, i, mats):
    m = M.members[i]
    el_mat = mats.get(int(m['elem']))
    z1, z2 = float(m['p1'][2]), float(m['p2'][2])
    return {'id': int(m['elem']), 'elements': [int(e) for e in m['eles']],
            'kind': member_kind(m), 'code': _t(m['code'] or ''), 'section': _t(m['sec_name']),
            'section_no': int(m['sec_no']), 'material': el_mat,
            'floor': None, 'floors': [], 'axis': None, 'axes': [],
            'story': None, 'story_name': None, 'story_from': None,
            'n1': _xyz(m['p1']), 'n2': _xyz(m['p2']),
            'b': _r(m['b'] * 1000.0), 'd': _r(m['d'] * 1000.0),
            'truss': bool(m['truss']), 'pin1': bool(m['pin1']), 'pin2': bool(m['pin2']),
            # 以下は組み立て用 (書き出す前に消す)
            '_z': (z1 + z2) / 2.0 if m['kind'] == 'beam' else min(z1, z2),
            '_floor_groups': member_floor_groups(M).get(i, []),
            '_xy': [(float(m['p1'][0]), float(m['p1'][1])), (float(m['p2'][0]), float(m['p2'][1]))],
            '_places': {'floor': [], 'axis': []}}


def _wall_record(M, el_id, mats):
    el = next((e for e in M.ir.elements if int(e.id) == int(el_id)), None)
    ids = [k for k in (el.nodes if el else []) if k in M.node_xyz]
    nodes = [_xyz(M.node_xyz[k]) for k in ids]
    name = M.ir.thickness_names.get(el.prop, '') if el else ''
    t = M.ir.thicknesses.get(el.prop) if el else None
    return {'id': int(el_id), 'elements': [int(el_id)], 'kind': 'wall', 'code': _t(name),
            'section': _t(name), 'section_no': None,
            'thickness_no': int(el.prop) if el else None,
            't': _r(t * 1000.0) if t else None, 'material': mats.get(int(el_id)),
            'floor': None, 'floors': [], 'axis': None, 'axes': [],
            'story': None, 'story_name': None, 'story_from': None, 'nodes': nodes,
            '_z': min((float(M.node_xyz[k][2]) for k in ids), default=0.0),
            '_floor_groups': [],
            '_xy': [(float(M.node_xyz[k][0]), float(M.node_xyz[k][1])) for k in ids],
            '_places': {'floor': [], 'axis': []}}


def _off_frame(rec, meta):
    """部材が構面の線 (通り芯) からどれだけ離れているか [mm、1mm で丸め] (平面の端点の最大)."""
    if 'frame_point' not in meta:
        return 0
    (cx, cy), (dx, dy) = meta['frame_point'], meta['frame_dir']
    d = max((abs((x - cx) * dy - (y - cy) * dx) for x, y in rec['_xy']), default=0.0)
    return int(round(d * 1000.0))


def _finish_places(rec):
    """floor / axis と floors / axes を決める.

    主な図 (下柱の×・面外の梁の断面記号だけ、ではない図) を先に、次の順で並べる:
      伏図: モデルの床の順 (低い方から)
      軸組図: 部材が乗っている構面 (構面の線からの距離が小さい順) → X 通りを先 → モデルの通りの順
    floor / axis はその先頭の主な図 (無ければ None)。画面で選んだ順には左右されない。
    """
    for one, alls in (('floor', 'floors'), ('axis', 'axes')):
        seen, ordered = set(), []
        for _main_rank, key, name in sorted(rec['_places'][one]):
            if name not in seen:
                seen.add(name)
                ordered.append((_main_rank, name))
        rec[alls] = [n for _r_, n in ordered]
        rec[one] = next((n for r_, n in ordered if r_ == 0), None)
        rec['_main_' + alls] = [n for r_, n in ordered if r_ == 0]


def build(M, figs, options, src_path):
    """図の列 (1 ファイル分) → 付属 JSON の dict."""
    origins = layout(figs)
    mats = {}
    for e in M.ir.elements:
        mat = M.ir.materials.get(e.mat)
        mats[int(e.id)] = mat.name if mat else None
    plan_order = {p['key']: k for k, p in enumerate(plan_keys(M))}
    plan_z = [(p['label'], plan_key_z(M, p['key'])) for p in plan_keys(M)]
    frame_order = {f['key']: k for k, f in enumerate(auto_frames(M))}
    figures, records = [], {}
    for fig, origin in zip(figs, origins):
        ent = _figure_entry(fig, origin)
        mids = []
        if fig.kind == 'list':
            secs = set(ent['sections'])
            for i, m in enumerate(M.members):
                if int(m['sec_no']) in secs:
                    mids.append(int(m['elem']))
                    if int(m['elem']) not in records:
                        records[int(m['elem'])] = _line_record(M, i, mats)
        else:
            for mid, how in (fig.meta or {}).get('members', {}).items():
                mid = int(mid)
                mids.append(mid)
                if mid not in records:
                    records[mid] = (_line_record(M, how[1], mats) if how[0] == 'line'
                                    else _wall_record(M, how[1], mats))
                rec = records[mid]
                rank = 0 if how[2] else 1          # 主な図が先
                if fig.kind == 'plan':
                    rec['_places']['floor'].append(
                        (rank, (plan_order.get(fig.key, 9999),), ent['name']))
                elif fig.kind == 'axis':
                    fam = 0 if abs(fig.meta['frame_dir'][0]) < 0.1 else 1    # X 通りが先
                    rec['_places']['axis'].append(
                        (rank, (_off_frame(rec, fig.meta), fam, frame_order.get(fig.key, 9999)),
                         ent['name']))
        ent['members'] = mids
        figures.append(ent)
    members = []
    for rec in records.values():
        _finish_places(rec)
        rec['story'], rec['story_name'], rec['story_from'] = _story(
            M, rec['_z'], rec['_floor_groups'] + rec['_main_floors']
            + [g for g, z in plan_z if abs(z - rec['_z']) <= STORY_TOL])   # 最後に高さが合う床
        for k in [k for k in rec if k.startswith('_')]:
            del rec[k]
        members.append(rec)
    return {
        'format': FORMAT, 'version': VERSION,
        'source': {'mgt': os.path.basename(src_path),
                   'exported': datetime.datetime.now().isoformat(timespec='seconds'),
                   'mgtkit': _mgtkit_version()},
        'paper': options.get('paper'),
        'scale': figs[0].scale if figs else None,
        'text_mm': round(float(options.get('text_mm') or 0.0), 4),
        'grid_mm': round(float(options.get('grid_mm') or 0.0), 4),
        'figures': figures,
        'members': members,
    }


def write(dxf_path, M, figs, options, src_path):
    """DXF と同名の .json を書く → そのパス."""
    out = os.path.splitext(dxf_path)[0] + '.json'
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(build(M, figs, options, src_path), f, ensure_ascii=False, indent=1)
    return out
