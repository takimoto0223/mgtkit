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
      stables: 昇順の正式版 [{tag, date, release, pr}] (pr は対応する
               済み提出 dict か None。None の最古の版 = 初回配布)
      chips:   昇順の帯 [{author, number, title, start, end, base_tag,
               target_tag, pending, lane}] (end/target_tag は確認中なら
               None。lane: -1 = 下レーン, +1 = 上レーン)
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

    # 1) タグのコミットに紐づく提出 (事実)。引けた版はここで確定する
    by_number = {m['number']: m for m in feats}
    for s in stables:
        pr = by_number.get(s['release'].get('pr_number'))
        if pr is not None:
            s['pr'] = pr
    # 2) 残り (提出を伴わない版・古い取得経路) だけ日時で貪欲に対応付ける。
    #    確定済みの提出は候補から外す (使い回して 1 つずつずれるのを防ぐ)
    taken = {s['pr']['number'] for s in stables if s['pr']}
    rest = [m for m in feats if m['number'] not in taken]
    i = len(rest) - 1
    for s in reversed(stables):
        if s['pr'] is not None:
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
    sha_to_tag = {s['release'].get('tag_sha'): s['tag'] for s in stables
                  if s['release'].get('tag_sha')}

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


def _assign_lanes(chips, today, base_dates):
    """帯のレーン割当。基本は下、重なったら上、それでも重なれば 2 段目の下.

    重なり判定は帯そのものだけでなく、基点ノードから帯まで横に走る
    派生線も含めた範囲 (基点の日〜終わりの日) で行う。線が他の帯を
    突っ切るのを防ぐため。
    """
    placed = {}

    def overlaps(span, spans):
        # 端の日が同じだけ (前の帯の公開日 = 次の帯の基点) は重なり扱い
        # しない。そうしないと連続する提出が交互にレーンを変えてしまう
        a0, a1 = span
        return any(a1 > b0 and b1 > a0 for b0, b1 in spans)

    for c in chips:
        start = c['start']
        base_date = base_dates.get(c['base_tag'])
        if base_date is not None and base_date < start:
            start = base_date
        span = (start, c['end'] or today)
        for lane in (-1, 1, -2, -3, -4):
            if not overlaps(span, placed.get(lane, [])):
                break
        c['lane'] = lane
        placed.setdefault(lane, []).append(span)


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
