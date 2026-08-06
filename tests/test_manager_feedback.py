"""manager/feedback.py (β版フィードバック → PR コメント) のテスト。"""
import pytest

from manager import feedback


class TestPrNumberFromRelease:
    def test_parses_from_notes(self):
        rel = {'tag': 'v1.1-beta.2',
               'notes': '提出 #12 (CSV出力の追加) の検証通過版です。'}
        assert feedback.pr_number_from_release(rel) == 12

    def test_none_when_absent(self):
        assert feedback.pr_number_from_release({'notes': '手動リリース'}) \
            is None
        assert feedback.pr_number_from_release({}) is None


class TestPostFeedback:
    REL = {'tag': 'v1.1-beta.2', 'notes': '提出 #12 の検証通過版です。'}

    @pytest.fixture()
    def gh_calls(self, monkeypatch):
        calls = []
        monkeypatch.setattr(feedback.ghcli, 'run_gh',
                            lambda args, timeout=60: calls.append(args)
                            or '')
        monkeypatch.setattr(feedback.settings, 'user_name',
                            lambda config=None: '山田太郎')
        return calls

    def test_posts_comment_to_pr(self, gh_calls):
        pr = feedback.post_feedback(self.REL, '検定比の表示が見やすくなった',
                                    {'repo': 'o/r'})
        assert pr == 12
        args = gh_calls[0]
        assert args[:3] == ['pr', 'comment', '12']
        body = args[args.index('--body') + 1]
        assert 'v1.1-beta.2' in body
        assert '山田太郎' in body
        assert '検定比の表示' in body

    def test_empty_text_rejected(self, gh_calls):
        with pytest.raises(feedback.FeedbackError, match='内容'):
            feedback.post_feedback(self.REL, '  ', {})
        assert gh_calls == []

    def test_release_without_pr_rejected(self, gh_calls):
        with pytest.raises(feedback.FeedbackError, match='対応する提出'):
            feedback.post_feedback({'tag': 'v9.9-beta.1', 'notes': 'x'},
                                   '感想', {})
        assert gh_calls == []
