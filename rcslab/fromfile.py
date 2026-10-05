# -*- coding: utf-8 -*-
"""MIDAS API を使わずに、mgt と板要素断面力テーブルの txt から作図データを作る.

    mgt : 節点 (*NODE)・要素 (*ELEMENT、板は N1〜N4 と ANGLE)・
          厚さ (*THICKNESS の名前と厚さ)・単位 (*UNIT)
    txt : MIDAS の結果テーブル「板要素断面力」をテキスト保存したもの
          (タブ区切り、1行目が見出し)。全ケース・全要素が入っていてよく、
          対象の厚さIDの要素・対象ケースの要素中心行だけを拾う。
          見出しは日本語 (要素/荷重/節点=中央) でも英語 (Elem/Load/Node=Cent)
          でもよい。単位は見出しの括弧 (kN/m, kN·m/m など) から換算する。

検討対象は「厚さID」か「グループ (mgt の *GROUP)」で選ぶ (candidates / load)。
戻り値の形は data.py の説明を参照。
"""

import datetime
import os
import re

from mgtkit.rcslab.data import (DIST as _DIST, FORCE as _FORCE,
                                normalize_case, strip_kind as _strip_kind,
                                target_key)

_HEAD_ELEM = ('要素', 'Elem')
_HEAD_LOAD = ('荷重', 'Load')
_HEAD_NODE = ('節点', 'Node')
_CENTER = ('中央', 'Cent', 'Center')


def _read_text(path):
    with open(path, 'rb') as f:
        raw = f.read()
    for enc in ('utf-8-sig', 'cp932'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


# ---------------------------------------------------------------------------
# mgt
# ---------------------------------------------------------------------------

def read_mgt(path):
    """{'nodes','lines','plates','thik_all','fdist'} (座標は m)."""
    sec, nodes, lines, plates, thik = None, {}, [], {}, {}
    fdist = 1.0
    for raw in _read_text(path).splitlines():
        s = raw.strip()
        if not s or s.startswith(';'):
            continue
        if s.startswith('*'):
            sec = s.split(';')[0].split(',')[0].strip().upper()
            continue
        f = [x.strip() for x in s.split(';')[0].split(',')]
        try:
            if sec == '*UNIT' and len(f) >= 2:
                fdist = _DIST.get(f[1].upper(), 1.0)
            elif sec == '*NODE' and len(f) >= 4:
                nodes[int(f[0])] = [float(f[1]), float(f[2]), float(f[3])]
            elif sec == '*ELEMENT' and len(f) >= 6:
                typ = f[1].upper()
                if typ in ('PLATE', 'WALL', 'PLSTRS', 'PLSTRN', 'AXISYM'):
                    nd = [int(x) for x in f[4:8] if x and int(x)]
                    ang = float(f[9]) if len(f) > 9 and f[9] else 0.0
                    plates[int(f[0])] = {'nodes': nd, 'thik': int(f[3]),
                                         'angle': ang}
                elif typ != 'SOLID':
                    lines.append([int(f[4]), int(f[5])])
            elif sec == '*THICKNESS' and len(f) >= 5 \
                    and f[1].upper() == 'VALUE':
                thik[int(f[0])] = {'name': f[2], 't': float(f[4])}
        except (ValueError, IndexError):
            continue
    nodes = {k: [v[0] * fdist, v[1] * fdist, v[2] * fdist]
             for k, v in nodes.items()}
    if not nodes or not plates:
        raise ValueError('mgt から節点・板要素を読めませんでした: %s' % path)
    return {'nodes': nodes, 'lines': lines, 'plates': plates,
            'thik_all': thik, 'fdist': fdist}


# ---------------------------------------------------------------------------
# 板要素断面力テーブル (txt)
# ---------------------------------------------------------------------------

def _unit_of(head):
    """'Mxx(kN·m/m)' → (力の係数, 長さの係数)."""
    m = re.search(r'\(([^)]*)\)', head)
    if not m:
        return 1.0, 1.0
    u = m.group(1).replace('·', '*').replace('・', '*').replace(' ', '')
    num, _, den = u.partition('/')
    fu = re.split(r'[*]', num)[0].upper()
    return _FORCE.get(fu, 1.0), _DIST.get(den.upper(), 1.0) if den else 1.0


def read_table(path, case_names, elems):
    """{ケース名(ST付き): {要素: [Mxx,Myy,Mxy,Mmax,Mmin,Mang,Vxx,Vyy]}}."""
    text = _read_text(path).splitlines()
    if not text:
        raise ValueError('板要素断面力の txt が空です: %s' % path)
    head = [h.strip() for h in text[0].split('\t')]
    base = [re.sub(r'\(.*\)', '', h).strip() for h in head]

    def _col(names):
        for i, b in enumerate(base):
            if b in names:
                return i
        raise ValueError('txt の見出しに %s がありません。MIDAS の「板要素'
                         '断面力」テーブルをタブ区切りで保存したものを指定'
                         'してください。(見出し: %s)'
                         % ('/'.join(names), ' '.join(head[:6])))

    ie, il, iN = _col(_HEAD_ELEM), _col(_HEAD_LOAD), _col(_HEAD_NODE)
    idx = [_col((k,)) for k in ('Mxx', 'Myy', 'Mxy', 'Mmax', 'Mmin')]
    angs = [i for i, b in enumerate(base) if b in ('角度', 'Angle')]
    idx.append(angs[-1] if angs else -1)
    idx += [_col(('Vxx',)), _col(('Vyy',))]
    fm, _ = _unit_of(head[idx[0]])
    fv, dv = _unit_of(head[idx[6]])
    fac = [fm] * 5 + [1.0] + [fv / dv] * 2
    want = {_strip_kind(c): normalize_case(c) for c in case_names}
    elems = set(elems)
    out = {v: {} for v in want.values()}
    found_cases = set()
    for line in text[1:]:
        p = line.split('\t')
        if len(p) <= max(idx):
            continue
        lc = _strip_kind(p[il])
        found_cases.add(lc)
        if lc not in want or p[iN].strip() not in _CENTER:
            continue
        try:
            e = int(p[ie])
        except ValueError:
            continue
        if e not in elems:
            continue
        out[want[lc]][e] = [float(p[i]) * f if i >= 0 else 0.0
                            for i, f in zip(idx, fac)]
    missing = [c for c, rows in out.items() if not rows]
    if missing:
        raise ValueError('txt に荷重ケース %s の要素中心 (中央/Cent) 行が'
                         'ありません。txt にあるケース: %s'
                         % ('・'.join(missing),
                            ', '.join(sorted(found_cases)) or 'なし'))
    return out


def _groups(mgt_path, plates):
    """{グループ名: [板要素...]} (板要素を含むグループだけ)."""
    from mgtkit.mgt import mgtopen_group
    names, elems, _nodes = mgtopen_group(mgt_path)
    out = {}
    for name, els in zip(names, elems):
        pl = sorted(int(e) for e in list(els) if int(e) in plates)
        if pl:
            out[str(name)] = pl
    return out


def _thick_summary(g, els):
    """要素群の厚さ: (最も多い厚さID, その厚さmm, 'S28×120・S12×8' 形式)."""
    cnt = {}
    for e in els:
        cnt[g['plates'][e]['thik']] = cnt.get(g['plates'][e]['thik'], 0) + 1
    order = sorted(cnt, key=lambda k: -cnt[k])
    tid = order[0]
    t = g['thik_all'].get(tid, {'name': str(tid), 't': 0.0})
    txt = '・'.join('%s×%d' % (g['thik_all'].get(k, {'name': str(k)})['name'],
                               cnt[k]) for k in order)
    return tid, t['t'] * g['fdist'] * 1000, txt


def candidates(mgt_path, kind):
    """画面の選択表: 厚さID またはグループの一覧 (板要素を含むものだけ)."""
    g = read_mgt(mgt_path)
    out = []
    if kind == 'group':
        for name, els in _groups(mgt_path, g['plates']).items():
            _tid, t, txt = _thick_summary(g, els)
            out.append({'key': target_key('group', name), 'id': name,
                        'name': name, 't': t, 'n': len(els), 'detail': txt})
    else:
        cnt = {}
        for p in g['plates'].values():
            cnt[p['thik']] = cnt.get(p['thik'], 0) + 1
        for tid in sorted(cnt):
            t = g['thik_all'].get(tid, {'name': str(tid), 't': 0.0})
            out.append({'key': target_key('thick', tid), 'id': tid,
                        'name': t['name'], 't': t['t'] * g['fdist'] * 1000,
                        'n': cnt[tid], 'detail': ''})
    return out


def load(mgt_path, txt_path, cases, kind, ids, log=print):
    """mgt + txt → 作図データ。kind='thick' (ids=厚さID) / 'group' (ids=名前)."""
    g = read_mgt(mgt_path)
    targets = {}
    if kind == 'group':
        groups = _groups(mgt_path, g['plates'])
        for name in ids:
            name = str(name)
            if name not in groups:
                raise ValueError('グループ %s に板要素がありません (mgt の '
                                 '*GROUP を確認してください)。' % name)
            _tid, t, _txt = _thick_summary(g, groups[name])
            targets[target_key('group', name)] = {
                'kind': 'group', 'id': name, 'name': name, 't': t,
                'elems': groups[name]}
    else:
        for tid in ids:
            tid = int(tid)
            t = g['thik_all'].get(tid)
            if t is None:
                raise ValueError('厚さID %s が mgt の *THICKNESS にありません。'
                                 % tid)
            els = sorted(e for e, p in g['plates'].items() if p['thik'] == tid)
            if not els:
                log('厚さID %d (%s) の板要素がありません。' % (tid, t['name']))
            targets[target_key('thick', tid)] = {
                'kind': 'thick', 'id': tid, 'name': t['name'],
                't': t['t'] * g['fdist'] * 1000, 'elems': els}
    if not targets:
        raise ValueError('検討対象を1つ以上選んでください。')
    allel = sorted({e for t in targets.values() for e in t['elems']})
    if not allel:
        raise ValueError('選んだ対象に板要素がありません。')
    forces = read_table(txt_path, [c['name'] for c in cases], allel)
    for c, rows in forces.items():
        miss = len(allel) - len(rows)
        log('荷重ケース %s: %d 要素%s' % (
            c, len(rows), (' (txt に無い要素 %d)' % miss) if miss else ''))
    return {
        'model': os.path.abspath(mgt_path),
        'source': os.path.abspath(txt_path),
        'fetched': datetime.datetime.fromtimestamp(
            os.path.getmtime(txt_path)).strftime('%Y-%m-%d %H:%M'),
        'nodes': g['nodes'], 'lines': g['lines'], 'plates': g['plates'],
        'targets': targets,
        'cases': [{'label': str(c.get('label') or _strip_kind(c['name'])),
                   'name': normalize_case(c['name'])} for c in cases],
        'forces': forces,
    }
