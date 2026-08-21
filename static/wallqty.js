'use strict';

// 木造壁量計算タブ。app.js の共通関数 ($ / api / setMsg / notesHtml / esc /
// fmtNum / fmtRatio / ratioClass / openFolderBtn / mgtBase) を借りる。
// app.js には手を入れず、このファイルは _tab_wallqty.html から読み込む。
//
// 画面の状態は WQ_DEF (階の定義 + その階の入力値) が唯一の持ち主。
//   [{name: '1F', levels: [0, 0.455], w: '300', af_x: '28', af_y: '37'}, ...]
// 下の階から順に並べる。表そのものが入力欄なので、表を読めば状態が作れる。

let WQ = null;      // 直近の /api/wallqty_read (または _check) の応答
let WQ_DEF = null;  // 階の定義。null = まだ読んでいない (モデルの *STORY を使う)

// ---------------- 保存 (mgtごと) ----------------

function wqStoreKey() { return 'mgtkit_wq_' + mgtBase(); }

function wqLoadStore() {
  try { return JSON.parse(localStorage.getItem(wqStoreKey())) || {}; }
  catch (e) { return {}; }
}

function wqSaveStore() {
  const rows = wqCollectRows();
  if (rows) WQ_DEF = rows;
  try {
    localStorage.setItem(wqStoreKey(), JSON.stringify({def: WQ_DEF}));
  } catch (e) {}
}

//: Tc [s] -> 地盤種別の呼び名 (令88条)。検討書PDFと同じ表記にする
const WQ_SOIL = {'0.4': '第一種地盤', '0.6': '第二種地盤', '0.8': '第三種地盤'};

// h と α から一次固有周期を出して、入力欄の横に出す
function wqTinfo() {
  const el = $('wq_tinfo');
  if (!el) return;
  const h = wqNum('wq_h', (WQ && WQ.model) ? WQ.model.h_bldg : 0);
  const a = wqNum('wq_alpha', 1.0);
  el.innerHTML = 'T = h × (0.02 + 0.01α) = <b>'
    + (h * (0.02 + 0.01 * a)).toFixed(4) + '</b> s';
}

function wqSoil(tc) {
  const n = WQ_SOIL[String(+tc)];
  return n ? tc + ' (' + n + ')' : String(tc);
}

function wqNum(id, dflt) {
  const el = $(id);
  const v = el && el.value !== '' ? parseFloat(el.value) : NaN;
  return isNaN(v) ? dflt : v;
}

function wqVal(id) {
  const el = $(id);
  return el && el.value !== '' ? el.value : '';
}

// ---------------- 階の定義テーブル ----------------

function wqParseLevels(text) {
  return String(text || '').replace(/[、\s]+/g, ',').split(',')
    .filter(s => s !== '').map(s => parseFloat(s));
}

// 表 -> 定義。入力途中でも壊れないよう、数値にならないレベルはそのまま
// 残さず落とす (検証は wqRecalc / wqCheck 側で行う)。
function wqCollectRows() {
  const rows = [...document.querySelectorAll('#wq_input tr[data-story]')];
  if (!rows.length) return null;
  const out = rows.map(tr => ({
    name: tr.querySelector('.wqname').value.trim(),
    levels: wqParseLevels(tr.querySelector('.wqlv').value),
    lvtext: tr.querySelector('.wqlv').value,
    w: tr.querySelector('.wqw').value,
    af_x: tr.querySelector('.wqafx').value,
    af_y: tr.querySelector('.wqafy').value,
  }));
  out.reverse();                       // 表は上の階から。内部は下から
  return out;
}

// サーバへ渡す形 (名前と床レベルだけ)。数値として妥当かをここで見る。
function wqDefForServer(msgId) {
  const def = (wqCollectRows() || WQ_DEF || []).filter(s => s.levels.length);
  for (const s of def) {
    if (s.levels.some(v => isNaN(v))) {
      setMsg(msgId, '階「' + esc(s.name || '?') + '」の床レベル "'
        + esc(s.lvtext || '') + '" に数値でない値があります。', 'msg-err');
      return null;
    }
  }
  if (!def.length) {
    setMsg(msgId, '階の定義が空です。階名と床レベルを1行以上入力して'
      + 'ください。', 'msg-err');
    return null;
  }
  const names = def.map(s => s.name);
  if (new Set(names).size !== names.length) {
    setMsg(msgId, '階名が重複しています。層重量の紐付けが狂うので、'
      + '別々の名前にしてください。', 'msg-err');
    return null;
  }
  return def.map(s => ({name: s.name, levels: s.levels}));
}

function wqAddStory() {
  const def = wqCollectRows() || WQ_DEF || [];
  const tops = def.map(s => Math.max.apply(null, s.levels.concat([0])));
  const top = tops.length ? Math.max.apply(null, tops) : 0;
  WQ_DEF = def.concat([{name: '新しい階', levels: [top + 3.0],
                        w: '', af_x: '', af_y: ''}]);
  wqRenderStories();
  wqSaveStore();
}

function wqDelStory(i) {
  const def = wqCollectRows() || WQ_DEF || [];
  def.splice(i, 1);
  WQ_DEF = def;
  wqRenderStories();
  wqSaveStore();
}

function wqRenderStories() {
  const def = WQ_DEF || [];
  const info = {};
  for (const st of ((WQ && WQ.stories) || [])) info[st.name] = st;
  let h = '<table class="res"><tr><th>階名</th>'
    + '<th>床レベル[m]<br><span class="hint">複数可(カンマ区切り)</span></th>'
    + '<th>階高[m]</th><th>壁面数</th><th>層重量 Wi [kN]</th>'
    + '<th>見付面積 X方向[m<sup>2</sup>]</th>'
    + '<th>見付面積 Y方向[m<sup>2</sup>]</th><th></th></tr>';
  for (let i = def.length - 1; i >= 0; i--) {     // 表は上の階から並べる
    const st = def[i];
    const got = info[st.name];
    const lv = st.lvtext !== undefined && st.lvtext !== null ? st.lvtext
      : st.levels.map(v => (+v).toFixed(3)).join(', ');
    h += '<tr data-story="' + i + '">'
      + '<td><input type="text" class="wqname" style="width:110px" value="'
      + esc(st.name) + '" oninput="wqSaveStore()"></td>'
      + '<td><input type="text" class="wqlv" style="width:150px" value="'
      + esc(lv) + '" oninput="wqSaveStore()"></td>'
      + '<td>' + (got ? esc(got.h_disp) : '-') + '</td>'
      + '<td>' + (got ? got.nwall : '-') + '</td>'
      + '<td><input type="number" class="wqw" step="1" min="0" value="'
      + esc(st.w || '') + '" oninput="wqSaveStore()"></td>'
      + '<td><input type="number" class="wqafx" step="0.1" min="0" value="'
      + esc(st.af_x || '') + '" oninput="wqSaveStore()"></td>'
      + '<td><input type="number" class="wqafy" step="0.1" min="0" value="'
      + esc(st.af_y || '') + '" oninput="wqSaveStore()"></td>'
      + '<td><button class="sub" onclick="wqDelStory(' + i + ')">削除</button>'
      + '</td></tr>';
  }
  h += '</table>';
  $('wq_input').innerHTML = h;
}

// ---------------- 読み込み ----------------

function wqReadParams(msgId) {
  const p = {
    mgt_path: $('mgt_path').value.trim(),
    keyword: $('wq_keyword').value.trim() || '壁倍率',
    diag_split: $('wq_diag').checked,
    slope_min: wqNum('wq_slope', 60),
    // 令46条の最小長さ規定。チェックを外したら 0 を送って判定させない
    min_len: $('wq_minlen').checked ? wqNum('wq_minlen_v', 600) / 1000 : 0,
    max_aspect: $('wq_aspect').checked ? wqNum('wq_aspect_v', 5) : 0,
    beta_cap: $('wq_betacap').checked ? wqNum('wq_betacap_v', 7) : 0,
    max_gap: $('wq_gap').checked ? wqNum('wq_gap_v', 300) / 1000 : 0,
    stories_def: null,
  };
  if (WQ_DEF && WQ_DEF.length) {
    p.stories_def = wqDefForServer(msgId || 'wq_read_msg');
    if (p.stories_def === null) return null;
  }
  return p;
}

async function wqRead(msgId) {
  msgId = msgId || 'wq_read_msg';
  if (!$('mgt_path').value.trim()) {
    setMsg(msgId, 'mgtファイルのパスを入力してください。', 'msg-err');
    return;
  }
  // 初回はブラウザに保存してある階定義を復元する (無ければモデルから)
  if (WQ_DEF === null) {
    const saved = wqLoadStore();
    if (saved.def && saved.def.length) WQ_DEF = saved.def;
  }
  const p = wqReadParams(msgId);
  if (!p) return;
  try {
    const j = await api('/api/wallqty_read', p);
    WQ = j;
    if (!WQ_DEF || !WQ_DEF.length) {
      WQ_DEF = j.story_def.map(s => ({name: s.name, levels: s.levels,
                                      w: '', af_x: '', af_y: ''}));
    }
    wqRenderExist(j);
    wqRenderStories();
    wqRenderPlan(j);
    wqRenderWalls(j);
    wqSaveStore();
    if ($('wq_h') && !$('wq_h').value) {      // 高さの既定はモデルの建物高さ
      $('wq_h').value = j.model.h_bldg.toFixed(3);
    }
    wqTinfo();
    $('wq_out').innerHTML = '';
    setMsg(msgId,
      j.stories.length + ' 階 / 耐力壁 ' + j.walls.length + ' 面を読み込み'
      + 'ました (材料: ' + esc(j.materials.join(', ')) + '、階の区切りは'
      + (j.story_src === 'input' ? '画面の階定義' : 'mgt の *STORY') + ')。'
      + notesHtml(j.notes), 'msg-ok');
  } catch (e) {
    WQ = null;
    setMsg(msgId, esc(e.message), 'msg-err');
  }
}

function wqRecalc() {
  if (!WQ) {
    setMsg('wq_read_msg', '先に「モデルの壁を読み込み」を実行してください。',
           'msg-err');
    return;
  }
  wqSaveStore();
  wqRead();
}

function wqResetStories() {
  WQ_DEF = null;
  try { localStorage.setItem(wqStoreKey(), JSON.stringify({})); } catch (e) {}
  wqRead();
}

function wqFillWindRef() {
  if (!WQ) {
    setMsg('wq_msg', '先に「モデルの壁を読み込み」を実行してください。',
           'msg-err');
    return;
  }
  const ref = {};
  for (const st of WQ.stories) ref[st.name] = WQ.wind_ref[st.no];
  let n = 0;
  for (const tr of document.querySelectorAll('#wq_input tr[data-story]')) {
    const r = ref[tr.querySelector('.wqname').value.trim()];
    if (!r) continue;
    tr.querySelector('.wqafx').value = r.x.toFixed(2);
    tr.querySelector('.wqafy').value = r.y.toFixed(2);
    n++;
  }
  wqSaveStore();
  setMsg('wq_msg', n + ' 階の見付面積にモデル外形からの概算値を入れました '
    + '(幅 X=' + fmtNum(WQ.model.x1 - WQ.model.x0, 2) + 'm / Y='
    + fmtNum(WQ.model.y1 - WQ.model.y0, 2) + 'm、最高部 Z='
    + fmtNum(WQ.model.z_top, 3) + 'm、床面+1.35m より上で算定)。'
    + '屋根形状は考慮していないので確認してください。', 'msg-note');
}

// ---------------- 存在壁量 ----------------

function wqRenderExist(j) {
  const qa = j.qa_unit;
  let h = '<div class="pdfblock"><h4>存在壁量 Σ(壁長L × 壁倍率β)</h4>'
    + '<table class="res"><tr><th>階</th><th>床レベル[m]</th>'
    + '<th>階高[m]</th><th>壁面数</th>'
    + '<th>X方向 ΣLβ[m]</th><th>X方向 許容耐力[kN]</th>'
    + '<th>Y方向 ΣLβ[m]</th><th>Y方向 許容耐力[kN]</th></tr>';
  for (const st of j.stories.slice().reverse()) {
    const ex = j.exist[st.no] || {X: 0, Y: 0};
    h += '<tr><td class="name">' + esc(st.name) + '</td>'
      + '<td>' + st.levels.map(v => fmtNum(v, 3)).join(' / ') + '</td>'
      + '<td>' + esc(st.h_disp) + '</td>'
      + '<td>' + st.nwall + '</td>'
      + '<td>' + fmtNum(ex.X, 3) + '</td>'
      + '<td>' + fmtNum(ex.X * qa, 2) + '</td>'
      + '<td>' + fmtNum(ex.Y, 3) + '</td>'
      + '<td>' + fmtNum(ex.Y * qa, 2) + '</td></tr>';
  }
  h += '</table><div class="hint">許容耐力 = ΣLβ × ' + qa + ' kN/m '
    + '(壁倍率1・長さ1m あたり)。建物高さ h = '
    + fmtNum(j.model.h_bldg, 3) + ' m、モデル単位 ' + esc(j.unit)
    + '</div></div>';
  $('wq_exist').innerHTML = h;
}

// ---------------- 平面図 ----------------

const WQ_COL = {X: '#2b6cb0', Y: '#2f855a', D: '#b45309', OFF: '#b3b8c2',
                Q4: '#d98b3a'};

// 図の大きさ。小さい建物は枠 (boxW×boxH) に収め、大きい建物は「1mあたりの
// 画素数」を minPx 以上に保って図そのものを大きくする (枠は横スクロール)。
// maxW/maxH は自動サイズの上限で、手動倍率 (wq_zoom) はその上から掛ける。
const WQ_PLAN = {minPx: 60, boxW: 660, boxH: 470, maxW: 2600, maxH: 1900};

function wqPlanScale(m) {
  const dx = Math.max(m.x1 - m.x0, 0.001);
  const dy = Math.max(m.y1 - m.y0, 0.001);
  const pad = Math.max(dx, dy) * 0.10 + 0.4;
  const bx0 = m.x0 - pad, bx1 = m.x1 + pad;
  const by0 = m.y0 - pad, by1 = m.y1 + pad;
  const W = bx1 - bx0, H = by1 - by0;
  let s = Math.max(Math.min(WQ_PLAN.boxW / W, WQ_PLAN.boxH / H),
                   WQ_PLAN.minPx);
  s = Math.min(s, WQ_PLAN.maxW / W, WQ_PLAN.maxH / H);
  s *= Math.max(0.25, wqNum('wq_zoom', 1));
  return {bx0: bx0, bx1: bx1, by0: by0, by1: by1, s: s,
          w: W * s, h: H * s};
}

// 倍率を変えたときは読み直さずに描き直すだけ
function wqZoom() {
  if (WQ) wqRenderPlan(WQ);
}

// 通り芯。mgt の *GROUP (グループ名=通り芯名) から取れなければ、壁の座標に
// X1,X2… / Y1,Y2… を自動で振る。全階を通して同じ名前になるようにする。
function wqAxisList(j, which) {
  const got = (j.axes && j.axes[which]) || [];
  if (got.length) return got.slice();
  const k = which === 'x' ? ['x1', 'x2'] : ['y1', 'y2'];
  const vals = [];
  for (const w of j.walls) vals.push(w[k[0]], w[k[1]]);
  const uniq = [...new Set(vals.map(v => Math.round(v * 1000) / 1000))]
    .sort((a, b) => a - b);
  const pre = which.toUpperCase();
  return uniq.map((v, i) => ({name: pre + (i + 1), v: v, auto: true}));
}

// 寸法線 (4分割の寸法を図の上と右に記入する)
function wqDim(x1, y1, x2, y2, tx, ty, txt, rot) {
  const t = 4;
  const vert = Math.abs(x2 - x1) < 0.5;
  const cap = (x, y) => (vert
    ? '<line x1="' + (x - t) + '" y1="' + y.toFixed(1) + '" x2="' + (x + t)
      + '" y2="' + y.toFixed(1) + '" stroke="#8a94a6"/>'
    : '<line x1="' + x.toFixed(1) + '" y1="' + (y - t) + '" x2="'
      + x.toFixed(1) + '" y2="' + (y + t) + '" stroke="#8a94a6"/>');
  return '<line x1="' + x1.toFixed(1) + '" y1="' + y1.toFixed(1) + '" x2="'
    + x2.toFixed(1) + '" y2="' + y2.toFixed(1) + '" stroke="#8a94a6"/>'
    + cap(x1, y1) + cap(x2, y2)
    + '<text x="' + tx.toFixed(1) + '" y="' + ty.toFixed(1)
    + '" font-size="9" fill="#556" text-anchor="middle"'
    + (rot ? ' transform="rotate(-90 ' + tx.toFixed(1) + ' '
             + ty.toFixed(1) + ')"' : '') + '>' + txt + '</text>';
}

function wqPlanSvg(j, st, g) {
  const ML = 48, MB = 24, MT = 40, MR = 58;
  const px = x => ML + (x - g.bx0) * g.s;
  const py = y => MT + (g.by1 - y) * g.s;
  const walls = j.walls.filter(w => w.story === st.no);
  const SW = g.w + ML + MR, SH = g.h + MT + MB;
  let s = '<svg viewBox="0 0 ' + SW + ' ' + SH + '" width="' + SW.toFixed(0)
    + '" height="' + SH.toFixed(0) + '" style="display:block;background:#fff;'
    + 'border:1px solid #ccd;border-radius:4px">';

  // 通り芯 (線は全部引き、符号は重なるものだけ間引く)
  let last = null;
  for (const a of wqAxisList(j, 'x')) {
    if (a.v < g.bx0 || a.v > g.bx1) continue;
    const X = px(a.v);
    s += '<line x1="' + X.toFixed(1) + '" y1="' + py(g.by1).toFixed(1)
      + '" x2="' + X.toFixed(1) + '" y2="' + py(g.by0).toFixed(1)
      + '" stroke="#e3e7ee"><title>' + esc(a.name + '  X=' + a.v.toFixed(3)
      + ' m') + '</title></line>';
    if (last !== null && Math.abs(X - last) < 24) continue;
    last = X;
    s += '<text x="' + X.toFixed(1) + '" y="' + (MT + g.h + 16).toFixed(1)
      + '" font-size="10" fill="#556" text-anchor="middle">'
      + esc(a.name) + '</text>';
  }
  last = null;
  for (const a of wqAxisList(j, 'y')) {
    if (a.v < g.by0 || a.v > g.by1) continue;
    const Y = py(a.v);
    s += '<line x1="' + px(g.bx0).toFixed(1) + '" y1="' + Y.toFixed(1)
      + '" x2="' + px(g.bx1).toFixed(1) + '" y2="' + Y.toFixed(1)
      + '" stroke="#e3e7ee"><title>' + esc(a.name + '  Y=' + a.v.toFixed(3)
      + ' m') + '</title></line>';
    if (last !== null && Math.abs(Y - last) < 13) continue;
    last = Y;
    s += '<text x="' + (ML - 5).toFixed(1) + '" y="' + (Y + 3.5).toFixed(1)
      + '" font-size="10" fill="#556" text-anchor="end">'
      + esc(a.name) + '</text>';
  }

  // 4分割法の側端部分。X方向の壁は Y軸で、Y方向の壁は X軸で4分割するので、
  // 帯が2組できる。どの範囲がどの側端かを塗りと符号で示す。
  const bb = st.bbox;
  if (bb) {
    const qx = (bb[1] - bb[0]) / 4, qy = (bb[3] - bb[2]) / 4;
    const band = (x0, y0, x1, y1, col) =>
      '<rect x="' + px(x0).toFixed(1) + '" y="' + py(y1).toFixed(1)
      + '" width="' + ((x1 - x0) * g.s).toFixed(1)
      + '" height="' + ((y1 - y0) * g.s).toFixed(1)
      + '" fill="' + col + '" opacity="0.08"/>';
    s += band(bb[0], bb[2], bb[1], bb[2] + qy, WQ_COL.X)      // X方向 側端①
      + band(bb[0], bb[3] - qy, bb[1], bb[3], WQ_COL.X)       // X方向 側端②
      + band(bb[0], bb[2], bb[0] + qx, bb[3], WQ_COL.Y)       // Y方向 側端①
      + band(bb[1] - qx, bb[2], bb[1], bb[3], WQ_COL.Y);      // Y方向 側端②
    const cX = (bb[0] + bb[1]) / 2, cY = (bb[2] + bb[3]) / 2;
    const lab = (x, y, t, col, rot) => {
      const X = px(x).toFixed(1), Y = py(y).toFixed(1);
      return '<text x="' + X + '" y="' + Y + '" font-size="10" fill="' + col
        + '" text-anchor="middle" opacity="0.85"'
        + (rot ? ' transform="rotate(-90 ' + X + ' ' + Y + ')"' : '')
        + '>' + t + '</text>';
    };
    s += lab(cX, bb[2] + qy / 2, 'X方向 側端①', WQ_COL.X, false)
      + lab(cX, bb[3] - qy / 2, 'X方向 側端②', WQ_COL.X, false)
      + lab(bb[0] + qx / 2, cY, 'Y方向 側端①', WQ_COL.Y, true)
      + lab(bb[1] - qx / 2, cY, 'Y方向 側端②', WQ_COL.Y, true);
    const q4 = [[bb[0] + qx, 'v'], [bb[1] - qx, 'v'],
                [bb[2] + qy, 'h'], [bb[3] - qy, 'h']];
    for (const [v, kind] of q4) {
      s += (kind === 'v'
        ? '<line x1="' + px(v).toFixed(1) + '" y1="' + py(bb[3]).toFixed(1)
          + '" x2="' + px(v).toFixed(1) + '" y2="' + py(bb[2]).toFixed(1) + '"'
        : '<line x1="' + px(bb[0]).toFixed(1) + '" y1="' + py(v).toFixed(1)
          + '" x2="' + px(bb[1]).toFixed(1) + '" y2="' + py(v).toFixed(1) + '"')
        + ' stroke="' + WQ_COL.Q4 + '" stroke-width="1"'
        + ' stroke-dasharray="8 3 2 3" opacity="0.75"/>';
    }
    // 4分割の寸法 (上に X、右に Y)
    const yd = MT - 20;
    const xs4 = [bb[0], bb[0] + qx, bb[1] - qx, bb[1]];
    for (let k = 0; k < 3; k++) {
      const a = px(xs4[k]), b = px(xs4[k + 1]);
      s += wqDim(a, yd, b, yd, (a + b) / 2, yd - 5,
                 (xs4[k + 1] - xs4[k]).toFixed(3), false);
    }
    const xd = ML + g.w + 26;
    const ys4 = [bb[2], bb[2] + qy, bb[3] - qy, bb[3]];
    for (let k = 0; k < 3; k++) {
      const a = py(ys4[k]), b = py(ys4[k + 1]);
      s += wqDim(xd, a, xd, b, xd + 12, (a + b) / 2,
                 (ys4[k + 1] - ys4[k]).toFixed(3), true);
    }
  }
  // 柱 (鉛直な線材) の位置。建物の平面サイズが分かるように □ で示す
  for (const c of (st.cols || [])) {
    s += '<rect x="' + (px(c[0]) - 3.5).toFixed(1) + '" y="'
      + (py(c[1]) - 3.5).toFixed(1) + '" width="7" height="7"'
      + ' fill="#5b6673" stroke="#39424e" stroke-width="0.8">'
      + '<title>' + esc('柱  X=' + c[0].toFixed(3) + ' / Y='
                        + c[1].toFixed(3) + ' m') + '</title></rect>';
  }
  // 外形と方位 (X右・Y上)
  const m = j.model;
  s += '<rect x="' + px(m.x0).toFixed(1) + '" y="' + py(m.y1).toFixed(1)
    + '" width="' + ((m.x1 - m.x0) * g.s).toFixed(1)
    + '" height="' + ((m.y1 - m.y0) * g.s).toFixed(1)
    + '" fill="none" stroke="#ccd" stroke-dasharray="4 3"/>'
    + '<g stroke="#889" fill="#889">'
    + '<line x1="' + (ML + 4) + '" y1="' + (MT + g.h - 4) + '" x2="'
    + (ML + 30) + '" y2="' + (MT + g.h - 4) + '"/>'
    + '<line x1="' + (ML + 4) + '" y1="' + (MT + g.h - 4) + '" x2="'
    + (ML + 4) + '" y2="' + (MT + g.h - 30) + '"/>'
    + '<text x="' + (ML + 33) + '" y="' + (MT + g.h - 1)
    + '" font-size="9" stroke="none">X</text>'
    + '<text x="' + ML + '" y="' + (MT + g.h - 33)
    + '" font-size="9" stroke="none">Y</text></g>';

  // 壁 (太さは一定。壁倍率は一覧とツールチップで見る)
  const LW = 5;
  walls.forEach(w => {
    const i = j.walls.indexOf(w);
    const col = !w.used ? WQ_COL.OFF
      : (w.dir === 'X' ? WQ_COL.X : (w.dir === 'Y' ? WQ_COL.Y : WQ_COL.D));
    // 算入しない壁は短い破線、勾配壁は長い破線で区別する
    const dash = !w.used ? ' stroke-dasharray="5 4"'
      : (w.sloped ? ' stroke-dasharray="13 4"' : '');
    const tip = st.name + '-' + w.name + '  ' + w.dir + '方向\n壁長 L='
      + w.L.toFixed(3) + ' m (水平投影) / 壁倍率 β=' + w.beta
      + (w.beta_capped ? ' (モデルは ' + w.beta_raw + '。上限で頭打ち)' : '')
      + '\n算入  X方向 L=' + w.lx.toFixed(3) + ' m, Lβ=' + w.lbx.toFixed(3)
      + ' m  /  Y方向 L=' + w.ly.toFixed(3) + ' m, Lβ=' + w.lby.toFixed(3)
      + ' m\n壁高 H=' + w.H.toFixed(3) + ' m / 勾配 '
      + w.slope.toFixed(1) + '°'
      + (w.sloped ? ' (勾配壁)' : ' (鉛直)')
      + '\n要素 ' + w.eles.join(' ')
      + (w.used ? '' : '\n算入しない: ' + w.why);
    // 開口で分かれた区間は別々の壁として渡ってくるので、ここは1本でよい。
    // 線端は butt (丸端だと線幅の半分だけ長く見えてしまう)
    s += '<line class="wqwall" data-w="' + i + '" x1="' + px(w.x1).toFixed(1)
      + '" y1="' + py(w.y1).toFixed(1) + '" x2="' + px(w.x2).toFixed(1)
      + '" y2="' + py(w.y2).toFixed(1) + '" stroke="' + col
      + '" stroke-width="' + LW + '" stroke-linecap="butt"'
      + dash + ' style="cursor:pointer"><title>' + esc(tip) + '</title></line>';
    // 壁符号は □で囲んだ数字。壁の中央から法線方向へ少しずらす
    const mx = (px(w.x1) + px(w.x2)) / 2, my = (py(w.y1) + py(w.y2)) / 2;
    const dx = px(w.x2) - px(w.x1), dy = py(w.y2) - py(w.y1);
    const len = Math.hypot(dx, dy) || 1;
    const off = LW / 2 + 9;
    const bx = mx + (-dy / len) * off, by = my + (dx / len) * off;
    const bw = 13 + 6 * Math.max(0, String(w.name).length - 1);
    s += '<g class="wqwall" data-w="' + i + '" style="cursor:pointer">'
      + '<rect x="' + (bx - bw / 2).toFixed(1) + '" y="' + (by - 7).toFixed(1)
      + '" width="' + bw + '" height="14" fill="#fff" stroke="' + col
      + '" stroke-width="1"/>'
      + '<text x="' + bx.toFixed(1) + '" y="' + (by + 3.7).toFixed(1)
      + '" font-size="10" fill="#223" text-anchor="middle">'
      + esc(w.name) + '</text>'
      + '<title>' + esc(tip) + '</title></g>';
  });
  return s + '</svg>';
}

function wqRenderPlan(j) {
  if (!j.walls.length) { $('wq_plan').innerHTML = ''; return; }
  const g = wqPlanScale(j.model);
  const named = !!((j.axes && j.axes.x && j.axes.x.length)
                   || (j.axes && j.axes.y && j.axes.y.length));
  let h = '<div class="hint" style="margin:4px 0">'
    + '<span style="color:' + WQ_COL.X + '">━ X方向の壁</span>　'
    + '<span style="color:' + WQ_COL.Y + '">━ Y方向の壁</span>　'
    + '<span style="color:' + WQ_COL.D + '">━ 斜め壁</span>　'
    + '<span style="color:' + WQ_COL.OFF + '">┄ 算入しない</span>　'
    + '<span style="color:' + WQ_COL.Q4 + '">─･─ 4分割の側端境界</span>　'
    + '薄い塗り = 側端部分 (青の帯=X方向の壁を数える範囲 / '
    + '緑の帯=Y方向の壁を数える範囲。一覧の「4分割法の振り分け」と対応)　'
    + '<span style="color:#5b6673">■ 柱 (鉛直な線材)</span>　'
    + '長い破線 = 勾配壁　□の数字 = 壁符号 (下の一覧と対応)<br>'
    + '図の上と右の寸法は 4分割した各部分の長さ [m]。'
    + '分割する平面の範囲は その階の壁と柱の外形です。'
    + (named ? '通り芯は mgt の *GROUP (グループ名) から取りました。'
             : '<b>mgt に通り芯のグループが無いため、壁の位置に X1,X2… / '
               + 'Y1,Y2… を自動で振っています。</b>')
    + '通り芯・柱にカーソルを載せると座標が出ます　/　縮尺 約 '
    + g.s.toFixed(0) + ' px per m (図が画面より広いときは横スクロール)</div>';
  for (const st of j.stories.slice().reverse()) {
    const ex = j.exist[st.no] || {X: 0, Y: 0};
    h += '<div class="pdfblock"><h4>' + esc(st.name)
      + '　<span class="hint">床レベル '
      + st.levels.map(v => fmtNum(v, 3)).join(' / ') + ' m　/　ΣLβ  X='
      + fmtNum(ex.X, 3) + ' m, Y=' + fmtNum(ex.Y, 3) + ' m</span></h4>'
      + '<div style="overflow-x:auto;max-width:100%">'
      + wqPlanSvg(j, st, g) + '</div></div>';
  }
  $('wq_plan').innerHTML = h;
}

// 図の壁をクリック -> 一覧の該当行を光らせる
document.addEventListener('click', ev => {
  const t = ev.target && ev.target.closest
    ? ev.target.closest('.wqwall') : null;
  if (!t) return;
  const row = $('wqrow_' + t.dataset.w);
  if (!row) return;
  document.querySelectorAll('#wq_walls tr.wqhit').forEach(r =>
    r.classList.remove('wqhit'));
  row.classList.add('wqhit');
  row.scrollIntoView({block: 'center', behavior: 'smooth'});
});

// ---------------- 壁の一覧 ----------------

// 4分割法で、その壁が側端部分へ何m算入されたか。中央部分しか無い壁は
// 4分割法に効かないので何も書かない。斜め壁は境界をまたぐことがあるので、
// 側端にかかった分だけを長さで出す。
function wqQ4Label(w) {
  const out = [];
  for (const d of ['X', 'Y']) {
    const q = w.q4 && w.q4[d];
    if (!q) continue;
    const p = [];
    if (q[0] > 0.0005) p.push('<b>側端①</b> ' + q[0].toFixed(2) + 'm');
    if (q[1] > 0.0005) p.push('<b>側端②</b> ' + q[1].toFixed(2) + 'm');
    if (p.length) out.push(d + ': ' + p.join(' + '));
  }
  return out.join('<br>');
}

function wqRenderWalls(j) {
  let h = '<style>#wq_walls tr.wqhit td{background:#fff3bf}</style>'
    + '<details class="pdflist" open><summary>耐力壁の一覧 ('
    + j.walls.length + ' 面) — クリックで開閉</summary>'
    + '<table class="res"><tr><th>壁符号</th><th>方向</th>'
    + '<th>高さ範囲</th><th>浮き[m]</th><th>L[m]</th>'
    + '<th>X方向<br>L[m]</th><th>Y方向<br>L[m]</th><th>β</th>'
    + '<th>X方向<br>Lβ[m]</th><th>Y方向<br>Lβ[m]</th>'
    + '<th>H[m]</th><th>階高/L</th>'
    + '<th>勾配[°]</th><th>4分割法の<br>振り分け</th>'
    + '<th>算入</th><th>要素</th></tr>';
  j.walls.forEach((w, i) => {
    h += '<tr id="wqrow_' + i + '"><td class="name">'
      + esc(w.story_name + '-' + w.name) + '</td>'
      + '<td>' + esc(w.dir) + '</td>'
      + '<td>' + esc(w.zrange) + '</td>'
      + '<td' + (w.gap > 0.001 ? ' class="warn"' : '') + '>'
      + fmtNum(w.gap, 3) + '</td>'
      + '<td>' + fmtNum(w.L, 3) + '</td>'
      + '<td' + (w.lx > 0 ? '' : ' class="na"') + '>'
      + (w.lx > 0 ? fmtNum(w.lx, 3) : '-') + '</td>'
      + '<td' + (w.ly > 0 ? '' : ' class="na"') + '>'
      + (w.ly > 0 ? fmtNum(w.ly, 3) : '-') + '</td>'
      + '<td' + (w.beta_capped ? ' class="warn"' : '') + '>' + w.beta
      + (w.beta_capped ? '<span class="cell-ele">モデル ' + w.beta_raw
                         + '</span>' : '') + '</td>'
      + '<td' + (w.lbx > 0 ? '' : ' class="na"') + '>'
      + (w.lbx > 0 ? fmtNum(w.lbx, 3) : '-') + '</td>'
      + '<td' + (w.lby > 0 ? '' : ' class="na"') + '>'
      + (w.lby > 0 ? fmtNum(w.lby, 3) : '-') + '</td>'
      + '<td>' + fmtNum(w.H, 3) + '</td>'
      + '<td' + (isFinite(w.aspect) && w.aspect > 5 ? ' class="warn"' : '')
      + '>' + (isFinite(w.aspect) ? fmtNum(w.aspect, 2) : '-') + '</td>'
      + '<td' + (w.sloped ? ' class="warn"' : '') + '>'
      + fmtNum(w.slope, 1) + '</td>'
      + '<td class="name">' + wqQ4Label(w) + '</td>'
      + '<td' + (w.used ? '>する'
                        : ' class="na">しない'
                          + (w.why ? ' (' + esc(w.why) + ')' : ''))
      + '</td>'
      + '<td class="name"><span class="cell-ele">' + w.eles.join(' ')
      + '</span></td></tr>';
  });
  h += '</table></details>';
  $('wq_walls').innerHTML = h;
}

// ---------------- 検定 ----------------

async function wqCheck() {
  if (!WQ) {
    setMsg('wq_msg', '先に「モデルの壁を読み込み」を実行してください。',
           'msg-err');
    return;
  }
  wqSaveStore();
  const p = wqReadParams('wq_msg');
  if (!p) return;
  const by = {};
  for (const s of (WQ_DEF || [])) by[s.name] = s;
  const stories = WQ.stories.map(st => {
    const s = by[st.name] || {};
    return {no: st.no, w: parseFloat(s.w) || 0,
            af_x: parseFloat(s.af_x) || 0, af_y: parseFloat(s.af_y) || 0};
  });
  if (!stories.some(s => s.w > 0)) {
    setMsg('wq_msg', '層重量を入力してください。', 'msg-err');
    return;
  }
  const body = Object.assign(p, {
    stories: stories,
    imp: wqNum('wq_imp', 1.0),
    z: wqNum('wq_z', 1.0),
    tc: parseFloat($('wq_tc').value),
    c0: wqNum('wq_c0', 0.2),
    qa: wqNum('wq_qa', 1.96),
    wind_k: wqNum('wq_windk', 50),
    height: wqVal('wq_h'),
    alpha: wqNum('wq_alpha', 1.0),
    project: ($('wq_project') || {}).value || '',
  });
  try {
    const j = await api('/api/wallqty_check', body);
    WQ = j;
    wqRenderExist(j);
    wqRenderStories();
    wqRenderPlan(j);
    wqRenderWalls(j);
    wqRenderResult(j);
    const bad = [];
    if (j.ng) bad.push('壁量の検定 NG ' + j.ng + ' 件');
    if (j.ng4) bad.push('4分割法 NG ' + j.ng4 + ' 件');
    setMsg('wq_msg', (bad.length ? bad.join(' / ') + '。'
                                 : '壁量・4分割法ともすべて OK です。')
           + notesHtml(j.notes), bad.length ? 'msg-err' : 'msg-ok');
  } catch (e) {
    setMsg('wq_msg', esc(e.message), 'msg-err');
  }
}

function wqRenderResult(j) {
  const p = j.params;
  let h = '<div class="pdfblock"><h4>壁量検定表</h4>'
    + '<div class="hint">I=' + p.imp + ' (地震力のみ)、Z=' + p.z + '、地盤種別 Tc='
    + wqSoil(p.tc) + '、C<sub>0</sub>=' + p.c0 + '、h='
    + fmtNum(p.h_bldg, 3) + 'm、α=' + (p.alpha !== undefined ? p.alpha : 1)
    + '、T=h(0.02+0.01α)=' + p.T.toFixed(3) + 's、Rt=' + p.Rt.toFixed(3)
    + '、全重量 ΣW='
    + fmtNum(p.w_total, 1) + 'kN、壁倍率1あたり ' + p.qa + 'kN/m、風圧力係数 '
    + p.wind_k + 'cm/m<sup>2</sup> (床面+' + p.wind_base_h
    + 'm より上の見付面積)</div>'
    + '<table class="res"><tr>'
    + '<th>階</th><th>Wi[kN]</th><th>ΣWi[kN]</th><th>αi</th><th>Ai</th>'
    + '<th>Ci</th><th>Qi[kN]</th><th>必要壁量<br>(地震)[m]</th>'
    + '<th>方向</th><th>存在壁量<br>ΣLβ[m]</th>'
    + '<th>見付面積<br>[m<sup>2</sup>]</th>'
    + '<th>必要壁量<br>(風)[m]</th><th>必要壁量<br>(採用)[m]</th>'
    + '<th>支配</th><th>検定比</th><th>判定</th><th>余裕[m]</th></tr>';
  for (const r of j.rows) {
    for (const d of ['x', 'y']) {
      const v = r[d];
      h += '<tr>';
      if (d === 'x') {
        h += '<td class="name" rowspan="2">' + esc(r.name) + '</td>'
          + '<td rowspan="2">' + fmtNum(r.w, 1) + '</td>'
          + '<td rowspan="2">' + fmtNum(r.wsum, 1) + '</td>'
          + '<td rowspan="2">' + r.alpha.toFixed(3) + '</td>'
          + '<td rowspan="2">' + r.ai.toFixed(3) + '</td>'
          + '<td rowspan="2">' + r.ci.toFixed(3) + '</td>'
          + '<td rowspan="2">' + fmtNum(r.qi, 2) + '</td>'
          + '<td rowspan="2">' + fmtNum(r.req_eq, 3) + '</td>';
      }
      h += '<td>' + d.toUpperCase() + '</td>'
        + '<td>' + fmtNum(v.exist, 3) + '</td>'
        + '<td>' + fmtNum(v.af, 2) + '</td>'
        + '<td>' + fmtNum(v.req_w, 3) + '</td>'
        + '<td>' + fmtNum(v.req, 3) + '</td>'
        + '<td>' + esc(v.gov) + '</td>'
        + '<td class="' + ratioClass(v.ratio) + '">' + fmtRatio(v.ratio)
        + '</td>'
        + '<td class="' + (v.ok ? '' : 'ng') + '">' + (v.ok ? 'OK' : 'NG')
        + '</td>'
        + '<td>' + fmtNum(v.margin, 3) + '</td></tr>';
    }
  }
  h += '</table></div>' + wqQuarterHtml(j);
  if (j.pdf) {
    h += '<div class="pdfblock"><h4>検討書 (PDF)</h4>'
      + pdfEmbedHtml(j.pdf, false) + '</div>';
  }
  if (j.csv && j.csv.length) {
    h += '<div class="pdfblock"><div class="row">CSV: ' + j.csv.map(c =>
      '<a class="dl" href="' + c.url + '">' + esc(c.name) + '</a>').join('')
      + openFolderBtn(j.out_dir) + '</div></div>';
  }
  $('wq_out').innerHTML = h;
}

// 4分割法 (令46条 / 平12建告1352号)
function wqQuarterHtml(j) {
  const any = j.rows.some(r => r.x.q4 || r.y.q4);
  if (!any) return '';
  let h = '<div class="pdfblock"><h4>4分割法 (令46条 / 平12建告1352号)</h4>'
    + '<div class="hint">X方向の壁は Y軸で、Y方向の壁は X軸で平面を4分割し、'
    + '両端の1/4 (側端部分) で 壁量充足率 = 側端の存在壁量 / 側端の必要壁量 '
    + 'を求めます。<b>両側の充足率がともに1.0超</b>、または'
    + '<b>壁率比 (小さい方÷大きい方) が0.5以上</b>で OK。<br>'
    + '側端部分の必要壁量は「その階の<b>地震力による</b>必要壁量 × 1/4」'
    + 'としています (奥行きが一定の平面なら側端は床面積の1/4)。L形など'
    + '側端の床面積が1/4でない平面では充足率の絶対値がずれますが、'
    + '判定の主軸である壁率比は両側を同じ値で割るため影響を受けません。'
    + '分割に使う平面の範囲は、その階の壁の外形です (図の一点鎖線)。</div>'
    + '<table class="res"><tr><th>階</th><th>方向</th><th>分割軸</th>'
    + '<th>側端① 範囲</th><th>側端① 存在壁量[m]</th><th>側端① 充足率</th>'
    + '<th>側端② 範囲</th><th>側端② 存在壁量[m]</th><th>側端② 充足率</th>'
    + '<th>中央部分[m]</th><th>側端の<br>必要壁量[m]</th><th>壁率比</th>'
    + '<th>判定</th><th>理由</th></tr>';
  for (const r of j.rows) {
    for (const d of ['x', 'y']) {
      const q = r[d].q4;
      if (!q) continue;
      const cell = s => '<td>' + esc(s.range) + '</td><td>'
        + fmtNum(s.exist, 3) + '</td><td class="'
        + (s.ratio === null || s.ratio === undefined ? 'na'
           : (s.ratio < 1.0 ? 'warn' : '')) + '">'
        + (s.ratio === null || s.ratio === undefined ? '-'
           : s.ratio.toFixed(2)) + '</td>';
      h += '<tr><td class="name">' + esc(r.name) + '</td>'
        + '<td>' + d.toUpperCase() + '</td><td>' + q.axis + '</td>'
        + cell(q.side[0]) + cell(q.side[1])
        + '<td>' + fmtNum(q.mid, 3) + '</td>'
        + '<td>' + fmtNum(q.req_side, 3) + '</td>'
        + '<td class="' + (q.wr === null || q.wr === undefined ? 'na'
                           : (q.wr < 0.5 ? 'ng' : '')) + '">'
        + (q.wr === null || q.wr === undefined ? '-' : q.wr.toFixed(3))
        + '</td>'
        + '<td class="' + (q.ok ? '' : 'ng') + '">' + (q.ok ? 'OK' : 'NG')
        + '</td><td class="name">' + esc(q.why) + '</td></tr>';
    }
  }
  return h + '</table></div>';
}
