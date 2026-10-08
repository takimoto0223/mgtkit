# -*- coding: utf-8 -*-
"""過去の更新ログの時系列図のモデル (描画に依存しない純ロジック).

図の文法の原本は manager/docs/mockups/history_flow.html:
- グレーの本線 = 正式版の列 (白丸 = 版、大きい丸 = 現行版)
- 人ごとの色の帯 = 提出された更新 (左端 = 提出日)
- 本線から降りる線 = その版から派生、本線へ上がる矢印 = 正式版として公開
- 点線の帯 = まだ確認中 (きょうの線まで)。同じ時期の帯は上下レーンに分ける

データの対応付け:
- 正式版 (prerelease でない release) と取り込み済みの提出 (merged PR) は、
  その版のタグが指すコミットに紐づく提出 (release['pr_number']) で
  対応付ける。これは GitHub 側の事実。引けなかった版だけ
  「公開日以前で最も新しい取り込み」で新しい順に貪欲に対応付ける
  (取り込み → 数分でリリースという運用の前提つき)。
  どちらでも対応が付かない最古の版は「初回配布」扱い
- 帯の基点 (どの版から作ったか) は、提出時に記録された版
  (提出者がマネージャーで取得した版。配布 ZIP の version.json 由来で
  PR 本文に残る)。記録が無い古い提出だけ、分岐点コミット → 提出日時、
  の順に推定へ倒す。推定した基点は表示でそれと分かるようにする
  (base_source: 'recorded' / 'fork' / 'estimate')
"""
import datetime
import re

# 提出者の色 (Tailwind 700 相当のトーン統一パレット)。ログイン名から
# 安定に割り当てる (順序はスナップショットに依存しない)
PERSON_COLORS = ['#b45309', '#0f766e', '#be185d', '#6d28d9',
                 '#1d4ed8', '#4d7c0f', '#a16207', '#0e7490']


def parse_date(s):
    """'YYYY-MM-DD...' を date に (解釈できなければ None)."""
    try:
        return datetime.date.fromisoformat((s or '')[:10])
    except ValueError:
        return None


def fmt_date(d, with_year=False):
    """図・一覧の日付表記 (例: 8/14, 2026/8/14)."""
    if d is None:
        return ''
    if with_year:
        return '%d/%d/%d' % (d.year, d.month, d.day)
    return '%d/%d' % (d.month, d.day)


def _version_key(tag):
    m = re.match(r'^v(\d+)\.(\d+)$', tag or '')
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def person_color(author, authors_sorted):
    """提出者 → 帯の色。authors_sorted はログイン名の昇順一覧."""
    try:
        idx = authors_sorted.index(author)
    except ValueError:
        idx = 0
    return PERSON_COLORS[idx % len(PERSON_COLORS)]


def build_timeline(releases, merged, pending, today=None):
    """図のモデルを組み立てる.

    releases: ghcli.fetch_releases 形 (prerelease もそのまま渡してよい)
    merged: reviews.list_merged 形 (取り込み済みの提出)
    pending: reviews.list_pending 形 (承認待ち。created_at 付き)
    戻り値: dict(stables, chips, authors)
      stables: 昇順の正式版 [{tag, date, release, pr, relabel}] (pr は
               対応する済み提出 dict か None。None の最古の版 = 初回配布。
               relabel = 前の版と同じ中身に番号だけ付け直した版)
      chips:   昇順の帯 [{author, number, title, start, end, base_tag,
               target_tag, pending, lane}] (end/target_tag は確認中なら
               None。lane: -1 = 本線のすぐ下、+1 = 上、
               -2, -3, ... = さらに下の段)
      authors: 登場する提出者のログイン名 (昇順。色割り当て用)
    """
    today = today or datetime.date.today()
    stables = []
    for r in releases:
        if r.get('prerelease'):
            continue
        d = parse_date(r.get('published_at'))
        if d is None:
            continue
        stables.append({'tag': r['tag'], 'date': d, 'release': r,
                        'pr': None})
    stables.sort(key=lambda s: (s['date'], _version_key(s['tag'])))

    feats = [dict(m) for m in merged if parse_date(m.get('merged_at'))]
    feats.sort(key=lambda m: (parse_date(m['merged_at']), m['number']))

    def _when(item, key):
        """前後関係の比較キー。時刻付き (…_full) があれば優先する.

        ISO 形式の文字列同士は辞書順の比較で時刻の前後になる。
        同じ日に提出と公開があるケースの取り違え防止 (日付だけだと
        公開より前に作られた提出が新しい版ベース扱いになる)。
        """
        return item.get(key + '_full') or item.get(key) or ''

    # 0) 前の正式版と同じコミットに番号だけ付け直した版 (例: v1.13 と同じ
    #    中身の v2.0) は、提出を伴わない版として扱う。帯は最初の版にだけ
    #    描く (同じ提出の帯が 2 本に見え、一覧にも 2 回出ていた)
    seen_sha = set()
    for s in stables:
        sha = s['release'].get('tag_sha')
        s['relabel'] = bool(sha) and sha in seen_sha
        if sha:
            seen_sha.add(sha)
    # 1) タグのコミットに紐づく提出 (事実)。引けた版はここで確定する
    by_number = {m['number']: m for m in feats}
    seen_pr = set()
    for s in stables:
        if s['relabel']:
            continue
        pr = by_number.get(s['release'].get('pr_number'))
        if pr is not None and pr['number'] not in seen_pr:
            s['pr'] = pr
            seen_pr.add(pr['number'])
        elif pr is not None:
            s['relabel'] = True
    # 2) 残り (提出を伴わない版・古い取得経路) だけ日時で貪欲に対応付ける。
    #    確定済みの提出は候補から外す (使い回して 1 つずつずれるのを防ぐ)
    taken = {s['pr']['number'] for s in stables if s['pr']}
    rest = [m for m in feats if m['number'] not in taken]
    i = len(rest) - 1
    for s in reversed(stables):
        if s['pr'] is not None or s['relabel']:
            continue
        pub = _when(s['release'], 'published_at')
        while i >= 0 and _when(rest[i], 'merged_at')[:len(pub)] > pub:
            i -= 1
        if i >= 0:
            s['pr'] = rest[i]
            i -= 1

    chips = []
    for s in stables:
        pr = s['pr']
        if not pr:
            continue
        start = parse_date(pr.get('created_at')) or s['date']
        chips.append({'author': pr.get('author') or '?',
                      'number': pr.get('number'),
                      'title': pr.get('title') or '',
                      'start': start, 'end': s['date'],
                      'created_full': _when(pr, 'created_at'),
                      'fork_sha': pr.get('fork_sha') or '',
                      'base_version': pr.get('base_version') or '',
                      'base_commit': pr.get('base_commit') or '',
                      'base_tag': None, 'base_source': 'estimate',
                      'target_tag': s['tag'],
                      'pending': False, 'lane': -1})
    for p in pending:
        if p.get('rejected_final'):
            # 必要数の却下がそろった提出は図に出さない (承認タブでも
            # 既定で非表示のもの。再提出されれば新しい提出として現れる)
            continue
        start = parse_date(p.get('created_at')) or today
        chips.append({'author': p.get('author') or '?',
                      'number': p.get('number'),
                      'title': p.get('title') or '',
                      'start': start, 'end': None,
                      'created_full': _when(p, 'created_at'),
                      'fork_sha': p.get('fork_sha') or '',
                      'base_version': p.get('base_version') or '',
                      'base_commit': p.get('base_commit') or '',
                      'base_tag': None, 'base_source': 'estimate',
                      'target_tag': None,
                      'pending': True, 'lane': -1})

    # 基点 (どの版から作られたか) は次の順で決める:
    # 1) 提出時に記録された版 (提出者が取得した配布物の version.json を
    #    提出が写したもの)。これだけが事実で、あとは推定
    # 2) 記録が無い古い提出は、分岐点コミット (提出のいちばん古い
    #    コミットの親) がリリースタグと一致すればそれ
    # 3) それも分からなければ「提出日時以前で最も新しい正式版」で推定
    #    (時刻まで比較)。新しい版の公開直後に古い版から提出されると
    #    取り違えるため、あくまで最後の手段
    dates = {s['tag']: s['date'] for s in stables}
    order = {s['tag']: i for i, s in enumerate(stables)}
    # 同じコミットの版が複数あれば最初の版を基点の名前にする (付け直した
    # 番号ではなく、提出が取り込まれた版)
    sha_to_tag = {}
    for s in stables:
        sha_to_tag.setdefault(s['release'].get('tag_sha'), s['tag'])
    sha_to_tag.pop(None, None)

    def _usable(tag, limit):
        # 自分が公開された版より後の版は基点にできない (同じ日に複数の版が
        # 出たときに前後が入れ替わるのを防ぐ)
        return bool(tag) and order.get(tag, len(stables)) < limit

    for c in chips:
        limit = order.get(c['target_tag'], len(stables))
        candidates = stables[:limit]
        # 1) 提出時の記録 (版名そのもの → 記録されたコミット の順に照合)
        recorded = c.get('base_version') or ''
        tag = recorded if _usable(recorded, limit) else ''
        if not tag:
            by_sha = sha_to_tag.get(c.get('base_commit') or '')
            tag = by_sha if _usable(by_sha, limit) else ''
        if tag:
            c['base_tag'] = tag
            c['base_source'] = 'recorded'
            continue
        # 2) 分岐点コミット
        by_fork = sha_to_tag.get(c.get('fork_sha') or '')
        if _usable(by_fork, limit):
            c['base_tag'] = by_fork
            c['base_source'] = 'fork'
            continue
        # 3) 提出日時からの推定
        base = None
        created = c.get('created_full') or c['start'].isoformat()
        for s in candidates:
            pub = _when(s['release'], 'published_at') \
                or s['date'].isoformat()
            n = min(len(pub), len(created))
            if pub[:n] <= created[:n]:
                base = s['tag']
        if base is None and candidates:
            base = candidates[0]['tag']
        c['base_tag'] = base
        c['base_source'] = 'estimate'

    chips.sort(key=lambda c: (c['start'], c['number'] or 0))
    _assign_lanes(chips, today, dates)
    authors = sorted({c['author'] for c in chips})
    return {'stables': stables, 'chips': chips, 'authors': authors}


def base_label(chip):
    """帯の基点の表示名 (「どの版を基に作ったか」として画面に出す文字列).

    提出時に記録された版があれば、それがそのまま答え。β版を基に作った
    提出は β の版名 (v1.3-beta.1 など) になる。β版は正式版の列に居ないので
    図の線は近い正式版から引くことになるが、**文字は記録どおり**にする
    (記録があるのに推定した版名を見せる方が誤解を招くため)。
    記録が無い古い提出だけ、日時からの推定に「(推定)」を添えて区別する。
    """
    c = chip or {}
    recorded = c.get('base_version') or ''
    if recorded:
        return recorded
    tag = c.get('base_tag') or ''
    if not tag:
        return '不明'
    return '%s (推定)' % tag if c.get('base_source') == 'estimate' else tag


def lane_order():
    """レーン番号を内側から順に返す (-1 → +1 → -2 → +2 → -3 → ...).

    本線の下と上を交互に使い、本線が図のなるべく中央に来るようにする
    (管理者指示 2026-10。以前は上を 1 段だけにして下へ伸ばしていたため、
    同時に何本も出ると本線が図の上端に寄り、下へ長い線が並んでいた)。
    同時に何本の枝が出ても重ねない = 図が縦に伸びるのは許容する
    (管理者指示 2026-08)。
    """
    n = 1
    while True:
        yield -n
        yield n
        n += 1


def pack_lanes(spans):
    """区間の列 → レーン番号の列 (終わりが早いものほど本線に近いレーン).

    区間は半開区間 [lo, hi) として扱う。端が同じだけ (前の帯の公開位置
    = 次の帯の基点) は重なりとしない。そうしないと連続する提出が交互に
    レーンを変えてしまう。lo/hi は日付でも x 座標でも比較さえできれば
    よい (モデルは日付、描画は px で同じ規則を使う)。

    置く順は「終わり (公開) が早い順、同じなら始まりが遅い順」。先に
    公開された帯ほど本線に近いと、合流の線が本線へ上がる (下がる) とき、
    それより本線寄りの帯はもう終わっていて横切らない。短い帯が長い帯の
    内側に収まる形になり、線の交差が減る (管理者指示 2026-10)。終わりも
    始まりも同じ (確認中どうしなど) は渡された順 = 提出順。戻り値は
    渡された区間の順に並べて返す。
    """
    order = sorted(range(len(spans)),
                   key=lambda i: (spans[i][1], _neg(spans[i][0]), i))
    placed = {}
    lanes = [None] * len(spans)
    for i in order:
        lo, hi = spans[i]
        for lane in lane_order():
            if all(hi <= b0 or b1 <= lo for b0, b1 in placed.get(lane, ())):
                break       # 空のレーンには必ず置けるので必ず抜ける
        lanes[i] = lane
        placed.setdefault(lane, []).append((lo, hi))
    return lanes


def _neg(v):
    """並べ替えの「降順」用。数値はそのまま負に、日付は序数を負にする."""
    if isinstance(v, (datetime.date, datetime.datetime)):
        return -v.toordinal()
    return -v


def _assign_lanes(chips, today, base_dates):
    """帯のレーン割当 (日付での粗い割当).

    重なり判定は帯そのものだけでなく、基点ノードから帯まで横に走る
    派生線も含めた範囲 (基点の日〜終わりの日) で行う。線が他の帯を
    突っ切るのを防ぐため。同じ日に始まって同じ日に終わる帯 (きょう
    公開した版から、きょう出された提出など) は幅が 0 になり重なりを
    見逃すので、どの帯も最低 1 日ぶんの幅があるものとして比べる。

    描画側 (historyview) は帯の実際の x 座標で割当をやり直す。日付が
    重ならなくても、確認中の帯がきょうの線に寄せられるなどで px では
    重なることがあるため。ここでの割当は図を使わない利用者向けの既定値。
    """
    day = datetime.timedelta(days=1)
    spans = []
    for c in chips:
        start = c['start']
        base_date = base_dates.get(c['base_tag'])
        if base_date is not None and base_date < start:
            start = base_date
        spans.append((start, max(c['end'] or today, start + day)))
    for c, lane in zip(chips, pack_lanes(spans)):
        c['lane'] = lane


def split_title(notes):
    """リリースノートを (見出し, 残り) に分ける.

    提出のタイトルは先頭の「# 〜」1 行として入っている
    (reviews.release_notes_from_pr)。起動タブはこれを太字の 1 行目に出す。
    版名だけの古い見出し (「# mgtkit v1.4 リリースノート」) は呼び出し前に
    落としてある前提なので、ここでは残った先頭の # 行をそのまま採る。
    見出しが無ければ (None, 全文)。
    """
    lines = (notes or '').strip().splitlines()
    if lines and lines[0].startswith('# '):
        return (lines[0][2:].strip() or None,
                '\n'.join(lines[1:]).strip())
    return None, (notes or '').strip()


_BULLET = re.compile(r'^([ \t\u3000]*)(?:[-*・]|\u2022)\s*(.*)$')
_HEADING = re.compile(r'^#{2,6}\s*(.+?)\s*$')


def bullet_lines(text):
    """更新内容などの文章を表示用に行ごとに読み分ける.

    戻り値: [(種類, 段, 文)]。種類は 'head' (## 見出し) / 'item' (箇条書き) /
    'text' (それ以外) / 'blank'。段は箇条書きの字下げ (半角 2 つ = 1 段、
    全角空白・タブは 1 つで 1 段)。提出の本文は Markdown のまま保存し、
    画面ではこの読み分けで見出し行と子項目を描き分ける (管理者指示 2026-10
    「階層が分かりにくい」)。
    """
    out = []
    for line in (text or '').splitlines():
        if not line.strip():
            out.append(('blank', 0, ''))
            continue
        m = _HEADING.match(line.strip())
        if m:
            out.append(('head', 0, m.group(1)))
            continue
        m = _BULLET.match(line)
        if m and m.group(2):
            pad = m.group(1)
            level = (pad.count(' ') // 2 + pad.count('\t')
                     + pad.count('\u3000'))
            out.append(('item', level, m.group(2).strip()))
            continue
        out.append(('text', 0, line.strip()))
    return out


_AI_HEAD = re.compile(r'^#{0,6}\s*AI が自動で調整した箇所\s*$')


def split_ai_note(notes):
    """リリースノートを (通常部分, AI 自動調整の説明) に分ける.

    「AI が自動で調整した箇所」という見出し行 (markdown の # は任意) から
    後ろを AI 調整の説明として琥珀色の枠で表示する。無ければ (全文, None)。
    """
    lines = (notes or '').splitlines()
    for idx, line in enumerate(lines):
        if _AI_HEAD.match(line.strip()):
            normal = '\n'.join(lines[:idx]).strip()
            ai = '\n'.join(lines[idx + 1:]).strip()
            return normal, (ai or None)
    return (notes or '').strip(), None
