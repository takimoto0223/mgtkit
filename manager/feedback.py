# -*- coding: utf-8 -*-
"""β版フィードバック → 対応する PR へのコメント投稿.

β版リリースのノートには beta-release ワークフローが「提出 #N ...」と
書き込むため、そこから対応 PR を特定してコメントする。
コメントは承認判断の材料になる (承認タブ・PR 上で見える)。
"""
import logging
import re

from . import ghcli, paths, settings

log = logging.getLogger(__name__)


class FeedbackError(Exception):
    """フィードバック送信の中断。str() はユーザー向けの平易な日本語メッセージ."""


def pr_number_from_release(release):
    """リリースノートから対応する提出 (PR) 番号を取り出す。無ければ None."""
    m = re.search(r'#(\d+)', release.get('notes') or '')
    return int(m.group(1)) if m else None


def post_feedback(release, text, config=None):
    """β版の感想・不具合報告を対応する PR にコメントとして投稿する.

    戻り値: 投稿先の PR 番号。
    """
    text = (text or '').strip()
    if not text:
        raise FeedbackError('フィードバックの内容を入力してください。')
    pr_number = pr_number_from_release(release)
    if pr_number is None:
        raise FeedbackError('このβ版に対応する提出が見つからないため、'
                            'フィードバックを送信できません。')
    name = settings.user_name(config) or '匿名'
    body = 'β版 %s のフィードバック (%s):\n\n%s' % (
        release.get('tag', '?'), name, text)
    ghcli.run_gh(['pr', 'comment', str(pr_number), '--repo',
                  paths.repo_slug(config), '--body', body])
    return pr_number
