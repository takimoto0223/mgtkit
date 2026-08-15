"""manager/reviews.py・conflicts.py のテスト (gh はモック、git は実操作)。"""
import subprocess

import pytest

from manager import conflicts, reviews
from manager.gitcli import run_git


class TestApprovalSummary:
    def test_latest_state_per_reviewer(self):
        reviews_data = [
            {'author': {'login': 'a'}, 'state': 'CHANGES_REQUESTED',
             'body': 'だめ'},
            {'author': {'login': 'a'}, 'state': 'APPROVED', 'body': ''},
            {'author': {'login': 'b'}, 'state': 'APPROVED', 'body': ''},
            {'author': {'login': 'c'}, 'state': 'COMMENTED',
             'body': 'メモ'},
        ]
        s = reviews.approval_summary(reviews_data)
        assert s['approved'] == ['a', 'b']
        assert s['rejected'] == []

    def test_rejection_blocks(self):
        s = reviews.approval_summary([
            {'author': {'login': 'a'}, 'state': 'APPROVED', 'body': ''},
            {'author': {'login': 'b'}, 'state': 'CHANGES_REQUESTED',
             'body': '検定比の丸めが違います',
             'submittedAt': '2026-08-10T09:00:00Z'},
        ])
        assert s['approved'] == ['a']
        assert s['rejected'] == [
            {'name': 'b', 'comment': '検定比の丸めが違います',
             'at': '2026-08-10T09:00:00Z'}]

    def test_dismissed_returns_to_neutral(self):
        # 承認・却下の後に取り消し (DISMISSED) されたらどちらにも数えない
        s = reviews.approval_summary([
            {'author': {'login': 'a'}, 'state': 'APPROVED', 'body': ''},
            {'author': {'login': 'a'}, 'state': 'DISMISSED', 'body': ''},
            {'author': {'login': 'b'}, 'state': 'CHANGES_REQUESTED',
             'body': 'x'},
            {'author': {'login': 'b'}, 'state': 'DISMISSED', 'body': ''},
        ])
        assert s['approved'] == []
        assert s['rejected'] == []

    def test_empty(self):
        s = reviews.approval_summary([])
        assert s == {'approved': [], 'rejected': []}

    def test_members_filter_excludes_outsiders(self):
        # public リポジトリでは第三者もレビューできるため、
        # collaborator 以外の承認・却下はカウントしない
        s = reviews.approval_summary([
            {'author': {'login': 'member-a'}, 'state': 'APPROVED',
             'body': ''},
            {'author': {'login': 'stranger'}, 'state': 'APPROVED',
             'body': ''},
            {'author': {'login': 'troll'}, 'state': 'CHANGES_REQUESTED',
             'body': 'いたずら却下'},
        ], members={'member-a', 'member-b'})
        assert s['approved'] == ['member-a']
        assert s['rejected'] == []

    def test_members_none_counts_everyone(self):
        s = reviews.approval_summary(
            [{'author': {'login': 'anyone'}, 'state': 'APPROVED',
              'body': ''}], members=None)
        assert s['approved'] == ['anyone']


class TestCollaborators:
    def test_returns_push_members(self, monkeypatch):
        monkeypatch.setattr(
            reviews.ghcli, 'run_gh',
            lambda args, timeout=60: '["yamada", "sato"]')
        assert reviews.collaborators({'repo': 'o/r'}) == {'yamada', 'sato'}

    def test_none_on_gh_error(self, monkeypatch):
        def boom(args, timeout=60):
            raise reviews.ghcli.GhError('通信エラー')
        monkeypatch.setattr(reviews.ghcli, 'run_gh', boom)
        assert reviews.collaborators({'repo': 'o/r'}) is None

    def test_none_on_empty(self, monkeypatch):
        monkeypatch.setattr(reviews.ghcli, 'run_gh',
                            lambda args, timeout=60: '[]')
        assert reviews.collaborators({'repo': 'o/r'}) is None


class TestSessionCache:
    """起動中ほぼ変わらない情報のキャッシュ (往復削減)."""

    def test_current_user_fetched_once(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            reviews.ghcli, 'run_gh',
            lambda args, timeout=60: calls.append(args) or 'yamada\n')
        assert reviews.current_user() == 'yamada'
        assert reviews.current_user() == 'yamada'
        assert len(calls) == 1

    def test_collaborators_cached(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            reviews.ghcli, 'run_gh',
            lambda args, timeout=60: calls.append(args)
            or '["yamada", "sato"]')
        assert reviews.collaborators({'repo': 'o/r'}) == {'yamada', 'sato'}
        assert reviews.collaborators({'repo': 'o/r'}) == {'yamada', 'sato'}
        assert len(calls) == 1

    def test_failure_is_not_cached(self, monkeypatch):
        def boom(args, timeout=60):
            raise reviews.ghcli.GhError('通信エラー')
        monkeypatch.setattr(reviews.ghcli, 'run_gh', boom)
        assert reviews.collaborators({'repo': 'o/r'}) is None
        # 復旧後は取得できる (失敗した結果を覚えていない)
        monkeypatch.setattr(reviews.ghcli, 'run_gh',
                            lambda args, timeout=60: '["yamada"]')
        assert reviews.collaborators({'repo': 'o/r'}) == {'yamada'}

    def test_clear_cache(self, monkeypatch):
        monkeypatch.setattr(reviews.ghcli, 'run_gh',
                            lambda args, timeout=60: 'yamada\n')
        assert reviews.current_user() == 'yamada'
        reviews.clear_cache()
        monkeypatch.setattr(reviews.ghcli, 'run_gh',
                            lambda args, timeout=60: 'sato\n')
        assert reviews.current_user() == 'sato'


class TestApprove:
    def test_self_approval_blocked_without_refetch(self, monkeypatch):
        """一覧取得済みの提出者名を渡せば PR 情報を取り直さない."""
        calls = []
        monkeypatch.setattr(
            reviews.ghcli, 'run_gh',
            lambda args, timeout=60: calls.append(args) or 'fujitaka\n')
        with pytest.raises(reviews.ReviewError, match='自分の提出'):
            reviews.approve(33, {'repo': 'o/r'}, author='fujitaka')
        # 呼んだのは自分のログイン名の取得のみ (pr view しない)
        assert [c[:2] for c in calls] == [['api', 'user']]

    def test_approves_with_known_author(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            reviews.ghcli, 'run_gh',
            lambda args, timeout=60: calls.append(args) or 'yamada\n')
        reviews.approve(33, {'repo': 'o/r'}, author='fujitaka')
        review = next(c for c in calls if c[:2] == ['pr', 'review'])
        assert review[2] == '33' and '--approve' in review
        assert not any(c[:2] == ['pr', 'view'] for c in calls)

    def test_author_omitted_falls_back_to_fetch(self, monkeypatch):
        """author 未指定なら従来どおり PR 情報から提出者を確認する."""
        import json as _json
        calls = []

        def fake(args, timeout=60):
            calls.append(args)
            if args[:2] == ['pr', 'view']:
                return _json.dumps({'author': {'login': 'fujitaka'}})
            if args[:2] == ['api', 'user']:
                return 'fujitaka\n'
            return ''

        monkeypatch.setattr(reviews.ghcli, 'run_gh', fake)
        with pytest.raises(reviews.ReviewError, match='自分の提出'):
            reviews.approve(33, {'repo': 'o/r'})
        assert any(c[:2] == ['pr', 'view'] for c in calls)


class TestSubmissionFilter:
    """承認タブはマネージャー経由の提出 (feature/) のみを対象とする."""

    def test_is_submission(self):
        assert reviews._is_submission({'headRefName': 'feature/yamada-1'})
        assert not reviews._is_submission({'headRefName': 'claude/x'})
        assert not reviews._is_submission({'headRefName': 'main'})
        assert not reviews._is_submission({})

    def test_list_pending_excludes_manager_updates(self, monkeypatch):
        import json as _json

        def fake_run_gh(args, timeout=60):
            if args[:2] == ['pr', 'list']:
                return _json.dumps([
                    {'number': 33, 'title': '組立断面', 'url': 'u',
                     'author': {'login': 'fujitaka'},
                     'headRefName': 'feature/fujitaka-20260808-1',
                     'reviews': [], 'statusCheckRollup': [],
                     'mergeable': 'MERGEABLE', 'comments': []},
                    {'number': 35, 'title': 'マネージャー更新', 'url': 'u',
                     'author': {'login': 'takimoto0223'},
                     'headRefName': 'claude/app-manager-phase-1',
                     'reviews': [], 'statusCheckRollup': [],
                     'mergeable': 'MERGEABLE', 'comments': []},
                ])
            return '[]'

        monkeypatch.setattr(reviews.ghcli, 'run_gh', fake_run_gh)
        pending = reviews.list_pending({'repo': 'o/r'})
        assert [p['number'] for p in pending] == [33]

    def test_list_pending_fetches_in_one_call(self, monkeypatch):
        """PR ごとの pr view を繰り返さない (往復回数 = 体感速度).

        gh 呼び出しは pr list 1 回 + collaborators 1 回のみで、
        レビュー・検証・コメントも pr list の結果から組み立てる。
        """
        import json as _json
        calls = []

        def fake_run_gh(args, timeout=60):
            calls.append(args)
            if args[:2] == ['pr', 'list']:
                return _json.dumps([
                    {'number': 33, 'title': '組立断面', 'url': 'u',
                     'author': {'login': 'fujitaka'},
                     'headRefName': 'feature/fujitaka-20260808-1',
                     'reviews': [
                         {'author': {'login': 'yamada'},
                          'state': 'APPROVED', 'body': ''}],
                     'statusCheckRollup': [],
                     'mergeable': 'CONFLICTING',
                     'comments': [
                         {'body': 'β版 v1.1-beta.1 のフィードバック '
                                  '(山田太郎):\n\n良い感じ',
                          'createdAt': '2026-08-05T12:34:56Z',
                          'author': {'login': 'yamada'},
                          'url': 'https://github.com/o/r/pull/33'
                                 '#issuecomment-987'}],
                     },
                    {'number': 40, 'title': '別提出', 'url': 'u',
                     'author': {'login': 'sato'},
                     'headRefName': 'feature/sato-20260810-1',
                     'reviews': [], 'statusCheckRollup': [],
                     'mergeable': 'MERGEABLE', 'comments': []},
                ])
            if args[0] == 'api' and 'collaborators' in args[1]:
                return '["yamada", "sato", "fujitaka"]'
            raise AssertionError('unexpected gh call: %r' % args)

        monkeypatch.setattr(reviews.ghcli, 'run_gh', fake_run_gh)
        pending = reviews.list_pending({'repo': 'o/r'})
        # PR が複数あっても gh は 2 回のみ (pr view なし)
        assert [c[:2] for c in calls] == [['pr', 'list'],
                                          ['api', 'repos/o/r/'
                                           'collaborators?per_page=100']]
        assert not any(c[:2] == ['pr', 'view'] for c in calls)
        # pr list の結果から承認・フィードバック・競合が組み立つ
        assert pending[0]['approved'] == ['yamada']
        assert pending[0]['conflicting'] is True
        assert pending[0]['feedback'][0]['name'] == '山田太郎'
        assert pending[1]['approved'] == []


_GRAPHQL_SNAPSHOT = {
    'data': {
        'viewer': {'login': 'yamada'},
        'repository': {
            'pullRequests': {'nodes': [
                {'number': 33, 'title': '組立断面', 'url': 'u',
                 'headRefName': 'feature/fujitaka-20260808-1',
                 'headRefOid': 'headsha33',
                 'mergeable': 'MERGEABLE',
                 'author': {'login': 'fujitaka'},
                 'reviews': {'nodes': [
                     {'state': 'APPROVED', 'body': '',
                      'submittedAt': '2026-08-10T09:00:00Z',
                      'author': {'login': 'yamada'}}]},
                 'comments': {'nodes': [
                     {'body': 'β版 v1.1-beta.1 のフィードバック '
                              '(山田太郎):\n\n良い感じ',
                      'createdAt': '2026-08-05T12:34:56Z',
                      'url': 'https://github.com/o/r/pull/33'
                             '#issuecomment-987',
                      'author': {'login': 'yamada'}}]},
                 'commits': {'nodes': [{'commit': {'statusCheckRollup': {
                     'contexts': {'nodes': [
                         {'__typename': 'CheckRun', 'status': 'COMPLETED',
                          'conclusion': 'SUCCESS'}]}}}}]}},
                {'number': 35, 'title': 'マネージャー更新', 'url': 'u',
                 'headRefName': 'claude/app-manager-x',
                 'mergeable': 'MERGEABLE', 'author': {'login': 't'},
                 'reviews': {'nodes': []}, 'comments': {'nodes': []},
                 'commits': {'nodes': []}},
            ]},
            'merged': {'nodes': [
                {'number': 29, 'title': '合成梁の検討を追加',
                 'headRefName': 'feature/yamada-20260801-1',
                 'headRefOid': 'headsha29',
                 'createdAt': '2026-08-01T09:00:00Z',
                 'mergedAt': '2026-08-04T10:00:00Z',
                 'author': {'login': 'yamada'}},
                {'number': 30, 'title': 'マネージャー修正',
                 'headRefName': 'claude/app-manager-y',
                 'createdAt': '2026-08-02T09:00:00Z',
                 'mergedAt': '2026-08-03T10:00:00Z',
                 'author': {'login': 't'}},
            ]},
            'releases': {'nodes': [
                {'tagName': 'v1.1-beta.1', 'name': 'v1.1-beta.1',
                 'isPrerelease': True, 'isDraft': False,
                 'description': '提出 #33 の検証通過版です。',
                 'publishedAt': '2026-08-05T00:00:00Z',
                 'tagCommit': {'oid': 'tagsha-beta1'},
                 'releaseAssets': {'nodes': [
                     {'name': 'mgtkit.zip', 'downloadUrl': 'http://x/z'}]}},
                {'tagName': 'v1.0', 'name': 'v1.0', 'isPrerelease': False,
                 'isDraft': True, 'description': '', 'publishedAt': '',
                 'releaseAssets': {'nodes': []}},
            ]},
        },
    },
}


class TestFetchSnapshot:
    """一括スナップショット取得 (GraphQL 1 回 + 従来経路フォールバック)."""

    def test_graphql_single_roundtrip(self, monkeypatch):
        import json as _json
        calls = []

        def fake(args, timeout=60):
            calls.append(args)
            if args[:2] == ['api', 'graphql']:
                return _json.dumps(_GRAPHQL_SNAPSHOT)
            if args[0] == 'api' and 'collaborators' in args[1]:
                return '["yamada", "fujitaka"]'
            if args[0] == 'api' and 'compare' in args[1]:
                return 'forksha-33\n'
            raise AssertionError('unexpected gh call: %r' % args)

        monkeypatch.setattr(reviews.ghcli, 'run_gh', fake)
        snap = reviews.fetch_snapshot({'repo': 'o/r'})
        # gh は GraphQL 1 回 + collaborators 1 回 + 分岐点 (提出ごと)。
        # 分岐点は並列のため順不同で比較する
        assert [c[:2] for c in calls[:2]] == [
            ['api', 'graphql'],
            ['api', 'repos/o/r/collaborators?per_page=100']]
        assert sorted(c[1] for c in calls[2:]) == [
            'repos/o/r/compare/main...headsha29?per_page=1',
            'repos/o/r/compare/main...headsha33?per_page=1']
        assert snap['me'] == 'yamada'
        # ログイン名はこの 1 回でキャッシュされ、追加の gh 呼び出しなし
        assert reviews.current_user() == 'yamada'
        assert len(calls) == 4
        # 承認待ち一覧は list_pending と同じ形 (feature/ のみ)
        pr = snap['pending'][0]
        assert [p['number'] for p in snap['pending']] == [33]
        assert pr['author'] == 'fujitaka'
        assert pr['approved'] == ['yamada']
        assert pr['checks'] == 'success'
        assert pr['conflicting'] is False
        assert pr['feedback'][0] == {
            'tag': 'v1.1-beta.1', 'name': '山田太郎', 'date': '2026-08-05',
            'text': '良い感じ', 'author': 'yamada', 'comment_id': '987'}
        # リリース一覧は fetch_releases と同じ形 (draft は除外)
        assert snap['releases'] == [
            {'tag': 'v1.1-beta.1', 'name': 'v1.1-beta.1',
             'prerelease': True, 'notes': '提出 #33 の検証通過版です。',
             'published_at': '2026-08-05',
             'published_at_full': '2026-08-05T00:00:00Z',
             'tag_sha': 'tagsha-beta1',
             'assets': [{'name': 'mgtkit.zip', 'url': 'http://x/z'}]}]
        # 済み提出は feature/ ブランチのみ・図に必要な要約だけ
        assert snap['merged'] == [
            {'number': 29, 'title': '合成梁の検討を追加',
             'author': 'yamada', 'created_at': '2026-08-01',
             'created_at_full': '2026-08-01T09:00:00Z',
             'merged_at': '2026-08-04',
             'merged_at_full': '2026-08-04T10:00:00Z',
             'head_sha': 'headsha29', 'fork_sha': 'forksha-33'}]
        # 承認待ちにも提出日が入る (図の帯の左端に使う)
        assert 'created_at' in pr
        # 分岐点コミットが付く (履歴図の基点の事実)
        assert pr['fork_sha'] == 'forksha-33'

    def test_known_forks_skip_refetch(self, monkeypatch):
        import json as _json
        calls = []

        def fake(args, timeout=60):
            calls.append(args)
            if args[:2] == ['api', 'graphql']:
                return _json.dumps(_GRAPHQL_SNAPSHOT)
            if args[0] == 'api' and 'collaborators' in args[1]:
                return '["yamada", "fujitaka"]'
            if args[0] == 'api' and 'compare' in args[1]:
                raise AssertionError('既知の分岐点を取り直している')
            raise AssertionError('unexpected gh call: %r' % args)

        monkeypatch.setattr(reviews.ghcli, 'run_gh', fake)
        snap = reviews.fetch_snapshot(
            {'repo': 'o/r'},
            known_forks={'33:headsha33': 'memo-33',
                         '29:headsha29': 'memo-29'})
        assert snap['pending'][0]['fork_sha'] == 'memo-33'
        assert snap['merged'][0]['fork_sha'] == 'memo-29'

    def test_falls_back_to_rest_on_graphql_error(self, monkeypatch):
        import json as _json
        calls = []

        def fake(args, timeout=60):
            calls.append(args)
            if args[:2] == ['api', 'graphql']:
                raise reviews.ghcli.GhError('通信エラー')
            if args[:2] == ['pr', 'list']:
                return _json.dumps([
                    {'number': 33, 'title': '組立断面', 'url': 'u',
                     'author': {'login': 'fujitaka'},
                     'headRefName': 'feature/fujitaka-20260808-1',
                     'reviews': [], 'statusCheckRollup': [],
                     'mergeable': 'MERGEABLE', 'comments': []}])
            if args[:2] == ['api', 'user']:
                return 'yamada\n'
            if args[0] == 'api' and 'collaborators' in args[1]:
                return '[]'
            if args[0] == 'api' and 'releases' in args[1]:
                return '[]'
            if args[0] == 'api' and 'matching-refs' in args[1]:
                return '[]'
            raise AssertionError('unexpected gh call: %r' % args)

        monkeypatch.setattr(reviews.ghcli, 'run_gh', fake)
        progress = []
        snap = reviews.fetch_snapshot({'repo': 'o/r'},
                                      on_progress=progress.append)
        assert snap['me'] == 'yamada'
        assert [p['number'] for p in snap['pending']] == [33]
        assert snap['releases'] == []
        # 進行状況が言語化される (最初の取得 → 方法を変えて再取得)
        assert len(progress) == 2
        assert '確認しています' in progress[0]
        assert '方法を変えて' in progress[1]

    def test_falls_back_on_unexpected_shape(self, monkeypatch):
        import json as _json

        def fake(args, timeout=60):
            if args[:2] == ['api', 'graphql']:
                return _json.dumps({'data': None})  # 想定外の応答
            if args[:2] == ['pr', 'list']:
                return '[]'
            if args[:2] == ['api', 'user']:
                return 'yamada\n'
            if args[0] == 'api':
                return '[]'
            raise AssertionError('unexpected gh call: %r' % args)

        monkeypatch.setattr(reviews.ghcli, 'run_gh', fake)
        snap = reviews.fetch_snapshot({'repo': 'o/r'})
        assert snap == {'pending': [], 'releases': [], 'me': 'yamada',
                        'merged': []}


class TestRejectedFinal:
    """却下が必要数そろった提出の判定と確定時刻."""

    def test_rejected_since(self):
        summary = {'rejected': [
            {'name': 'a', 'comment': '', 'at': '2026-08-11T00:00:00Z'},
            {'name': 'b', 'comment': '', 'at': '2026-08-10T00:00:00Z'},
        ]}
        # 2 人目 (時刻順で後の方) の却下時刻が確定時刻
        assert reviews.rejected_since(summary, 2) == '2026-08-11T00:00:00Z'

    def test_not_final(self):
        summary = {'rejected': [{'name': 'a', 'comment': '', 'at': 'x'}]}
        assert reviews.rejected_since(summary, 2) is None

    def test_cleanup_days_default_and_config(self):
        assert reviews.rejected_cleanup_days({}) == 3
        assert reviews.rejected_cleanup_days(
            {'manager': {'rejected_cleanup_days': 7}}) == 7


class TestCancelMyReview:
    def _fake_gh(self, calls):
        import json as _json

        def fake(args, timeout=60):
            calls.append(args)
            if args[:2] == ['api', 'user']:
                return 'yamada\n'
            if args[0] == 'api' and '/reviews?' in args[1]:
                return _json.dumps([
                    {'id': 10, 'user': {'login': 'yamada'},
                     'state': 'CHANGES_REQUESTED'},
                    {'id': 11, 'user': {'login': 'sato'},
                     'state': 'APPROVED'},
                    {'id': 12, 'user': {'login': 'yamada'},
                     'state': 'APPROVED'},
                ])
            return ''
        return fake

    def test_dismisses_own_latest_review(self, monkeypatch):
        calls = []
        monkeypatch.setattr(reviews.ghcli, 'run_gh', self._fake_gh(calls))
        reviews.cancel_my_review(33, {'repo': 'o/r'})
        dismiss = calls[-1]
        assert dismiss[:3] == ['api', '-X', 'PUT']
        # 自分 (yamada) の最新レビュー id=12 を取り消す (他人の 11 ではない)
        assert dismiss[3] == 'repos/o/r/pulls/33/reviews/12/dismissals'

    def test_nothing_to_cancel(self, monkeypatch):
        import json as _json

        def fake(args, timeout=60):
            if args[:2] == ['api', 'user']:
                return 'yamada\n'
            if args[0] == 'api' and '/reviews?' in args[1]:
                return _json.dumps([
                    {'id': 11, 'user': {'login': 'sato'},
                     'state': 'APPROVED'}])
            return ''

        monkeypatch.setattr(reviews.ghcli, 'run_gh', fake)
        with pytest.raises(reviews.ReviewError, match='取り消せる'):
            reviews.cancel_my_review(33, {'repo': 'o/r'})


class TestDeleteBetasFor:
    def test_deletes_only_matching_beta(self, monkeypatch):
        calls = []
        monkeypatch.setattr(reviews.ghcli, 'run_gh',
                            lambda args, timeout=60: calls.append(args)
                            or '')
        monkeypatch.setattr(
            reviews.ghcli, 'fetch_releases',
            lambda repo, limit=30: [
                {'tag': 'v1.1-beta.2', 'prerelease': True,
                 'notes': '提出 #33 の検証通過版です。'},
                {'tag': 'v1.1-beta.1', 'prerelease': True,
                 'notes': '提出 #31 の検証通過版です。'},
                {'tag': 'v1.1', 'prerelease': False, 'notes': '#33'},
            ])
        reviews.delete_betas_for(33, {'repo': 'o/r'})
        deletes = [a for a in calls if a[:2] == ['release', 'delete']]
        assert [d[2] for d in deletes] == ['v1.1-beta.2']

    def test_fetch_failure_is_silent(self, monkeypatch):
        def boom(repo, limit=30):
            raise reviews.ghcli.GhError('通信エラー')
        monkeypatch.setattr(reviews.ghcli, 'fetch_releases', boom)
        reviews.delete_betas_for(33, {'repo': 'o/r'})  # 例外にならない


class TestResetAllReviews:
    def test_dismisses_latest_review_of_each_member(self, monkeypatch):
        import json as _json
        calls = []

        def fake(args, timeout=60):
            calls.append(args)
            if args[0] == 'api' and '/reviews?' in args[1]:
                return _json.dumps([
                    {'id': 10, 'user': {'login': 'yamada'},
                     'state': 'CHANGES_REQUESTED'},
                    {'id': 11, 'user': {'login': 'yamada'},
                     'state': 'APPROVED'},
                    {'id': 12, 'user': {'login': 'sato'},
                     'state': 'APPROVED'},
                    {'id': 13, 'user': {'login': 'suzuki'},
                     'state': 'COMMENTED'},
                ])
            return ''

        monkeypatch.setattr(reviews.ghcli, 'run_gh', fake)
        count = reviews.reset_all_reviews(33, {'repo': 'o/r'})
        assert count == 2
        dismissed = sorted(a[3] for a in calls if a[:3] == ['api', '-X',
                                                            'PUT'])
        # 各メンバーの最新レビューのみ (yamada は 11、sato は 12)
        assert dismissed == [
            'repos/o/r/pulls/33/reviews/11/dismissals',
            'repos/o/r/pulls/33/reviews/12/dismissals']

    def test_partial_failure_continues(self, monkeypatch):
        import json as _json

        def fake(args, timeout=60):
            if args[0] == 'api' and '/reviews?' in args[1]:
                return _json.dumps([
                    {'id': 11, 'user': {'login': 'yamada'},
                     'state': 'APPROVED'},
                    {'id': 12, 'user': {'login': 'sato'},
                     'state': 'APPROVED'},
                ])
            if '/reviews/11/' in (args[3] if len(args) > 3 else ''):
                raise reviews.ghcli.GhError('通信エラー')
            return ''

        monkeypatch.setattr(reviews.ghcli, 'run_gh', fake)
        assert reviews.reset_all_reviews(33, {'repo': 'o/r'}) == 1


class TestWithdraw:
    """提出者本人による取り下げ (PR クローズ + ブランチ・β版の削除)."""

    def _fake_gh(self, calls, pr_author='fujitaka'):
        import json as _json

        def fake(args, timeout=60):
            calls.append(args)
            if args[:2] == ['api', 'user']:
                return 'fujitaka\n'
            if args[:2] == ['pr', 'view']:
                return _json.dumps({'author': {'login': pr_author},
                                    'reviews': [], 'statusCheckRollup': [],
                                    'mergeable': 'MERGEABLE',
                                    'headRefName': 'feature/x',
                                    'title': 'x', 'body': '',
                                    'comments': []})
            return ''
        return fake

    def test_only_author_can_withdraw(self, monkeypatch):
        calls = []
        monkeypatch.setattr(reviews.ghcli, 'run_gh',
                            self._fake_gh(calls, pr_author='yamada'))
        with pytest.raises(reviews.ReviewError, match='本人'):
            reviews.withdraw(33, '', {'repo': 'o/r'})
        assert not any(a[:2] == ['pr', 'close'] for a in calls)

    def test_closes_pr_and_deletes_beta(self, monkeypatch):
        calls = []
        monkeypatch.setattr(reviews.ghcli, 'run_gh', self._fake_gh(calls))
        monkeypatch.setattr(
            reviews.ghcli, 'fetch_releases',
            lambda repo, limit=30: [
                {'tag': 'v1.1-beta.2', 'prerelease': True,
                 'notes': '提出 #33 の検証通過版です。'},
                {'tag': 'v1.1-beta.1', 'prerelease': True,
                 'notes': '提出 #31 の検証通過版です。'},
            ])
        reviews.withdraw(33, '実装をやり直したい', {'repo': 'o/r'})
        comment = next(a for a in calls if a[:2] == ['pr', 'comment'])
        assert '取り下げ' in comment[comment.index('--body') + 1]
        close = next(a for a in calls if a[:2] == ['pr', 'close'])
        assert close[2] == '33' and '--delete-branch' in close
        # 対応するβ版 (v1.1-beta.2) だけが削除される
        deletes = [a for a in calls if a[:2] == ['release', 'delete']]
        assert [d[2] for d in deletes] == ['v1.1-beta.2']

    def test_beta_cleanup_failure_is_not_fatal(self, monkeypatch):
        calls = []

        def fake(args, timeout=60):
            calls.append(args)
            if args[:2] == ['api', 'user']:
                return 'fujitaka\n'
            if args[:2] == ['pr', 'view']:
                import json as _json
                return _json.dumps({'author': {'login': 'fujitaka'},
                                    'comments': []})
            if args[:2] == ['release', 'delete']:
                raise reviews.ghcli.GhError('通信エラー')
            return ''

        monkeypatch.setattr(reviews.ghcli, 'run_gh', fake)
        monkeypatch.setattr(
            reviews.ghcli, 'fetch_releases',
            lambda repo, limit=30: [
                {'tag': 'v1.1-beta.2', 'prerelease': True,
                 'notes': '提出 #33 の検証通過版です。'}])
        reviews.withdraw(33, '', {'repo': 'o/r'})  # 例外にならない
        assert any(a[:2] == ['pr', 'close'] for a in calls)


class TestParseFeedback:
    def test_extracts_feedback_comments_only(self):
        comments = [
            {'body': 'β版 v1.1-beta.2 のフィードバック (山田太郎):\n\n'
                     '検定比の表示が見やすくなった',
             'createdAt': '2026-08-05T12:34:56Z',
             'author': {'login': 'yamada'},
             'url': 'https://github.com/o/r/pull/12#issuecomment-987'},
            {'body': '検証に失敗したため自動修正を行いました。',
             'createdAt': '2026-08-05T13:00:00Z'},
        ]
        assert reviews.parse_feedback(comments) == [
            {'tag': 'v1.1-beta.2', 'name': '山田太郎',
             'date': '2026-08-05', 'text': '検定比の表示が見やすくなった',
             'author': 'yamada', 'comment_id': '987'}]

    def test_roundtrip_with_post_feedback_format(self):
        # feedback.post_feedback が組み立てる本文と同一形式を確実に拾う
        from manager import feedback as fb_mod
        rel = {'tag': 'v2.0-beta.1', 'notes': '提出 #34 の検証通過版です。'}
        body = fb_mod._comment_body(rel['tag'], '佐藤次郎',
                                    '複数行の\n感想も保持される')
        assert fb_mod.pr_number_from_release(rel) == 34
        fb = reviews.parse_feedback(
            [{'body': body, 'createdAt': '2026-08-06T00:00:00Z'}])
        assert fb == [{'tag': 'v2.0-beta.1', 'name': '佐藤次郎',
                       'date': '2026-08-06',
                       'text': '複数行の\n感想も保持される',
                       'author': '', 'comment_id': None}]

    def test_empty(self):
        assert reviews.parse_feedback(None) == []
        assert reviews.parse_feedback([]) == []


class TestReleaseRules:
    def _pr(self, **kw):
        pr = {'approved': ['a', 'b', 'c'], 'rejected': [],
              'checks': 'success', 'conflicting': False}
        pr.update(kw)
        return pr

    CFG = {'repo': 'o/r',
           'branch_protection': {'required_approvals': 3},
           'manager': {'admins': ['boss']}}

    def test_release_allowed_for_admin(self):
        assert reviews.can_release(self._pr(), self.CFG, me='boss')

    def test_not_admin(self):
        assert not reviews.can_release(self._pr(), self.CFG, me='member')

    def test_not_enough_approvals(self):
        assert not reviews.can_release(self._pr(approved=['a', 'b']),
                                       self.CFG, me='boss')

    def test_rejection_blocks(self):
        pr = self._pr(rejected=[{'name': 'x', 'comment': 'ng'}])
        assert not reviews.can_release(pr, self.CFG, me='boss')

    def test_failing_checks_block(self):
        assert not reviews.can_release(self._pr(checks='failure'),
                                       self.CFG, me='boss')

    def test_conflict_blocks(self):
        assert not reviews.can_release(self._pr(conflicting=True),
                                       self.CFG, me='boss')

    def test_empty_admins_means_everyone_can_release(self):
        # 管理者リストが空 (既定) = 必要数の承認がそろえば誰でもリリース可
        cfg = {'repo': 'o/r',
               'branch_protection': {'required_approvals': 3},
               'manager': {'admins': []}}
        assert reviews.admins(cfg) == []
        assert reviews.can_operate_release('anyone', cfg)
        assert reviews.can_release(self._pr(), cfg, me='member')

    def test_admins_unset_means_everyone(self):
        assert reviews.can_operate_release('anyone', {'repo': 'o/r'})


class TestNextStableVersion:
    def _patch(self, monkeypatch, releases):
        monkeypatch.setattr(reviews.ghcli, 'fetch_releases',
                            lambda repo, limit=30: releases)

    def test_increments_minor(self, monkeypatch):
        self._patch(monkeypatch, [
            {'tag': 'v1.3-beta.2', 'prerelease': True},
            {'tag': 'v1.2', 'prerelease': False},
            {'tag': 'v1.1', 'prerelease': False},
        ])
        assert reviews.next_stable_version({'repo': 'o/r'}) == 'v1.3'

    def test_no_releases(self, monkeypatch):
        self._patch(monkeypatch, [])
        assert reviews.next_stable_version({'repo': 'o/r'}) == 'v1.0'


class TestReleaseNotesFromPr:
    """リリースノートは提出時の PR 本文から機械抽出する (API 不使用)."""

    BODY = ('## 更新内容\n\n'
            '### 1. 組立断面への対応\n'
            '- 2C・2L 断面を読み込みから計算書まで扱えます。\n\n'
            '## ご利用にあたっての制限事項\n\n'
            '- 2L は等辺山形鋼のみ対応です。\n\n'
            '## 影響範囲\n\n- section.py ほか\n\n'
            '## 変更ファイルの説明\n\n- section.py — 読み込みの追加\n\n'
            '# 提出時の警告 (承認時に確認)\n- eval の使用')

    def test_reuses_user_facing_sections_only(self):
        notes = reviews.release_notes_from_pr(self.BODY, 'v1.2')
        assert notes.startswith('# mgtkit v1.2 リリースノート')
        assert '### 1. 組立断面への対応' in notes
        assert '## ご利用にあたっての制限事項' in notes
        assert '- 2L は等辺山形鋼のみ対応です。' in notes
        # レビュー担当者向けの節と提出時の警告は転載しない
        assert '影響範囲' not in notes
        assert '変更ファイルの説明' not in notes
        assert '提出時の警告' not in notes

    def test_limitations_none_is_omitted(self):
        body = ('## 更新内容\n\n- 出力の改善。\n\n'
                '## ご利用にあたっての制限事項\n\n- なし\n\n'
                '## 影響範囲\n\n- output.py')
        notes = reviews.release_notes_from_pr(body, 'v1.2')
        assert '- 出力の改善。' in notes
        assert '制限事項' not in notes

    def test_limitations_heading_variants(self):
        body = ('## 更新内容\n\n- 改善。\n\n'
                '## 制限事項\n\n- 大きなファイルは未対応です。')
        notes = reviews.release_notes_from_pr(body, 'v1.2')
        assert '## ご利用にあたっての制限事項' in notes
        assert '- 大きなファイルは未対応です。' in notes

    def test_crlf_body(self):
        body = ('## 更新内容\r\n\r\n- 改善。\r\n\r\n'
                '## 影響範囲\r\n\r\n- a.py')
        notes = reviews.release_notes_from_pr(body, 'v1.2')
        assert '- 改善。' in notes
        assert '影響範囲' not in notes

    def test_no_update_section_returns_none(self):
        # 様式に沿わない本文 (手書き等) は呼び出し側の定型文に任せる
        assert reviews.release_notes_from_pr('自由記述の本文', 'v1.2') is None
        assert reviews.release_notes_from_pr('', 'v1.2') is None
        assert reviews.release_notes_from_pr(None, 'v1.2') is None


def _git(args, cwd):
    subprocess.run(['git'] + args, cwd=cwd, check=True,
                   capture_output=True, text=True)


@pytest.fixture()
def conflict_env(tmp_path, monkeypatch):
    """main が先行して同一行を変更している衝突状態を作る."""
    origin = tmp_path / 'origin.git'
    _git(['init', '--bare', '-b', 'main', str(origin)], cwd=tmp_path)
    seed = tmp_path / 'seed'
    seed.mkdir()
    _git(['init', '-b', 'main', '.'], cwd=seed)
    (seed / 'app.py').write_text('value = 1\n')
    _git(['add', '-A'], cwd=seed)
    _git(['-c', 'user.name=t', '-c', 'user.email=t@e.com',
          'commit', '-m', 'base'], cwd=seed)
    # 提出ブランチ: value を 2 に
    _git(['checkout', '-b', 'feature/x-1'], cwd=seed)
    (seed / 'app.py').write_text('value = 2  # 提出者の変更\n')
    _git(['add', '-A'], cwd=seed)
    _git(['-c', 'user.name=t', '-c', 'user.email=t@e.com',
          'commit', '-m', 'feature'], cwd=seed)
    # main 先行: 同じ行を 3 に
    _git(['checkout', 'main'], cwd=seed)
    (seed / 'app.py').write_text('value = 3  # 最新版の変更\n')
    _git(['add', '-A'], cwd=seed)
    _git(['-c', 'user.name=t', '-c', 'user.email=t@e.com',
          'commit', '-m', 'main advance'], cwd=seed)
    _git(['remote', 'add', 'origin', str(origin)], cwd=seed)
    _git(['push', 'origin', 'main', 'feature/x-1'], cwd=seed)

    workrepo = tmp_path / 'workrepo'
    _git(['clone', str(origin), str(workrepo)], cwd=tmp_path)

    monkeypatch.setattr(conflicts, 'ensure_work_repo',
                        lambda slug, d: str(workrepo))
    monkeypatch.setattr(
        conflicts.ghcli, 'run_gh',
        lambda args, timeout=60: 'resolver\n'
        if args[:2] == ['api', 'user'] else (_ for _ in ()).throw(
            AssertionError('unexpected gh call: %r' % args)))
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    return {'origin': str(origin), 'workrepo': str(workrepo)}


class TestConflicts:
    def test_analyze_detects_conflict(self, conflict_env):
        analysis = conflicts.analyze('feature/x-1', {})
        try:
            assert analysis['conflicted'] == ['app.py']
            assert not analysis['merged_clean']
            assert 'app.py' in analysis['explanation']
        finally:
            conflicts.abort(analysis)

    def test_resolve_theirs(self, conflict_env):
        analysis = conflicts.analyze('feature/x-1', {})
        summary = conflicts.resolve(analysis, 'theirs', {})
        assert '最新版を優先' in summary
        content = run_git(['show', 'origin/feature/x-1:app.py'],
                          cwd=conflict_env['workrepo'])
        assert 'value = 3' in content

    def test_resolve_ours(self, conflict_env):
        analysis = conflicts.analyze('feature/x-1', {})
        summary = conflicts.resolve(analysis, 'ours', {})
        assert 'あなたの変更を優先' in summary
        content = run_git(['show', 'origin/feature/x-1:app.py'],
                          cwd=conflict_env['workrepo'])
        assert 'value = 2' in content

    def test_resolve_both_via_claude(self, conflict_env, monkeypatch):
        monkeypatch.setattr(
            conflicts.claude_helper, 'generate_merge',
            lambda files, policy: {
                'summary': '両方の値を保持する形に統合',
                'files': [{'path': 'app.py',
                           'content': 'value = 2\nlatest = 3\n'}]})
        analysis = conflicts.analyze('feature/x-1', {})
        summary = conflicts.resolve(analysis, 'both', {})
        assert '統合' in summary
        content = run_git(['show', 'origin/feature/x-1:app.py'],
                          cwd=conflict_env['workrepo'])
        assert 'latest = 3' in content

    def test_resolve_both_without_key_aborts(self, conflict_env,
                                             monkeypatch):
        monkeypatch.setattr(conflicts.claude_helper, 'generate_merge',
                            lambda files, policy: None)
        analysis = conflicts.analyze('feature/x-1', {})
        with pytest.raises(conflicts.ConflictError, match='API キー'):
            conflicts.resolve(analysis, 'both', {})
        # merge は取り消されている (作業ツリーが衝突状態のまま残らない)
        status = run_git(['status', '--porcelain'],
                         cwd=conflict_env['workrepo'])
        assert 'UU' not in status
