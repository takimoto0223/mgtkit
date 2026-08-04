# -*- coding: utf-8 -*-
"""Midas_QR 支点反力図 (figure_reaction.m / p_reaction.m の移植).

MATLAB GUIツール Midas_QR (privatetool_function/MIDAS/QR/) のうち、
支点反力図を描く figure_reaction.m (633行) と補助関数 p_reaction.m を
Python/matplotlib (Agg) へ移植したもの。

移植元:
  figure_reaction.m : 支点ID図 + 鉛直(長期)反力図 + 水平/短期反力図 (伏図)
  p_reaction.m      : 反力位置マーカー+注記 (figure_reaction-old.m 専用の補助)
  ※ figure_reaction-old.m は旧版のため移植しない。

呼び出し元 (MIDAS_QRplot_fig.m 322-420行) の流れ:
  1. listdlg で鉛直荷重ケース(case_L)・短期or水平荷重ケース(case_S)を選択。
  2. case_S があれば「水平荷重のみ」か「短期荷重(長期鉛直+水平)」かを questdlg
     で選択。「水平のみ」なら case_H=case_S, case_S=[]、「短期」なら case_H=[]。
     したがって case_S と case_H は排他 (同時に非空にならない)。
  3. 'All' の場合: pick_coefi を全通りに適用し、reaction は全データのまま。
     'byGroup' の場合: 選択グループの節点だけ reaction を抽出し
     sortrows(plot_reaction,[2 1]) (ケース,節点番号順) に並べ替えたうえで
     load_case_index_reaction を作り直して渡す。
  4. figure_reaction が (a)支点ID図 1枚 (b)case_L 各ケース1枚
     (c)case_S または case_H 各ケース1枚 を描く。

MATLAB版との意図的差異:
  - listdlg('反力レベルを指定してください') は case_height 引数に置換
    (1-based index列。None=全レベル選択扱い)。
  - print_format/close_set 引数は廃止し、常に out_dir へPDF保存。
    ファイル名は 'reaction_<連番>_<ケース名>.pdf'
    (支点ID図は 'reaction_<連番>_支点ID図.pdf')。伏図のため構面名は付かない。
  - タイトル等の strcat は既存移植 (draw_stress) と同様、全角スペースを
    文字どおり連結する (MATLABのstrcatはchar入力の末尾空白を落とすため、
    原典実行結果とは空白の有無が異なりうる。cosmeticな差)。
  - 用紙設定は draw_model._PAPER_LAND (PaperSize値) を使用し、
    paper_orient (1=横 それ以外=縦) で幅高さを入れ替える。
"""

import os

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

try:
    from .util import find_index, doublecheck
    from .mgt import node_index_get
    from .ratio_pipeline import column_beam_judge
    from .draw_model import (
        _setup_japanese_font, _plot_plan_axis, _text, _kline, _safe_name,
        _PAPER_LAND,
    )
except ImportError:  # スクリプト実行時
    from util import find_index, doublecheck
    from mgt import node_index_get
    from ratio_pipeline import column_beam_judge
    from draw_model import (
        _setup_japanese_font, _plot_plan_axis, _text, _kline, _safe_name,
        _PAPER_LAND,
    )


# ---------------------------------------------------------------------------
# 小物
# ---------------------------------------------------------------------------

def _isempty(x):
    """MATLAB isempty 相当."""
    if x is None:
        return True
    if isinstance(x, (list, tuple, str)):
        return len(x) == 0
    return np.asarray(x).size == 0


def _num2str(x, fmt):
    """MATLAB num2str(x, format): sprintf後の前後空白を除去した文字列."""
    return (fmt % float(x)).strip()


def _checked_index(index, what):
    """find_index の結果を検査して int ndarray で返す.

    NOTE: MATLAB版 find_index は未検出時 0 を返し node(0,:) アクセスで
    エラー停止する。Python版は -1 を返すため、負のままだと node[-1] が
    末尾行を指してしまう。原典と同様にエラーとするため明示的に検査する。
    """
    idx = np.atleast_1d(np.asarray(index, dtype=int))
    if np.any(idx < 0):
        raise IndexError('figure_reaction: %s が node データ内に'
                         '見つかりません (原典はfind_index=0でエラー停止)' % what)
    return idx


def _support_level_label(z_reac, num_z_reac):
    """x_label ='支点レベル：...' の組立て (figure_reaction.m 112-119行)."""
    x_label = '支点レベル：'
    z = np.atleast_1d(np.asarray(z_reac, dtype=float)).ravel()
    if num_z_reac == 1:
        x_label = x_label + _num2str(z[0], '%15.2f') + 'm'
    else:
        for i_r in range(z.size):
            x_label = x_label + '　' + _num2str(z[i_r], '%15.2f') + 'm/'
    return x_label


def _finish_reaction_figure(fig, ax, title, x_label, xlim, ylim,
                            title_fontsize, paper_orient, paper_size):
    """軸範囲・タイトル・用紙サイズ設定 (figure_reaction.m 105-156行相当).

    daspect([1 1 1]) / box on / set(gcf,'Paper...') をまとめて行う。
    draw_model._finish_figure と同等だが、原典どおり paper_orient 引数
    (1=landscape それ以外=portrait) で向きを決める。
    """
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlim(xlim)
    ax.set_ylim(ylim)
    ax.set_title(title, fontsize=title_fontsize)
    ax.set_xlabel(x_label, fontsize=title_fontsize)
    if paper_orient == 1:
        w, h = _PAPER_LAND[paper_size]
    else:
        h, w = _PAPER_LAND[paper_size]
    fig.set_size_inches(w, h)
    ax.set_aspect('equal', adjustable='box')  # daspect([1 1 1])
    ax.set_position([0.04, 0.06, 0.92, 0.87])
    for s in ax.spines.values():              # box on
        s.set_linewidth(0.5)


def _plot_support_level(ax, element, node, wall_element, truss_stress, z_reac):
    """支点レベルにある梁・壁・トラス要素のプロット (figure_reaction.m 38-79行).

    原典では各図の冒頭で同一ブロックが4回繰り返されるため関数化した。
    """
    z_reac = np.atleast_1d(np.asarray(z_reac, dtype=float)).ravel()

    # %%%%%% 支点レベルにある要素のプロット %%%%%%
    if _isempty(element):
        pass
    else:
        c_b_result = column_beam_judge(
            np.arange(element.shape[0]), element, node)
        b_index = np.where(c_b_result - 2)[0]  # 柱(2)以外 = 梁・斜め材
        # beam要素から指定されたレベルにあるものを取り出す
        for row in b_index:
            # MATLAB: element(b_index(i_beam)) は線形index → 1列目の要素番号
            inode_index, jnode_index = node_index_get(
                element[row, 0], element, node)  # i端の支点のZ座標
            z_inode = node[inode_index, 3]
            if np.sum(z_inode == z_reac) == 0:
                pass
            else:
                _kline(ax, [node[inode_index, 1], node[jnode_index, 1]],
                       [node[inode_index, 2], node[jnode_index, 2]])

    # %%%%%% 支点レベルにある壁要素のプロット %%%%%%
    if _isempty(wall_element):
        pass
    else:
        wall_element = np.asarray(wall_element, dtype=float)
        for i_wall in range(wall_element.shape[0]):
            if np.sum(wall_element[i_wall, 0] == z_reac) == 0:
                pass
            else:
                node_index = _checked_index(
                    find_index(node[:, 0], wall_element[i_wall, 2:4]),
                    '壁要素の節点番号')
                _kline(ax,
                       [node[node_index[0], 1], node[node_index[1], 1]],
                       [node[node_index[0], 2], node[node_index[1], 2]],
                       linewidth=1.0)

    # %%%%%% 支点レベルにあるトラス要素のプロット %%%%%%
    if _isempty(truss_stress):
        pass
    else:
        truss_ele_no = doublecheck(truss_stress[:, 0])
        for i_truss in range(truss_ele_no.size):
            inode_index, jnode_index = node_index_get(
                truss_ele_no[i_truss], element, node)
            z_inode = min(node[inode_index, 3], node[jnode_index, 3])
            if np.sum(z_inode == z_reac) == 0:
                pass
            else:
                _kline(ax, [node[inode_index, 1], node[jnode_index, 1]],
                       [node[inode_index, 2], node[jnode_index, 2]],
                       linewidth=1.0)


def _one_reaction_figure(element, node, wall_element, truss_stress,
                         z_reac, num_z_reac, num_reaction, reac_nod_index,
                         annot, boxed,
                         axis_xtick, axis_ytick, axis_name, axis_node,
                         axis_plot_coefi,
                         mergin_LEFT, mergin_RIGHT, mergin_TOP, mergin_BOTTOM,
                         reaction_fontsize, title, title_fontsize,
                         paper_orient, paper_size, out_path):
    """1枚分の反力図を描いてPDF保存する (figure_reaction.m の繰返しブロック).

    annot(i_r) は i_r 番目(0-based)の支点の注記文字列を返す関数。
    戻り値: 更新後の (axis_xtick, axis_ytick)
    (原典では plot_plan_axis の戻りが次の図へ引き継がれる)。
    """
    fig = plt.figure()
    ax = fig.add_subplot(111)

    _plot_support_level(ax, element, node, wall_element, truss_stress, z_reac)

    # %%%%%% 反力値(または支点ID)のplot %%%%%%
    if num_z_reac == 1:
        for i_r in range(num_reaction):
            _text(ax, node[reac_nod_index[i_r], 1],
                  node[reac_nod_index[i_r], 2], annot(i_r),
                  ha='left', va='top', fs=reaction_fontsize, boxed=boxed)
    else:
        for i_r in range(num_reaction):
            if np.sum(node[reac_nod_index[i_r], 3] == z_reac) == 0:
                pass
            else:
                _text(ax, node[reac_nod_index[i_r], 1],
                      node[reac_nod_index[i_r], 2], annot(i_r),
                      ha='left', va='top', fs=reaction_fontsize, boxed=boxed)

    # %%%%%% 通り芯情報を記入する %%%%%%
    axis_xtick, axis_ytick = _plot_plan_axis(
        ax, axis_xtick, axis_ytick, node, axis_name, axis_node,
        axis_plot_coefi, mergin_LEFT, mergin_BOTTOM, reaction_fontsize)

    # %%%%%% 応力図の枠の大きさを設定 %%%%%%
    xlim = (np.min(axis_xtick) - mergin_LEFT,
            np.max(axis_xtick) + mergin_RIGHT)
    ylim = (np.min(axis_ytick) - mergin_BOTTOM,
            np.max(axis_ytick) + mergin_TOP)
    x_label = _support_level_label(z_reac, num_z_reac)

    _finish_reaction_figure(fig, ax, title, x_label, xlim, ylim,
                            title_fontsize, paper_orient, paper_size)
    fig.savefig(out_path, format='pdf')
    plt.close(fig)
    return axis_xtick, axis_ytick


# ---------------------------------------------------------------------------
# p_reaction.m
# ---------------------------------------------------------------------------

def p_reaction(ax, plot_reaction, node, reac_nod_index, i_r,
               reaction_fontsize):
    """p_reaction.m: 反力位置に赤×マーカーと注記文字列を描く.

    figure_reaction-old.m (未移植の旧版) だけが使用する補助関数。
    互換のため小関数として移植する。

    引数:
      ax             : matplotlib Axes (MATLAB版は current axes)
      plot_reaction  : 注記文字列 (複数行は '\\n' 区切り。MATLAB版はstrvcat)
      node           : mgtopen_node の戻り値 (N x [番号,X,Y,Z,...])
      reac_nod_index : 支点節点の node 内 行index列 (0-based)
      i_r            : 支点の連番 (0-based。MATLAB版は1-based)
      reaction_fontsize : 注記フォントサイズ
    """
    ax.plot(node[reac_nod_index[i_r], 1], node[reac_nod_index[i_r], 2],
            'rx', markersize=2)
    _text(ax, node[reac_nod_index[i_r], 1], node[reac_nod_index[i_r], 2],
          plot_reaction, ha='left', va='top', fs=reaction_fontsize)


# ---------------------------------------------------------------------------
# figure_reaction.m
# ---------------------------------------------------------------------------

def figure_reaction(unit_element, axis_element, reaction, axis_node, node,
                    load_case_index_reaction, element, uniele_ID, axis_name,
                    mergin_LEFT, mergin_RIGHT, mergin_TOP, mergin_BOTTOM,
                    axisname_location, line_location, dimension_fontsize,
                    axis_fontsize, title_fontsize, reaction_fontsize,
                    load_case_name, paper_orient, paper_size,
                    case_L, case_S, wall_element, truss_stress, case_H,
                    fig_no, axis_plot_coefi, out_dir, case_height=None):
    """figure_reaction.m: 支点反力図 (伏図) を描きPDF保存する.

    構成 (原典と同じ):
      1枚目          : 支点ID図 (支点番号を白地黒枠で注記)
      case_L 各ケース: 鉛直(長期)荷重時・基礎反力図 (支点ID + 鉛直反力FZ)
      case_S 各ケース: 水平/短期荷重時・基礎反力図 (支点ID + X/Y/Z 反力)
      case_H 各ケース: 水平/短期荷重時・基礎反力図 (X/Y/Z 反力のみ)
      ※ case_S と case_H は排他 (呼び出し元 MIDAS_QRplot_fig.m 340-349行で
        「水平荷重のみ」を選ぶと case_H=case_S, case_S=[] になる)。

    引数 (呼び出し元 MIDAS_QRplot_fig.m 322-420行 の意味):
      unit_element : mgtopen_unit_2015 の戻り値 (結合部材のcell相当list)。
                     ※原典の figure_reaction.m 内では未使用。
      axis_element : 選択構面(通り)ごとの要素番号列のlist (cell相当)。
                     ※原典内では未使用 (通り芯注記は axis_node を使う)。
      reaction     : 反力データ N x 8 [節点,ケース,FX,FY,FZ,MX,MY,MZ]。
                     各ケース内で節点番号昇順に並んでいること
                     ('byGroup'時は呼び出し元が sortrows([2 1]) 済み)。
      axis_node    : 選択構面(通り)ごとの節点番号列のlist (cell相当)。
                     通り芯符号の記入 (plot_plan_axis) に使用。
      node         : mgtopen_node の戻り値 (N x [番号,X,Y,Z,...])。
      load_case_index_reaction : 各荷重ケースの reaction 内先頭行 (0-based;
                     draw_stress._load_case_split の戻り値。MATLAB版は
                     1-based だが本移植では 0-based に一貫置換)。
      element      : mgtopen_element の戻り値
                     (N x [番号,材料,断面,節点i,節点j,β,...])。
      uniele_ID    : 結合部材を構成する原要素番号の昇順列。※原典内では未使用。
      axis_name    : 選択構面(通り)名のlist[str] (pick_coefi の戻り)。
      mergin_LEFT/RIGHT/TOP/BOTTOM : 図枠の余白 [m]。
      axisname_location, line_location, dimension_fontsize, axis_fontsize :
                     ※原典内では未使用 (シグネチャ互換のため残置)。
      title_fontsize, reaction_fontsize : タイトル/反力注記フォントサイズ。
      load_case_name : 荷重ケース名 list[str]。case_L 等の 1-based index で
                     load_case_name[case-1] を参照する。
      paper_orient : 1=横置き(landscape), それ以外=縦置き(portrait)。
      paper_size   : 1=A1, 2=A2, 3=A3, 4=A4。
      case_L       : 鉛直(長期)荷重ケースの 1-based index列 (listdlg相当)。
      case_S       : 短期荷重(長期鉛直+水平)ケースの 1-based index列。
      wall_element : mgtopen_wall の戻り値第1要素 (N x 10;
                     1列目=ベースレベルZ, 3-4列目=下端節点番号)。
      truss_stress : トラス応力 N x 4 [要素,ケース,Fx(i),Fx(j)]。
                     支点レベルのトラス要素の描画にのみ使用。
      case_H       : 水平のみ荷重ケースの 1-based index列。
      fig_no       : 図の連番 (原典の print -depsc 連番。ファイル名に使用)。
      axis_plot_coefi : pick_coefi の戻り (N x 3 [1|2, A, B])。
                     1列目 1=X通り(X一定) / 2=Y通り。
      out_dir      : PDF出力先ディレクトリ。
      case_height  : 支点レベルが複数ある場合に表示するレベルの 1-based
                     index列 (原典の listdlg『反力レベルを指定してください』
                     の置換え)。None=全レベル表示。単一レベルの場合は無視。

    戻り値: (fig_no, made)
      fig_no : 更新後の図連番
      made   : 生成したPDFパスのlist

    NOTE(原典の癖・そのまま移植):
      - 支点ID図の注記は reaction(load_case_index_reaction(case_L(1))+i_r-1,1)
        を参照するため、case_L が空で case_S/case_H のみ指定の場合は
        原典同様ここでエラーになる (IndexError)。
      - num_z_reac はレベル選択(case_height)前の doublecheck(z_reaction) の
        個数で固定される。選択で1レベルに絞っても複数レベル扱いの分岐
        ('Z:'接頭辞・レベルフィルタ) のまま描かれる。
      - case_L ループの num_z_reac==1 分岐では鉛直反力値に 'Z:' 接頭辞が
        付かない (複数レベル分岐では付く)。
      - case_S と case_H が両方非空の場合はどの短期/水平図も描かれない
        (elif の取りこぼし。呼び出し元からは発生しない)。
      - x_label の複数レベル表記は原典どおり末尾に '/' が残る。
    """
    _setup_japanese_font()
    os.makedirs(out_dir, exist_ok=True)

    node = np.asarray(node, dtype=float)
    reaction = np.asarray(reaction, dtype=float)
    lci = np.atleast_1d(np.asarray(load_case_index_reaction,
                                   dtype=int)).ravel()
    axis_plot_coefi = np.atleast_2d(np.asarray(axis_plot_coefi, dtype=float))
    case_L = [] if _isempty(case_L) else \
        [int(c) for c in np.atleast_1d(case_L).ravel()]
    case_S = [] if _isempty(case_S) else \
        [int(c) for c in np.atleast_1d(case_S).ravel()]
    case_H = [] if _isempty(case_H) else \
        [int(c) for c in np.atleast_1d(case_H).ravel()]

    made = []

    # 反力データ数
    num_reaction = int(reaction.shape[0] / lci.size)

    # 基本的に全反力値をplotする．ただしレベルが別れている場合にはレベル指定する
    # 支点となっている支点の位置情報を取り出すためnodeデータ内でのindex取得
    reac_nod_index = _checked_index(
        find_index(node[:, 0], reaction[:, 0]), '反力データの節点番号')
    reac_nod_index = doublecheck(reac_nod_index).astype(int)

    axis_xtick = node[reac_nod_index, 1]
    axis_ytick = node[reac_nod_index, 2]

    z_reaction = node[reac_nod_index, 3]
    z_reac = doublecheck(z_reaction)
    num_z_reac = doublecheck(z_reaction).size
    if num_z_reac == 1:
        pass
    else:
        # MATLAB: listdlg('反力レベルを指定してください') → case_height 引数化
        if case_height is not None:
            ch = np.atleast_1d(np.asarray(case_height, dtype=int)).ravel()
            z_reac = z_reac[ch - 1]

    if _isempty(case_L) and _isempty(case_S) and _isempty(case_H):
        # 長期短期の設定なし→プロットできない
        return fig_no, made

    # %%%%%% 支点ID図 (原典 36-173行) %%%%%%
    # NOTE: 原典は case_L(1) を参照するため case_L が空だとここでエラー
    def annot_id(i_r):
        return _num2str(
            reaction[lci[case_L[0] - 1] + i_r, 0], '%15.0f')

    out = os.path.join(out_dir, 'reaction_%d_%s.pdf'
                       % (fig_no, _safe_name('支点ID図')))
    axis_xtick, axis_ytick = _one_reaction_figure(
        element, node, wall_element, truss_stress,
        z_reac, num_z_reac, num_reaction, reac_nod_index,
        annot_id, True,
        axis_xtick, axis_ytick, axis_name, axis_node, axis_plot_coefi,
        mergin_LEFT, mergin_RIGHT, mergin_TOP, mergin_BOTTOM,
        reaction_fontsize, '支点ID図', title_fontsize,
        paper_orient, paper_size, out)
    made.append(out)
    fig_no = fig_no + 1

    # %%%%%% 鉛直(長期)荷重時・基礎反力図 (原典 179-322行) %%%%%%
    for i in range(len(case_L)):
        r0 = int(lci[case_L[i] - 1])
        if num_z_reac == 1:
            def annot_L(i_r, r0=r0):
                return ('支点ID:'
                        + _num2str(reaction[r0 + i_r, 0], '%15.0f') + '\n'
                        + _num2str(reaction[r0 + i_r, 4], '%15.1f'))
        else:
            def annot_L(i_r, r0=r0):
                return ('支点ID:'
                        + _num2str(reaction[r0 + i_r, 0], '%15.0f') + '\n'
                        + 'Z:' + _num2str(reaction[r0 + i_r, 4], '%15.1f'))

        case_name = load_case_name[case_L[i] - 1]
        title = ('鉛直(長期)荷重時・基礎反力図'
                 + '[　' + '　荷重ケース：' + case_name + '　]')
        out = os.path.join(out_dir, 'reaction_%d_%s.pdf'
                           % (fig_no, _safe_name(case_name)))
        axis_xtick, axis_ytick = _one_reaction_figure(
            element, node, wall_element, truss_stress,
            z_reac, num_z_reac, num_reaction, reac_nod_index,
            annot_L, False,
            axis_xtick, axis_ytick, axis_name, axis_node, axis_plot_coefi,
            mergin_LEFT, mergin_RIGHT, mergin_TOP, mergin_BOTTOM,
            reaction_fontsize, title, title_fontsize,
            paper_orient, paper_size, out)
        made.append(out)
        fig_no = fig_no + 1

    # %%%%%% 水平/短期荷重時・基礎反力図 (原典 324-633行) %%%%%%
    if _isempty(case_H):  # 短期のみ
        if _isempty(case_S):
            pass
        else:
            case_H1 = case_S
            for i_lcase in range(len(case_H1)):
                r0 = int(lci[case_H1[i_lcase] - 1])

                def annot_S(i_r, r0=r0):
                    return '\n'.join([
                        '支点ID:'
                        + _num2str(reaction[r0 + i_r, 0], '%15.0f'),
                        'X:' + _num2str(reaction[r0 + i_r, 2], '%15.1f'),
                        'Y:' + _num2str(reaction[r0 + i_r, 3], '%15.1f'),
                        'Z:' + _num2str(reaction[r0 + i_r, 4], '%15.1f')])

                case_name = load_case_name[case_H1[i_lcase] - 1]
                title = ('水平/短期荷重時・基礎反力図'
                         + '[　' + '　荷重ケース：' + case_name + '　]')
                out = os.path.join(out_dir, 'reaction_%d_%s.pdf'
                                   % (fig_no, _safe_name(case_name)))
                axis_xtick, axis_ytick = _one_reaction_figure(
                    element, node, wall_element, truss_stress,
                    z_reac, num_z_reac, num_reaction, reac_nod_index,
                    annot_S, False,
                    axis_xtick, axis_ytick, axis_name, axis_node,
                    axis_plot_coefi,
                    mergin_LEFT, mergin_RIGHT, mergin_TOP, mergin_BOTTOM,
                    reaction_fontsize, title, title_fontsize,
                    paper_orient, paper_size, out)
                made.append(out)
                fig_no = fig_no + 1

    elif _isempty(case_S):  # 水平のみ
        case_H1 = case_H
        for i_lcase in range(len(case_H1)):
            r0 = int(lci[case_H1[i_lcase] - 1])

            def annot_H(i_r, r0=r0):
                return '\n'.join([
                    'X:' + _num2str(reaction[r0 + i_r, 2], '%15.1f'),
                    'Y:' + _num2str(reaction[r0 + i_r, 3], '%15.1f'),
                    'Z:' + _num2str(reaction[r0 + i_r, 4], '%15.1f')])

            case_name = load_case_name[case_H1[i_lcase] - 1]
            title = ('水平/短期荷重時・基礎反力図'
                     + '[　' + '　荷重ケース：' + case_name + '　]')
            out = os.path.join(out_dir, 'reaction_%d_%s.pdf'
                               % (fig_no, _safe_name(case_name)))
            axis_xtick, axis_ytick = _one_reaction_figure(
                element, node, wall_element, truss_stress,
                z_reac, num_z_reac, num_reaction, reac_nod_index,
                annot_H, False,
                axis_xtick, axis_ytick, axis_name, axis_node,
                axis_plot_coefi,
                mergin_LEFT, mergin_RIGHT, mergin_TOP, mergin_BOTTOM,
                reaction_fontsize, title, title_fontsize,
                paper_orient, paper_size, out)
            made.append(out)
            fig_no = fig_no + 1
    # NOTE: case_S と case_H が両方非空の場合は原典同様なにも描かない

    return fig_no, made
