# -*- coding: utf-8 -*-
"""β版発行のお知らせ (提出への @メンション付きコメント).

beta-release ジョブ (.github/workflows/test.yml) がβ版を登録した直後に
呼ぶ。提出 (PR) に参加者全員を @メンションしたコメントを書き込み、
GitHub の通知メールで「β版が発行されました。確認してください。」と
リリースノート (更新内容・制限事項) を届ける。

- 宛先は push 権限を持つメンバー (reviews.collaborators) から提出者を
  除いた全員。提出者は自分の提出への書き込みとして別途通知が届く
- リリースノートは正式版と同じ組み立て (reviews.release_notes_from_pr)
- 必要数の承認がそろった提出には送らない。リリース直前の取り込み直し
  で検証が走り直し、β版がもう 1 つ発行されることがあるため
  (確認はもう済んでいる)

使い方 (リポジトリルートで): python3 -m manager.betanotice --pr 12 \
    --version v1.6-beta.2 [--dry-run]
"""
import argparse
import json
import logging
import os
import sys
import tempfile

from . import ghcli, paths, reviews

log = logging.getLogger(__name__)

NO_NOTES = '(この提出には更新内容の記載がありません)'


def recipients(members, author):
    """@メンションする相手 (提出者とボットを除く、名前順)."""
    return sorted(m for m in (members or ())
                  if m and m != author and not m.endswith('[bot]'))


def should_notify(pr_reviews, members, config=None):
    """お知らせを送るか。必要数の承認がそろっていれば送らない."""
    summary = reviews.approval_summary(pr_reviews, members)
    return len(summary['approved']) < reviews.required_approvals(config)


def notice_body(version, title, author, pr_body, mentions):
    """コメント本文 (Markdown)."""
    lines = []
    if mentions:
        lines += [' '.join('@%s' % m for m in mentions), '']
    lines += [
        'β版 **%s** が発行されました。確認してください。' % version,
        '',
        '- 提出: %s' % ((title or '').strip() or '(タイトルなし)'),
        '- 提出者: %s' % (author or '?'),
        '',
        'アプリマネージャーの「β版の確認と承認」タブから試せます。'
        '気づいたことはβ版のフィードバックから送ってください。',
        '',
        '---',
        '',
        reviews.release_notes_from_pr(pr_body, version, title) or NO_NOTES,
    ]
    return '\n'.join(lines).rstrip() + '\n'


def _pr_info(pr_number, config):
    out = ghcli.run_gh([
        'pr', 'view', str(pr_number), '--repo', paths.repo_slug(config),
        '--json', 'title,body,author,reviews'])
    return json.loads(out)


def _post_comment(pr_number, body, config):
    # 本文が長くてもコマンドライン長の上限に当たらないようファイル経由
    fd, path = tempfile.mkstemp(suffix='.md')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(body)
        ghcli.run_gh(['pr', 'comment', str(pr_number), '--repo',
                      paths.repo_slug(config), '--body-file', path])
    finally:
        os.remove(path)


def notify(pr_number, version, config=None, dry_run=False):
    """お知らせを書き込む。戻り値: 書き込んだ本文 (送らなかったら None)."""
    info = _pr_info(pr_number, config)
    members = reviews.collaborators(config)
    if members is None:
        log.warning('メンバー一覧を取得できないため @メンションなしで書き込みます')
    if not should_notify(info.get('reviews'), members, config):
        log.info('#%s は必要数の承認がそろっているため送りません', pr_number)
        return None
    author = (info.get('author') or {}).get('login')
    body = notice_body(version, info.get('title'), author,
                       info.get('body'), recipients(members, author))
    if dry_run:
        print(body)
    else:
        _post_comment(pr_number, body, config)
    return body


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    ap = argparse.ArgumentParser(description='β版発行のお知らせ')
    ap.add_argument('--pr', type=int, required=True)
    ap.add_argument('--version', required=True)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)
    body = notify(args.pr, args.version, paths.load_config(),
                  dry_run=args.dry_run)
    print('送信しました' if body and not args.dry_run else '送信なし')
    return 0


if __name__ == '__main__':
    sys.exit(main())
