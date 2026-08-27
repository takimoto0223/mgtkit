# -*- coding: utf-8 -*-
"""過去の更新ログの時系列図モデル (manager/history.py) のテスト."""
import datetime

from manager import history, versions

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

    def test_tag_commit_pairing_is_used_when_present(self):
        """タグのコミットに紐づく提出があれば、日時ではなくそれで対応付ける."""
        rel = [dict(_rel('v1.2', '2026-08-18'), pr_number=83),
               dict(_rel('v1.1', '2026-08-14'), pr_number=31)]
        mg = [_merged(31, 'fujitaka', '2026-08-07', '2026-08-14'),
              _merged(83, 'tomiriri', '2026-08-14', '2026-08-18')]
        tl = history.build_timeline(rel, mg, [], today=D(2026, 8, 18))
        by_tag = {s['tag']: s for s in tl['stables']}
        assert by_tag['v1.1']['pr']['number'] == 31
        assert by_tag['v1.2']['pr']['number'] == 83

    def test_release_without_submission_does_not_shift_the_rest(self):
        """提出を伴わない版 (管理者が手で出した版) が混ざっても、
        他の版の提出者がずれない.

        日時の貪欲マッチだけだと 1 件のずれが後続すべてに波及し、
        全部の版が別人の名前になる (実機で起きうる並び)。
        """
        rel = [dict(_rel('v1.3', '2026-08-20')),            # 手で出した版
               dict(_rel('v1.2', '2026-08-18'), pr_number=83),
               dict(_rel('v1.1', '2026-08-14'), pr_number=31),
               dict(_rel('v1.0', '2026-08-06'))]            # 初回配布
        mg = [_merged(31, 'fujitaka', '2026-08-07', '2026-08-14'),
              _merged(83, 'tomiriri', '2026-08-14', '2026-08-18')]
        tl = history.build_timeline(rel, mg, [], today=D(2026, 8, 21))
        got = {s['tag']: (s['pr'] or {}).get('number')
               for s in tl['stables']}
        assert got == {'v1.0': None, 'v1.1': 31, 'v1.2': 83, 'v1.3': None}

    def test_falls_back_to_dates_without_tag_pairing(self):
        # 古い取得経路 (pr_number が無い) では従来どおり日時で対応付ける
        rel = [_rel('v1.2', '2026-08-18'), _rel('v1.1', '2026-08-14')]
        mg = [_merged(31, 'fujitaka', '2026-08-07', '2026-08-14'),
              _merged(83, 'tomiriri', '2026-08-14', '2026-08-18')]
        tl = history.build_timeline(rel, mg, [], today=D(2026, 8, 18))
        by_tag = {s['tag']: s for s in tl['stables']}
        assert by_tag['v1.1']['pr']['number'] == 31
        assert by_tag['v1.2']['pr']['number'] == 83

    def test_same_submission_is_not_used_for_two_versions(self):
        # 事実で確定した提出は、日時の埋め合わせの候補から外す
        rel = [dict(_rel('v1.2', '2026-08-18'), pr_number=83),
               dict(_rel('v1.1', '2026-08-17'))]
        mg = [_merged(83, 'tomiriri', '2026-08-14', '2026-08-16')]
        tl = history.build_timeline(rel, mg, [], today=D(2026, 8, 18))
        got = {s['tag']: (s['pr'] or {}).get('number')
               for s in tl['stables']}
        assert got == {'v1.1': None, 'v1.2': 83}

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

    def test_recorded_base_beats_everything(self):
        """提出時に記録された版 (取得した版) が最優先.

        日時からも分岐点からも v1.1 に見えるが、提出は v1.0 を取得して
        作られている、という実データの再現。
        """
        rel = [dict(_rel('v1.1', '2026-08-14'),
                    published_at_full='2026-08-14T07:18:00Z',
                    tag_sha='sha-v11'),
               dict(_rel('v1.0', '2026-08-06'),
                    published_at_full='2026-08-06T01:02:00Z',
                    tag_sha='sha-v10')]
        pend = [{'number': 83, 'title': 'x', 'author': 'tomiriri',
                 'created_at': '2026-08-14',
                 'created_at_full': '2026-08-14T07:26:55Z',
                 'fork_sha': 'sha-v11',        # 推定はどちらも v1.1 を指す
                 'base_version': 'v1.0', 'base_commit': 'sha-v10'}]
        tl = history.build_timeline(rel, [], pend, today=D(2026, 8, 18))
        c = tl['chips'][0]
        assert c['base_tag'] == 'v1.0'
        assert c['base_source'] == 'recorded'
        assert history.base_label(c) == 'v1.0'

    def test_recorded_commit_used_when_version_unknown(self):
        # 版名がタグ一覧に無い (削除された等) ときは記録のコミットで照合
        rel = [dict(_rel('v1.1', '2026-08-14'), tag_sha='sha-v11'),
               dict(_rel('v1.0', '2026-08-06'), tag_sha='sha-v10')]
        pend = [{'number': 83, 'title': 'x', 'author': 'a',
                 'created_at': '2026-08-14',
                 'base_version': 'v0.9', 'base_commit': 'sha-v10'}]
        tl = history.build_timeline(rel, [], pend, today=D(2026, 8, 18))
        c = tl['chips'][0]
        assert c['base_tag'] == 'v1.0'
        assert c['base_source'] == 'recorded'
        # 表示は記録どおりの版名 (β版から作った提出もそのまま出す)
        assert history.base_label(c) == 'v0.9'

    def test_beta_base_is_shown_as_recorded(self):
        """β版を基に作った提出は、β版の版名をそのまま出す.

        β版は正式版の列に居ないので図の線は近い正式版から引くが、
        「どの版を基に作ったか」の文字は記録どおりにする (記録がある
        のに推定した版名を見せる方が誤解を招くため)。
        """
        rel = [dict(_rel('v1.1', '2026-08-14'), tag_sha='sha-v11'),
               dict(_rel('v1.2-beta.1', '2026-08-16', prerelease=True),
                    tag_sha='sha-b1'),
               dict(_rel('v1.0', '2026-08-06'), tag_sha='sha-v10')]
        pend = [{'number': 90, 'title': 'x', 'author': 'a',
                 'created_at': '2026-08-17',
                 'base_version': 'v1.2-beta.1', 'base_commit': 'sha-b1'}]
        tl = history.build_timeline(rel, [], pend, today=D(2026, 8, 18))
        c = tl['chips'][0]
        # 線は正式版から引く (β版はグレーの本線に居ない)
        assert c['base_tag'] == 'v1.1'
        # 文字は記録どおり。推定扱いの「(推定)」は付けない
        assert history.base_label(c) == 'v1.2-beta.1'

    def test_record_without_version_still_resolves_by_commit(self):
        # version.json の版名が空でも、記録のコミットが残っていれば
        # そこから版を突き止める (印は version=? で書かれる)
        body = versions.base_marker('', 'sha-v10')
        assert versions.base_from_body(body) == {'version': '',
                                                 'commit': 'sha-v10'}
        rel = [dict(_rel('v1.1', '2026-08-14'), tag_sha='sha-v11'),
               dict(_rel('v1.0', '2026-08-06'), tag_sha='sha-v10')]
        base = versions.base_from_body(body)
        pend = [{'number': 91, 'title': 'x', 'author': 'a',
                 'created_at': '2026-08-16',
                 'base_version': base['version'],
                 'base_commit': base['commit']}]
        tl = history.build_timeline(rel, [], pend, today=D(2026, 8, 18))
        c = tl['chips'][0]
        assert c['base_tag'] == 'v1.0'
        assert c['base_source'] == 'recorded'
        assert history.base_label(c) == 'v1.0'

    def test_estimated_base_is_marked_as_such(self):
        # 記録も分岐点も無い古い提出は推定。事実と区別できる表示にする
        rel = [_rel('v1.1', '2026-08-14'), _rel('v1.0', '2026-08-06')]
        pend = [{'number': 83, 'title': 'x', 'author': 'a',
                 'created_at': '2026-08-10'}]
        tl = history.build_timeline(rel, [], pend, today=D(2026, 8, 18))
        c = tl['chips'][0]
        assert c['base_source'] == 'estimate'
        assert history.base_label(c) == 'v1.0 (推定)'

    def test_fork_base_is_not_marked_as_estimate(self):
        # 分岐点コミットは git の事実なので「(推定)」は付けない
        rel = [dict(_rel('v1.1', '2026-08-14'), tag_sha='sha-v11'),
               dict(_rel('v1.0', '2026-08-06'), tag_sha='sha-v10')]
        pend = [{'number': 83, 'title': 'x', 'author': 'a',
                 'created_at': '2026-08-16', 'fork_sha': 'sha-v10'}]
        tl = history.build_timeline(rel, [], pend, today=D(2026, 8, 18))
        c = tl['chips'][0]
        assert c['base_source'] == 'fork'
        assert history.base_label(c) == 'v1.0'

    def test_fork_sha_beats_timestamp_estimate(self):
        # 公開の後に古い版の内容が提出されたケース: 日時推定では v1.1
        # ベースに見えるが、分岐点コミットがある場合はそれが事実
        rel = [dict(_rel('v1.1', '2026-08-14'),
                    published_at_full='2026-08-14T09:00:00Z',
                    tag_sha='sha-v11'),
               dict(_rel('v1.0', '2026-08-06'),
                    published_at_full='2026-08-06T09:00:00Z',
                    tag_sha='sha-v10')]
        pend = [{'number': 83, 'title': 'x', 'author': 'tomiri',
                 'created_at': '2026-08-14',
                 'created_at_full': '2026-08-14T10:00:00Z',
                 'fork_sha': 'sha-v10'}]
        tl = history.build_timeline(rel, [], pend, today=D(2026, 8, 15))
        assert tl['chips'][0]['base_tag'] == 'v1.0'

    def test_base_is_never_a_later_version(self):
        # 同じ日に複数の版が出ても、自分が公開された版より後の版を
        # 基点にはしない (前後が入れ替わって線が逆走するのを防ぐ)
        rel = [_rel('v1.2', '2026-08-14'), _rel('v1.1', '2026-08-14'),
               _rel('v1.0', '2026-08-14')]
        mg = [_merged(8, 'a', '2026-08-14', '2026-08-14'),
              _merged(9, 'b', '2026-08-14', '2026-08-14')]
        tl = history.build_timeline(rel, mg, [], today=D(2026, 8, 14))
        by_num = {c['number']: c for c in tl['chips']}
        assert by_num[8]['target_tag'] == 'v1.1'
        assert by_num[8]['base_tag'] == 'v1.0'
        assert by_num[9]['target_tag'] == 'v1.2'
        assert by_num[9]['base_tag'] in ('v1.0', 'v1.1')

    def test_fork_sha_after_target_is_ignored(self):
        # 分岐点が自分の公開版より後の版を指す (取り違えた記録) 場合は
        # 事実として採用しない
        rel = [dict(_rel('v1.2', '2026-08-18'), tag_sha='sha-v12'),
               dict(_rel('v1.1', '2026-08-14'), tag_sha='sha-v11'),
               dict(_rel('v1.0', '2026-08-06'), tag_sha='sha-v10')]
        mg = [dict(_merged(31, 'a', '2026-08-07', '2026-08-14'),
                   fork_sha='sha-v12'),
              _merged(83, 'b', '2026-08-14', '2026-08-18')]
        tl = history.build_timeline(rel, mg, [], today=D(2026, 8, 18))
        c = {x['number']: x for x in tl['chips']}[31]
        assert c['target_tag'] == 'v1.1'
        assert c['base_tag'] == 'v1.0'

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

    def test_same_day_submissions_do_not_share_a_lane(self):
        # きょう公開した版から、きょう 2 本出された提出 (提出 #167/#168)。
        # 幅 0 の期間どうしを「重なっていない」と見て同じレーンに載せて
        # いた = 図で帯が重なっていた
        rel = [_rel('v1.7', '2026-08-27'), _rel('v1.6', '2026-08-21')]
        pend = [{'number': 167, 'title': 'a', 'author': 'tomiriri',
                 'created_at': '2026-08-27', 'base_version': 'v1.7'},
                {'number': 168, 'title': 'b', 'author': 'tomiriri',
                 'created_at': '2026-08-27', 'base_version': 'v1.7'}]
        tl = history.build_timeline(rel, [], pend, today=D(2026, 8, 27))
        by_num = {c['number']: c for c in tl['chips']}
        assert by_num[167]['lane'] != by_num[168]['lane']

    def test_lanes_keep_going_when_many_overlap(self):
        # 同時に何本出ても重ねない (レーンは下へいくらでも深くする)
        rel = [_rel('v1.1', '2026-08-27'), _rel('v1.0', '2026-08-06')]
        pend = [{'number': 200 + i, 'title': 't', 'author': 'a%d' % i,
                 'created_at': '2026-08-20', 'base_version': 'v1.1'}
                for i in range(6)]
        tl = history.build_timeline(rel, [], pend, today=D(2026, 8, 27))
        lanes = [c['lane'] for c in tl['chips'] if c['pending']]
        assert len(set(lanes)) == len(lanes) == 6


class TestPackLanes:
    def test_touching_spans_share_a_lane(self):
        # 端が同じだけ (前の帯の終わり = 次の帯の始まり) は重ならない
        assert history.pack_lanes([(0, 10), (10, 20), (20, 30)]) == [-1] * 3

    def test_overlapping_spans_go_outward_in_order(self):
        # 内側 (本線に近い) から順に埋める: -1 → +1 → -2 → -3
        assert history.pack_lanes([(0, 10)] * 4) == [-1, 1, -2, -3]

    def test_freed_lane_is_reused(self):
        assert history.pack_lanes([(0, 10), (5, 15), (10, 20)]) == [-1, 1, -1]

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


class TestSplitTitle:
    """リリースノートの先頭の見出し (提出のタイトル) を切り出す."""

    def test_headline_is_split_off(self):
        title, rest = history.split_title(
            '# 荷重分布図の PDF 書き出しに対応\n\n## 更新内容\n\n- 1 図')
        assert title == '荷重分布図の PDF 書き出しに対応'
        assert rest.startswith('## 更新内容')

    def test_no_headline(self):
        title, rest = history.split_title('## 更新内容\n\n- 改善')
        assert title is None
        assert rest == '## 更新内容\n\n- 改善'

    def test_empty(self):
        assert history.split_title('') == (None, '')
        assert history.split_title(None) == (None, '')


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
