# -*- coding: utf-8 -*-
"""Claude API によるコミットメッセージ・PR 本文の自動生成.

- API キーは環境変数 ANTHROPIC_API_KEY から読む (コードに埋め込まない)
- キー未設定・SDK 未導入・API エラー時は None を返し、呼び出し側が
  定型文へフォールバックする (提出処理は Claude なしでも完結する)
"""
import logging
import os

from .paths import load_config

log = logging.getLogger(__name__)

_MAX_DIFF_CHARS = 30000


def _model():
    return ((load_config().get('manager') or {}).get('claude_model')
            or 'claude-opus-5')


def _client():
    if not os.environ.get('ANTHROPIC_API_KEY'):
        return None
    try:
        import anthropic
    except ImportError:
        log.warning('anthropic SDK が未導入のため自動生成をスキップします')
        return None
    return anthropic.Anthropic()


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


def generate_pr_body(diff_summary, diff_text, base_version, notes=''):
    """diff から PR 本文 (更新点のまとめ) を生成する."""
    prompt = (
        'あなたは構造設計ツール mgtkit のリポジトリ管理を手伝っています。\n'
        'メンバーが %s を基点に改造したファイル一式を提出しました。\n'
        '以下の変更内容から、レビュー担当者(Git に詳しくない構造設計者)向けの\n'
        'PR 本文を日本語で作成してください。\n'
        '構成: 「## 更新内容」(機能単位の箇条書き)、'
        '「## 影響範囲」(変更されたファイルと役割)。\n'
        '危険な操作(ファイル削除・外部通信・サブプロセス起動・eval 等)が'
        '含まれる場合は「## 注意」として明示してください。\n'
        'Markdown 本文のみを出力してください。\n\n'
        '%s\n\n# 変更ファイル一覧\n%s\n\n# 変更差分(抜粋)\n%s'
        % (base_version, notes, diff_summary, diff_text[:_MAX_DIFF_CHARS]))
    return _generate(prompt, max_tokens=2000)
