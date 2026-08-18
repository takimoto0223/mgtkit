# -*- coding: utf-8 -*-
"""ボタン連打で同じ処理が二重に走るのを防ぐ札 (画面部品に依存しない).

人は反応がないと何回もクリックする。ボタンの無効化やカードの描き直しは
「押した後」に効くため、押した直後に届いた 2 回目のクリックまでは
止められない (実際、却下ボタンの連打で同じ差し戻しが 2〜3 件 GitHub に
登録されていた)。

そこで送信を始める前にこの札を立て、終わったら下ろす。札が立っている
間の操作は何もせずに捨てるので、表示がどう出ていても実際の送信は
1 回に保たれる。

使い方 (押した瞬間に呼び、実処理は裏で):

    if not guard.begin(pr_number):
        return                      # 連打の 2 回目以降
    ...表示を先に切り替える...

    def work():
        try:
            ...送信...
        finally:
            guard.end(pr_number)
    run_bg(work)
"""
import threading


class ActionGuard:
    """処理中の対象 (提出番号など) を控えておく札.

    begin/end は画面のループと裏スレッドの両方から呼ばれるため、
    取りこぼしがないようロックで守る。
    """

    def __init__(self):
        self._busy = set()
        self._lock = threading.Lock()

    def begin(self, key):
        """開始できたら True。すでに処理中なら False (呼び出し側は何もしない)."""
        with self._lock:
            if key in self._busy:
                return False
            self._busy.add(key)
            return True

    def end(self, key):
        """終了 (成功・失敗どちらでも必ず呼ぶ。finally 推奨)."""
        with self._lock:
            self._busy.discard(key)

    def busy(self, key):
        """その対象が処理中か (表示の判断用)."""
        with self._lock:
            return key in self._busy

    def clear(self):
        """全部下ろす (画面の作り直しなどの仕切り直し用)."""
        with self._lock:
            self._busy.clear()
