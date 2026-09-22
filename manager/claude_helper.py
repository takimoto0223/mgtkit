# -*- coding: utf-8 -*-
"""Claude API によるコミットメッセージ・PR 本文の自動生成.

- API キーは「本人のキー」を使う: マネージャーの初回セットアップで登録した
  settings.json を優先し、無ければ環境変数 ANTHROPIC_API_KEY
  (管理者キーの共用はしない)
- キー未設定・SDK 未導入・API エラー時は None を返し、呼び出し側が
  定型文へフォールバックする (提出処理は Claude なしでも完結する)
- ただし提出の「Claude で自動作成する」ルートだけは strict=True で呼ぶ。
  黙って定型文に落ちると、更新内容が空のまま正式版まで進んでしまうため、
  失敗の理由を持った ClaudeError を送出して提出そのものを止める
  (管理者の指示 2026-08)
"""
import logging

from . import settings
from .paths import load_config

log = logging.getLogger(__name__)

_MAX_DIFF_CHARS = 30000


class ClaudeError(Exception):
    """Claude API 呼び出しの失敗。str() はそのまま画面に出す日本語."""

    def __init__(self, message, detail=''):
        super().__init__(message)
        self.detail = detail


# HTTP の番号ごとの「利用者が次に何をすればよいか」。番号は画面にも出す
# (提出者が管理者へ伝えるときの手がかりになる)
_STATUS_HINTS = {
    401: 'Claude の API キーが受け付けられませんでした。'
         '設定タブでキーを登録し直してください。',
    403: 'Claude の呼び出しが拒否されました。API キーの残高・権限の'
         '不足が考えられます。Claude Console (API キーの発行元) で'
         '確認してください。',
    404: 'この API キーでは指定のモデル (%s) を利用できません。'
         '管理者に連絡してください。',
    429: 'Claude が混み合っています。少し待ってから提出し直してください。',
}


def _status_error(status_code):
    """APIStatusError を利用者向けの ClaudeError に翻訳する."""
    hint = _STATUS_HINTS.get(status_code)
    if hint and status_code == 404:
        hint = hint % _model()
    if hint is None:
        if status_code and status_code >= 500:
            hint = ('Claude 側で一時的な不具合が起きています。'
                    '少し待ってから提出し直してください。')
        else:
            hint = ('Claude の呼び出しが拒否されました。'
                    '管理者に連絡してください。')
    return ClaudeError(hint, 'HTTP %s' % status_code)


def _model():
    return ((load_config().get('manager') or {}).get('claude_model')
            or 'claude-opus-5')


def _client(strict=False):
    key = settings.api_key()
    if not key:
        if strict:
            raise ClaudeError('Claude の API キーが登録されていません。'
                              '設定タブで登録してください。')
        return None
    try:
        import anthropic
    except ImportError:
        log.warning('anthropic SDK が未導入のため自動生成をスキップします')
        if strict:
            raise ClaudeError(
                '自動作成に必要な部品 (anthropic) がこの PC に入っていません。'
                'マネージャーを一度閉じて起動し直すと導入されます。',
                'ImportError: anthropic')
        return None
    return anthropic.Anthropic(api_key=key)


def _record_usage(response):
    """API 応答の利用量をローカルへ積算する (失敗しても生成は続行)."""
    try:
        from . import usage
        usage.record(response.usage)
    except Exception:
        log.debug('利用量の記録に失敗しました', exc_info=True)


def _generate(prompt, max_tokens=1500, strict=False):
    """本文を 1 つ作る。strict=True なら失敗を ClaudeError で知らせる."""
    client = _client(strict=strict)
    if client is None:
        return None
    try:
        import anthropic
        response = client.messages.create(
            model=_model(),
            max_tokens=max_tokens,
            messages=[{'role': 'user', 'content': prompt}],
        )
        _record_usage(response)
        if response.stop_reason == 'refusal':
            log.warning('Claude が生成を辞退しました')
            return _failed(strict, 'Claude が文章の作成を辞退しました。'
                                   '手入力に切り替えて提出してください。',
                           'stop_reason=refusal')
        text = next((b.text for b in response.content if b.type == 'text'),
                    None)
        if not text or not text.strip():
            return _failed(strict, 'Claude から文章が返りませんでした。'
                                   'もう一度試すか、手入力に切り替えて'
                                   'ください。',
                           'stop_reason=%s' % response.stop_reason)
        return text.strip()
    except ClaudeError:
        raise    # 上の _failed が送出したもの。下の Exception に化けさせない
    except anthropic.APIConnectionError:
        log.warning('Claude API に接続できませんでした')
        return _failed(strict, 'Claude に接続できませんでした。'
                               'ネットワーク接続を確認してください。',
                       'APIConnectionError')
    except anthropic.APIStatusError as e:
        log.warning('Claude API エラー: %s', e.status_code)
        if strict:
            raise _status_error(e.status_code)
        return None
    except Exception as e:
        log.exception('Claude API 呼び出しで予期しないエラー')
        return _failed(strict, 'Claude の呼び出しで予期しないエラーが'
                               '起きました。', '%s: %s'
                       % (type(e).__name__, e))


def _failed(strict, message, detail=''):
    """strict なら ClaudeError、そうでなければ None (従来の握り潰し)."""
    if strict:
        raise ClaudeError(message, detail)
    return None


def generate_commit_message(diff_summary, diff_text, strict=False):
    """diff からコミットメッセージ (1行要約 + 箇条書き) を生成する.

    提出ルートでは使わない (タイトルは generate_pr_body の 1 行目で
    まとめて作り、API 呼び出しを提出 1 回につき 1 回にする)。
    """
    prompt = (
        'あなたは構造設計ツール mgtkit のリポジトリ管理を手伝っています。\n'
        '以下の変更内容から、日本語のコミットメッセージを作成してください。\n'
        '形式: 1行目に50字以内の要約、空行、変更点の箇条書き(最大5項目)。\n'
        'コミットメッセージ本文のみを出力してください。\n\n'
        '# 変更ファイル一覧\n%s\n\n# 変更差分(抜粋)\n%s'
        % (diff_summary, diff_text[:_MAX_DIFF_CHARS]))
    return _generate(prompt, max_tokens=800, strict=strict)


_FIX_SCHEMA = {
    'type': 'object',
    'properties': {
        'cause': {'type': 'string',
                  'description': 'なぜ検証が失敗していたか (日本語)'},
        'summary': {'type': 'string',
                    'description': '何を変えたか (日本語、簡潔に)'},
        'files': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'path': {'type': 'string'},
                    'content': {'type': 'string',
                                'description': '修正後のファイル全文'},
                },
                'required': ['path', 'content'],
                'additionalProperties': False,
            },
        },
    },
    'required': ['cause', 'summary', 'files'],
    'additionalProperties': False,
}

_FIX_GUARDS = (
    '守るべきルール (違反した修正は破棄されます):\n'
    '- tests/ 配下のファイルは変更・削除してはならない\n'
    '- bare except による例外の握りつぶしをしない\n'
    '- テストの skip 追加やアサーションの緩和をしない\n'
    '- 仕様の変更をしない (テストが期待する動作に実装を合わせる)\n'
    '- 変更は失敗の修正に必要な最小限にとどめる\n')


def generate_fix(failure_log, files):
    """CI 失敗ログと関連ファイルから修正案を生成する.

    files: {path: content}。戻り値: dict(cause, summary, files=[{path,content}])
    または None (キー未設定・失敗時)。
    """
    import json as _json
    client = _client()
    if client is None:
        return None
    file_parts = []
    total = 0
    for path, content in files.items():
        total += len(content)
        if total > 120000:
            file_parts.append('# %s\n(サイズ上限のため省略)' % path)
            continue
        file_parts.append('# %s\n```\n%s\n```' % (path, content))
    prompt = (
        'あなたは構造設計ツール mgtkit の CI 検証失敗を修正します。\n'
        '以下の失敗ログと関連ファイルを読み、原因を分析して修正後の\n'
        'ファイル全文を返してください。\n\n%s\n'
        '# 失敗ログ (末尾抜粋)\n```\n%s\n```\n\n'
        '# 関連ファイル\n%s'
        % (_FIX_GUARDS, failure_log[-20000:], '\n\n'.join(file_parts)))
    try:
        import anthropic
        with client.messages.stream(
            model=_model(),
            max_tokens=48000,
            output_config={'format': {'type': 'json_schema',
                                      'schema': _FIX_SCHEMA}},
            messages=[{'role': 'user', 'content': prompt}],
        ) as stream:
            response = stream.get_final_message()
        _record_usage(response)
        if response.stop_reason == 'refusal':
            log.warning('Claude が修正生成を辞退しました')
            return None
        text = next((b.text for b in response.content if b.type == 'text'),
                    None)
        return _json.loads(text) if text else None
    except anthropic.APIConnectionError:
        log.warning('Claude API に接続できませんでした')
        return None
    except anthropic.APIStatusError as e:
        log.warning('Claude API エラー: %s', e.status_code)
        return None
    except Exception:
        log.exception('修正生成で予期しないエラー')
        return None


def generate_failure_summary(failure_log):
    """3回失敗後の「Git を知らない人向け」3行要約."""
    text = _generate(
        'CI の検証が自動修正でも直りませんでした。以下の失敗ログから、\n'
        'Git や CI を知らない構造設計者向けに「何が起きたか・どうすれば\n'
        'よいか」を日本語3行以内で平易にまとめてください。本文のみ出力。\n\n'
        '```\n%s\n```' % failure_log[-15000:], max_tokens=500)
    return text or ('自動修正では検証を通過できませんでした。'
                    '提出内容に問題がある可能性があります。管理者に相談してください。')


def generate_conflict_explanation(conflict_files):
    """衝突箇所を「機能レベルの説明」に翻訳する.

    conflict_files: {path: 衝突マーカー付きの内容}
    """
    parts = []
    total = 0
    for path, text in conflict_files.items():
        total += len(text)
        if total > 60000:
            parts.append('# %s\n(省略)' % path)
            continue
        parts.append('# %s\n```\n%s\n```' % (path, text))
    return _generate(
        'あなたは構造設計ツール mgtkit のリポジトリ管理を手伝っています。\n'
        '提出された変更と最新版の間で、同じ箇所への変更の衝突が起きました。\n'
        '以下の衝突マーカー付きファイル (<<<<<<< が提出者の変更、>>>>>>> が'
        '最新版の変更) を読み、Git を知らない構造設計者向けに「どのファイルの'
        'どの機能同士がぶつかっているか」を日本語で平易に説明してください。\n'
        '例:「main.py で、あなたの機能a(CSV出力)と、最新版の機能b(ログ強化)が'
        '同じ関数を変更しています」。説明のみ出力。\n\n%s'
        % '\n\n'.join(parts), max_tokens=1000) or \
        '提出された変更と最新版が同じ箇所を変更しています: ' + \
        '、'.join(conflict_files)


def generate_merge(conflict_files, policy_instruction):
    """選択された方針に従い、衝突ファイルの統合結果を生成する.

    戻り値: dict(summary, files=[{path, content}]) または None。
    """
    import json as _json
    client = _client()
    if client is None:
        return None
    parts = []
    for path, text in conflict_files.items():
        parts.append('# %s\n```\n%s\n```' % (path, text))
    schema = {
        'type': 'object',
        'properties': {
            'summary': {'type': 'string',
                        'description': '統合内容の説明 (日本語)'},
            'files': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'path': {'type': 'string'},
                        'content': {'type': 'string',
                                    'description': '統合後のファイル全文 '
                                                   '(衝突マーカーなし)'},
                    },
                    'required': ['path', 'content'],
                    'additionalProperties': False,
                },
            },
        },
        'required': ['summary', 'files'],
        'additionalProperties': False,
    }
    prompt = (
        'あなたは構造設計ツール mgtkit のコード統合を行います。\n'
        '以下の衝突マーカー付きファイル (<<<<<<< HEAD 側が提出者の変更、'
        '>>>>>>> 側が最新版の変更) を、次の方針で統合してください。\n\n'
        '方針: %s\n\n'
        'ルール: 衝突マーカーを残さない / 方針にない変更を加えない / '
        '両方の機能を残す場合は矛盾なく共存させる。\n\n%s'
        % (policy_instruction, '\n\n'.join(parts)))
    try:
        import anthropic
        with client.messages.stream(
            model=_model(),
            max_tokens=48000,
            output_config={'format': {'type': 'json_schema',
                                      'schema': schema}},
            messages=[{'role': 'user', 'content': prompt}],
        ) as stream:
            response = stream.get_final_message()
        _record_usage(response)
        if response.stop_reason == 'refusal':
            return None
        text = next((b.text for b in response.content if b.type == 'text'),
                    None)
        return _json.loads(text) if text else None
    except anthropic.APIConnectionError:
        log.warning('Claude API に接続できませんでした')
        return None
    except anthropic.APIStatusError as e:
        log.warning('Claude API エラー: %s', e.status_code)
        return None
    except Exception:
        log.exception('統合生成で予期しないエラー')
        return None


def generate_pr_body(diff_summary, diff_text, base_version, notes='',
                     strict=False):
    """diff から PR 本文 (更新点のまとめ) を生成する.

    「## 更新内容」「## ご利用にあたっての制限事項」の 2 節は、正式リリース時に
    reviews.release_notes_from_pr がそのままリリースノートへ転載する様式
    (リリース時は API を使わない)。節名を変えるときは両方を揃えること
    (submit.fallback_pr_body も同じ様式)。
    """
    prompt = (
        'あなたは構造設計ツール mgtkit のリポジトリ管理を手伝っています。\n'
        'メンバーが %s を基点に改造したファイル一式を提出しました。\n'
        '以下の変更内容から、レビュー担当者(Git に詳しくない構造設計者)向けの\n'
        'PR 本文を日本語で作成してください。\n'
        '1 行目は「# <タイトル>」。タイトルは 50 字以内の体言止めで、\n'
        'メンバーの一覧画面と正式版リリースノートの見出しにそのまま出ます。\n'
        '利用者 (構造設計者) が読んで何が変わるか分かる言葉にし、\n'
        '箇条書きの記号・ファイル名・接頭辞は付けないでください。\n'
        '例: # 荷重分布図の PDF 書き出しに対応\n'
        '続けて次の 4 節を必ずこの順で:「## 更新内容」'
        '「## ご利用にあたっての制限事項」「## 影響範囲」'
        '「## 変更ファイルの説明」。\n'
        '節の見出しや箇条書きをタイトルと同じ文言にしないでください。\n'
        '「## 更新内容」と「## ご利用にあたっての制限事項」は、そのまま'
        '正式版のリリースノートに転載されます。利用者 (構造設計者) 向けの'
        '平易な言葉で書き、ファイル名・関数名などの実装詳細は'
        '「## 影響範囲」以降に書いてください。\n'
        '「## 更新内容」は機能単位の箇条書き。\n'
        '「## ご利用にあたっての制限事項」には、対応していない条件・'
        'エラーで停止するケース・未対応の機能や出力など、利用時の注意を'
        '箇条書きで書いてください。該当が無ければ「- なし」とだけ'
        '書いてください。\n'
        '「## 影響範囲」は変更されたファイルと役割、'
        '「## 変更ファイルの説明」は下記の形式。\n'
        '「## 変更ファイルの説明」は、変更ファイル一覧の各ファイルについて\n'
        '「- パス — 説明」を 1 ファイル 1 行、必ずこの形式で書いてください。\n'
        'パスは変更ファイル一覧の表記をそのまま使い、説明は「何のために'
        'どんな機能を加えたか(変えたか・消したか)」を 60 字以内の体言止めで。'
        '主要な関数名があれば含めてください。\n'
        '例: - s_check.py — 2丁合わせ溝形鋼 TC2_analysis() と'
        'アングル TL2_analysis() の断面算定の追加\n'
        '危険な操作(ファイル削除・外部通信・サブプロセス起動・eval 等)が'
        '含まれる場合は「## 注意」として明示してください。\n'
        'Markdown 本文のみを出力してください。\n\n'
        '%s\n\n# 変更ファイル一覧\n%s\n\n# 変更差分(抜粋)\n%s'
        % (base_version, notes, diff_summary, diff_text[:_MAX_DIFF_CHARS]))
    return _generate(prompt, max_tokens=2000, strict=strict)
