# -*- coding: utf-8 -*-
"""ログのファイル出力 (コンソールに加えて manager.log にも残す).

これまでログの出力先は起動 .bat のコンソールだけで、メンバーの PC では
その画面はふつう閉じられている。エラーの案内に「詳しくはログを」と
書いても見る場所が無く、管理者も事後に原因を追えなかった。そこで
`<install_root>/manager.log` へも残す (聞くときは「manager.log を
送ってください」で済む)。

方針:

- **API キーは絶対にログへ出さない**。いま出す箇所は無いが、将来の
  ``log.debug(..., exc_info=True)`` などで例外メッセージに紛れても
  ファイルに残らないよう、書式化の最後に ``sk-ant-`` で始まる文字列を
  すべて塗りつぶす (MaskingFormatter)。コンソール側にも同じものを掛ける
- 失敗してもマネージャーの起動を止めない (ログはあくまで補助)。
  マネージャーを 2 窓開いた場合、Windows ではローテーション (付け替え)
  が失敗することがあるが、logging は既定でエラーを握り潰して書き
  続けるのでそのままにする (uiguard は連打防止であって単一インスタンス
  の担保ではない)
- 上限 1MB × 2 世代。設定ファイル類と同じ「マネージャーの持ち物」
  (<install_root>) に置き、リポジトリのクローンには置かない
"""
import logging
import logging.handlers
import os
import re

from . import paths

log = logging.getLogger(__name__)

LOG_NAME = 'manager.log'
_FORMAT = '%(asctime)s %(levelname)s %(name)s: %(message)s'
# キー全体を消す (途中までの一致でも復元の手がかりを残さない)
_MASK = re.compile(r'sk-ant-[0-9A-Za-z_\-]+')


class MaskingFormatter(logging.Formatter):
    """書式化の最後に API キーを塗りつぶす.

    record の段階 (msg / args) でなく**出力される文字列**に掛けるので、
    引数経由・例外メッセージ経由・スタックトレース経由のどれで
    紛れ込んでも残らない。
    """

    def format(self, record):
        return _MASK.sub('sk-ant-***', super().format(record))


def log_path(config=None):
    return os.path.join(paths.install_root(config), LOG_NAME)


def setup(config=None):
    """コンソール + ファイルの二本立てにする (main の起動時に一度呼ぶ).

    戻り値: ログファイルのパス (作れなかったときは None)。
    二度呼ばれても重ねて追加しない。
    """
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.INFO)
    for handler in root.handlers:
        if not isinstance(handler.formatter, MaskingFormatter):
            handler.setFormatter(MaskingFormatter(_FORMAT))

    path = log_path(config)
    for handler in root.handlers:
        if getattr(handler, 'baseFilename', None) == os.path.abspath(path):
            return path                      # 既に付いている
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # delay=True: 最初の書き込みまでファイルを開かない
        # (作れない環境でも setup 自体は成功させる)
        file_handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=1_000_000, backupCount=2,
            encoding='utf-8', delay=True)
    except OSError:
        log.warning('ログファイルを用意できませんでした: %s', path,
                    exc_info=True)
        return None
    file_handler.setFormatter(MaskingFormatter(_FORMAT))
    file_handler.setLevel(logging.INFO)
    root.addHandler(file_handler)
    log.info('ログをファイルにも残します: %s', path)
    return path
