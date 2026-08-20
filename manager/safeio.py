# -*- coding: utf-8 -*-
"""状態ファイルを壊さずに保存するための共通処理.

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
"""
import json
import logging
import os

log = logging.getLogger(__name__)


def write_json(path, data, indent=None, mode=None):
    """JSON を書き潰さずに保存する (一時ファイル → ``os.replace``).

    indent: ``json.dump`` と同じ (None なら 1 行)。
    mode: 保存後のパーミッション。差し替える**前**の一時ファイルに
    当てるので、settings.json の中身が一瞬でも他人から読める状態に
    ならない (Windows では NTFS の ACL に従うため効かない)。

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
    try:
        with open(tmp, 'wb') as f:
            f.write(data_bytes)
            f.flush()
            try:
                os.fsync(f.fileno())    # 電源断でも中身が残るように
            except OSError:
                pass                    # 対応しない置き場所ではあきらめる
        if mode is not None:
            try:
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
