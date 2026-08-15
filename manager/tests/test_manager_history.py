# -*- coding: utf-8 -*-
"""過去の更新ログの時系列図モデル (manager/history.py) のテスト."""
import datetime

from manager import history

D = datetime.date


def _rel(tag, date, prerelease=False, notes=''):
    return {'tag': tag, 'name': tag, 'prerelease': prerelease,
            'notes': notes, 'published_at': date, 'assets': []}


def _merged(number, author, created, merged):
    return {'number': number, 'title': 't%d' % number, 'author': author,
            'created_at': created, 'merged_at': merged}


RELEASES = [
    _rel('v1.6', '2026-08-14'),
    _rel('v1.7-beta.1', '2026-08-14', prerelease=True),
    _rel('v1.5', '2026-08-10'),
    _rel('v1.4', '2026-08-02'),
    _rel('v1.3', '2026-07-28'),
]
MERGED = [
    _merged(35, 'sato', '2026-08-11', '2026-08-14'),
    _merged(31, 'tamura', '2026-08-04', '2026-08-10'),
    _merged(27, 'tamura', '2026-07-29', '2026-08-02'),
]
PENDING = [
    {'number': 40, 'title': '一時フォルダの利用を廃止', 'author': 'tomiri',
     'created_at': '2026-08-15'},
    {'number': 38, 'title': '横補剛の追加検討', 'author': 'takahashi',
     'created_at': '2026-08-11'},
    {'number': 41, 'title': '梁貫通孔の検討', 'author': 'yamada',
     'created_at': '2026-08-12'},
]
TODAY = D(2026, 8, 16)


class TestBuildTimeline:
    def test_release_pr_pairing_and_initial(self):
        tl = history.build_timeline(RELEASES, MERGED, [], today=TODAY)
        stables = tl['stables']
        # 昇順・prerelease 除外
        assert [s['tag'] for s in stables] == ['v1.3', 'v1.4', 'v1.5',
                                               'v1.6']
        # 対応付け: 公開日以前で最も新しい取り込み。v1.3 は初回配布
        assert stables[0]['pr'] is None
        assert [s['pr']['number'] for s in stables[1:]] == [27, 31, 35]

    def test_chip_base_and_target(self):
        tl = history.build_timeline(RELEASES, MERGED, PENDING, today=TODAY)
        by_num = {c['number']: c for c in tl['chips']}
        # #27 は v1.3 を基に作られ v1.4 として公開
        assert by_num[27]['base_tag'] == 'v1.3'
        assert by_num[27]['target_tag'] == 'v1.4'
        # #31 は v1.4 を基に v1.5、#35 は v1.5 を基に v1.6
        assert by_num[31]['base_tag'] == 'v1.4'
        assert by_num[35]['base_tag'] == 'v1.5'
        # 確認中: #38 は v1.5 基点、#40 は v1.6 基点で end なし
        assert by_num[38]['pending'] and by_num[38]['base_tag'] == 'v1.5'
        assert by_num[38]['end'] is None and by_num[38]['target_tag'] is None
        assert by_num[40]['base_tag'] == 'v1.6'

    def test_same_day_timestamps_pick_older_base(self):
        # v1.1 の公開 (09:00) と同じ日でも、それより前 (08:00) に
        # 作られた提出のベースは v1.0 (実データでの取り違えの再現)
        rel = [dict(_rel('v1.1', '2026-08-14'),
                    published_at_full='2026-08-14T09:00:00Z'),
               dict(_rel('v1.0', '2026-08-06'),
                    published_at_full='2026-08-06T09:00:00Z')]
        pend = [{'number': 83, 'title': 'x', 'author': 'tomiri',
                 'created_at': '2026-08-14',
                 'created_at_full': '2026-08-14T08:00:00Z'},
                {'number': 84, 'title': 'y', 'author': 'tomiri',
                 'created_at': '2026-08-14',
                 'created_at_full': '2026-08-14T10:00:00Z'}]
        tl = history.build_timeline(rel, [], pend, today=D(2026, 8, 15))
        by_num = {c['number']: c for c in tl['chips']}
        assert by_num[83]['base_tag'] == 'v1.0'   # 公開より前に作成
        assert by_num[84]['base_tag'] == 'v1.1'   # 公開より後に作成

    def test_same_day_submit_release_keeps_base_older(self):
        # 提出日 = 自分の公開日でも基点は自分より前の版になる
        rel = [_rel('v1.1', '2026-08-01'), _rel('v1.0', '2026-07-01')]
        mg = [_merged(5, 'a', '2026-08-01', '2026-08-01')]
        tl = history.build_timeline(rel, mg, [], today=TODAY)
        chip = tl['chips'][0]
        assert chip['target_tag'] == 'v1.1'
        assert chip['base_tag'] == 'v1.0'

    def test_lane_split_on_overlap(self):
        tl = history.build_timeline(RELEASES, MERGED, PENDING, today=TODAY)
        by_num = {c['number']: c for c in tl['chips']}
        # #35 (8/11-8/14) と #38 (8/11-継続中) は時期が重なる → 上下に分离
        assert by_num[35]['lane'] != by_num[38]['lane']
        # 先に置かれた方が下レーン
        assert by_num[35]['lane'] == -1
        # 連続する提出 (前の公開日 = 次の基点) はレーンを変えない
        assert by_num[27]['lane'] == -1
        assert by_num[31]['lane'] == -1
        # 3 つ目の同時期の帯は 2 段目の下レーンへ
        assert by_num[41]['lane'] == -2
        # 前の帯の公開日から始まる帯は下レーンに戻れる
        assert by_num[40]['lane'] == -1

    def test_authors_and_colors_stable(self):
        tl = history.build_timeline(RELEASES, MERGED, PENDING, today=TODAY)
        assert tl['authors'] == sorted(tl['authors'])
        c1 = history.person_color('sato', tl['authors'])
        c2 = history.person_color('sato', tl['authors'])
        assert c1 == c2 and c1 in history.PERSON_COLORS

    def test_rejected_pending_is_hidden(self):
        pend = PENDING + [{'number': 42, 'title': 'x', 'author': 'sato',
                           'created_at': '2026-08-15',
                           'rejected_final': True}]
        tl = history.build_timeline(RELEASES, MERGED, pend, today=TODAY)
        assert 42 not in [c['number'] for c in tl['chips']]

    def test_merged_pr_without_release_is_ignored(self):
        # リリースに対応しない取り込み (失敗など) は帯にならない
        mg = MERGED + [_merged(99, 'x', '2026-08-15', '2026-08-15')]
        tl = history.build_timeline(RELEASES, mg, [], today=TODAY)
        assert 99 not in [c['number'] for c in tl['chips']]


class TestSplitAiNote:
    def test_no_ai_section(self):
        normal, ai = history.split_ai_note('v1.6 リリース。\n\n- 修正')
        assert 'v1.6' in normal and ai is None

    def test_split_on_heading(self):
        notes = ('- 横補剛の追加検討\n\n## AI が自動で調整した箇所\n'
                 '丸め処理は v1.6 の方式を採用しました。')
        normal, ai = history.split_ai_note(notes)
        assert normal == '- 横補剛の追加検討'
        assert ai == '丸め処理は v1.6 の方式を採用しました。'

    def test_fmt_date(self):
        assert history.fmt_date(D(2026, 8, 2)) == '8/2'
        assert history.fmt_date(D(2026, 8, 2), with_year=True) == '2026/8/2'
        assert history.fmt_date(None) == ''
