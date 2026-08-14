# -*- coding: utf-8 -*-
"""過去の更新ログの時系列図のモデル (描画に依存しない純ロジック).

図の文法の原本は manager/docs/mockups/history_flow.html:
- グレーの本線 = 正式版の列 (白丸 = 版、大きい丸 = 現行版)
- 人ごとの色の帯 = 提出された更新 (左端 = 提出日)
- 本線から降りる線 = その版から派生、本線へ上がる矢印 = 正式版として公開
- 点線の帯 = まだ確認中 (きょうの線まで)。同じ時期の帯は上下レーンに分ける

データの対応付け:
- 正式版 (prerelease でない release) と取り込み済みの提出 (merged PR) は
  「公開日以前で最も新しい取り込み」を新しい順に貪欲に対応付ける
  (マネージャーの運用では取り込み → 数分でリリースなので日付で安定する)。
  対応が付かない最古の版は「初回配布」扱い
- 帯の基点 (どの版から作ったか) は「提出日以前で最も新しい正式版」。
  自分が公開された版と同じになった場合はその 1 つ前の版に倒す
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

    # 新しい順に「公開日以前で最も新しい取り込み」を対応付ける
    i = len(feats) - 1
    for s in reversed(stables):
        while i >= 0 and parse_date(feats[i]['merged_at']) > s['date']:
            i -= 1
        if i >= 0:
            s['pr'] = feats[i]
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
                      'base_tag': None, 'target_tag': s['tag'],
                      'pending': False, 'lane': -1})
    for p in pending:
        start = parse_date(p.get('created_at')) or today
        chips.append({'author': p.get('author') or '?',
                      'number': p.get('number'),
                      'title': p.get('title') or '',
                      'start': start, 'end': None,
                      'base_tag': None, 'target_tag': None,
                      'pending': True, 'lane': -1})

    # 基点: 提出日以前で最も新しい正式版 (自分の公開版と同じなら 1 つ前)
    dates = {s['tag']: s['date'] for s in stables}
    for c in chips:
        base = None
        for s in stables:
            if s['date'] <= c['start'] and s['tag'] != c['target_tag']:
                base = s['tag']
        if base is None and stables:
            for s in stables:
                if s['tag'] != c['target_tag']:
                    base = s['tag']
                    break
        c['base_tag'] = base

    chips.sort(key=lambda c: (c['start'], c['number'] or 0))
    _assign_lanes(chips, today, dates)
    authors = sorted({c['author'] for c in chips})
    return {'stables': stables, 'chips': chips, 'authors': authors}


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
