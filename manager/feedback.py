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


def _comment_body(tag, name, text):
    """コメント本文 (reviews.parse_feedback が拾う形式)."""
    return 'β版 %s のフィードバック (%s):\n\n%s' % (tag, name, text)


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
    body = _comment_body(release.get('tag', '?'), name, text)
    ghcli.run_gh(['pr', 'comment', str(pr_number), '--repo',
                  paths.repo_slug(config), '--body', body])
    return pr_number


def update_feedback(comment_id, tag, text, config=None):
    """自分のフィードバックを書き換える.

    GitHub 側の権限により、他人のコメントは書き換えられない
    (UI でも本人のフィードバックにのみ編集ボタンを出す)。
    """
    text = (text or '').strip()
    if not text:
        raise FeedbackError('フィードバックの内容を入力してください。')
    name = settings.user_name(config) or '匿名'
    ghcli.run_gh(['api', '-X', 'PATCH',
                  'repos/%s/issues/comments/%s'
                  % (paths.repo_slug(config), comment_id),
                  '-f', 'body=%s' % _comment_body(tag, name, text)])


def delete_feedback(comment_id, config=None):
    """自分のフィードバックを削除する (権限は update_feedback と同様)."""
    ghcli.run_gh(['api', '-X', 'DELETE',
                  'repos/%s/issues/comments/%s'
                  % (paths.repo_slug(config), comment_id)])
