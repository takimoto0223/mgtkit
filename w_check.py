# -*- coding: utf-8 -*-
"""木造断面検定 (W_SB/W_SR_analysis(+_text), TSR, W_ratio_analysis 等) の逐語移植.

元コード:
  msrc/structural_function/W/NMQ_analysis/W_SB_analysis.m
  msrc/structural_function/W/NMQ_analysis/W_SR_analysis.m
  msrc/structural_function/W/NMQ_analysis_text/W_SB_analysis_text.m
  msrc/structural_function/W/NMQ_analysis_text/W_SR_analysis_text.m
  msrc/structural_function/W/tension_analysis/TSR_analysis.m (W_TSR_analysis)
  msrc/privatetool_function/MIDAS/ratio/W/W_ratio_analysis.m
  msrc/privatetool_function/MIDAS/ratio/W/W_truss_ratio_analysis.m
  木材許容応力度表: privatetool_function/MIDAS/ratio/W/W_AL.mat
      → data/W_AL.json (scipy.io.loadmat でJSON化して同梱)

共通規約 (s_check.py と同じ流儀):
  - 断面力 stress は kN・m 系。列 = [軸力N, せん断力弱軸方向Fy,
    せん断力強軸方向Fz, 曲げモーメント強軸My, 曲げモーメント弱軸Mz]。
    行 = 要素あたり3行 (i端/中央/j端)。
  - 内部で N/mm2 系へ変換。断面性能は cm 系 (B*関数出力)。
  - length = [座屈長さ(強)m, 座屈長さ(弱)m, 圧縮フランジ間距離m]。
  - timecase: >10=短期, 1=長期(wal_up==1.3なら中長期), 9=中長期, 10=中短期。
  - wal_up: MIDASratioplot_fig.m 冒頭の questdlg「長期荷重の木部材の
    許容応力度は？」の引数化。長期=1.0 / 中長期(積雪)=1.3 (既定は長期)。
  - w_al = W_AL表の1行 [Fc Ft Fby Fs Fbz ...] (基準強度 N/mm2)。
  - ratio 出力は [ratio_cb ratio_sz ratio_sy 0] の 行数×4列
    (鉄骨と同じ4列構成。4列目(組合せ応力)は元コードで 0 固定)。
  - MATLABの `if ベクトル>0` は全要素が真のときのみ真 → np.all で再現。
  - *_analysis_text は strvcat による行の積み上げ → list[str] を返す。

元コードの怪しい挙動もそのまま再現している (改変禁止のため)。
(例: W_SB_analysis の細長比150超で fc=0.000001 とする231017國江編集、
 W_SB_analysis と _text で min(B,D) の閾値が 1/50 と異なる点、
 W_truss_ratio_analysis の詳細文で設計用せん断力を /1000 表示する点など)
"""

import json
import os
import re

import numpy as np

from .util import find_index
from .alst import ALST_S
from .secprops import BSB, BSR
from .mgt import node_index_get
from .s_check import (
    _matlabenv, _m, _num2str, _maxratio_index, _st_index,
)


# ===========================================================================
# W_AL.mat (木材の許容応力度表) → data/W_AL.json
# ===========================================================================

_W_AL_CACHE = None


def _load_W_AL():
    """W_AL.mat 同梱JSONの読み込み (キャッシュ).

    戻り値: (W_AL ndarray(77x16), W_name list[str])
      W_AL 列1-5 = [Fc 圧縮, Ft 引張, Fb 曲げ(強軸), Fs せん断, Fb 曲げ(弱軸)]
      (基準強度 N/mm2。長期許容応力度 = 1.1/3×基準強度)。列6以降は未使用(0)。
      行番号(1-based)が mgtopen_material の W_type_index (material 3列目) に対応。
    """
    global _W_AL_CACHE
    if _W_AL_CACHE is None:
        path = os.path.join(os.path.dirname(__file__), 'data', 'W_AL.json')
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        _W_AL_CACHE = (np.asarray(d['W_AL'], dtype=float), list(d['W_name']))
    return _W_AL_CACHE


def w_al_names():
    """W_AL表の行ラベル一覧 (UIの木材種別ドロップダウン用)."""
    return list(_load_W_AL()[1])


# ===========================================================================
# 材料名 → W_AL 行番号の自動マッチング
# (MATLAB版 mgtopen_material.m は listdlg で対話選択するため対応する原典
#  ロジックは存在しない。ここでは一意に確定できる場合のみ自動対応し、
#  曖昧・不明の場合は 0 を返して UI での選択に委ねる)
# ===========================================================================

_ZEN2HAN = {ord(z): ord(h) for z, h in zip(
    '０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ'
    'ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ－：',
    '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    'abcdefghijklmnopqrstuvwxyz-:')}


def _w_norm(s):
    """全角英数字→半角、空白・引用符除去、大文字化."""
    s = str(s).translate(_ZEN2HAN)
    for ch in (' ', '　', '"', "'"):
        s = s.replace(ch, '')
    return s.upper()


def w_al_match(material_name):
    """材料名から W_AL 行番号 (1-based) を自動決定する。不能なら 0.

    規則 (一意に確定できる場合のみ非0):
      1. E***F*** / E***-F*** パターン → W_name の 'E***-F***' と照合。
         完全一致 (E-F表記の直後に文字が続かない) 行が一つならその行。
         (例: E105F300 は 'E105-F300' と 'E105-F300唐松' の両方を含むため
          完全一致の 'E105-F300' 行を採用)
      2. 構造用合板 + 1級/2級。
      3. 樹種名 + 等級 (無等級/甲1-3級/乙1-3級/E50-E150) が共に含まれ、
         該当行が一つの場合。
    """
    name = _w_norm(material_name)
    W_AL, W_name = _load_W_AL()
    names_n = [_w_norm(n) for n in W_name]

    # 1. E***F*** パターン
    mo = re.search(r'E(\d+)-?F(\d+)', name)
    if mo:
        key = 'E%s-F%s' % (mo.group(1), mo.group(2))
        exact = []
        partial = []
        for i, n in enumerate(names_n):
            pos = n.find(key)
            if pos == -1:
                continue
            if pos + len(key) == len(n):  # E-F表記で終わる=完全一致
                exact.append(i + 1)
            else:
                partial.append(i + 1)
        if len(exact) == 1:
            return exact[0]
        if not exact and len(partial) == 1:
            return partial[0]
        return 0

    # 2. 構造用合板
    if '合板' in name:
        if '1級' in name:
            return names_n.index(_w_norm('構造用合板1級')) + 1
        if '2級' in name:
            return names_n.index(_w_norm('構造用合板２級')) + 1
        return 0

    # 3. 樹種名 + 等級
    species = ['べいまつ', 'からまつ', 'すぎ', 'あかまつ', 'ダフリカ',
               'ひば', 'ひのき', 'べいつが', 'えぞ・とど', 'えぞとど',
               'ヒノキ', '同一等級集成材', '対称異等級集成材']
    grades = ['無等級(並列使用)', '無等級', '甲1級', '甲2級', '甲3級',
              '乙1級', '乙2級', '乙3級',
              'E50', 'E70', 'E90', 'E110', 'E130', 'E150', 'BP材']
    sp = None
    for s in sorted(species, key=len, reverse=True):
        if _w_norm(s) in name:
            sp = 'えぞ・とど' if s == 'えぞとど' else s
            break
    gr = None
    for g in grades:
        if _w_norm(g) in name:
            gr = g
            break
    if sp is None:
        return 0
    cand = []
    for i, n in enumerate(names_n):
        if not n.startswith(_w_norm(sp)):
            continue
        if gr is None:
            cand.append(i + 1)
        elif _w_norm(gr) in n:
            # 無等級 は 無等級(並列使用) にも部分一致するため除外判定
            if (gr == '無等級' and '並列' in n):
                continue
            cand.append(i + 1)
    if len(cand) == 1:
        return cand[0]
    return 0


# ===========================================================================
# W_SB_analysis.m (中実角断面)
# ===========================================================================

@_matlabenv
def W_SB_analysis(sectionsize, length, stress, timecase, w_al, section_no,
                  wal_up):
    """■木材（ムク）の軸力(N)2軸曲げ(MM)せん断(Q)に対する断面算定."""
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    w_al = np.asarray(w_al, dtype=float).ravel()

    # 断面性能（ムク材の断面性能は基本的に鋼材と同じなので転用する）
    B = sectionsize[1]
    D = sectionsize[0]
    a = BSB([D, B], 1)
    Zy = BSB([D, B], 7)
    iys = BSB([sectionsize[0], sectionsize[1]], 5)
    ixs = BSB([sectionsize[0], sectionsize[1]], 4)
    Zx = BSB([D, B], 6)

    # 長短期設定
    if timecase > 10:  # 短期
        timecase = 2
        timecase2 = 1.0
    elif timecase == 1:
        if wal_up == 1.3:
            timecase = 1
            timecase2 = 1.3
        else:
            timecase = 1
            timecase2 = 1.0
    elif timecase == 9:  # 中長期
        timecase = 1
        timecase2 = 1.3
    elif timecase == 10:  # 中短期
        timecase = 2
        timecase2 = 0.8
    else:
        print('ERROR:長短期設定ミス')
        raise RuntimeError('長短期設定ミス')

    # 断面力取り込み（単位をN/mm2系にする．）
    Fxx = stress[:, 0] * 10 ** 3  # 軸力[N]
    My = stress[:, 3] * 10 ** 6   # 曲げモーメント強軸[Nmm]
    Mz = stress[:, 4] * 10 ** 6   # 曲げモーメント弱軸[Nmm]
    Fz = stress[:, 2] * 10 ** 3   # せん断力強軸方向[N]
    Fy = stress[:, 1] * 10 ** 3   # せん断力弱軸方向[N]

    # 長期許容応力度計算
    Fc = w_al[0]
    Ft = w_al[1]
    Fby = w_al[2]
    Fs = w_al[3]
    Fbz = w_al[4]

    # 圧縮許容応力度
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)

    if lamdadfc > 150 or min(B, D) < 1:
        # 細長比が150を超えるものはfcを極小に %231017國江編集,50→1に変更
        fc = 0.000001
    elif lamdadfc > 100:
        fc = 3000 * Fc / (lamdadfc) ** 2 * 1.1 / 3
    elif lamdadfc > 30:
        fc = Fc * (1.3 - 0.01 * lamdadfc) * 1.1 / 3
    else:
        fc = Fc * 1.1 / 3

    # 曲げ許容応力度
    fby = 1.1 / 3 * Fby
    fbz = 1.1 / 3 * Fbz

    # 引張許容応力度
    ft = 1.1 / 3 * Ft

    # せん断許容応力度
    fs = 1.1 / 3 * Fs

    if timecase == 2 and timecase2 == 1:  # 短期
        ft = ft * 2 / 1.1
        fs = fs * 2 / 1.1
        fc = fc * 2 / 1.1
        fby = fby * 2 / 1.1
        fbz = fbz * 2 / 1.1
    elif timecase == 2 and timecase2 == 0.8:  # 中短期
        ft = ft * 2 / 1.1 * 0.8
        fs = fs * 2 / 1.1 * 0.8
        fc = fc * 2 / 1.1 * 0.8
        fby = fby * 2 / 1.1 * 0.8
        fbz = fbz * 2 / 1.1 * 0.8
    elif timecase == 1 and timecase2 == 1.3:  # 中長期
        ft = ft * 1.3
        fs = fs * 1.3
        fc = fc * 1.3
        fby = fby * 1.3
        fbz = fbz * 1.3

    # 応力度の計算ならびに検定値の計算
    sigma_cx = Fxx / (a * 100)
    if np.all(sigma_cx > 0):
        ratio_cx = np.abs(sigma_cx) / ft
    else:
        ratio_cx = np.abs(sigma_cx) / fc

    sigma_by = np.abs(My) / (Zx * 1000)
    sigma_bz = np.abs(Mz) / (Zy * 1000)

    ratio_by = sigma_by / fby
    ratio_bz = sigma_bz / fbz

    ratio_cb = ratio_cx + ratio_by + ratio_bz

    sigma_sz = 1.5 * np.abs(Fz) / (a * 100)
    sigma_sy = 1.5 * np.abs(Fy) / (a * 100)

    ratio_sz = sigma_sz / fs
    ratio_sy = sigma_sy / fs

    ratio = np.column_stack([ratio_cb, ratio_sz, ratio_sy, np.zeros(3)])
    return ratio


# ===========================================================================
# W_SR_analysis.m (中実丸断面)
# ===========================================================================

@_matlabenv
def W_SR_analysis(sectionsize, length, stress, timecase, w_al, section_no,
                  wal_up):
    """●木材（ムク）の軸力(N)2軸曲げ(MM)せん断(Q)に対する断面算定 (211221國江作成)."""
    sectionsize = np.atleast_1d(np.asarray(sectionsize, dtype=float)).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    w_al = np.asarray(w_al, dtype=float).ravel()

    # 断面性能
    a = BSR(sectionsize, 1)
    ixs = BSR(sectionsize, 4)
    Zx = BSR(sectionsize, 6)

    # 長短期設定
    if timecase > 10:  # 短期
        timecase = 2
        timecase2 = 1.0
    elif timecase == 1:
        if wal_up == 1.3:
            timecase = 1
            timecase2 = 1.3
        else:
            timecase = 1
            timecase2 = 1.0
    elif timecase == 9:  # 中長期
        timecase = 1
        timecase2 = 1.3
    elif timecase == 10:  # 中短期
        timecase = 2
        timecase2 = 0.8
    else:
        print('ERROR:長短期設定ミス')
        raise RuntimeError('長短期設定ミス')

    # 断面力取り込み（単位をN/mm2系にする．）
    Fxx = stress[:, 0] * 10 ** 3  # 軸力[N]
    My = stress[:, 3] * 10 ** 6   # 曲げモーメント強軸[Nmm]
    Mz = stress[:, 4] * 10 ** 6   # 曲げモーメント弱軸[Nmm]
    Fz = stress[:, 2] * 10 ** 3   # せん断力強軸方向[N]
    Fy = stress[:, 1] * 10 ** 3   # せん断力弱軸方向[N]

    # 長期許容応力度計算
    Fc = w_al[0]
    Ft = w_al[1]
    Fby = w_al[2]
    Fs = w_al[3]
    Fbz = w_al[4]

    # 圧縮許容応力度
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / ixs
    lamdadfc = max(lamdax, lamday)

    if lamdadfc > 150:  # 細長比が150を超えるものは壁扱いで圧縮検定対象外とする
        Fxx = np.zeros(3)
        My = np.zeros(3)
        Mz = np.zeros(3)
        fc = Fc * 1.1 / 3
    elif lamdadfc > 100:
        fc = 3000 * Fc / (lamdadfc) ** 2 * 1.1 / 3
    elif lamdadfc > 30:
        fc = Fc * (1.3 - 0.01 * lamdadfc) * 1.1 / 3
    else:
        fc = Fc * 1.1 / 3

    # 曲げ許容応力度
    fby = 1.1 / 3 * Fby
    fbz = 1.1 / 3 * Fbz

    # 引張許容応力度
    ft = 1.1 / 3 * Ft

    # せん断許容応力度
    fs = 1.1 / 3 * Fs

    if timecase == 2 and timecase2 == 1:  # 短期
        ft = ft * 2 / 1.1
        fs = fs * 2 / 1.1
        fc = fc * 2 / 1.1
        fby = fby * 2 / 1.1
        fbz = fbz * 2 / 1.1
    elif timecase == 2 and timecase2 == 0.8:  # 中短期
        ft = ft * 2 / 1.1 * 0.8
        fs = fs * 2 / 1.1 * 0.8
        fc = fc * 2 / 1.1 * 0.8
        fby = fby * 2 / 1.1 * 0.8
        fbz = fbz * 2 / 1.1 * 0.8
    elif timecase == 1 and timecase2 == 1.3:  # 中長期
        ft = ft * 1.3
        fs = fs * 1.3
        fc = fc * 1.3
        fby = fby * 1.3
        fbz = fbz * 1.3

    # 応力度の計算ならびに検定値の計算
    sigma_cx = Fxx / (a * 100)
    if np.all(sigma_cx > 0):
        ratio_cx = np.abs(sigma_cx) / ft
    else:
        ratio_cx = np.abs(sigma_cx) / fc

    sigma_by = np.abs(My) / (Zx * 1000)
    sigma_bz = np.abs(Mz) / (Zx * 1000)

    ratio_by = sigma_by / fby
    ratio_bz = sigma_bz / fbz

    ratio_cb = ratio_cx + ratio_by + ratio_bz

    sigma_sz = 1.5 * np.abs(Fz) / (a * 100)
    sigma_sy = 1.5 * np.abs(Fy) / (a * 100)

    ratio_sz = sigma_sz / fs
    ratio_sy = sigma_sy / fs

    ratio = np.column_stack([ratio_cb, ratio_sz, ratio_sy, np.zeros(3)])
    return ratio


# ===========================================================================
# W_SB_analysis_text.m
# ===========================================================================

@_matlabenv
def W_SB_analysis_text(sectionsize, length, stress, timecase, w_al, ele_no,
                       section_no, LOAS_CASE_NAME, section_name, wal_up):
    """■木材（ムク）角材の断面算定詳細テキスト (W_SB_analysis_text.m)."""
    n2s = _num2str
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    w_al = np.asarray(w_al, dtype=float).ravel()

    # 断面性能
    B = sectionsize[1]
    D = sectionsize[0]
    a = BSB([D, B], 1)
    Zy = BSB([D, B], 7)
    iys = BSB([sectionsize[0], sectionsize[1]], 5)
    ixs = BSB([sectionsize[0], sectionsize[1]], 4)
    Ix = BSB([sectionsize[0], sectionsize[1]], 2)
    Iy = BSB([sectionsize[0], sectionsize[1]], 3)
    Zx = BSB([D, B], 6)

    text = ['角材・軸力および2軸曲げに対する断面算定']
    text.append('軸力を考慮した角材の断面算定を行う．曲げについては2軸曲げを考慮する．')
    text.append('また，せん断についても弱軸および強軸方向の検討をおこなう．')
    text.append('せん断力のみを負担する構造用合板につてはせん断のみの検定とする')
    text.append('　　')
    text.append('断面符号：' + section_name + '　断面番号：' + n2s(section_no)
                + '　要素番号：' + n2s(ele_no))
    text.append('　　')
    text.append('角材サイズ：■-' + n2s(sectionsize[0]) + 'x' + n2s(sectionsize[1]))

    # 長短期設定
    if timecase > 10:
        timecase = 2
        timecase2 = 1.0
        t_case = '短期'
    elif timecase == 1:
        if wal_up == 1.3:
            timecase = 1
            timecase2 = 1.3
            t_case = '中長期'
        else:
            timecase = 1
            timecase2 = 1.0
            t_case = '長期'
    elif timecase == 9:  # 中長期
        timecase = 1
        timecase2 = 1.3
        t_case = '中長期'
    elif timecase == 10:  # 中短期
        timecase = 2
        timecase2 = 0.8
        t_case = '中短期'
    else:
        print('ERROR:長短期設定ミス')
        raise RuntimeError('長短期設定ミス')
    text.append('*断面性能')
    text.append('断面積：' + n2s(a) + 'cm2' + '　断面二次モーメント(強)：'
                + n2s(Ix) + 'cm4' + '　断面二次モーメント(弱)：' + n2s(Iy) + 'cm4')
    text.append('断面係数(強)：' + n2s(Zx) + 'cm3' + '　断面係数(弱)：'
                + n2s(Zy) + 'cm3')
    text.append('断面二次半径(強)：' + n2s(ixs) + 'cm' + '　断面二次半径(弱)：'
                + n2s(iys) + 'cm')

    # 断面力取り込み（単位をN/mm2系にする．）
    Fxx = stress[:, 0] * 10 ** 3  # 軸力[N]
    My = stress[:, 3] * 10 ** 6   # 曲げモーメント強軸[Nmm]
    Mz = stress[:, 4] * 10 ** 6   # 曲げモーメント弱軸[Nmm]
    Fz = stress[:, 2] * 10 ** 3   # せん断力強軸方向[N]
    Fy = stress[:, 1] * 10 ** 3   # せん断力弱軸方向[N]

    # 長期許容応力度計算
    Fc = w_al[0]
    Ft = w_al[1]
    Fby = w_al[2]
    Fs = w_al[3]
    Fbz = w_al[4]
    # 圧縮許容応力度
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)

    if lamdadfc > 150 or min(B, D) < 50:
        # 細長比が150を超えるものは壁扱いで圧縮検定対象外とする
        fc = 0.000001
    elif lamdadfc > 100:
        fc = 3000 * Fc / (lamdadfc) ** 2 * 1.1 / 3
    elif lamdadfc > 30:
        fc = Fc * (1.3 - 0.01 * lamdadfc) * 1.1 / 3
    else:
        fc = Fc * 1.1 / 3

    # 曲げ許容応力度
    fby = 1.1 / 3 * Fby
    fbz = 1.1 / 3 * Fbz

    # 引張許容応力度
    ft = 1.1 / 3 * Ft

    # せん断許容応力度
    fs = 1.1 / 3 * Fs

    text.append('　　')
    text.append('*部材支持条件')
    text.append('座屈長さ(強)：' + n2s(length[0] * 1000, '%15.0f') + 'mm'
                + '　座屈長さ(弱)：' + n2s(length[1] * 1000, '%15.0f') + 'mm'
                + '　圧縮フランジ間距離：' + n2s(length[2] * 1000, '%15.0f') + 'mm')

    text.append('　　')
    text.append('*木材情報:基準強度')
    text.append('圧縮：' + n2s(Fc, '%15.2f') + 'N/mm2' + '　引張：'
                + n2s(Ft, '%15.2f') + 'N/mm2'
                + '　曲げ（強軸）：' + n2s(Fby, '%15.2f') + 'N/mm2'
                + '　曲げ（弱軸）：' + n2s(Fbz, '%15.2f') + 'N/mm2'
                + '　せん断：' + n2s(Fs, '%15.2f') + 'N/mm2')

    if timecase == 2 and timecase2 == 1:  # 短期
        ft = ft * 2 / 1.1
        fs = fs * 2 / 1.1
        fc = fc * 2 / 1.1
        fby = fby * 2 / 1.1
        fbz = fbz * 2 / 1.1
        text.append('　　')
        text.append('*木材情報:短期許容応力度')
        text.append('圧縮：' + n2s(fc, '%15.2f') + 'N/mm2' + '　引張：'
                    + n2s(ft, '%15.2f') + 'N/mm2'
                    + '　曲げ：' + n2s(fby, '%15.2f') + 'N/mm2'
                    + '　せん断：' + n2s(fs, '%15.2f') + 'N/mm2')
    elif timecase == 2 and timecase2 == 0.8:  # 中短期
        ft = ft * 2 / 1.1 * 0.8
        fs = fs * 2 / 1.1 * 0.8
        fc = fc * 2 / 1.1 * 0.8
        fby = fby * 2 / 1.1 * 0.8
        fbz = fbz * 2 / 1.1 * 0.8
        text.append('　　')
        text.append('*木材情報:中短期許容応力度')
        text.append('圧縮：' + n2s(fc, '%15.2f') + 'N/mm2' + '　引張：'
                    + n2s(ft, '%15.2f') + 'N/mm2'
                    + '　曲げ：' + n2s(fby, '%15.2f') + 'N/mm2'
                    + '　せん断：' + n2s(fs, '%15.2f') + 'N/mm2')
    elif timecase == 1 and timecase2 == 1.3:  # 中長期
        ft = ft * 1.3
        fs = fs * 1.3
        fc = fc * 1.3
        fby = fby * 1.3
        fbz = fbz * 1.3
        text.append('　　')
        text.append('*木材情報:中長期許容応力度')
        text.append('圧縮：' + n2s(fc, '%15.2f') + 'N/mm2' + '　引張：'
                    + n2s(ft, '%15.2f') + 'N/mm2'
                    + '　曲げ：' + n2s(fby, '%15.2f') + 'N/mm2'
                    + '　せん断：' + n2s(fs, '%15.2f') + 'N/mm2')
    else:
        text.append('　　')
        text.append('*木材情報:長期許容応力度')
        text.append('圧縮：' + n2s(fc, '%15.2f') + 'N/mm2' + '　引張：'
                    + n2s(ft, '%15.2f') + 'N/mm2'
                    + '　曲げ：' + n2s(fby, '%15.2f') + 'N/mm2'
                    + '　せん断：' + n2s(fs, '%15.2f') + 'N/mm2')

    # 応力度の計算ならびに検定値の計算
    sigma_cx = Fxx / (a * 100)
    if np.all(sigma_cx > 0):
        ratio_cx = np.abs(sigma_cx) / ft
    else:
        ratio_cx = np.abs(sigma_cx) / fc

    sigma_by = np.abs(My) / (Zx * 1000)
    sigma_bz = np.abs(Mz) / (Zy * 1000)

    ratio_by = sigma_by / fby
    ratio_bz = sigma_bz / fbz

    ratio_cb = ratio_cx + ratio_by + ratio_bz

    sigma_sz = np.abs(Fz) / (a * 100)
    sigma_sy = np.abs(Fy) / (a * 100)

    ratio_sz = 1.5 * sigma_sz / fs
    ratio_sy = 1.5 * sigma_sy / fs

    ratio = np.column_stack([ratio_cb, ratio_sz, ratio_sy, np.zeros(3)])

    # 最大検定比のものを記す
    max_index = _maxratio_index(ratio)
    st_index = _st_index(max_index)

    text.append('　　')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　i端')
    text.append('軸力' + n2s(_m(Fxx, st_index) / 1000, '%15.1f')
                + 'kN　曲げM(強)' + n2s(_m(My, st_index) / 10 ** 6, '%15.1f')
                + 'kNm　曲げM(弱)' + n2s(_m(Mz, st_index) / 10 ** 6, '%15.1f') + 'kNm')
    text.append('せん断力(強)' + n2s(_m(Fz, st_index) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)' + n2s(_m(Fy, st_index) / 10 ** 3, '%15.1f') + 'kN')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　中央')
    text.append('軸力' + n2s(_m(Fxx, st_index + 1) / 1000, '%15.1f')
                + 'kN　曲げM(強)' + n2s(_m(My, st_index + 1) / 10 ** 6, '%15.1f')
                + 'kNm　曲げM(弱)' + n2s(_m(Mz, st_index + 1) / 10 ** 6, '%15.1f') + 'kNm')
    text.append('せん断力(強)' + n2s(_m(Fz, st_index + 1) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)' + n2s(_m(Fy, st_index + 1) / 10 ** 3, '%15.1f') + 'kN')

    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　j端')
    text.append('軸力' + n2s(_m(Fxx, st_index + 2) / 1000, '%15.1f')
                + 'kN　曲げM(強)' + n2s(_m(My, st_index + 2) / 10 ** 6, '%15.1f')
                + 'kNm　曲げM(弱)' + n2s(_m(Mz, st_index + 2) / 10 ** 6, '%15.1f') + 'kNm')
    text.append('せん断力(強)' + n2s(_m(Fz, st_index + 2) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)' + n2s(_m(Fy, st_index + 2) / 10 ** 3, '%15.1f') + 'kN')

    text.append('　　')
    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　i端')
    text.append('直応力度σx' + n2s(_m(sigma_cx, st_index), '%15.1f')
                + 'N/mm2　直応力度σy' + n2s(_m(sigma_by, st_index), '%15.1f')
                + 'N/mm2　直応力度σz' + n2s(_m(sigma_bz, st_index), '%15.1f') + 'N/mm2')
    text.append('せん断応力度τz' + n2s(_m(sigma_sz, st_index), '%15.1f') + 'N/mm2'
                + '　せん断応力度τz' + n2s(_m(sigma_sy, st_index), '%15.1f') + 'N/mm2')
    text.append('　　')
    # 元コードのまま (中央/j端の見出しも 'i端' と表記されている)
    text.append('*設計用応力度　[' + t_case + ']　i端')
    text.append('直応力度σx' + n2s(_m(sigma_cx, st_index + 1), '%15.1f')
                + 'N/mm2　直応力度σy' + n2s(_m(sigma_by, st_index + 1), '%15.1f')
                + 'N/mm2　直応力度σz' + n2s(_m(sigma_bz, st_index + 1), '%15.1f') + 'N/mm2')
    text.append('せん断応力度τz' + n2s(_m(sigma_sz, st_index + 1), '%15.1f') + 'N/mm2'
                + '　せん断応力度τz' + n2s(_m(sigma_sy, st_index + 1), '%15.1f') + 'N/mm2')
    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　i端')
    text.append('直応力度σx' + n2s(_m(sigma_cx, st_index + 2), '%15.1f')
                + 'N/mm2　直応力度σy' + n2s(_m(sigma_by, st_index + 2), '%15.1f')
                + 'N/mm2　直応力度σz' + n2s(_m(sigma_bz, st_index + 2), '%15.1f') + 'N/mm2')
    text.append('せん断応力度τz' + n2s(_m(sigma_sz, st_index + 2), '%15.1f') + 'N/mm2'
                + '　せん断応力度τz' + n2s(_m(sigma_sy, st_index + 2), '%15.1f') + 'N/mm2')
    text.append('　　')

    text.append('　　')
    text.append('　　')
    text.append('*許容応力度　[' + t_case + ']')
    text.append('引張許容応力度：' + n2s(ft, '%15.2f') + 'N/mm2')
    text.append('せん断許容応力度：' + n2s(fs, '%15.2f') + 'N/mm2')

    text.append('圧縮許容応力度：' + n2s(fc, '%15.2f') + 'N/mm2')
    text.append('細長比(強)：' + n2s(lamdax, '%15.0f') + '　細長比(弱)：'
                + n2s(lamday, '%15.0f'))

    text.append('曲げ許容応力度(強)：' + n2s(_m(fby, st_index), '%15.2f') + 'N/mm2')
    text.append('曲げ許容応力度(弱)：' + n2s(_m(fbz, st_index), '%15.2f') + 'N/mm2')
    text.append('　　')
    text.append('　　')
    text.append('*検定比　[' + t_case + ']　**i端　中央　j端の順に示す**')
    text.append('ニ軸曲げ＋圧縮：' + n2s(ratio[st_index - 1, 0], '%15.3f') + ',　'
                + n2s(ratio[st_index, 0], '%15.3f') + ',　'
                + n2s(ratio[st_index + 1, 0], '%15.3f'))
    text.append('せん断：' + n2s(np.max(ratio[st_index - 1, 1:3]), '%15.3f') + ',　'
                + n2s(np.max(ratio[st_index, 1:3]), '%15.3f') + ',　'
                + n2s(np.max(ratio[st_index + 1, 1:3]), '%15.3f'))

    return text


# ===========================================================================
# W_SR_analysis_text.m
# ===========================================================================

@_matlabenv
def W_SR_analysis_text(sectionsize, length, stress, timecase, w_al, ele_no,
                       section_no, LOAS_CASE_NAME, section_name, wal_up):
    """●木材（ムク）丸材の断面算定詳細テキスト (W_SR_analysis_text.m, 211221國江作成)."""
    n2s = _num2str
    sectionsize = np.atleast_1d(np.asarray(sectionsize, dtype=float)).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    w_al = np.asarray(w_al, dtype=float).ravel()

    # 断面性能
    a = BSR(sectionsize, 1)
    Ix = BSR(sectionsize, 2)
    ixs = BSR(sectionsize, 4)
    Zx = BSR(sectionsize, 6)

    text = ['丸材・軸力および2軸曲げに対する断面算定']
    text.append('軸力を考慮した丸材の断面算定を行う．曲げについては2軸曲げを考慮する．')
    text.append('また，せん断についても弱軸および強軸方向の検討をおこなう．')
    text.append('せん断力のみを負担する構造用合板についてはせん断のみの検定とする')
    text.append('　　')
    text.append('断面符号：' + section_name + '　断面番号：' + n2s(section_no)
                + '　要素番号：' + n2s(ele_no))
    text.append('　　')
    text.append('丸材サイズ：●-φ' + n2s(sectionsize[0]))

    # 長短期設定
    if timecase > 10:
        timecase = 2
        timecase2 = 1.0
        t_case = '短期'
    elif timecase == 1:
        if wal_up == 1.3:
            timecase = 1
            timecase2 = 1.3
            t_case = '中長期'
        else:
            timecase = 1
            timecase2 = 1.0
            t_case = '長期'
    elif timecase == 9:  # 中長期
        timecase = 1
        timecase2 = 1.3
        t_case = '中長期'
    elif timecase == 10:  # 中短期
        timecase = 2
        timecase2 = 0.8
        t_case = '中短期'
    else:
        print('ERROR:長短期設定ミス')
        raise RuntimeError('長短期設定ミス')
    text.append('*断面性能')
    text.append('断面積：' + n2s(a) + 'cm2' + '　断面二次モーメント：'
                + n2s(Ix) + 'cm4')
    text.append('断面係数：' + n2s(Zx) + 'cm3　断面二次半径：' + n2s(ixs) + 'cm')

    # 断面力取り込み（単位をN/mm2系にする．）
    Fxx = stress[:, 0] * 10 ** 3  # 軸力[N]
    My = stress[:, 3] * 10 ** 6   # 曲げモーメント強軸[Nmm]
    Mz = stress[:, 4] * 10 ** 6   # 曲げモーメント弱軸[Nmm]
    Fz = stress[:, 2] * 10 ** 3   # せん断力強軸方向[N]
    Fy = stress[:, 1] * 10 ** 3   # せん断力弱軸方向[N]

    # 長期許容応力度計算
    Fc = w_al[0]
    Ft = w_al[1]
    Fby = w_al[2]
    Fs = w_al[3]
    Fbz = w_al[4]
    # 圧縮許容応力度
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / ixs
    lamdadfc = max(lamdax, lamday)

    if lamdadfc > 150:  # 細長比が150を超えるものは壁扱いで圧縮検定対象外とする
        Fxx = np.zeros(3)
        My = np.zeros(3)
        Mz = np.zeros(3)
        fc = Fc * 1.1 / 3
    elif lamdadfc > 100:
        fc = 3000 * Fc / (lamdadfc) ** 2 * 1.1 / 3
    elif lamdadfc > 30:
        fc = Fc * (1.3 - 0.01 * lamdadfc) * 1.1 / 3
    else:
        fc = Fc * 1.1 / 3

    # 曲げ許容応力度
    fby = 1.1 / 3 * Fby
    fbz = 1.1 / 3 * Fbz

    # 引張許容応力度
    ft = 1.1 / 3 * Ft

    # せん断許容応力度
    fs = 1.1 / 3 * Fs

    text.append('　　')
    text.append('*部材支持条件')
    text.append('座屈長さ(x)：' + n2s(length[0] * 1000, '%15.0f') + 'mm'
                + '　座屈長さ(y)：' + n2s(length[1] * 1000, '%15.0f') + 'mm')

    text.append('　　')
    text.append('*木材情報:基準強度')
    text.append('圧縮：' + n2s(Fc, '%15.2f') + 'N/mm2' + '　引張：'
                + n2s(Ft, '%15.2f') + 'N/mm2'
                + '　曲げ（強軸）：' + n2s(Fby, '%15.2f') + 'N/mm2'
                + '　曲げ（弱軸）：' + n2s(Fbz, '%15.2f') + 'N/mm2'
                + '　せん断：' + n2s(Fs, '%15.2f') + 'N/mm2')

    if timecase == 2 and timecase2 == 1:  # 短期
        ft = ft * 2 / 1.1
        fs = fs * 2 / 1.1
        fc = fc * 2 / 1.1
        fby = fby * 2 / 1.1
        fbz = fbz * 2 / 1.1
        text.append('　　')
        text.append('*木材情報:短期許容応力度')
        text.append('圧縮：' + n2s(fc, '%15.2f') + 'N/mm2' + '　引張：'
                    + n2s(ft, '%15.2f') + 'N/mm2'
                    + '　曲げ：' + n2s(fby, '%15.2f') + 'N/mm2'
                    + '　せん断：' + n2s(fs, '%15.2f') + 'N/mm2')
    elif timecase == 2 and timecase2 == 0.8:  # 中短期
        ft = ft * 2 / 1.1 * 0.8
        fs = fs * 2 / 1.1 * 0.8
        fc = fc * 2 / 1.1 * 0.8
        fby = fby * 2 / 1.1 * 0.8
        fbz = fbz * 2 / 1.1 * 0.8
        text.append('　　')
        text.append('*木材情報:中短期許容応力度')
        text.append('圧縮：' + n2s(fc, '%15.2f') + 'N/mm2' + '　引張：'
                    + n2s(ft, '%15.2f') + 'N/mm2'
                    + '　曲げ：' + n2s(fby, '%15.2f') + 'N/mm2'
                    + '　せん断：' + n2s(fs, '%15.2f') + 'N/mm2')
    elif timecase == 1 and timecase2 == 1.3:  # 中長期
        ft = ft * 1.3
        fs = fs * 1.3
        fc = fc * 1.3
        fby = fby * 1.3
        fbz = fbz * 1.3
        text.append('　　')
        text.append('*木材情報:中長期許容応力度')
        text.append('圧縮：' + n2s(fc, '%15.2f') + 'N/mm2' + '　引張：'
                    + n2s(ft, '%15.2f') + 'N/mm2'
                    + '　曲げ：' + n2s(fby, '%15.2f') + 'N/mm2'
                    + '　せん断：' + n2s(fs, '%15.2f') + 'N/mm2')
    else:
        text.append('　　')
        text.append('*木材情報:長期許容応力度')
        text.append('圧縮：' + n2s(fc, '%15.2f') + 'N/mm2' + '　引張：'
                    + n2s(ft, '%15.2f') + 'N/mm2'
                    + '　曲げ：' + n2s(fby, '%15.2f') + 'N/mm2'
                    + '　せん断：' + n2s(fs, '%15.2f') + 'N/mm2')

    # 応力度の計算ならびに検定値の計算
    sigma_cx = Fxx / (a * 100)
    if np.all(sigma_cx > 0):
        ratio_cx = np.abs(sigma_cx) / ft
    else:
        ratio_cx = np.abs(sigma_cx) / fc

    sigma_by = np.abs(My) / (Zx * 1000)
    sigma_bz = np.abs(Mz) / (Zx * 1000)

    ratio_by = sigma_by / fby
    ratio_bz = sigma_bz / fbz

    ratio_cb = ratio_cx + ratio_by + ratio_bz

    sigma_sz = np.abs(Fz) / (a * 100)
    sigma_sy = np.abs(Fy) / (a * 100)

    ratio_sz = 1.5 * sigma_sz / fs
    ratio_sy = 1.5 * sigma_sy / fs

    ratio = np.column_stack([ratio_cb, ratio_sz, ratio_sy, np.zeros(3)])

    # 最大検定比のものを記す
    max_index = _maxratio_index(ratio)
    st_index = _st_index(max_index)

    text.append('　　')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　i端')
    text.append('軸力' + n2s(_m(Fxx, st_index) / 1000, '%15.1f')
                + 'kN　曲げM(強)' + n2s(_m(My, st_index) / 10 ** 6, '%15.1f')
                + 'kNm　曲げM(弱)' + n2s(_m(Mz, st_index) / 10 ** 6, '%15.1f') + 'kNm')
    text.append('せん断力(強)' + n2s(_m(Fz, st_index) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)' + n2s(_m(Fy, st_index) / 10 ** 3, '%15.1f') + 'kN')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　中央')
    text.append('軸力' + n2s(_m(Fxx, st_index + 1) / 1000, '%15.1f')
                + 'kN　曲げM(強)' + n2s(_m(My, st_index + 1) / 10 ** 6, '%15.1f')
                + 'kNm　曲げM(弱)' + n2s(_m(Mz, st_index + 1) / 10 ** 6, '%15.1f') + 'kNm')
    text.append('せん断力(強)' + n2s(_m(Fz, st_index + 1) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)' + n2s(_m(Fy, st_index + 1) / 10 ** 3, '%15.1f') + 'kN')

    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　j端')
    text.append('軸力' + n2s(_m(Fxx, st_index + 2) / 1000, '%15.1f')
                + 'kN　曲げM(強)' + n2s(_m(My, st_index + 2) / 10 ** 6, '%15.1f')
                + 'kNm　曲げM(弱)' + n2s(_m(Mz, st_index + 2) / 10 ** 6, '%15.1f') + 'kNm')
    text.append('せん断力(強)' + n2s(_m(Fz, st_index + 2) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)' + n2s(_m(Fy, st_index + 2) / 10 ** 3, '%15.1f') + 'kN')

    text.append('　　')
    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　i端')
    text.append('直応力度σx' + n2s(_m(sigma_cx, st_index), '%15.1f')
                + 'N/mm2　直応力度σy' + n2s(_m(sigma_by, st_index), '%15.1f')
                + 'N/mm2　直応力度σz' + n2s(_m(sigma_bz, st_index), '%15.1f') + 'N/mm2')
    text.append('せん断応力度τz' + n2s(_m(sigma_sz, st_index), '%15.1f') + 'N/mm2'
                + '　せん断応力度τz' + n2s(_m(sigma_sy, st_index), '%15.1f') + 'N/mm2')
    text.append('　　')
    # 元コードのまま (中央/j端の見出しも 'i端' と表記されている)
    text.append('*設計用応力度　[' + t_case + ']　i端')
    text.append('直応力度σx' + n2s(_m(sigma_cx, st_index + 1), '%15.1f')
                + 'N/mm2　直応力度σy' + n2s(_m(sigma_by, st_index + 1), '%15.1f')
                + 'N/mm2　直応力度σz' + n2s(_m(sigma_bz, st_index + 1), '%15.1f') + 'N/mm2')
    text.append('せん断応力度τz' + n2s(_m(sigma_sz, st_index + 1), '%15.1f') + 'N/mm2'
                + '　せん断応力度τz' + n2s(_m(sigma_sy, st_index + 1), '%15.1f') + 'N/mm2')
    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　i端')
    text.append('直応力度σx' + n2s(_m(sigma_cx, st_index + 2), '%15.1f')
                + 'N/mm2　直応力度σy' + n2s(_m(sigma_by, st_index + 2), '%15.1f')
                + 'N/mm2　直応力度σz' + n2s(_m(sigma_bz, st_index + 2), '%15.1f') + 'N/mm2')
    text.append('せん断応力度τz' + n2s(_m(sigma_sz, st_index + 2), '%15.1f') + 'N/mm2'
                + '　せん断応力度τz' + n2s(_m(sigma_sy, st_index + 2), '%15.1f') + 'N/mm2')
    text.append('　　')

    text.append('　　')
    text.append('　　')
    text.append('*許容応力度　[' + t_case + ']')
    text.append('引張許容応力度：' + n2s(ft, '%15.2f') + 'N/mm2')
    text.append('せん断許容応力度：' + n2s(fs, '%15.2f') + 'N/mm2')

    text.append('圧縮許容応力度：' + n2s(fc, '%15.2f') + 'N/mm2')
    text.append('細長比(x)：' + n2s(lamdax, '%15.0f') + '　細長比(y)：'
                + n2s(lamday, '%15.0f'))

    text.append('曲げ許容応力度(強)：' + n2s(_m(fby, st_index), '%15.2f') + 'N/mm2')
    text.append('曲げ許容応力度(弱)：' + n2s(_m(fbz, st_index), '%15.2f') + 'N/mm2')
    text.append('　　')
    text.append('　　')
    text.append('*検定比　[' + t_case + ']　**i端　中央　j端の順に示す**')
    text.append('ニ軸曲げ＋圧縮：' + n2s(ratio[st_index - 1, 0], '%15.3f') + ',　'
                + n2s(ratio[st_index, 0], '%15.3f') + ',　'
                + n2s(ratio[st_index + 1, 0], '%15.3f'))
    text.append('せん断：' + n2s(np.max(ratio[st_index - 1, 1:3]), '%15.3f') + ',　'
                + n2s(np.max(ratio[st_index, 1:3]), '%15.3f') + ',　'
                + n2s(np.max(ratio[st_index + 1, 1:3]), '%15.3f'))

    return text


# ===========================================================================
# structural_function/W/tension_analysis/TSR_analysis.m
# (鉄骨版 s_check.TSR_analysis と同名別実装のため W_TSR_analysis として移植。
#  W_ratio_analysis からは参照されないが原典の TSR 系として保持する)
# ===========================================================================

@_matlabenv
def W_TSR_analysis(sectionsize, stress, timecase, SN):
    """○鋼管（ムク）の引張力に対する断面算定 (W配下 TSR_analysis.m).

    鉄骨版 (s_check.TSR_analysis) との差異 (原典どおり):
      座屈計算なし・有効断面0.75の低減なし・timecase 9/10(中長期/中短期)対応・
      圧縮となった場合も ft で検定 (fc は計算しない)。
    """
    sectionsize = np.atleast_1d(np.asarray(sectionsize, dtype=float)).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BSR(sectionsize, 1)

    # 断面力取り込み（単位をN/mm2系にする．）
    Fx1 = stress[:, 0] * 10 ** 3  # 軸力[N]
    Fx2 = stress[:, 1] * 10 ** 3  # 軸力[N]

    # 長短期設定
    if timecase > 10:  # 短期
        timecase = 2
        timecase2 = 1.0
    elif timecase == 1:  # 長期
        timecase = 1
        timecase2 = 1.0
    elif timecase == 9:  # 中長期
        timecase = 1
        timecase2 = 1.3
    elif timecase == 10:  # 中短期
        timecase = 2
        timecase2 = 0.8
    else:
        print('ERROR:長短期設定ミス')
        raise RuntimeError('長短期設定ミス')

    # 長期許容応力度計算
    if SN[0] == 1:
        t = sectionsize[0]
        ALstressS = ALST_S([SN[1], t])
        F = ALstressS[2, 0]
        ft = ALstressS[0, 0]
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5

    if timecase == 2 and timecase2 == 1:  # 短期
        ft = ft * 2 / 1.1
    elif timecase == 2 and timecase2 == 0.8:  # 中短期
        ft = ft * 2 / 1.1 * 0.8
    elif timecase == 1 and timecase2 == 1.3:  # 中長期
        ft = ft * 1.3

    # 応力度の計算ならびに検定値の計算
    sigma_cx1 = Fx1 / (a * 100)
    sigma_cx2 = Fx2 / (a * 100)

    if np.all(sigma_cx1 >= 0) and np.all(sigma_cx2 >= 0):
        ratio_cx1 = np.abs(sigma_cx1) / ft
        ratio_cx2 = np.abs(sigma_cx2) / ft
    else:
        print("ERROR='テンション材に圧縮！？'")
        ratio_cx1 = np.abs(sigma_cx1) / ft
        ratio_cx2 = np.abs(sigma_cx2) / ft

    ratio = np.column_stack([ratio_cx1, ratio_cx2])
    return ratio


# ===========================================================================
# W_ratio_analysis.m (privatetool_function/MIDAS/ratio/W/)
# ===========================================================================

@_matlabenv
def W_ratio_analysis(sectionsize, buck_length, stress, timecase, material_no,
                     ele_no, maxratios, maxratios_text, section_no,
                     LOAS_CASE_NAME, pick_section_name, wal_up):
    """木造部材の断面検定（曲げ圧縮部材の検定）ディスパッチャ (W_ratio_analysis.m).

    material_no は mgtopen_material が material 3列目に格納した W_AL 行番号
    (1-based, MATLAB版 listdlg の W_type_index に相当)。material_no が 0 の
    場合は材料名から W_AL 行が確定していないためエラーとする
    (UI の木材種別選択 / run_steel_check の w_material_map で指定する)。

    sectionsize は m 単位 (内部で×1000)。後ろから2番目の要素×1000が種別コード:
      1000:H(未対応), 2000:中実角→W_SB, 3000:中実丸→W_SR
    戻り値: (ratio_output, maxratios, maxratios_text)
    maxratios = [要素番号, 最大検定比]。
    """
    # 許容応力度
    al_index = int(material_no)
    W_AL, W_name = _load_W_AL()
    if al_index < 1 or al_index > W_AL.shape[0]:
        # MATLAB版は listdlg で必ず選択されるため 0 はあり得ない
        raise RuntimeError(
            '木材料の許容応力度 (W_AL行) が未確定です (material_no=%d, '
            '断面番号=%g, 要素番号=%g)。材料名から自動対応できない木材料は '
            'UIの木材種別選択 (run_steel_check の w_material_map) で'
            '指定してください。' % (al_index, section_no, ele_no))
    wood_al = W_AL[al_index - 1, :]

    sectionsize = np.asarray(sectionsize, dtype=float).ravel() * 1000
    maxratios = list(maxratios)
    n = max(sectionsize.shape)
    code = sectionsize[n - 2]

    if code == 1000:  # H鋼断面の検定
        print('木材のH断面は対応していません')
        raise RuntimeError('木材のH断面は対応していません')  # MATLAB: stop
    elif code == 2000:  # 中実角断面の検定
        ratio_output = W_SB_analysis(sectionsize[0:n - 2], buck_length,
                                     stress, timecase, wood_al, section_no,
                                     wal_up)
        if np.max(np.max(ratio_output)) > max(np.atleast_1d(maxratios[1])):
            maxratios[0] = ele_no
            maxratios[1] = float(np.max(np.max(ratio_output)))
            maxratios_text = W_SB_analysis_text(
                sectionsize[0:n - 2], buck_length, stress, timecase, wood_al,
                ele_no, section_no, LOAS_CASE_NAME, pick_section_name, wal_up)

    elif code == 3000:  # 中実丸断面の検定
        ratio_output = W_SR_analysis(sectionsize[0:n - 2], buck_length,
                                     stress, timecase, wood_al, section_no,
                                     wal_up)
        if np.max(np.max(ratio_output)) > max(np.atleast_1d(maxratios[1])):
            maxratios[0] = ele_no
            maxratios[1] = float(np.max(np.max(ratio_output)))
            print(sectionsize)  # 元コードのまま (sectionsize を表示)
            maxratios_text = W_SR_analysis_text(
                sectionsize[0:n - 2], buck_length, stress, timecase, wood_al,
                ele_no, section_no, LOAS_CASE_NAME, pick_section_name, wal_up)
    else:
        print('木材の断面形状として不適切です．')
        raise RuntimeError('木材の断面形状として不適切です．')  # MATLAB: stop

    return ratio_output, maxratios, maxratios_text


# ===========================================================================
# W_truss_ratio_analysis.m (privatetool_function/MIDAS/ratio/W/)
# ===========================================================================

@_matlabenv
def W_truss_ratio_analysis(stress, timecase, ele_no, maxratios,
                           maxratios_text, section_no, section_index,
                           w_panel_co, element, node):
    """木造筋かい・壁面（トラス要素・Xブレース置換）の断面算定 (W_truss_ratio_analysis.m).

    w_panel_co: ndarray (断面数×2) [断面番号, 壁倍率]。
      MATLAB版は w_panel_co.m の listdlg で断面ごとに壁倍率を対話指定する。
      Python版は run_steel_check の引数 w_panel_co (dict) から構成した行列。
    戻り値: (ratio_output, maxratios, maxratios_text)
    """
    n2s = _num2str
    stress = np.asarray(stress, dtype=float).ravel()
    w_panel_co = np.atleast_2d(np.asarray(w_panel_co, dtype=float))
    maxratios = list(maxratios)

    # 軸力をせん断力に換算
    nodei_index, nodej_index = node_index_get(ele_no, element, node)
    ele_length = float(np.linalg.norm(node[nodei_index, 1:4]
                                      - node[nodej_index, 1:4]))
    ele_length_xy = float(np.linalg.norm(node[nodei_index, 1:3]
                                         - node[nodej_index, 1:3]))

    Qshear = ele_length_xy / ele_length * stress
    AL_index = find_index(w_panel_co[:, 0], section_no)

    AL_shear = w_panel_co[AL_index, 1] * 1.96 * ele_length_xy

    ratio_output = np.abs(Qshear / AL_shear)

    if np.max(np.max(ratio_output)) > max(np.atleast_1d(maxratios[1])):
        maxratios[0] = ele_no
        maxratios[1] = float(np.max(np.max(ratio_output)))
        maxratios_text = ['####木造筋かい・壁面に対する断面算定####']
        maxratios_text.append('　　')

        maxratios_text.append('断面番号：' + n2s(section_no) + '　要素番号：'
                              + n2s(ele_no))
        maxratios_text.append('　　')
        maxratios_text.append('壁倍率：' + n2s(w_panel_co[AL_index, 1], '%15.1f')
                              + '倍')

        # 長短期設定
        if timecase >= 10:
            t_case = '短期'
        elif timecase == 1:
            t_case = '長期'
        else:
            print('ERROR:長短期設定ミス')
            raise RuntimeError('長短期設定ミス')  # MATLAB: stop
        maxratios_text.append('*許容せん断力（mあたり）')
        maxratios_text.append(n2s(w_panel_co[AL_index, 1] * 0.5 * 1.96, '%15.1f')
                              + 'kN/m')
        maxratios_text.append('**Xブレース置換により実際の壁の許容せん断力の半分の数値')
        maxratios_text.append('　　')
        maxratios_text.append('壁長さ：' + n2s(ele_length_xy, '%15.1f') + 'm')

        # 長期許容応力度計算
        # 応力度の計算ならびに検定値の計算

        maxratios_text.append('　　')
        maxratios_text.append('　　')
        maxratios_text.append('*設計用せん断力　[' + t_case + ']　i端')
        # 元コードのまま (kN応力を /1000 表示)
        maxratios_text.append('Q1:' + n2s(_m(Qshear, 1) / 1000, '%15.1f') + 'kN')

        maxratios_text.append('　　')
        maxratios_text.append('*設計用せん断力　[' + t_case + ']　j端')
        maxratios_text.append('Q2:' + n2s(_m(Qshear, 2) / 1000, '%15.1f') + 'kN')

        maxratios_text.append('　　')
        maxratios_text.append('　　')
        maxratios_text.append('*許容せん断力　[' + t_case + ']')
        maxratios_text.append(n2s(AL_shear, '%15.1f') + 'kN')
        maxratios_text.append('**Xブレース置換により実際の壁の許容せん断力の半分の数値')
        maxratios_text.append('　　')
        maxratios_text.append('　　')
        maxratios_text.append('*検定比　[' + t_case + ']　**i端　j端の順に示す**')
        maxratios_text.append('引張検定：' + n2s(_m(ratio_output, 1), '%15.3f')
                              + ',　' + n2s(_m(ratio_output, 2), '%15.3f'))

    return ratio_output, maxratios, maxratios_text
