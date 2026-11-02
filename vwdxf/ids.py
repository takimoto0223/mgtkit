# -*- coding: utf-8 -*-
"""部材 ID の印 (DXF の XDATA) と付属 JSON.

structural-cad (構造図 CAD) が、符号の一括変更・更新の差分反映に使う。
仕様は structural-cad_ソース/docs/mgtkit_DXF_ID_実装メモ.md (2026-10-13)。
この版は structural-cad が読む項目を中心に作り直したもの (2026-10-31)。メモとの違い:
  - story / story_name / story_from は常に null (structural-cad は使わない)
  - origin は通り芯から決まらなければ図の範囲の左下 (origin_from = "extent")

XDATA: アプリ名 STRUCTURAL_CAD、グループコード 1000 の "KEY=VALUE" を
MID, EL, KIND, CODE, SEC, SECNO, ROLE, FIG, FL, AX の順に (無いキーは飛ばす)。全図形に ROLE と FIG が付く。
ROLE: outline / code / section / hatch / grid / grid-mark / level / level-mark / title /
      list-cell / list-frame / open-bay (木造の X) / other
"""
import datetime
import json
import os

APPID = 'STRUCTURAL_CAD'
XKEYS = ('MID', 'EL', 'KIND', 'CODE', 'SEC', 'SECNO', 'ROLE', 'FIG', 'FL', 'AX')


def member_kind(m):
    return 'truss' if m.get('truss') else m['kind']


def member_meta(m, role, **extra):
    """線材 (柱・梁・斜材・トラス) の印. MID = 結合した部材の代表の要素番号."""
    d = {'MID': int(m['elem']), 'KIND': member_kind(m), 'CODE': m.get('code') or '',
         'SEC': m.get('sec_name') or '', 'ROLE': role}
    eles = [int(e) for e in (m.get('eles') or [])]
    if len(eles) >= 2:
        d['EL'] = ','.join(str(e) for e in eles)
    d.update({k: v for k, v in extra.items() if v is not None})
    return d


def wall_meta(el_id, name, role, **extra):
    """壁・面要素の印. MID = 面要素の番号、CODE = 板厚の名前."""
    d = {'MID': int(el_id), 'KIND': 'wall', 'CODE': name or '', 'ROLE': role}
    d.update({k: v for k, v in extra.items() if v is not None})
    return d


def plain_meta(role, **extra):
    d = {'ROLE': role}
    d.update({k: v for k, v in extra.items() if v is not None})
    return d


def figure_id(fig):
    """図の ID: plan:<床>[:beam|:column] / axis:<構面> / list:<題名>[:<頁>]."""
    key = str(fig.key)
    name = key[2:] if key.startswith('G:') else key
    if fig.kind == 'plan':
        return 'plan:%s' % name + (':%s' % fig.sheet if fig.sheet in ('beam', 'column') else '')
    if fig.kind == 'axis':
        return 'axis:%s' % name
    info = fig.info or {}
    lid = 'list:%s' % info.get('name', name)
    return lid + (':%d' % info['page'] if info.get('pages', 1) > 1 else '')


def xdata_tags(meta, fig_id, clean):
    """meta → XDATA の [(1000, 'KEY=VALUE')]. clean = DXF に書ける文字への置き換え (render/dxf.dxf_text)."""
    d = dict(meta or {'ROLE': 'other'})
    d.setdefault('ROLE', 'other')
    d['FIG'] = fig_id
    out = []
    for k in XKEYS:
        v = d.get(k)
        if v is None or v == '':
            continue
        v = clean(str(v))
        if k == 'EL' and len(v.encode('cp932', 'replace')) > 240:
            continue          # DXF の文字列の上限 (255 バイト)。全部は JSON の elements にある
        out.append((1000, '%s=%s' % (k, v)))
    return out


# ---------------------------------------------------------------------------
# 部材の床 (軸組図の FL) と付属 JSON
# ---------------------------------------------------------------------------

def floor_index(M):
    """床グループ → (名前, z [m], 要素の集合, 節点の集合) の低い順 (キャッシュ)."""
    if not hasattr(M, '_id_floors'):
        from .members import group_floor_nodes, plan_key_z, plan_keys
        out = []
        for pl in plan_keys(M):
            g = pl['key'][2:]
            try:
                z = plan_key_z(M, pl['key'])
            except ValueError:
                continue
            out.append((g, z, set(int(e) for e in M.groups[g].elem_ids),
                        set(int(n) for n in group_floor_nodes(M, g))))
        M._id_floors = out
    return M._id_floors


def member_floor(M, m):
    """部材の床 (実装メモの規則): 梁・斜材は ELEM_LIST に入っている床グループ、柱は下端の節点が載る床グループ (低い方)."""
    fl = floor_index(M)
    if m['kind'] == 'column':
        n_lo = m['n1'] if float(m['p1'][2]) <= float(m['p2'][2]) else m['n2']
        hit = [g for g, _z, _e, ns in fl if int(n_lo) in ns]
    else:
        els = set(int(e) for e in (m.get('eles') or [m['elem']]))
        hit = [g for g, _z, es, _n in fl if es & els]
    return hit[0] if hit else None


def _mm3(p):
    return [round(float(v) * 1000.0, 3) for v in p]


def _member_record(M, m):
    from . import sections
    mat = M.ir.materials.get(next((e.mat for e in M.ir.elements if e.id == m['elem']), None))
    return {'id': int(m['elem']), 'elements': [int(e) for e in (m.get('eles') or [m['elem']])],
            'kind': member_kind(m), 'code': m.get('code') or '', 'section': m.get('sec_name') or '',
            'section_no': int(m['sec_no']), 'material': (mat.name if mat else ''),
            'floor': None, 'floors': [], 'axis': None, 'axes': [],
            'story': None, 'story_name': None, 'story_from': None,
            'n1': _mm3(m['p1']), 'n2': _mm3(m['p2']),
            'b': round(float(m.get('b') or sections.width(m['sec'])) * 1000.0, 3),
            'd': round(float(m.get('d') or sections.depth(m['sec'])) * 1000.0, 3),
            'truss': bool(m.get('truss')), 'pin1': bool(m.get('pin1')), 'pin2': bool(m.get('pin2'))}


def _wall_record(M, el):
    t = float(M.ir.thicknesses.get(el.prop, 0.0))
    name = M.ir.thickness_names.get(el.prop, '')
    mat = M.ir.materials.get(el.mat)
    return {'id': int(el.id), 'elements': [int(el.id)], 'kind': 'wall', 'code': name,
            'section': name, 'section_no': None, 'thickness_no': int(el.prop),
            't': round(t * 1000.0, 3), 'material': (mat.name if mat else ''),
            'floor': None, 'floors': [], 'axis': None, 'axes': [],
            'story': None, 'story_name': None, 'story_from': None,
            'nodes': [_mm3(M.node_xyz[n]) for n in el.nodes if n in M.node_xyz]}


_MAIN_ROLES = ('outline', 'code', 'hatch')


def _grids_of(fig, origin):
    """図の通り芯 (印 ROLE=grid の線) → grids と origin (DXF 座標)."""
    from .draw.primitives import Line
    ox, oy = origin
    X, Y, other = [], [], []
    for p in fig.prims:
        if not isinstance(p, Line) or not p.meta or p.meta.get('ROLE') != 'grid':
            continue
        a = (p.p1[0] + ox, p.p1[1] + oy)
        b = (p.p2[0] + ox, p.p2[1] + oy)
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = (dx * dx + dy * dy) ** 0.5 or 1.0
        name = p.meta.get('AX', '')
        if abs(dx) / L < 0.01:
            X.append({'name': name, 'coord': round((a[0] + b[0]) / 2.0, 3)})
        elif abs(dy) / L < 0.01:
            Y.append({'name': name, 'coord': round((a[1] + b[1]) / 2.0, 3)})
        else:
            other.append({'name': name, 'family': None, 'point': [round(a[0], 3), round(a[1], 3)],
                          'dir': [round(dx / L, 6), round(dy / L, 6)]})
    g = {'X': sorted(X, key=lambda v: v['coord']), 'Y': sorted(Y, key=lambda v: v['coord'])}
    if other:
        g['other'] = other
    return g


def figure_record(M, fig, origin, levels):
    """図 1 枚の figures[] の行."""
    from .draw.primitives import prims_bounds
    fid = figure_id(fig)
    ox, oy = origin
    bx0, by0, bx1, by1 = prims_bounds(fig.prims)
    bbox = [round(bx0 + ox, 3), round(by0 + oy, 3), round(bx1 + ox, 3), round(by1 + oy, 3)]
    info = fig.info or {}
    key = str(fig.key)
    rec = {'id': fid, 'kind': fig.kind, 'name': info.get('name', key[2:] if key.startswith('G:') else key),
           'variant': (fig.sheet if fig.kind == 'plan' else None), 'title': info.get('title', fig.title),
           'scale': int(fig.scale), 'bbox': bbox, 'offset': [round(ox, 3), round(oy, 3)]}
    mids = sorted({p.meta['MID'] for p in fig.prims if p.meta and 'MID' in p.meta})
    if fig.kind in ('plan', 'axis'):
        g = _grids_of(fig, origin)
        lv = [{'name': n, 'z': round(z * 1000.0, 3)} for n, z in (levels or [])]
        if fig.kind == 'plan':
            rec['grids'] = g
            if g['X'] and g['Y']:
                rec['origin'], rec['origin_from'] = [g['X'][0]['coord'], g['Y'][0]['coord']], 'grid'
            else:
                rec['origin'], rec['origin_from'] = [bbox[0], bbox[1]], 'extent'
            if info.get('z') is not None:
                rec['z'] = round(info['z'] * 1000.0, 3)
                if not any(abs(v['z'] - rec['z']) < 1.0 for v in lv):
                    lv = sorted(lv + [{'name': rec['name'], 'z': rec['z']}], key=lambda v: v['z'])
        else:
            rec['frame_dir'] = [round(v, 6) for v in info.get('frame_dir', (1.0, 0.0))]
            rec['grids'] = {'X': g['X'] + g['Y'], 'Y': []}
            if g['X'] and lv:
                rec['origin'], rec['origin_from'] = [g['X'][0]['coord'], lv[0]['z'] + oy], 'grid'
            else:
                rec['origin'], rec['origin_from'] = [bbox[0], bbox[1]], 'extent'
        rec['levels'] = lv
    else:
        rec['sections'] = list(info.get('sections', []))
        rec['page'] = int(info.get('page', 1))
        rec['pages'] = int(info.get('pages', 1))
        secs = set(rec['sections'])
        mids = sorted(int(m['elem']) for m in M.members if int(m['sec_no']) in secs)
    rec['members'] = mids
    return rec


def build_json(M, kind, figs, origins, levels, o):
    """1 つの DXF (図の種類ごと) の付属 JSON (dict)."""
    walls = {e.id: e for e in M.ir.elements if not e.is_line}
    by_mid = {int(m['elem']): m for m in M.members}
    figures, members = [], {}
    for fig, origin in zip(figs, origins):
        rec = figure_record(M, fig, origin, levels)
        figures.append(rec)
        name = rec['name']
        # 部材がこの図にどう出たか (主な図 = 外形・符号・ハッチが出た図。断面記号だけの図は従)
        seen = {}
        for p in fig.prims:
            mid = p.meta.get('MID') if p.meta else None
            if mid is None:
                continue
            seen[mid] = seen.get(mid, False) or p.meta.get('ROLE') in _MAIN_ROLES
        if kind == 'list':
            seen = {mid: True for mid in rec['members']}
        for mid, main in seen.items():
            r = members.get(mid)
            if r is None:
                if mid in by_mid:
                    r = _member_record(M, by_mid[mid])
                elif mid in walls:
                    r = _wall_record(M, walls[mid])
                else:
                    continue
                r['_main'], r['_sub'] = [], []
                members[mid] = r
            (r['_main'] if main else r['_sub']).append(name)
    for r in members.values():
        main = list(dict.fromkeys(r.pop('_main')))
        names = list(dict.fromkeys(main + r.pop('_sub')))      # 出た図すべて (主な図が先)
        if kind == 'plan':
            r['floors'] = names
            r['floor'] = main[0] if main else None             # 主な図が無ければ null (実装メモ)
        elif kind == 'axis':
            r['axes'] = names
            r['axis'] = main[0] if main else None
    return {'format': 'mgtkit-dxf', 'version': 1,
            'source': {'mgt': os.path.basename(getattr(M, 'path', '') or ''),
                       'exported': datetime.datetime.now().replace(microsecond=0).isoformat(),
                       'mgtkit': _mgtkit_version()},
            'paper': o.get('paper', 'A3'), 'scale': int(figs[0].scale) if figs else 0,
            'text_mm': round(float(o.get('text_mm', 0.0)), 4),
            'grid_mm': round(float(o.get('grid_mm', 0.0)), 4),
            'figures': figures, 'members': sorted(members.values(), key=lambda r: r['id'])}


def _mgtkit_version():
    try:
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'version.json')
        with open(p, encoding='utf-8') as f:
            return str(json.load(f).get('version', '')).lstrip('v')
    except (OSError, ValueError):
        return ''


def write_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return path


__all__ = ['APPID', 'build_json', 'figure_id', 'member_floor', 'member_meta', 'plain_meta',
           'wall_meta', 'write_json', 'xdata_tags']
