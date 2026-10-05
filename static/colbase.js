'use strict';
// 鉄骨露出柱脚の検定タブ (colbase)。共通ヘルパは app.js のものを
// グローバル参照で借りる: $ / api / setMsg / esc / notesHtml /
// pdfListHtml / openFolderBtn / pickFile / CTYPE_LABEL。

let CB = null;   // 対象読込の結果 {groups, cases, saved, ab_types, ab_areas}

// 仕様表の列定義: [キー, 見出し, 幅(px), 種類]
const CB_COLS = [
  ['bp_D', 'BP せい<br>(H方向)', 72, 'num'],
  ['bp_B', 'BP 幅<br>(B方向)', 72, 'num'],
  ['bp_t', 'BP厚', 60, 'num'],
  ['bp_F', 'BP<br>F値', 64, 'num'],
  ['ab_type', 'AB種類', 90, 'abtype'],
  ['ab_d', '呼び径<br>M', 46, 'abd'],
  ['ab_Ab', '軸部<br>Ab', 80, 'num'],
  ['ab_Abe', 'ねじ部<br>ae', 72, 'num'],
  ['ab_F', 'AB<br>F値', 64, 'num'],
  ['ductile', '伸び<br>能力', 34, 'chk'],
  ['n_all', '全<br>本数', 52, 'int'],
  ['nt_H', '引張側<br>本数H', 52, 'int'],
  ['dtl_H', '縁〜芯<br>H', 60, 'num'],
  ['nt_B', '引張側<br>本数B', 52, 'int'],
  ['dtl_B', '縁〜芯<br>B', 60, 'num'],
  ['hole', '孔径', 60, 'num'],
  ['lb', '有効長<br>lb', 68, 'num'],
  ['la', '定着長<br>la', 68, 'num'],
  ['Fc', '基礎<br>Fc', 56, 'fc'],
  ['n_ratio', 'ヤング<br>係数比 n', 56, 'num'],
  ['fnd_D', '柱形せい<br>(H方向)', 72, 'num'],
  ['fnd_B', '柱形幅<br>(B方向)', 72, 'num'],
  ['fnd_h', '柱形立上<br>り高さ', 72, 'num'],
  ['anc_type', '定着の形式', 110, 'anctype'],
  ['anc_Dp', '定着金物<br>Dp', 64, 'num'],
];
const CB_DIAMS = [16, 20, 22, 24, 27, 30, 33, 36, 39, 42, 45, 48];

function cbRouteChanged() {
  const r = $('cb_route').value;
  if (r === '1-2') $('cb_gamma').value = '1.67';
  else if (r === '2' || r === '3') $('cb_gamma').value = '2.0';
  $('cb_gamma').disabled = (r === '1-1');
}

// ---------------- 対象読込 ----------------

function cbStressPath() {
  const v = $('cb_beam_stress_path').value.trim();
  if (v) return v;
  return $('beam_stress_path') ? $('beam_stress_path').value.trim() : '';
}

function cbCell(g, col, v) {
  const [key, , w, kind] = col;
  const a = ' data-g="' + esc(g) + '" data-k="' + key + '"';
  const val = (v === null || v === undefined) ? '' : v;
  if (kind === 'abtype') {
    return '<select class="cbin"' + a + ' onchange="cbAbChanged(this)">' +
      Object.keys(CB.ab_types).map(t => '<option value="' + t + '"' +
        (t === val ? ' selected' : '') + '>' + t + '</option>').join('') +
      '</select>';
  }
  if (kind === 'abd') {
    return '<select class="cbin"' + a + ' onchange="cbAbChanged(this)">' +
      CB_DIAMS.map(d => '<option value="' + d + '"' +
        (+d === +val ? ' selected' : '') + '>' + d + '</option>')
        .join('') + '</select>';
  }
  if (kind === 'anctype') {
    return '<select class="cbin"' + a + ' onchange="cbAncChanged(this)">' +
      [['each', '定着金物 個別 (Dp角)'], ['plate', '定着金物 列を連結 (幅Dp)'],
       ['hook', 'フック']].map(o =>
        '<option value="' + o[0] + '"' + (o[0] === (val || 'each') ?
          ' selected' : '') + '>' + o[1] + '</option>').join('') +
      '</select>';
  }
  if (kind === 'fc') {
    return '<input type="number" class="cbin"' + a + ' value="' + val +
      '" style="width:' + w + 'px" onchange="cbFcChanged(this)">';
  }
  if (kind === 'chk') {
    return '<input type="checkbox" class="cbin"' + a +
      (+val ? ' checked' : '') + '>';
  }
  return '<input type="number" class="cbin"' + a + ' value="' + val +
    '" style="width:' + w + 'px"' + (kind === 'int' ? ' step="1"' : '') +
    '>';
}

function cbRowFor(g) {
  return document.querySelector('#cb_targets_box tr[data-g="' +
    CSS.escape(g) + '"]');
}

function cbSetVal(g, key, v) {
  const el = document.querySelector('#cb_targets_box .cbin[data-g="' +
    CSS.escape(g) + '"][data-k="' + key + '"]');
  if (!el) return;
  if (el.type === 'checkbox') el.checked = !!(+v);
  else el.value = (v === null || v === undefined) ? '' : v;
}

// コンクリートに対する鋼材のヤング係数比 n (RC規準の値)
function cbNratio(fc) {
  if (fc <= 27) return 15;
  if (fc <= 36) return 13;
  if (fc <= 48) return 11;
  if (fc <= 60) return 9;
  return 7;
}

// 基礎 Fc を入力したらヤング係数比 n を自動で入れる (n は手で変更可)
function cbFcChanged(el) {
  const fc = parseFloat(el.value);
  if (isNaN(fc) || fc <= 0) return;
  cbSetVal(el.dataset.g, 'n_ratio', cbNratio(fc));
}

// 定着の形式がフックなら定着金物寸法 Dp は使わない (入力欄を無効化)
function cbAncChanged(el) {
  const dp = document.querySelector('#cb_targets_box .cbin[data-g="' +
    CSS.escape(el.dataset.g) + '"][data-k="anc_Dp"]');
  if (!dp) return;
  dp.disabled = el.value === 'hook';
  dp.title = dp.disabled ? 'フックのため定着金物寸法は使いません' : '';
}

// AB種類・径を変えたら F値・伸び能力・断面積の既定値を入れ直す
function cbAbChanged(el) {
  const g = el.dataset.g;
  const get = k => document.querySelector('#cb_targets_box .cbin[data-g="' +
    CSS.escape(g) + '"][data-k="' + k + '"]').value;
  const t = get('ab_type'), d = +get('ab_d');
  const info = CB.ab_types[t];
  const abe = CB.ab_areas[d] || Math.PI * d * d / 4 * 0.75;
  const ab = t.startsWith('ABR') ? abe : Math.PI * d * d / 4;
  cbSetVal(g, 'ab_F', info.F);
  cbSetVal(g, 'ductile', info.ductile ? 1 : 0);
  cbSetVal(g, 'ab_Ab', ab.toFixed(1));
  cbSetVal(g, 'ab_Abe', abe);
  if (el.dataset.k === 'ab_d') cbSetVal(g, 'hole', d + 5);
}

async function cbRead() {
  setMsg('cb_msg', '', '');
  $('cb_result').innerHTML = '';
  try {
    const j = await api('/api/colbase_read', {
      mgt_path: $('mgt_path').value,
      beam_stress_path: cbStressPath()});
    CB = j;
    const saved = {};
    (j.saved || []).forEach(s => { saved[s.group] = s; });
    // ① 検討対象のグループを選ぶ (柱脚を含むグループの一覧)
    let h = '<div class="row"><b>① 検討対象のグループを選ぶ</b>' +
      ' <button class="sub" onclick="cbCheckAll(true)">すべて選択</button>' +
      ' <button class="sub" onclick="cbCheckAll(false)">すべて解除</button>' +
      '</div><table class="res" id="cb_group_tbl" style="width:auto">' +
      '<thead><tr><th>検討</th><th>グループ</th><th>柱脚数</th>' +
      '<th>柱断面</th></tr></thead><tbody>';
    j.groups.forEach(g => {
      const on = saved[g.name] ? ' checked' : '';
      h += '<tr><td><input type="checkbox" class="cbuse" value="' +
        esc(g.name) + '"' + on + '></td>' +
        '<td style="white-space:nowrap">' + esc(g.name) + '</td><td>' +
        g.n + '本</td>' +
        '<td style="text-align:left">' + g.secs.map(s => esc(s.name) +
          ' <span class="hint">(' + s.shape + ' F' + s.F + ')</span>')
          .join('<br>') + '</td></tr>';
    });
    h += '</tbody></table>';
    if (j.saved_path) {
      h += '<div class="row"><span class="hint">保存済みの仕様 ' +
        esc(j.saved_path) + ' を読み込みました (該当グループを選択済み)' +
        '</span></div>';
    }
    // ② ①で選んだグループだけ仕様の入力欄を出す (選択を外した行は
    //    非表示にするだけで入力値は保持する)
    h += '<div class="row" style="margin-top:12px"><b>② 選んだグループの' +
      '柱脚仕様を入力する</b></div>' +
      '<div class="row"><button class="sub" onclick="cbCopyFirst()">' +
      '最初の行の仕様を他の行へコピー</button>' +
      ' <button class="sub" onclick="cbSaveCsv()">仕様をCSVに保存</button>' +
      ' <button class="sub" onclick="cbShowFigs()">仕様図を表示</button>' +
      ' <label class="sub" style="cursor:pointer">CSVから読込 ' +
      '<input type="file" accept=".csv,.txt" onchange="cbLoadCsv(this)">' +
      '</label> <span id="cb_csv_msg"></span></div>' +
      '<div id="cb_spec_empty" class="row"><span class="hint">①で検討する' +
      'グループを選ぶと、ここに入力欄が出ます</span></div>' +
      '<div id="cb_spec_wrap" style="overflow-x:auto">' +
      '<table class="res" id="cb_spec_tbl" style="width:auto">' +
      '<thead><tr><th>グループ</th>' +
      CB_COLS.map(c => '<th>' + c[1] + '</th>').join('') +
      '</tr></thead><tbody>';
    j.groups.forEach(g => {
      const sp = saved[g.name] || g.default;
      h += '<tr data-g="' + esc(g.name) + '">' +
        '<td style="white-space:nowrap">' + esc(g.name) + '</td>' +
        CB_COLS.map(c => '<td>' + cbCell(g.name, c, sp[c[0]]) + '</td>')
          .join('') + '</tr>';
    });
    h += '</tbody></table></div>' +
      '<div class="row"><span class="hint">寸法は mm、F値・Fc は N/mm²。' +
      'H方向 = 柱断面のせい方向 (強軸)、B方向 = 幅方向。' +
      '「縁〜芯」= BP縁から引張側アンカーボルト芯まで。引張側本数0 は' +
      'その方向にABが無い (全本数が中心に集中とみなす)。' +
      'ねじ部有効断面積の既定値は並目ねじ (JIS B 1082) の値、ABR は軸部も' +
      '同じ値にしています。製品カタログの値に直してください。' +
      '孔径・定着長・柱形・立上り高さ・Dp は空欄可 (該当する検討を省略)。' +
      'コーン状破壊は定着長 la・柱形寸法・定着金物 Dp から計算します ' +
      '(定着金物: 個別=ボルトごとの Dp角の座金・定着板、連結=引張側の列を' +
      'つないだ幅 Dp のアンカープレート。フックは定着金物なしとして' +
      'ボルト芯から半径 la で計算)</span></div>' +
      '<div id="cb_fig_box"></div>';
    $('cb_targets_box').innerHTML = h;
    document.querySelectorAll('#cb_targets_box .cbin[data-k="anc_type"]')
      .forEach(cbAncChanged);
    document.querySelectorAll('#cb_targets_box input.cbuse')
      .forEach(cb => cb.addEventListener('change', cbSyncRows));
    cbSyncRows();
    cbListenFigRedraw();
    if (j.cases) {
      let hc = '<table class="res" style="max-width:640px"><thead><tr>' +
        '<th>荷重ケース</th><th>種別</th><th>ケース名 (検討書に記載)' +
        '</th></tr></thead><tbody>';
      j.cases.forEach(c => {
        hc += '<tr><td>ケース ' + c.no + '</td><td>' +
          '<select class="cbctype" data-no="' + c.no + '">' +
          ['L', 'H', 'S', 'M'].map(t =>
            '<option value="' + t + '"' +
            (c.type === t ? ' selected' : '') + '>' +
            CTYPE_LABEL[t] + '</option>').join('') +
          '</select></td>' +
          '<td><input type="text" class="cbcname" data-no="' + c.no +
          '" value="' + esc(c.name) + '"></td></tr>';
      });
      $('cb_cases_box').innerHTML = hc + '</tbody></table>';
    } else {
      $('cb_cases_box').innerHTML = '<span class="hint">応力ファイルが' +
        '未指定のためケース一覧を表示できません。応力ファイルを指定して' +
        '「対象読込」し直してください</span>';
    }
    setMsg('cb_msg', '柱脚 ' + j.n_bases + ' 本、柱脚を含むグループ ' +
      j.groups.length + ' 件を読み込みました。①で検討する' +
      'グループを選び、②で仕様を入力してください。', 'msg-ok');
    if (j.notes && j.notes.length) {
      $('cb_msg').innerHTML += notesHtml(j.notes);
    }
  } catch (e) { setMsg('cb_msg', esc(e.message), 'msg-err'); }
}

// 表の1グループ分の仕様を読む
function cbSpecOf(g) {
  const sp = {group: g};
  document.querySelectorAll('#cb_targets_box .cbin[data-g="' +
    CSS.escape(g) + '"]').forEach(el => {
    sp[el.dataset.k] = el.type === 'checkbox' ? (el.checked ? 1 : 0)
      : el.value;
  });
  return sp;
}

// 仕様図を表示中なら、仕様表の入力を変えたときに描き直す。
// cb_targets_box 自体は対象読込のたびに作り直されないので登録は1回だけ
let CB_FIG_TIMER = null;
function cbListenFigRedraw() {
  const box = $('cb_targets_box');
  if (box.dataset.figListen) return;
  box.dataset.figListen = '1';
  box.addEventListener('change', ev => {
    const t = ev.target;
    if (!t.classList.contains('cbin') && !t.classList.contains('cbuse')) {
      return;
    }
    if (!$('cb_fig_box') || !$('cb_fig_box').innerHTML) return;
    clearTimeout(CB_FIG_TIMER);
    CB_FIG_TIMER = setTimeout(cbShowFigs, 400);
  });
}

// 入力中の仕様で仕様図 (平面図・断面図) を描いて表示する
async function cbShowFigs() {
  const box = $('cb_fig_box');
  try {
    const j = await api('/api/colbase_figure', {
      mgt_path: $('mgt_path').value, specs: cbCollectSpecs(true)});
    let h = '<div class="row"><b>仕様図</b> <span class="hint">' +
      '入力した仕様どおりの縮尺 (代表柱 = グループ内で断面積が最大の柱)。' +
      '検定実行時に検討書にも取り込まれます</span></div>';
    j.figs.forEach(f => {
      h += '<div style="margin:4px 0 10px"><img src="' + f.url +
        '&t=' + Date.now() +
        '" alt="' + esc(f.group) + ' の仕様図" style="max-width:100%;' +
        'border:1px solid #dde;background:#fff;display:block"></div>';
    });
    if (j.notes && j.notes.length) h += notesHtml(j.notes);
    box.innerHTML = h;
  } catch (e) {
    box.innerHTML = '<div class="msg-err">' + esc(e.message) + '</div>';
  }
}

function cbCollectSpecs(onlyChecked) {
  const out = [];
  document.querySelectorAll('#cb_targets_box input.cbuse').forEach(cb => {
    if (!onlyChecked || cb.checked) out.push(cbSpecOf(cb.value));
  });
  return out;
}

// ①の選択に合わせて②の入力行を出し入れする
function cbSyncRows() {
  const on = new Set([...document.querySelectorAll(
    '#cb_targets_box input.cbuse')].filter(cb => cb.checked)
    .map(cb => cb.value));
  document.querySelectorAll('#cb_spec_tbl tbody tr').forEach(tr => {
    tr.style.display = on.has(tr.dataset.g) ? '' : 'none';
  });
  $('cb_spec_wrap').style.display = on.size ? '' : 'none';
  $('cb_spec_empty').style.display = on.size ? 'none' : '';
}

function cbCheckAll(v) {
  document.querySelectorAll('#cb_targets_box input.cbuse')
    .forEach(cb => { cb.checked = v; });
  cbSyncRows();
}

function cbApply(specs) {
  let n = 0;
  specs.forEach(sp => {
    if (!cbRowFor(sp.group)) return;
    CB_COLS.forEach(c => { if (c[0] in sp) cbSetVal(sp.group, c[0], sp[c[0]]); });
    const u = document.querySelector('#cb_targets_box input.cbuse[value="' +
      CSS.escape(sp.group) + '"]');
    if (u) u.checked = true;
    n++;
  });
  cbSyncRows();
  return n;
}

function cbCopyFirst() {
  const specs = cbCollectSpecs(true);
  if (specs.length < 2) {
    alert('①でコピー先のグループも選んでください。');
    return;
  }
  const src = specs[0];
  specs.slice(1).forEach(sp => {
    const c = Object.assign({}, src, {group: sp.group});
    cbApply([c]);
  });
}

async function cbSaveCsv() {
  try {
    const j = await api('/api/colbase_spec_save', {
      mgt_path: $('mgt_path').value, specs: cbCollectSpecs(true)});
    $('cb_csv_msg').innerHTML = '<a href="' + j.url + '">' +
      esc(j.path) + '</a> に保存しました (チェックしたグループのみ)';
  } catch (e) { $('cb_csv_msg').textContent = '保存エラー: ' + e.message; }
}

async function cbLoadCsv(input) {
  if (!input.files.length) return;
  try {
    // Excel で保存し直した CSV は Shift_JIS になるため UTF-8 失敗時に切替
    const buf = await input.files[0].arrayBuffer();
    let text;
    try {
      text = new TextDecoder('utf-8', {fatal: true}).decode(buf);
    } catch (_e) {
      text = new TextDecoder('shift_jis').decode(buf);
    }
    const j = await api('/api/colbase_spec_parse', {text: text});
    const n = cbApply(j.specs);
    $('cb_csv_msg').textContent = input.files[0].name + ' から ' + n +
      ' グループの仕様を読み込みました' +
      (n < j.specs.length ? ' (モデルに無いグループ ' +
        (j.specs.length - n) + ' 件は無視)' : '');
  } catch (e) { $('cb_csv_msg').textContent = '読込エラー: ' + e.message; }
  input.value = '';
}

function cbCollectCases() {
  const out = [];
  document.querySelectorAll('#cb_cases_box select.cbctype').forEach(s => {
    const name = document.querySelector(
      '#cb_cases_box input.cbcname[data-no="' + s.dataset.no + '"]');
    out.push({no: parseFloat(s.dataset.no), type: s.value,
              name: name ? name.value : ('C' + s.dataset.no)});
  });
  return out;
}

// ---------------- 検定実行 ----------------

function cbOx(ok) {
  if (ok === null || ok === undefined) return '<td>-</td>';
  return ok ? '<td>OK</td>' : '<td class="ng">NG</td>';
}

function cbR(v) {
  return '<td' + (v > 1.0 + 1e-9 ? ' class="ng"' : '') + '>' +
    v.toFixed(2) + '</td>';
}

const CB_STATE = {0: 'N=0', 1: '全面圧縮', 2: '引張なし', 3: '片側引張',
                  4: '両側引張', 5: '圧縮なし'};

async function cbRun() {
  setMsg('cb_msg', '検定中...', 'msg-note');
  $('cb_result').innerHTML = '';
  try {
    const route = $('cb_route').value;
    const j = await api('/api/colbase_run', {
      mgt_path: $('mgt_path').value,
      beam_stress_path: cbStressPath(),
      case_types: cbCollectCases(),
      specs: cbCollectSpecs(true),
      project: $('cb_project').value.trim(),
      params: {route: route, gamma: parseFloat($('cb_gamma').value),
               bolt_method: $('cb_bolt_method').value,
               do_bp: $('cb_do_bp').checked,
               sheared_edge: $('cb_edge').value === '1',
               }});
    let h = '<div class="row"><b>グループ別の結果</b></div>' +
      '<table class="res" style="max-width:900px"><thead><tr>' +
      '<th>グループ</th><th>柱脚数</th><th>許容応力度 最大比</th>' +
      '<th>NG</th><th>γ倍応力 最大比</th><th>NG</th>' +
      '<th>保有耐力接合でない</th><th>基礎コン NG</th>' +
      '<th>KBS H (kN・m/rad)</th><th>KBS B</th></tr></thead><tbody>';
    const kb = v => v ? (Math.round(v[0]) === Math.round(v[1])
      ? cbInt(v[0]) : cbInt(v[0]) + '〜' + cbInt(v[1])) : '-';
    j.summary.forEach(s => {
      h += '<tr><td>' + esc(s.group) + '</td><td>' + s.n + '</td>' +
        cbR(s.r_allow) + '<td' + (s.ng_allow ? ' class="ng"' : '') + '>' +
        s.ng_allow + '</td>' +
        (route === '1-1' ? '<td>-</td><td>-</td><td>-</td><td>-</td>' :
          cbR(s.r_ult) + '<td' + (s.ng_ult ? ' class="ng"' : '') + '>' +
          s.ng_ult + '</td><td>' + s.n_hoyu_ng + '</td>' +
          '<td' + (s.ng_fnd ? ' class="ng"' : '') + '>' + s.ng_fnd +
          '</td>') +
        '<td>' + kb(s.kbs_H) + '</td><td>' + kb(s.kbs_B) + '</td></tr>';
    });
    h += '</tbody></table>';
    // 告示1456号
    if (j.kokuji.length) {
      h += '<div class="row"><b>仕様規定 (平12建告1456号)</b>' +
        '<span class="hint">「除外可」は許容応力度計算を行えば適用除外 ' +
        '(規定を満たさなくても判定は「-」)。ハ (定着) は定着部の' +
        'コーン状破壊の検討を満たした場合にただし書きで除外' +
        '</span></div>' +
        '<table class="res" style="max-width:900px"><thead><tr>' +
        '<th>グループ</th><th>項目</th><th>規定</th><th>設計</th>' +
        '<th>判定</th><th>適用</th></tr></thead><tbody>';
      j.kokuji.forEach(k => k.items.forEach(it => {
        h += '<tr><td>' + esc(k.group) + '</td><td>' + esc(it.item) +
          '</td><td>' + esc(it.req) + '</td><td>' + esc(it.have) + '</td>' +
          (it.mark === 'OK' ? '<td>OK</td>' : it.mark === 'NG' ?
            '<td class="ng">NG</td>' : '<td>-</td>') +
          '<td>' + (it.basis === 'ただし書き' ? '除外 (ただし書き)'
            : it.exempt ? '除外可' : '必須' +
              (it.why ? ' <span class="hint">(' + esc(it.why) + ')</span>'
                : '')) +
          '</td></tr>';
      }));
      h += '</tbody></table>';
    }
    // 回転剛性 (グループ・柱断面・方向ごと。検討書の (5) と同じ内容)
    const kseen = new Set();
    let hk = '';
    j.rows.forEach(r => {
      const key = r.group + '\u0000' + r.sec + '\u0000' + r.dir;
      if (kseen.has(key)) return;
      kseen.add(key);
      hk += '<tr><td>' + esc(r.group) + '</td><td>' + esc(r.sec) +
        '</td><td>' + r.dir + '</td><td>' + r.kbs.nt + '</td>' +
        '<td>' + r.kbs.dt.toFixed(0) + '</td><td>' + r.kbs.dc.toFixed(0) +
        '</td><td style="text-align:right">' + cbInt(r.kbs.K) +
        '</td></tr>';
    });
    if (hk) {
      h += '<div class="row"><b>柱脚の回転剛性 KBS</b>' +
        '<span class="hint">解析モデルの柱脚ばね値と一致しているか確認して' +
        'ください</span></div>' +
        '<table class="res" style="max-width:760px"><thead><tr>' +
        '<th>グループ</th><th>柱断面</th><th>方向</th><th>nt</th>' +
        '<th>dt (mm)</th><th>dc (mm)</th><th>KBS (kN・m/rad)</th>' +
        '</tr></thead><tbody>' + hk + '</tbody></table>';
    }
    // 許容応力度
    h += '<div class="row"><b>許容応力度の検定</b> (柱脚・方向ごとに' +
      '検定比最大のケース)</div><table class="res"><thead><tr>' +
      '<th>グループ</th><th>要素</th><th>柱</th><th>方向</th>' +
      '<th>ケース</th><th>N</th><th>M</th><th>Q</th><th>状態</th>' +
      '<th>xn</th><th>σc/fc</th><th>σt/fts</th><th>τ/fs</th>' +
      '<th>BP σb/fb\'</th><th>判定</th></tr></thead><tbody>';
    j.rows.forEach(r => {
      const a = r.allow;
      if (!a) return;
      h += '<tr><td>' + esc(r.group) + '</td><td>' + r.ele + '</td>' +
        '<td>' + esc(r.sec) + '</td><td>' + r.dir + '</td>' +
        '<td>' + esc(a.case) + '</td>' +
        '<td>' + (a.N / 1e3).toFixed(1) + '</td>' +
        '<td>' + (a.M / 1e6).toFixed(1) + '</td>' +
        '<td>' + (a.Q / 1e3).toFixed(1) + '</td>' +
        '<td>' + CB_STATE[a.st.case] + '</td>' +
        '<td>' + (a.st.xn == null ? '-' : a.st.xn.toFixed(0)) + '</td>' +
        cbR(a.r_c) + cbR(a.bolt.r_t) +
        (a.bolt.fric ? '<td>摩擦</td>' : cbR(a.bolt.r_s)) +
        (a.bp ? cbR(a.bp.r) : '<td>-</td>') +
        cbOx(a.r <= 1.0 + 1e-9) + '</tr>';
    });
    h += '</tbody></table><div class="row"><span class="hint">単位 kN・' +
      'kN・m・mm。N は圧縮が正。「摩擦」は Q≦0.4(T+N) でABがせん断を' +
      '負担しないもの</span></div>';
    // γ倍応力
    const ult = j.rows.filter(r => r.ult);
    if (ult.length) {
      h += '<div class="row"><b>保有耐力接合の判定等 (γ=' +
        j.params.gamma + ')</b></div><table class="res"><thead><tr>' +
        '<th>グループ</th><th>要素</th><th>方向</th><th>ケース</th>' +
        '<th>N*</th><th>M*</th><th>Q*</th><th>Mu (My)</th><th>αMpc</th>' +
        '<th>保有耐力接合</th><th>M*/Mu</th><th>Qu (Qy)</th>' +
        '<th>Q*/Qu</th><th>σc/Fc・T/Pb</th><th>判定</th></tr></thead><tbody>';
      ult.forEach(r => {
        const u = r.ult;
        const brittle = !!u.st;
        h += '<tr><td>' + esc(r.group) + '</td><td>' + r.ele + '</td>' +
          '<td>' + r.dir + '</td><td>' + esc(u.case) + '</td>' +
          '<td>' + (u.N / 1e3).toFixed(1) + '</td>' +
          '<td>' + (u.M / 1e6).toFixed(1) + '</td>' +
          '<td>' + (u.Q / 1e3).toFixed(1) + '</td>' +
          '<td>' + ((brittle ? u.My_eff : u.u.Mu) / 1e6).toFixed(1) +
          '</td><td>' + (u.alpha * u.Mpc / 1e6).toFixed(1) + '</td>' +
          '<td' + (u.hoyu ? '' : ' class="ng"') + '>' +
          (u.hoyu ? '○' : '×') + '</td>' +
          (brittle || u.hoyu ? '<td>-</td>' : cbR(u.r_m)) +
          '<td>' + ((brittle ? u.Qy : u.u.Qu) / 1e3).toFixed(1) + '</td>' +
          cbR(brittle ? u.Q / u.Qy : u.r_q) +
          (brittle ? '<td>' + (u.st.sc / cbFc(j, r)).toFixed(2) + '・' +
            (u.st.T / u.Pb).toFixed(2) + '</td>' : '<td>-</td>') +
          cbOx(u.ok) + '</tr>';
      });
      h += '</tbody></table><div class="row"><span class="hint">' +
        '伸び能力なしの柱脚は Mu 欄に min(Mu, My)、Qu 欄に Qy を表示。' +
        (route === '3' ? 'ルート3: 保有耐力接合 × の柱脚は1階の Ds を' +
          '0.05割増し (伸び能力なしは種別D相当) — 判定欄は Qu>Q* のみ。' : '') +
        '</span></div>';
    }
    // コーン状破壊
    const cone = j.rows.filter(r => r.fnd && (r.fnd.cone_t || r.fnd.cone_s));
    if (cone.length) {
      h += '<div class="row"><b>コーン状破壊</b> (技術基準 付1.2-30、' +
        'φ1=0.6)</div><table class="res"><thead><tr>' +
        '<th>グループ</th><th>要素</th><th>方向</th>' +
        '<th>Ac (×10³mm²)</th><th>Tp</th><th>Tu</th><th>判定</th>' +
        '<th>c</th><th>Acv (×10³mm²)</th><th>Qc</th><th>QD</th>' +
        '<th>判定</th></tr></thead><tbody>';
      cone.forEach(r => {
        const t = r.fnd.cone_t, c = r.fnd.cone_s;
        h += '<tr><td>' + esc(r.group) + '</td><td>' + r.ele + '</td>' +
          '<td>' + r.dir + '</td>' +
          (t ? '<td>' + (t.Ac / 1e3).toFixed(0) + '</td><td>' +
            (t.Tp / 1e3).toFixed(1) + '</td><td>' +
            (t.skip ? '省略' : (t.Tu / 1e3).toFixed(1)) + '</td>' +
            (t.skip ? '<td>-</td>' : cbOx(t.ok))
            : r.fnd.cone_t_na === 'no_row'
              ? '<td colspan="4">対象外 (引張側の列なし)</td>'
              : '<td>-</td><td>-</td><td>-</td><td>-</td>') +
          (c ? '<td>' + c.c.toFixed(0) + '</td><td>' +
            (c.Acv / 1e3).toFixed(0) + '</td><td>' +
            (c.Qc / 1e3).toFixed(1) + '</td><td>' +
            (c.skip ? '省略' : (c.QD / 1e3).toFixed(1)) + '</td>' +
            (c.skip ? '<td>-</td>' : cbOx(c.ok))
            : '<td>-</td><td>-</td><td>-</td><td>-</td><td>-</td>') +
          '</tr>';
      });
      h += '</tbody></table><div class="row"><span class="hint">単位 kN・' +
        'mm。定着: Tu=nt・Ab・F < Tp (ABに引張が生じなければ省略)。' +
        '列状せん断: Qc > QD=γ倍せん断力 (ABがせん断を負担しなければ' +
        '省略)。柱形の外 (基礎梁上面) へのコーンの広がりは無視' +
        '</span></div>';
    }
    // TeX・PDF
    h += '<div class="row"><b>TeXソース (計算書組込み用)</b>: ' +
      j.tex_files.map(f => '<a href="' + f.url + '" download="' + f.name +
        '">' + esc(f.name) + '</a>').join('　') +
      '　<a href="' + j.spec_csv.url + '">' + esc(j.spec_csv.name) +
      '</a> (柱脚仕様)</div>';
    if (j.pdf) h += pdfListHtml([j.pdf]);
    h += openFolderBtn(j.out_dir);
    if (j.preview_pngs && j.preview_pngs.length) {
      h += '<div class="row"><b>プレビュー (コンパイル結果)</b></div>';
      j.preview_pngs.forEach(u => {
        h += '<img src="' + u + '&t=' + Date.now() +
          '" style="max-width:100%;border:1px ' +
          'solid #bbb;margin:4px 0;display:block">';
      });
    }
    $('cb_result').innerHTML = h;
    // 横に長い表は枠内で横スクロールさせる
    $('cb_result').querySelectorAll('table.res').forEach(t => {
      const w = document.createElement('div');
      w.style.overflowX = 'auto';
      t.parentNode.insertBefore(w, t); w.appendChild(t);
    });
    setMsg('cb_msg', '検定が完了しました。', 'msg-ok');
    if (j.notes && j.notes.length) {
      $('cb_msg').innerHTML += notesHtml(j.notes);
    }
  } catch (e) { setMsg('cb_msg', esc(e.message), 'msg-err'); }
}

// 回転剛性などの大きい数値を整数・3桁区切りで表示する
function cbInt(v) {
  return Math.round(v).toLocaleString('ja-JP');
}

function cbFc(j, r) {
  const sp = cbCollectSpecs(true).find(s => s.group === r.group);
  return sp ? +sp.Fc : 1;
}
