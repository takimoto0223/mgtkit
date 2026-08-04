# -*- coding: utf-8 -*-
"""RC壁要素(板壁)の断面検定 (MATLAB原典からの逐語移植).

元コード (MatlabT/):
  privatetool_function/MIDAS/ratio/RC/RCwall_ratio_analysis.m (462行)
      主関数 RCwall_ratio_analysis
      ローカル関数 SA_RW4_HMD_WALLELE_text (同ファイル139行以降)

内部で呼ぶ既移植関数 (再実装せず import 再利用):
  rc_check.SA_RW4_HMD / rc_check.SA_RW4Qratio /
  rc_check.Area_steelbar / rc_check.outf_steelbar_JIS
  src_check.SD_check / s_check._num2str

呼び出し側 (MIDASratioplot_fig.m 1342-1421行) の引数の意味:
  element_thick: 壁厚[mm] (thickness_wall行から断面ID列を除去した値)
  wall_width:  壁幅[m] (node1-node2距離) → 内部で*1000してL[mm]
  wall_height: 壁高さ[m] (node2-node3距離。SA_RW4QratioのL引数へ渡るが未使用)
  stress: 壁1枚あたり2行(壁頭/壁脚) x 6列 [Nx, Fy, Fz, Mx, My, Mz] (kN, kNm)
          1列目Nxは入口で符号反転される(圧縮を正へ)
  timecase: 荷重ケース番号 (1=長期, >=10(11,12..)=短期)
  Fc: ele_material(2:3) = [材料種別, Fc値] → 内部で Fc=[Fc(2) 0] に組替え
  wall_ID: select_wall(1:2)=[レベル, 壁ID] (wall_ID(2)のみ使用)
  thick_no: 厚さ断面ID
  maxratios: 長さ2 [最大検定比の壁ID, 最大検定比]
  maxratios_text: 最大検定比の詳細文 (更新時のみ差し替え)
  QL: 長期せん断力 2x2 [Qy(Fy), Qz(Fz)] (kN)。timecase==1では stress(:,2:3)
  qup_wall: せん断割増し係数 (詳細文の表示にのみ使用。検定計算には原典どおり
            1.0 直書きが渡る。下記NOTE参照)
  walldesign_index, select_wall: 原典で未使用 (シグネチャ維持のため受け取る)
  RCbar_arrange: *REBAR-WALL配筋行 (mgt.mgtopen_RCwall の RCwalls 1行,9列)
      [高さ, 壁ID, 縦筋径, 縦筋ピッチ, 横筋径, 横筋ピッチ,
       補強筋径, 補強筋間隔, 補強筋本数]
  reduction: 壁せん断低減率 (wallinfo_get(wall_r,...)。空なら1.0)
  wall_cover: 壁のかぶり厚[mm] (40未満は詳細文生成時にstop)
  LOAS_CASE_NAME: 荷重ケース名 (詳細文表示用)

返り値: (ratio_output(2x4), maxratios(長さ2), maxratios_text)
  ratio_output 列 = [NMy検定比, NMz検定比, Qz検定比, Qy検定比]
  (原典は zeros(2,3) 初期化後 (:,3:4) 代入で 2x4 へ自動拡張)

原典の疑義 (詳細は wall_report.md):
  - 原典127/203行: SA_RW4Qratio(...,QL,1.0) と 9引数呼び出し
    (シグネチャは10引数で末尾RCQが欠落)。MATLABのnargin挙動では
    未参照引数の欠落は許容され、SA_RW4Qratio.m 内で RCQ は一切参照されない
    ため実行時エラーにならない。Python側は RCQ=None を渡して再現。
  - 同箇所で qup_wall 引数でなく 1.0 直書き → 短期のせん断割増しが無効
    (Qs1=stressそのまま)。一方詳細文には qup_wall の値を表示する不整合。
  - 補強筋あり(di_sp!=0)の場合 steelbar(1)=2/3 を作るが、SA_RW4_HMD.m の
    該当分岐(elseif steelbar(1)==2 / ==3)は原典で全行コメントアウトされて
    おり、MATLABでも M/maxN 未定義の実行時エラーになる。Pythonでも同様に
    rc_check.SA_RW4_HMD 内で UnboundLocalError となる (忠実再現)。
"""

import math

import numpy as np

from .rc_check import (
    Area_steelbar,
    outf_steelbar_JIS,
    SA_RW4_HMD,
    SA_RW4Qratio,
)
from .src_check import SD_check
from .s_check import _num2str


# 鉄筋コンクリート壁の断面検定
def RCwall_ratio_analysis(element_thick, wall_width, wall_height, stress, timecase,
                          Fc, wall_ID, thick_no, maxratios, maxratios_text, QL,
                          qup_wall, walldesign_index, select_wall, RCbar_arrange,
                          reduction, wall_cover, LOAS_CASE_NAME):
    """RCwall_ratio_analysis.m の逐語移植 (RC壁要素=板壁の断面検定).

    元ファイル: privatetool_function/MIDAS/ratio/RC/RCwall_ratio_analysis.m 1-136行
    引数・返り値はモジュールdocstring参照。
    """
    stress = np.array(np.atleast_2d(np.asarray(stress, dtype=float)))  # コピー(呼び出し側を破壊しない)
    RCbar_arrange = np.asarray(RCbar_arrange, dtype=float).ravel()
    wall_ID = np.asarray(wall_ID, dtype=float).ravel()
    maxratios = np.array(np.asarray(maxratios, dtype=float).ravel())
    QL = np.atleast_2d(np.asarray(QL, dtype=float))

    stress[:, 0] = stress[:, 0] * -1
    # NOTE: 原典8行目 ratio_output=zeros(2,3) → 127行目の(:,3:4)代入で2x4へ自動拡張
    ratio_output = np.zeros((2, 4))

    t = float(np.asarray(element_thick, dtype=float).ravel()[0])  # 壁厚（mm）
    L = float(wall_width) * 1000  # 壁長さ
    Fc = np.array([np.asarray(Fc, dtype=float).ravel()[1], 0.0])

    # 低減率
    if reduction is None:
        reduction = 1.0
    else:
        _red = np.asarray(reduction, dtype=float).ravel()
        if _red.size == 0:  # MATLAB: isempty(reduction)
            reduction = 1.0
        else:
            reduction = float(_red[0])

    # 縦筋
    di_v = RCbar_arrange[2]  # 径
    SD_v = SD_check(di_v)  # 種
    pitch_v = RCbar_arrange[3]  # ピッチ

    # 横筋
    di_h = RCbar_arrange[4]  # 径
    SD_h = SD_check(di_h)  # 種
    pitch_h = RCbar_arrange[5]  # ピッチ
    HOOP = [di_h, pitch_h, 2, 2, SD_h, float(wall_cover)]

    # 補強筋
    di_sp = RCbar_arrange[6]  # 径

    if di_sp == 0:
        nv = math.ceil((L - 2 * wall_cover) / pitch_v)
        steelbar_y = [1, 2 * nv, di_v, nv, 2, SD_v, 0]
        steelbar_z = [1, 2 * nv, di_v, 2, nv, SD_v, 0]
    else:
        SD_sp = SD_check(di_sp)  # 種
        pitch_sp = RCbar_arrange[7]  # ピッチ
        n_sp = RCbar_arrange[8]  # 本数
        if L - 2 * wall_cover - 2 * pitch_sp * (n_sp / 2 - 1) >= pitch_v:
            nv_v = math.ceil((L - 2 * wall_cover - 2 * pitch_sp * (n_sp / 2 - 1)) / pitch_v)
            n_sp = n_sp * 2
            num = 2 * nv_v + n_sp
        elif (L - 2 * wall_cover) / (n_sp - 1) > max(2.5 * di_sp, di_sp + 25 * 1.25):
            nv_v = 0
            n_sp = n_sp * 2
            num = 2 * nv_v + n_sp
        else:
            nv_v = 0
            n_sp = (math.floor((L - 2 * wall_cover) / max(2.5 * di_sp, di_sp + 25 * 1.25)) + 1) * 2
            print('壁補強筋が指定本数入らないので配筋可能な本数に修正しました')
            num = 2 * nv_v + n_sp
        # NOTE: 原典57-58行 steelbar(1)=2/3 → SA_RW4_HMD.m の対応分岐は原典で
        #       全行コメントアウト済みのためMATLAB/Pythonとも実行時エラーになる
        steelbar_y = [2, num, di_v, nv_v, 2, SD_v, di_sp, n_sp, pitch_sp, SD_sp]
        steelbar_z = [3, num, di_v, nv_v, 2, SD_v, di_sp, n_sp, pitch_sp, SD_sp]

    # 軸力と強軸周りの許容曲げモーメントに対して検定を行う
    e = np.zeros(2)
    for ie in range(1, 3):
        Form_y = [t, L]
        if stress[ie - 1, 0] == 0:  # 軸力がゼロ→曲げのみで検定
            e[ie - 1] = 10 ** 6
        else:
            e[ie - 1] = abs(stress[ie - 1, 4]) / stress[ie - 1, 0] * 1000
        # [N M Xn]=SA_RW4_AIJ(e(ie),Form_y,steelbar_y,HOOP,Fc,timecase);
        M, maxN = SA_RW4_HMD(stress[ie - 1, 0], Form_y, steelbar_y, HOOP, Fc, timecase)
        if e[ie - 1] == 0:
            if stress[ie - 1, 0] > 0:  # 圧縮
                # ratio_output(ie,1) = abs(stress(ie,1)).*10^3./abs(N);
                ratio_output[ie - 1, 0] = abs(stress[ie - 1, 0]) * 10 ** 3 / abs(maxN[0])
            else:  # 引張
                # ratio_output(ie,1) = abs(stress(ie,1)).*10^3./abs(M);
                ratio_output[ie - 1, 0] = abs(stress[ie - 1, 0]) * 10 ** 3 / abs(maxN[1])
        else:
            if stress[ie - 1, 0] > 0:  # 圧縮
                ratio_output[ie - 1, 0] = max(abs(stress[ie - 1, 4]) * 10 ** 6 / abs(M),
                                              abs(stress[ie - 1, 0]) * 10 ** 3 / abs(maxN[0]))
            else:  # 引張
                ratio_output[ie - 1, 0] = max(abs(stress[ie - 1, 4]) * 10 ** 6 / abs(M),
                                              abs(stress[ie - 1, 0]) * 10 ** 3 / abs(maxN[1]))

            # ratio_output(ie,1) = abs(stress(ie,4)).*10^6./M;
            # ratio_output(ie,1) = abs(stress(ie,5)).*10^6./abs(M);

    # 軸力と弱軸周りの許容曲げモーメントに対して検定を行う
    # 許容NM曲線を呼び出し
    for ie in range(1, 3):
        Form_z = [L, t]
        if stress[ie - 1, 0] == 0:  # 軸力がゼロ→曲げのみで検定
            e[ie - 1] = 10 ** 6
        else:
            e[ie - 1] = abs(stress[ie - 1, 5]) / stress[ie - 1, 0] * 1000
        # [N M Xn]=SA_RW4_AIJ(e(ie),Form_z,steelbar_z,HOOP,Fc,timecase);
        M, maxN = SA_RW4_HMD(stress[ie - 1, 0], Form_z, steelbar_z, HOOP, Fc, timecase)
        if e[ie - 1] == 0:
            if stress[ie - 1, 0] > 0:  # 圧縮
                ratio_output[ie - 1, 1] = abs(stress[ie - 1, 0]) * 10 ** 3 / abs(maxN[0])
            else:  # 引張
                ratio_output[ie - 1, 1] = abs(stress[ie - 1, 0]) * 10 ** 3 / abs(maxN[1])
        else:
            if stress[ie - 1, 0] > 0:  # 圧縮
                ratio_output[ie - 1, 1] = max(abs(stress[ie - 1, 5]) * 10 ** 6 / abs(M),
                                              abs(stress[ie - 1, 0]) * 10 ** 3 / abs(maxN[0]))
            else:  # 引張
                ratio_output[ie - 1, 1] = max(abs(stress[ie - 1, 5]) * 10 ** 6 / abs(M),
                                              abs(stress[ie - 1, 0]) * 10 ** 3 / abs(maxN[1]))

    # せん断の検定（短期のせん断の検定のためには長期のせん断力も必要）
    Form_Q = np.array([[L, t], [L, t]])
    # NOTE: 原典127行は SA_RW4Qratio(...,QL,1.0) の9引数呼び出し。
    #       SA_RW4Qratio.m のシグネチャは10引数 (末尾RCQ) だが関数内でRCQは
    #       一切参照されないためMATLABのnargin挙動では欠落しても実行時エラーに
    #       ならない。Python側は RCQ=None を渡して同挙動を再現。
    #       また qup_wall 引数でなく 1.0 直書き → せん断割増しが計算上は無効。
    ratio_Q, ALW_Q, _Qs1 = SA_RW4Qratio(Form_Q, wall_height, steelbar_y, HOOP, Fc,
                                        stress, timecase, QL, 1.0, None)
    ratio_output[:, 2:4] = np.asarray(ratio_Q, dtype=float)
    ratio_output[:, 2:4] = ratio_output[:, 2:4] / reduction

    # 最大検定値の保存
    if np.max(ratio_output) > np.max(maxratios[1]):
        maxratios[0] = wall_ID[1]
        maxratios[1] = np.max(ratio_output)
        maxratios_text = SA_RW4_HMD_WALLELE_text(
            L, steelbar_y, steelbar_z, HOOP, Fc, stress, timecase,
            QL, wall_ID[1], thick_no, qup_wall, reduction, LOAS_CASE_NAME,
            Form_Q, Form_y, Form_z)

    return ratio_output, maxratios, maxratios_text


def SA_RW4_HMD_WALLELE_text(ele_length, steelbar_y, steelbar_z, HOOP, Fc, stress, timecase,
                            QL, ele_no, section_no, qup_wall, reduction, LOAS_CASE_NAME,
                            Form_Q, Form_y, Form_z):
    """RCwall_ratio_analysis.m 139-462行 ローカル関数 SA_RW4_HMD_WALLELE_text の逐語移植.

    RC壁の断面算定詳細のテキスト出力
    軸力(N)＋曲げ(MM)，せん断(Q)に対する断面算定
    返り値: list[str] (MATLAB strvcat 相当)
    """
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    steelbar_y = np.asarray(steelbar_y, dtype=float).ravel()
    steelbar_z = np.asarray(steelbar_z, dtype=float).ravel()
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    QL = np.atleast_2d(np.asarray(QL, dtype=float))

    e = np.zeros(2)
    M = np.zeros((2, 2))
    maxN = np.zeros((2, 2, 2))
    ratio_output = np.zeros((2, 4))
    for ie in range(1, 3):
        if stress[ie - 1, 0] == 0:  # 軸力がゼロ→曲げのみで検定
            e[ie - 1] = 10 ** 6
        else:
            e[ie - 1] = abs(stress[ie - 1, 4]) / stress[ie - 1, 0] * 1000
        # [N M Xn]=SA_RW4_AIJ(e(ie),Form_y,steelbar_y,HOOP,Fc,timecase);
        M[ie - 1, 0], maxN[ie - 1, 0, :] = SA_RW4_HMD(stress[ie - 1, 0], Form_y,
                                                      steelbar_y, HOOP, Fc, timecase)

        if e[ie - 1] == 0:
            if stress[ie - 1, 0] > 0:  # 圧縮
                ratio_output[ie - 1, 0] = abs(stress[ie - 1, 0]) * 10 ** 3 / abs(maxN[ie - 1, 0, 0])
            else:  # 引張
                ratio_output[ie - 1, 0] = abs(stress[ie - 1, 0]) * 10 ** 3 / abs(maxN[ie - 1, 0, 1])
        else:
            if stress[ie - 1, 0] > 0:  # 圧縮
                ratio_output[ie - 1, 0] = max(abs(stress[ie - 1, 4]) * 10 ** 6 / abs(M[ie - 1, 0]),
                                              abs(stress[ie - 1, 0]) * 10 ** 3 / abs(maxN[ie - 1, 0, 0]))
            else:  # 引張
                ratio_output[ie - 1, 0] = max(abs(stress[ie - 1, 4]) * 10 ** 6 / abs(M[ie - 1, 0]),
                                              abs(stress[ie - 1, 0]) * 10 ** 3 / abs(maxN[ie - 1, 0, 1]))
    # 軸力と弱軸周りの許容曲げモーメントに対して検定を行う

    # 許容NM曲線を呼び出し
    for ie in range(1, 3):
        if stress[ie - 1, 0] == 0:  # 軸力がゼロ→曲げのみで検定
            e[ie - 1] = 10 ** 6
        else:
            e[ie - 1] = abs(stress[ie - 1, 5]) / stress[ie - 1, 0] * 1000
        # [N M Xn]=SA_RW4_AIJ(e(ie),Form_z,steelbar_z,HOOP,Fc,timecase);
        M[ie - 1, 1], maxN[ie - 1, 1, :] = SA_RW4_HMD(stress[ie - 1, 0], Form_z,
                                                      steelbar_z, HOOP, Fc, timecase)
        if e[ie - 1] == 0:
            if stress[ie - 1, 0] > 0:  # 圧縮
                ratio_output[ie - 1, 1] = abs(stress[ie - 1, 0]) * 10 ** 3 / abs(maxN[ie - 1, 1, 0])
            else:  # 引張
                ratio_output[ie - 1, 1] = abs(stress[ie - 1, 0]) * 10 ** 3 / abs(maxN[ie - 1, 1, 1])
        else:
            if stress[ie - 1, 0] > 0:  # 圧縮
                ratio_output[ie - 1, 1] = max(abs(stress[ie - 1, 5]) * 10 ** 6 / abs(M[ie - 1, 1]),
                                              abs(stress[ie - 1, 0]) * 10 ** 3 / abs(maxN[ie - 1, 1, 0]))
            else:  # 引張
                ratio_output[ie - 1, 1] = max(abs(stress[ie - 1, 5]) * 10 ** 6 / abs(M[ie - 1, 1]),
                                              abs(stress[ie - 1, 0]) * 10 ** 3 / abs(maxN[ie - 1, 1, 1]))

    # せん断(Q)に対する断面算定
    # NOTE: 原典203行も 127行と同じ 9引数呼び出し (RCQ欠落, qup_wall=1.0直書き)
    ratio_Q, ALW_Q, Qs1 = SA_RW4Qratio(Form_Q, ele_length, steelbar_y, HOOP, Fc,
                                       stress, timecase, QL, 1.0, None)
    ratio_output[:, 2:4] = np.asarray(ratio_Q, dtype=float)
    ratio_output[:, 2:4] = ratio_output[:, 2:4] / reduction
    # NOTE: MATLABの ALW_Q(1)/ALW_Q(2) 線形添字(列優先)を再現するためravel(order='F')
    ALW_Q = np.asarray(ALW_Q, dtype=float).ravel(order='F')
    Qs1 = np.atleast_2d(np.asarray(Qs1, dtype=float))
    Fxx = stress[:, 0] * 10 ** 3  # 軸力[N]
    My = stress[:, 4] * 10 ** 6  # 曲げモーメント強軸[Nmm]
    Mz = stress[:, 5] * 10 ** 6  # 曲げモーメント弱軸[Nmm]
    Fz = stress[:, 2] * 10 ** 3  # せん断力強軸方向[N]
    Fy = stress[:, 1] * 10 ** 3  # せん断力弱軸方向[N]

    # %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%以上で検定値の算出終了

    # RC断面の外形情報
    b = Form_y[0]
    D = Form_y[1]

    # 帯筋情報を読み込み
    # NOTE: load bar_table.mat は rc_check のヘルパー関数が内部保持するため不要
    di_support = HOOP[0]; pitch = HOOP[1]; SD_support = HOOP[4]; cover_depth = HOOP[5]

    aw = np.zeros(2)
    aw[0] = Area_steelbar(di_support, HOOP[2])  # Fyせん断補強筋本数
    aw[1] = Area_steelbar(di_support, HOOP[3])  # Fzせん断補強筋数

    # 主筋情報：steelbar = [type 総本数num_steel，径D，せい方向本数nv，幅方向本数nh, SD]
    if steelbar_y[0] == 1:  # 端部補強筋なしの場合
        num = steelbar_y[1]; nv = steelbar_y[3]
        nh = steelbar_y[4]; di_main = steelbar_y[2]; SD_main = steelbar_y[5]

        # 柱主筋の重心の縁距離
        dc = cover_depth + outf_steelbar_JIS(di_support) + outf_steelbar_JIS(di_main) * 0.5
        dd = (D - 2 * dc) / (nv - 1)

        if (nh - 1) * 2 + (nv - 1) * 2 == num:
            pass
        else:
            print('ERROR = 配筋情報ミス')
            raise ValueError('配筋情報ミス')  # NOTE: MATLABのstop（未定義関数）相当

    elif steelbar_y[0] == 2:  # 端部補強筋ありの場合<強軸>

        num_v = steelbar_y[3] * 2
        di_main = steelbar_y[2]; SD_main = steelbar_y[5]
        nv_v = steelbar_y[3]
        nh = steelbar_y[4]
        a_v = Area_steelbar(di_main, 1)

        di_sp = steelbar_y[6]
        n_sp = steelbar_y[7]
        pitch_sp = steelbar_y[8]; SD_sp = steelbar_y[9]
        a_sp = Area_steelbar(di_sp, 1)

        if num_v > 0:
            dd1 = (D - 2 * cover_depth - 2 * pitch_sp * (n_sp / 4 - 1)) / (nv_v + 1)
            dd2 = pitch_sp
        else:
            dd1 = np.inf
            dd2 = (D - 2 * cover_depth) / (n_sp / 2 - 1)

        # 柱主筋の重心の縁距離
        dc = cover_depth + outf_steelbar_JIS(di_support) + outf_steelbar_JIS(di_sp) * 0.5
        dd = min(dd1, dd2)

    text = ['RC壁の断面算定内容（最大検定値の検定内容）']
    text.append('軸力と強軸および弱軸周りの曲げを考慮した断面算定を行う．')
    text.append('せん断については弱軸および強軸方向の検討を行う．')
    text.append('　　')

    text.append('厚さID　：' + _num2str(section_no) + '　壁ID　：' + _num2str(ele_no))
    text.append('　　')
    text.append('RC壁サイズ(モデル内)　：bxD-' + _num2str(b, '%15.1f') + '[mm] x'
                + _num2str(D, '%15.1f') + '[mm]')
    if steelbar_y[0] == 1:  # 端部補強筋なしの場合
        text.append('　　')
        text.append('＊配筋情報')
        text.append('縦筋本数　：' + _num2str(num) + '-D' + _num2str(di_main)
                    + '　横筋　：D' + _num2str(di_support) + '@' + _num2str(pitch))
    elif steelbar_y[0] == 2:  # 端部補強筋ありの場合

        if num_v > 0:
            text.append('　　')
            text.append('＊配筋情報')
            # NOTE: 原典285行 num2str(num_v*2)。num_v は既に steelbar_y(4)*2 なので
            #       表示は縦筋列数の4倍になる (原典どおり再現)
            text.append('縦筋本数　：' + _num2str(num_v * 2) + '-D' + _num2str(di_main)
                        + '　横筋　：D' + _num2str(di_support) + '@' + _num2str(pitch))
            text.append('曲げ補強筋本数　：' + _num2str(n_sp) + '-D' + _num2str(di_sp))

        else:
            text.append('　　')
            text.append('＊配筋情報')
            text.append('縦筋本数(曲げ補強のみ)：' + _num2str(n_sp) + '-D' + _num2str(di_sp)
                        + '　横帯筋　：D' + _num2str(di_support) + '@' + _num2str(pitch))
    text.append('　　')
    text.append('　　')
    text.append('＊使用材料（鉄筋およびコンクリート）')
    text.append('主筋　：SD' + _num2str(SD_main) + '　横筋　：SD' + _num2str(SD_support))
    text.append('コンクリート　：Fc' + _num2str(np.asarray(Fc, dtype=float).ravel()[0]))
    text.append('　　')

    # 純圧縮のplot
    if timecase >= 2:
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        t_case = LOAS_CASE_NAME
    else:
        ERROR = '長短期設定ミス'  # NOTE: MATLAB同様ここでは停止しない
        print('ERROR = ' + ERROR)  # NOTE: 原典308行セミコロン無し代入のecho相当
        # NOTE: t_case未定義のまま後続で参照 → MATLAB同様に実行時エラー(UnboundLocalError)

    text.append('*****計算外規定*****')

    # 構造細則(計算外規定のチェック)

    # その０：鉄筋あきのチェック
    # dd2は幅方向の鉄筋間隔，ddはせい方向の間隔
    dd2 = (b - 2 * dc) / (nh - 1)

    if steelbar_y[0] == 1:
        dd = dd - outf_steelbar_JIS(di_main)
        dd2 = dd2 - outf_steelbar_JIS(di_main)
    elif steelbar_y[0] == 2:
        dd = dd - outf_steelbar_JIS(di_sp)
        dd2 = dd2 - outf_steelbar_JIS(di_sp)
    check_dd = min(dd, dd2)
    min_dd = max(1.5 * di_main, 25 * 1.25)
    text.append('　　')
    text.append('＊縦筋のあきの検討')
    text.append('最低あき寸法（「径の1.5倍」・「粗骨材寸法の1.25倍」・「25mm」の最大値）　：'
                + _num2str(min_dd, '%15.2f') + '[mm]')
    text.append('壁幅方向のあき　：' + _num2str(dd2, '%15.1f') + '[mm]　　壁長さ方向のあき[mm]　：'
                + _num2str(dd, '%15.1f') + '[mm]')

    if check_dd >= min_dd:
        text.append('縦筋のあき間隔の検討　：OK')
    else:
        print('鉄筋あき不足(RC壁）　壁ID：' + _num2str(ele_no) + '　厚さID：' + _num2str(section_no))
        print('壁長さ方向あき：' + _num2str(dd, '%15.2f') + 'mm')
        print('壁幅方向あき：' + _num2str(dd2, '%15.2f') + 'mm')
        print('あきの最小値：' + _num2str(min_dd, '%15.2f') + 'mm')
        text.append('主筋のあき間隔の検討　：NG')
        # stop

    # その３：HOOP間隔の規定
    text.append('　　')
    text.append('＊横筋間隔の規定')
    if di_support == 10 and pitch <= 300:
        text.append('横筋径　：D' + _num2str(di_support) + '　横筋間隔　：' + _num2str(pitch) + '　OK')
    elif di_support > 10 and pitch <= 300:
        text.append('横筋径　：D' + _num2str(di_support) + '　横筋間隔　：' + _num2str(pitch) + '　OK')
    else:
        print('横筋間隔NG')
        text.append('横筋径　：D' + _num2str(di_support) + '　横筋間隔　：' + _num2str(pitch)
                    + '　横筋間隔NG')
        # stop

    # その５：鉄筋かぶりあつ
    text.append('　　')
    text.append('＊鉄筋かぶりの規定(40mm)')
    if cover_depth >= 40:
        text.append('かぶり厚　：' + _num2str(cover_depth) + 'mm≧40mm　：OK')
    else:
        print('ERROR = 鉄筋かぶり厚再検討（40mm未満）')
        text.append('かぶり厚　：' + _num2str(cover_depth) + 'mm＜40mm　：NG')
        raise ValueError('鉄筋かぶり厚再検討（40mm未満）')  # NOTE: MATLABのstop相当
    text.append('　　')
    text.append('　　')
    text.append('*****設計用応力*****')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　壁頭')
    text.append('軸力　：' + _num2str(Fxx[0] / 1000, '%15.1f') + '　[kN]　　曲げMy　：'
                + _num2str(My[0] / 10 ** 6, '%15.1f') + '　[kNm]　　曲げMz　：'
                + _num2str(Mz[0] / 10 ** 6, '%15.1f') + '　[kNm]')
    if timecase == 1:
        text.append('せん断力Qz　：' + _num2str(Fz[0] / 10 ** 3, '%15.1f') + '　[kN]'
                    + '　　せん断力Qy　：' + _num2str(Fy[0] / 10 ** 3, '%15.1f') + '　[kN]')
    else:
        text.append('せん断力Qz　：' + _num2str(Fz[0] / 10 ** 3, '%15.1f') + '　[kN]'
                    + '　　せん断力Qy　：' + _num2str(Fy[0] / 10 ** 3, '%15.1f') + '　[kN]')
        text.append('長期荷重時せん断力Qz　：' + _num2str(QL[0, 1], '%15.1f') + '[kN]'
                    + '　　Qy　：' + _num2str(QL[0, 0], '%15.1f') + '[kN]')
        text.append('せん断割増し(n=' + _num2str(qup_wall, '%15.2f') + ')から決まる設計用せん断力Qz：'
                    + _num2str(Qs1[0, 0], '%15.1f') + '[kN]' + '　　Qy　：'
                    + _num2str(Qs1[1, 0], '%15.1f') + '[kN]')

    # NOTE: 原典382-391行 (「中央」の設計用応力表示) は原典で全行コメントアウト済み

    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　壁脚')
    text.append('軸力　：' + _num2str(Fxx[1] / 1000, '%15.1f') + '　[kN]　　曲げMy　：'
                + _num2str(My[1] / 10 ** 6, '%15.1f') + '　[kNm]　　曲げMz　：'
                + _num2str(Mz[1] / 10 ** 6, '%15.1f') + '　[kNm]')
    if timecase == 1:
        text.append('せん断力Qz　：' + _num2str(Fz[1] / 10 ** 3, '%15.1f') + '　[kN]'
                    + '　　せん断力Qy　：' + _num2str(Fy[1] / 10 ** 3, '%15.1f') + '　[kN]')
    else:
        text.append('せん断力Qz　：' + _num2str(Fz[1] / 10 ** 3, '%15.1f') + '　[kN]'
                    + '　　せん断力Qy　：' + _num2str(Fy[1] / 10 ** 3, '%15.1f') + '　[kN]')
        text.append('長期荷重時せん断力Qz　：' + _num2str(QL[1, 1], '%15.1f') + '[kN]'
                    + '　　Qy　：' + _num2str(QL[1, 0], '%15.1f') + '[kN]')
        text.append('せん断割増し(n=' + _num2str(qup_wall, '%15.2f') + ')から決まる設計用せん断力Qz：'
                    + _num2str(Qs1[0, 1], '%15.1f') + '[kN]' + '　　Qy　：'
                    + _num2str(Qs1[1, 1], '%15.1f') + '[kN]')
    text.append('　　')
    text.append('　　')

    # NOTE: 原典408-417行 e_y/e_z はstrvcatで組み立てられるが以降未使用 (原典どおり再現)
    e_y = []; e_z = []
    for ie in range(1, 3):  # e_y
        if stress[ie - 1, 0] == 0:  # 軸力がゼロ→曲げのみで検定
            e_y.append('Inf')
            e_z.append('Inf')
        else:
            e_y.append(_num2str(abs(stress[ie - 1, 4]) / stress[ie - 1, 0] * 1000, '%15.0f'))
            e_z.append(_num2str(abs(stress[ie - 1, 5]) / stress[ie - 1, 0] * 1000, '%15.0f'))

    text.append('*****許容耐力・検定比*****')
    text.append('　　')
    text.append('*せん断耐力低減(r)：　' + _num2str(reduction, '%15.2f'))

    text.append('*許容耐力　[' + t_case + ']　壁頭')
    text.append('許容曲げ　(N+My)　：' + _num2str(M[0, 0] / 10 ** 6, '%15.1f') + '　[kNm]')
    text.append('許容曲げ　(N+Mz)　：' + _num2str(M[0, 1] / 10 ** 6, '%15.1f') + '　[kNm]')

    text.append('最大許容軸力（圧縮）　：' + _num2str(maxN[0, 0, 0] / 10 ** 3, '%15.1f') + '　[kN]')
    text.append('最大許容軸力（引張）　：' + _num2str(maxN[0, 0, 1] / 10 ** 3, '%15.1f') + '　[kN]')

    text.append('せん断力Qz　：' + _num2str(ALW_Q[0], '%15.1f') + '　[kN]' + '　せん断力Qy　：'
                + _num2str(ALW_Q[0], '%15.1f') + '　[kN]')
    text.append('低減後のせん断耐力Qz　：' + _num2str(ALW_Q[0] * reduction, '%15.1f') + '　[kN]'
                + '　低減後のせん断耐力Qy　：' + _num2str(ALW_Q[0] * reduction, '%15.1f') + '　[kN]')
    text.append('　　')
    text.append('検定比　[NMy]　：' + _num2str(ratio_output[0, 0], '%15.2f') + '　　[NMz]　：'
                + _num2str(ratio_output[0, 1], '%15.2f'))
    text.append('検定比　[Qz]　：' + _num2str(ratio_output[0, 2], '%15.2f') + '　　[Qy]　：'
                + _num2str(ratio_output[0, 3], '%15.2f'))

    # NOTE: 原典438-447行 (「中央」の許容耐力表示) は原典で全行コメントアウト済み

    text.append('　　')
    text.append('*許容耐力　[' + t_case + ']　壁脚')
    text.append('許容曲げ　(N+My)　：' + _num2str(M[1, 0] / 10 ** 6, '%15.1f') + '　[kNm]')
    text.append('許容曲げ　(N+Mz)　：' + _num2str(M[1, 1] / 10 ** 6, '%15.1f') + '　[kNm]')
    # NOTE: 原典453-454行は maxN(2,1,:) (強軸側) を表示 (maxN(2,2,:)ではない。原典どおり)
    text.append('最大許容軸力（圧縮）　：' + _num2str(maxN[1, 0, 0] / 10 ** 3, '%15.1f') + '　[kN]')
    text.append('最大許容軸力（引張）　：' + _num2str(maxN[1, 0, 1] / 10 ** 3, '%15.1f') + '　[kN]')

    text.append('せん断力Qz　：' + _num2str(ALW_Q[1], '%15.1f') + '　[kN]' + '　せん断力Qy　：'
                + _num2str(ALW_Q[1], '%15.1f') + '　[kN]')
    text.append('低減後のせん断耐力Qz　：' + _num2str(ALW_Q[1] * reduction, '%15.1f') + '　[kN]'
                + '　低減後のせん断耐力Qy　：' + _num2str(ALW_Q[1] * reduction, '%15.1f') + '　[kN]')
    text.append('　　')
    text.append('検定比　[NMy]　：' + _num2str(ratio_output[1, 0], '%15.2f') + '　　[NMz]　：'
                + _num2str(ratio_output[1, 1], '%15.2f'))
    text.append('検定比　[Qz]　：' + _num2str(ratio_output[1, 2], '%15.2f') + '　　[Qy]　：'
                + _num2str(ratio_output[1, 3], '%15.2f'))

    return text
