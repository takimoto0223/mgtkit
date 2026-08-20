# -*- coding: utf-8 -*-
"""状態ファイルの保存と、フォルダの後片付けの共通処理.

保存 (write_json) は「途中で落ちても前の中身を失わない」ため、
後片付け (rmtree) は「読み取り専用のファイルがあっても消せる」ためにある。

--- 保存 ---

素の ``open(path, 'w')`` は、開いた時点で中身を空にしてから書き直す。
書いている途中で落ちる (強制終了・電源断・書き込み先が一杯) と、
残るのは 0 バイトのファイルになる。

マネージャーの状態ファイル (settings.json / usage.json /
local_state.json / reviews_cache.json) の読み手はどれも壊れた
ファイルを「未登録」「記録なし」と読み替えるため、**失ったことに
誰も気づかない**。とくに次の 2 つは失うと痛い:

- ``settings.json`` — 本人の Claude API キー (入れ直しになる)
- ``usage.json`` — 記録した時点の単価で確定額を保存しており、
  後から計算し直すことができない

そこで別名の一時ファイルに全部書ききってから ``os.replace`` で
差し替える。差し替えは OS が「途中の状態を見せない」やり方で行うので、
いつ落ちても読み手が見るのは**前の中身か新しい中身のどちらか**になる。

``diffcache.save`` が先に同じ流儀で書かれていたので、書き方をそちらに
そろえてある (理由は manager/docs/decisions.md)。

--- 後片付け ---

``shutil.rmtree`` は読み取り専用のファイルを消せない。git は Windows で
クローンの中身 (pack / loose object) に読み取り専用属性を付けるため、
クローンを含むフォルダは素の rmtree では必ず途中で止まる。

``rmtree`` は属性を落としてから消し直し、**消えたかどうかを True/False
で返す**。後片付けの失敗で本筋を止めないよう例外は投げないが、黙って
「消えたつもり」にもならないので、呼び出し側が次回に回すなどの判断を
できる。
"""
import json
import logging
import os
import shutil
import stat
import sys

log = logging.getLogger(__name__)

# shutil.rmtree の失敗ハンドラは Python 3.12 で onerror → onexc に
# 変わった (第 3 引数が例外情報の組か例外そのものかの違いだけで、
# ここでは使わないため同じ関数で足りる)
_RMTREE_HANDLER = 'onexc' if sys.version_info >= (3, 12) else 'onerror'


def write_json(path, data, indent=None, mode=None):
    """JSON を書き潰さずに保存する (一時ファイル → ``os.replace``).

    indent: ``json.dump`` と同じ (None なら 1 行)。
    mode: 保存後のパーミッション。一時ファイルを**作る瞬間**に当てる。
    中身を書いてから ``chmod`` すると、その間だけ他人から読める窓が
    残るため (settings.json は API キーそのもの)。Windows では NTFS の
    ACL に従うので効かない。

    保存できたら path を返す。失敗したときは中途半端な一時ファイルを
    片付けたうえで例外をそのまま投げる (「保存できなかった」ことを
    呼び出し側が扱えるようにするため)。
    """
    data_bytes = json.dumps(data, ensure_ascii=False,
                            indent=indent).encode('utf-8')
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    # 2 窓開いていても一時名が交錯しないようプロセス番号を混ぜる
    # (diffcache.save と同じ流儀)
    tmp = '%s.%d.tmp' % (path, os.getpid())

    def _create(name, flags):
        # 作る瞬間から権限を当てる (書いてから chmod では手遅れ)
        return os.open(name, flags, 0o666 if mode is None else mode)

    try:
        with open(tmp, 'wb', opener=_create) as f:
            f.write(data_bytes)
            f.flush()
            try:
                os.fsync(f.fileno())    # 電源断でも中身が残るように
            except OSError:
                pass                    # 対応しない置き場所ではあきらめる
        if mode is not None:
            try:
                # 前回の書きかけが残っていたときは作成時の権限が効かない
                # ため、念のため当て直す (umask で削られた分もここで戻る)
                os.chmod(tmp, mode)
            except OSError:
                pass                    # Windows では NTFS ACL に従う
        os.replace(tmp, path)
    except BaseException:
        # 書きかけを残さない (残すとフォルダが一時ファイルで散らかる)
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return path


def _make_writable(target):
    """本人が読み書きできるようにする (フォルダは中を見る権限も).

    いま付いている権限に足すだけで、他人向けの権限は落とさない。
    """
    try:
        mode = os.stat(target).st_mode
        mode |= stat.S_IRUSR | stat.S_IWUSR
        if stat.S_ISDIR(mode):
            mode |= stat.S_IXUSR
        os.chmod(target, mode)
    except OSError:
        pass


def _retry_writable(func, path, _exc):
    """読み取り専用で失敗した消去を、属性を落としてからやり直す.

    消せない原因はその項目自身の属性 (git が付ける読み取り専用) と、
    それを入れているフォルダ側の属性の両方がありうるので、両方を緩める。
    それでも消えなければ何もしない (残ったかどうかは rmtree の戻り値で
    分かるため、ここで騒ぐ必要はない)。
    """
    _make_writable(path)
    parent = os.path.dirname(path)
    if parent:
        _make_writable(parent)
    try:
        func(path)
    except OSError:
        pass


def rmtree(path):
    """フォルダを丸ごと消す。読み取り専用のファイルがあっても消す.

    git は Windows でクローンの中身 (pack / loose object) に読み取り
    専用属性を付けるため、クローンを含むフォルダは素の
    ``shutil.rmtree`` では必ず途中で止まる。

    戻り値: 消えたら True (もともと無かったときも True)、残っていたら
    False。後片付けで本筋を止めないため例外は投げないので、消えたか
    どうかは戻り値で判断する (残ったものは次の機会に消せばよい)。
    """
    if not path or not os.path.exists(path):
        return True
    try:
        shutil.rmtree(path, **{_RMTREE_HANDLER: _retry_writable})
    except OSError:
        log.debug('フォルダを消せませんでした: %s', path, exc_info=True)
    if os.path.exists(path):
        log.info('フォルダが残りました (次の機会に消します): %s', path)
        return False
    return True
