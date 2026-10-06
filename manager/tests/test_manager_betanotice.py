"""manager/betanotice.py (β版発行のお知らせ) のテスト。"""
import json

import pytest

from manager import betanotice

PR_BODY = ('# CSV 出力に対応\n\n'
           '## 更新内容\n\n- 部材リストを CSV で保存できる\n\n'
           '## ご利用にあたっての制限事項\n\n- なし\n\n'
           '## 影響範囲\n\n- 出力まわり\n')


class TestRecipients:
    def test_excludes_author_and_bots(self):
        assert betanotice.recipients(
            {'sato', 'yamada', 'suzuki', 'github-actions[bot]'},
            'yamada') == ['sato', 'suzuki']

    def test_none_members(self):
        assert betanotice.recipients(None, 'yamada') == []


class TestShouldNotify:
    def _rev(self, name, state):
        return {'author': {'login': name}, 'state': state}

    def test_notifies_before_approvals(self):
        revs = [self._rev('sato', 'APPROVED')]
        assert betanotice.should_notify(revs, {'sato', 'suzuki'})

    def test_skips_when_approvals_complete(self):
        # リリース直前の取り込み直しで発行されたβ版には送らない
        revs = [self._rev('sato', 'APPROVED'),
                self._rev('suzuki', 'APPROVED')]
        assert not betanotice.should_notify(revs, {'sato', 'suzuki'})

    def test_outsider_approval_not_counted(self):
        revs = [self._rev('sato', 'APPROVED'),
                self._rev('stranger', 'APPROVED')]
        assert betanotice.should_notify(revs, {'sato', 'suzuki'})


class TestNoticeBody:
    def test_mentions_message_and_notes(self):
        body = betanotice.notice_body('v1.6-beta.2', 'CSV 出力に対応',
                                      'yamada', PR_BODY, ['sato', 'suzuki'])
        lines = body.splitlines()
        assert lines[0] == '@sato @suzuki'
        assert 'β版 **v1.6-beta.2** が発行されました。確認してください。' \
            in body
        assert '- 提出者: yamada' in body
        # リリースノートは正式版と同じ組み立て (利用者向けの節だけ)
        assert '## 更新内容' in body
        assert '部材リストを CSV で保存できる' in body
        assert '影響範囲' not in body
        assert '制限事項' not in body  # 「- なし」だけなら省く

    def test_no_mentions_line_when_empty(self):
        body = betanotice.notice_body('v1.6-beta.2', 't', 'yamada',
                                      PR_BODY, [])
        assert not body.startswith('@')
        assert body.startswith('β版 **v1.6-beta.2**')

    def test_fallback_when_no_notes(self):
        body = betanotice.notice_body('v1.6-beta.2', 't', 'yamada',
                                      '手書きの本文', ['sato'])
        assert betanotice.NO_NOTES in body


class TestNotify:
    @pytest.fixture()
    def gh(self, monkeypatch):
        calls = []
        posted = []
        reviews_ = []

        def run_gh(args, timeout=60):
            calls.append(args)
            if args[:2] == ['pr', 'view']:
                return json.dumps({
                    'title': 'CSV 出力に対応', 'body': PR_BODY,
                    'author': {'login': 'yamada'}, 'reviews': reviews_})
            if args[:2] == ['pr', 'comment']:
                path = args[args.index('--body-file') + 1]
                with open(path, encoding='utf-8') as f:
                    posted.append(f.read())
                return ''
            raise AssertionError(args)

        monkeypatch.setattr(betanotice.ghcli, 'run_gh', run_gh)
        monkeypatch.setattr(betanotice.reviews, 'collaborators',
                            lambda config=None: {'yamada', 'sato',
                                                 'suzuki'})
        return calls, posted, reviews_

    def test_posts_comment(self, gh):
        calls, posted, _ = gh
        body = betanotice.notify(12, 'v1.6-beta.2', {'repo': 'o/r'})
        assert posted == [body]
        assert posted[0].startswith('@sato @suzuki\n')
        comment = [c for c in calls if c[:2] == ['pr', 'comment']][0]
        assert comment[2] == '12' and comment[4] == 'o/r'

    def test_skips_when_approved(self, gh):
        calls, posted, reviews_ = gh
        reviews_ += [{'author': {'login': 'sato'}, 'state': 'APPROVED'},
                     {'author': {'login': 'suzuki'}, 'state': 'APPROVED'}]
        assert betanotice.notify(12, 'v1.6-beta.2', {}) is None
        assert posted == []

    def test_dry_run_does_not_post(self, gh, capsys):
        _, posted, _ = gh
        body = betanotice.notify(12, 'v1.6-beta.2', {}, dry_run=True)
        assert posted == []
        assert body in capsys.readouterr().out
