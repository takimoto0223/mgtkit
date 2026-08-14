# -*- coding: utf-8 -*-
"""Claude API によるコミットメッセージ・PR 本文の自動生成.

- API キーは「本人のキー」を使う: マネージャーの初回セットアップで登録した
  settings.json を優先し、無ければ環境変数 ANTHROPIC_API_KEY
  (管理者キーの共用はしない)
- キー未設定・SDK 未導入・API エラー時は None を返し、呼び出し側が
  定型文へフォールバックする (提出処理は Claude なしでも完結する)
"""
import logging

from . import settings
from .paths import load_config

log = logging.getLogger(__name__)

_MAX_DIFF_CHARS = 30000


def _model():
    return ((load_config().get('manager') or {}).get('claude_model')
            or 'claude-opus-5')


def _client():
    key = settings.api_key()
    if not key:
        return None
    try:
        import anthropic
    except ImportError:
        log.warning('anthropic SDK が未導入のため自動生成をスキップします')
        return None
    return anthropic.Anthropic(api_key=key)


def _record_usage(response):
    """API 応答の利用量をローカルへ積算する (失敗しても生成は続行)."""
    try:
        from . import usage
        usage.record(response.usage)
    except Exception:
        log.debug('利用量の記録に失敗しました', exc_info=True)


def _generate(prompt, max_tokens=1500):
    client = _client()
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
            return None
        text = next((b.text for b in response.content if b.type == 'text'),
                    None)
        return text.strip() if text else None
    except anthropic.APIConnectionError:
        log.warning('Claude API に接続できませんでした')
        return None
    except anthropic.APIStatusError as e:
        log.warning('Claude API エラー: %s', e.status_code)
        return None
    except Exception:
        log.exception('Claude API 呼び出しで予期しないエラー')
        return None


def generate_commit_message(diff_summary, diff_text):
    """diff からコミットメッセージ (1行要約 + 箇条書き) を生成する."""
    prompt = (
        'あなたは構造設計ツール mgtkit のリポジトリ管理を手伝っています。\n'
        '以下の変更内容から、日本語のコミットメッセージを作成してください。\n'
        '形式: 1行目に50字以内の要約、空行、変更点の箇条書き(最大5項目)。\n'
        'コミットメッセージ本文のみを出力してください。\n\n'
        '# 変更ファイル一覧\n%s\n\n# 変更差分(抜粋)\n%s'
        % (diff_summary, diff_text[:_MAX_DIFF_CHARS]))
    return _generate(prompt, max_tokens=800)


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


def generate_release_notes(pr_title, pr_body, version):
    """正式リリース時のリリースノートを PR 情報から生成する."""
    return _generate(
        '構造設計ツール mgtkit の正式版 %s のリリースノートを作成して'
        'ください。以下の提出内容(PR)のタイトルと本文をもとに、利用者'
        '(構造設計者) 向けに更新内容を日本語の箇条書きでまとめてください。'
        '本文のみ出力。\n\n# タイトル\n%s\n\n# 本文\n%s'
        % (version, pr_title, pr_body or '(本文なし)'), max_tokens=1000)


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


def generate_pr_body(diff_summary, diff_text, base_version, notes=''):
    """diff から PR 本文 (更新点のまとめ) を生成する."""
    prompt = (
        'あなたは構造設計ツール mgtkit のリポジトリ管理を手伝っています。\n'
        'メンバーが %s を基点に改造したファイル一式を提出しました。\n'
        '以下の変更内容から、レビュー担当者(Git に詳しくない構造設計者)向けの\n'
        'PR 本文を日本語で作成してください。\n'
        '構成: 「## 更新内容」(機能単位の箇条書き)、'
        '「## 影響範囲」(変更されたファイルと役割)、'
        '「## 変更ファイルの説明」(下記の形式)。\n'
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
    return _generate(prompt, max_tokens=2000)
