# -*- coding: utf-8 -*-
"""作図データの保存・読込と、単位・荷重ケース名の共通処理.

作図データ (dict) の形:
    model   : mgt のパス
    source  : 断面力 txt のパス
    fetched : txt の更新日時 (表示用)
    nodes   : {節点: [x, y, z]} (m)
    lines   : [[i, j], ...] 背景に描く線材
    plates  : {要素: {'nodes': [...], 'thik': 厚さID, 'angle': 度}}
    targets : {キー: {'kind': 'thick'|'group', 'id': 厚さID/グループ名,
                     'name': 断面名, 't': 板厚mm, 'elems': [要素...]}}
    cases   : [{'label': 'TL', 'name': '1(ST)'}, ...] (先頭が長期)
    forces  : {ケース名: {要素: [Mxx,Myy,Mxy,Mmax,Mmin,Mang,Vxx,Vyy]}}
"""

import json
import re

#: 力・長さの単位 → kN, m への係数
FORCE = {'KN': 1.0, 'N': 1e-3, 'TONF': 9.80665, 'KGF': 9.80665e-3,
         'KIPS': 4.4482216, 'LBF': 4.4482216e-3}
DIST = {'M': 1.0, 'CM': 1e-2, 'MM': 1e-3, 'FT': 0.3048, 'IN': 0.0254}


def normalize_case(name):
    """'1' → '1(ST)'。(ST)/(CB) などの種別付きはそのまま."""
    s = str(name).strip()
    return s if re.search(r'\([A-Za-z]+\)$', s) else s + '(ST)'


def strip_kind(name):
    return re.sub(r'\([A-Za-z]+\)$', '', str(name).strip())


def target_key(kind, ident):
    return ('T%s' % ident) if kind == 'thick' else ('G:%s' % ident)


def save_data(data, path):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)


def load_data(path):
    with open(path, encoding='utf-8') as f:
        d = json.load(f)
    if 'targets' not in d:
        raise ValueError('古い形式の取得データです。txt から読み込み直して'
                         'ください。')
    d['nodes'] = {int(k): v for k, v in d['nodes'].items()}
    d['plates'] = {int(k): v for k, v in d['plates'].items()}
    d['forces'] = {c: {int(e): v for e, v in rows.items()}
                   for c, rows in d['forces'].items()}
    return d
