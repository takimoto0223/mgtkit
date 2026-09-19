# -*- coding: utf-8 -*-
"""*MEMBER (分割部材) の結合 → 表示用の要素列.

tools/struct_cad_py の merge.py を移植 (2026-09-19)。*MEMBER の子要素を、内部のヒンジ
(*FRAME-RLS の解放) で区切ったセグメントごとに 1 本にする。一直線に続くだけの別部材は
連結しない (構造図βの自動連結はやめた)。

戻り値の要素は、結合したものは代表要素 (セグメントの中で *MEMBER の並び順が最初のもの) の ID を持つ。
"""
from __future__ import annotations

from collections import Counter

from .model import Element


def _endpoints(subs):
    """要素群の自由端 (1 回だけ現れる節点) 2 つ。分岐していれば None."""
    cnt = Counter()
    for el in subs:
        cnt[el.nodes[0]] += 1
        cnt[el.nodes[1]] += 1
    ends = [n for n, c in cnt.items() if c == 1]
    return ends if len(ends) == 2 else None


def _end_release(ir, node, subs):
    for el in subs:
        r = ir.releases.get(el.id, (False, False))
        if el.nodes[0] == node:
            return r[0]
        if el.nodes[1] == node:
            return r[1]
    return False


def _segments(ir, subs):
    """子要素を内部ヒンジで分けたセグメントのリスト (剛で続くところだけ結合)."""
    parent = {s.id: s.id for s in subs}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    node_els = {}
    for s in subs:
        for nid in s.nodes:
            node_els.setdefault(nid, []).append(s)
    for nid, els in node_els.items():
        if len(els) != 2:
            continue
        a, b = els
        ra = ir.releases.get(a.id, (False, False))[a.nodes.index(nid)]
        rb = ir.releases.get(b.id, (False, False))[b.nodes.index(nid)]
        if ra or rb:
            continue
        parent[find(a.id)] = find(b.id)
    groups = {}
    for s in subs:
        groups.setdefault(find(s.id), []).append(s)
    return list(groups.values())


def merge_members(ir):
    """→ (線要素のリスト, 解放 {要素ID: (i端, j端)}, 通過節点 {要素ID: [節点]}, 元要素 {要素ID: [元ID]})."""
    ele_by_id = {e.id: e for e in ir.elements}
    used = set()
    merged = []
    releases = dict(ir.releases)
    covered = {}
    origin = {}
    for melems in ir.members:
        subs = [ele_by_id[e] for e in melems if e in ele_by_id and ele_by_id[e].is_line]
        if len(subs) < 2:
            continue
        for seg in _segments(ir, subs):
            if len(seg) < 2:
                continue
            ends = _endpoints(seg)
            if ends is None:
                continue
            seg_ids = {s.id for s in seg}
            prim_id = next((e for e in melems if e in seg_ids), seg[0].id)
            prim = ele_by_id[prim_id]
            merged.append(Element(prim_id, prim.type, prim.mat, prim.prop,
                                  [ends[0], ends[1]], prim.angle, prim.role))
            cov = []
            for el in seg:
                used.add(el.id)
                for nid in el.nodes:
                    if nid not in cov:
                        cov.append(nid)
            covered[prim_id] = cov
            origin[prim_id] = [e for e in melems if e in seg_ids]
            releases[prim_id] = (_end_release(ir, ends[0], seg), _end_release(ir, ends[1], seg))
    lines = [e for e in ir.elements if e.is_line and e.id not in used] + merged
    return lines, releases, covered, origin


__all__ = ['merge_members']
