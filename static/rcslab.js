'use strict';

// RCスラブの検討書タブ。app.js の共通関数 ($ / api / setMsg / notesHtml /
// pdfListHtml / esc / openFolderBtn) を借りる。
// app.js には手を入れず、このファイルは _tab_rcslab.html から読み込む。

const RS_DEFAULT_CASES = [
  ['TL', '1(ST)'], ['TL+KX', '21(ST)'], ['TL-KX', '22(ST)'],
  ['TL+KY', '23(ST)'], ['TL-KY', '24(ST)']];
let RS = null;          // 直近の取得結果 {summary, spec, defaults, out_dir}

function rsBody(extra) {
  return Object.assign({mgt_path: $('mgt_path') ? $('mgt_path').value.trim()
                                                : ''}, extra || {});
}

// ---------------------------------------------------------------- 荷重ケース表
function rsCaseRow(label, name) {
  const tr = document.createElement('tr');
  tr.innerHTML = '<td class="rs-kind"></td>' +
    '<td><input type="text" class="rs-label" style="width:110px" value="' +
    esc(label || '') + '"></td>' +
    '<td><input type="text" class="rs-name" style="width:110px" value="' +
    esc(name || '') + '"></td>' +
    '<td><button class="sub" onclick="rsDelCase(this)">削除</button></td>';
  $('rs_cases_tbl').querySelector('tbody').appendChild(tr);
  rsKinds();
}
function rsKinds() {
  $('rs_cases_tbl').querySelectorAll('td.rs-kind').forEach((td, i) => {
    td.textContent = i === 0 ? '長期' : '短期';
  });
}
function rsAddCase() { rsCaseRow('', ''); }
function rsDelCase(btn) { btn.closest('tr').remove(); rsKinds(); }
function rsSetCases(cases) {
  $('rs_cases_tbl').querySelector('tbody').innerHTML = '';
  cases.forEach(c => rsCaseRow(c[0], c[1]));
}
function rsCases() {
  return Array.from($('rs_cases_tbl').querySelectorAll('tbody tr')).map(tr => ({
    label: tr.querySelector('.rs-label').value.trim(),
    name: tr.querySelector('.rs-name').value.trim(),
  })).filter(c => c.name);
}

// ---------------------------------------------------------------- 読み込み
function rsKind() {
  const r = document.querySelector('input[name="rs_kind"]:checked');
  return r ? r.value : 'thick';
}

// 前回の入力 (荷重ケース・txt・対象) を戻す
async function rsRestore() {
  if (!$('mgt_path').value.trim()) return;
  try {
    const j = await api('/api/rcslab_spec', rsBody());
    const sp = j.spec || {};
    if (sp.cases && sp.cases.length) rsSetCases(sp.cases.map(c => [c.label, c.name]));
    if (sp.txt_path && !$('rs_txt_path').value) $('rs_txt_path').value = sp.txt_path;
    if (sp.kind) {
      const r = document.querySelector('input[name="rs_kind"][value="' + sp.kind + '"]');
      if (r) r.checked = true;
    }
    await rsCandidates(sp.kind === rsKind() ? sp.ids : null);
  } catch (e) { /* 前回の入力が無いだけ */ }
}

async function rsCandidates(checkedIds) {
  const box = $('rs_pick_box');
  if (!$('mgt_path').value.trim()) {
    box.innerHTML = '<span class="hint">画面上部の「mgtファイル」欄に mgt を' +
      '指定してください</span>';
    return;
  }
  try {
    const j = await api('/api/rcslab_candidates', rsBody({kind: rsKind()}));
    if (!j.items.length) {
      box.innerHTML = '<span class="hint">板要素を含む' +
        (j.kind === 'group' ? 'グループ' : '厚さ') + 'がありません</span>';
      return;
    }
    const want = Array.isArray(checkedIds) ? new Set(checkedIds.map(String)) : null;
    box.innerHTML = j.items.map(it => {
      const on = want ? want.has(String(it.id)) : false;
      const lab = j.kind === 'group'
        ? esc(it.name) + ' <span class="hint">(板 ' + it.n + ' 枚: ' +
          esc(it.detail) + ')</span>'
        : 'ID ' + esc(it.id) + ' ' + esc(it.name) + ' <span class="hint">(t=' +
          it.t.toFixed(0) + 'mm、板 ' + it.n + ' 枚)</span>';
      return '<label><input type="checkbox" class="rs_pick" value="' +
        esc(it.id) + '"' + (on ? ' checked' : '') + '> ' + lab + '</label>';
    }).join('');
  } catch (e) { box.innerHTML = '<div class="err">' + esc(e.message) + '</div>'; }
}

async function rsFile() {
  if (!$('mgt_path').value.trim()) {
    setMsg('rs_fetch_msg', '画面上部の「mgtファイル」欄に mgt を指定して' +
      'ください (形状・厚さ・グループを読みます)。', 'err');
    return;
  }
  const ids = checkedVals('rs_pick');
  if (!ids.length) {
    setMsg('rs_fetch_msg', '検討対象を1つ以上選んでください (「一覧を読み' +
      '込む」で表示)。', 'err');
    return;
  }
  try {
    const j = await api('/api/rcslab_file', rsBody({
      txt_path: $('rs_txt_path').value.trim(), cases: rsCases(),
      kind: rsKind(), ids: ids}));
    rsShow(j, 'rs_fetch_msg', 'txt から読み込みました');
  } catch (e) { setMsg('rs_fetch_msg', esc(e.message), 'err'); }
}

async function rsLoad() {
  try {
    const j = await api('/api/rcslab_load', rsBody());
    rsShow(j, 'rs_fetch_msg', '前回の読み込みデータを使います');
  } catch (e) { setMsg('rs_fetch_msg', esc(e.message), 'err'); }
}

function rsShow(j, msgId, head) {
  RS = j;
  const s = j.summary;
  setMsg(msgId, esc(head) + ' (txt ' + esc(s.fetched || '') + ')' +
    openFolderBtn(j.out_dir) + notesHtml(j.notes), 'ok');
  if (j.spec && j.spec.title) $('rs_title').value = j.spec.title;
  if (j.spec && j.spec.file_name) $('rs_file').value = j.spec.file_name;
  if (j.spec && j.spec.font) $('rs_font').value = j.spec.font;
  const saved = {};
  ((j.spec && j.spec.sections) || []).forEach(x => { if (x.key) saved[x.key] = x; });
  const caseOpts = s.cases.map(c =>
    '<option value="' + esc(c.name) + '">' + esc(c.label) + ' (' +
    esc(c.name) + ')</option>').join('');
  let h = '';
  s.targets.forEach(t => {
    const sv = saved[t.key] || {};
    const use = sv.use === undefined ? t.n > 0 : sv.use;
    const shortOn = sv.short === undefined ? true : sv.short;
    const pre = sv.pre === undefined ? '' : sv.pre;
    const post = sv.post === undefined ? j.defaults.post : sv.post;
    const title = t.kind === 'group' ? 'グループ ' + esc(t.id)
                                     : '厚さID ' + esc(t.id);
    h += '<fieldset class="rs-sec" data-key="' + esc(t.key) +
      '" style="min-width:0">' +
      '<legend><label><input type="checkbox" class="rs-use"' +
      (use ? ' checked' : '') + '> ' + title + '</label></legend>' +
      '<div class="row">断面名: <input type="text" class="rs-name" value="' +
      esc(sv.name || t.name) + '" style="width:100px">' +
      '<span class="hint">厚さ ' + t.t.toFixed(0) + 'mm・板要素 ' + t.n +
      ' 枚</span>　<label><input type="checkbox" class="rs-short"' +
      (shortOn ? ' checked' : '') + '> 短期ケースと最大応力のまとめも載せる' +
      '</label></div>' +
      '<div class="row">前文 (&lt;TL&gt; の前、任意):<br>' +
      '<textarea class="rs-pre" rows="2" style="width:min(720px,95%)">' +
      esc(pre) + '</textarea>' +
      ' <button class="sub" onclick="rsLongOnly(this)">長期のみの定型文</button>' +
      '</div>' +
      '<div class="row">本文 (長期の図の後):<br>' +
      '<textarea class="rs-post" rows="3" style="width:min(720px,95%)">' +
      esc(post) + '</textarea></div>' +
      rsCheckHtml(t, sv.check || {}) +
      '<div class="row">図の確認: <select class="rs-pvcase">' + caseOpts +
      '</select> <button class="sub" onclick="rsPreview(this,\'M\')">曲げ</button>' +
      ' <button class="sub" onclick="rsPreview(this,\'V\')">せん断</button></div>' +
      '<div class="rs-pv"></div></fieldset>';
  });
  $('rs_secs_box').innerHTML = h;
}

// 長期のみの断面 (階段の踊り場など): 短期を外して前文を入れる
function rsLongOnly(btn) {
  const fs = btn.closest('.rs-sec');
  fs.querySelector('.rs-short').checked = false;
  const pre = fs.querySelector('.rs-pre');
  if (!pre.value.trim()) {
    pre.value = '(ここにスラブの位置づけ) であるため、' +
      RS.defaults.long_only_pre;
  }
  const post = fs.querySelector('.rs-post');
  post.value = '次項に上記の応力に対する検討結果を示す.';
}

async function rsPreview(btn, kind) {
  const fs = btn.closest('.rs-sec');
  const box = fs.querySelector('.rs-pv');
  try {
    const j = await api('/api/rcslab_preview', rsBody({
      key: fs.dataset.key,
      case: fs.querySelector('.rs-pvcase').value, kind: kind}));
    box.innerHTML = '<img src="' + j.url + '" style="max-width:min(760px,100%);' +
      'border:1px solid #ccd;background:#fff">';
  } catch (e) { box.innerHTML = '<div class="err">' + esc(e.message) + '</div>'; }
}

function rsSections() {
  return Array.from(document.querySelectorAll('#rs_secs_box .rs-sec')).map(fs => ({
    key: fs.dataset.key,
    use: fs.querySelector('.rs-use').checked,
    name: fs.querySelector('.rs-name').value.trim(),
    short: fs.querySelector('.rs-short').checked,
    pre: fs.querySelector('.rs-pre').value,
    post: fs.querySelector('.rs-post').value,
    check: rsCheckVals(fs),
  }));
}

// ---------------------------------------------------------------- 検定条件
// 設計用応力は自動 (長期=先頭ケース、短期=最大応力のまとめ)。入力はしない
const RS_BARS = ['D10', 'D13', 'D16', 'D19', 'D22', 'D25', 'D29', 'D32'];

function rsSel(cls, opts, val) {
  return '<select class="' + cls + '">' + opts.map(o =>
    '<option' + (String(o) === String(val) ? ' selected' : '') + '>' +
    esc(o) + '</option>').join('') + '</select>';
}

function rsNum(cls, val, w, ph) {
  return '<input type="text" class="' + cls + '" style="width:' + (w || 56) +
    'px" value="' + esc(val === undefined || val === null ? '' : val) + '"' +
    (ph !== undefined ? ' placeholder="' + esc(ph) + '"' : '') + '>';
}

// 保存済みの 'D16@200' を径とピッチに分ける (旧形式の復元用)
function rsBarSplit(spec) {
  const m = String(spec || '').toUpperCase().match(/(D\d+)[^@]*@(\d+)/);
  return m ? [m[1], m[2]] : ['D13', '200'];
}

function rsBarHtml(key, spec) {
  const [d, p] = rsBarSplit(spec);
  return rsSel('rs-ck-' + key + 'd', RS_BARS, d) + ' @ ' +
    rsNum('rs-ck-' + key + 'p', p, 44) + 'mm';
}

function rsCheckHtml(t, c) {
  const on = c.enabled === undefined ? true : c.enabled;
  const single = !!c.single;
  const dirName = 'rs_dir_' + t.key.replace(/[^A-Za-z0-9]/g, '_');
  const main = c.dir === 'main';
  return '<div class="row"><label><input type="checkbox" class="rs-ck-on"' +
    (on ? ' checked' : '') + '> 検定ページを載せる</label>' +
    '<span class="hint">設計用応力は応力図の最大・最小を自動で使います' +
    ' (長期=' + esc(RS && RS.summary.cases[0] ? RS.summary.cases[0].label : '') +
    '、短期=最大応力のまとめ)</span></div>' +
    '<div class="row">' +
    rsSel('rs-ck-single', ['ダブル配筋', 'シングル配筋'],
          single ? 'シングル配筋' : 'ダブル配筋') +
    '　Fc ' + rsNum('rs-ck-fc', c.Fc === undefined ? 24 : c.Fc, 44) +
    ' N/mm² ' + rsSel('rs-ck-ctype', ['普通', '軽量1種', '軽量2種'],
                      c.ctype || '普通') +
    '　鉄筋 ' + rsSel('rs-ck-sd', ['SD295', 'SD345', 'SD390', 'SD490'],
                     c.sd || 'SD295') +
    '　板厚 ' + rsNum('rs-ck-t', c.t, 50, t.t.toFixed(0)) + ' mm</div>' +
    '<div class="row">検討方向 ' +
    '<label class="inline"><input type="radio" class="rs-ck-dir" name="' +
    dirName + '" value="main"' + (main ? ' checked' : '') +
    '> 主筋方向 (短辺・外側の段)</label>' +
    '<label class="inline"><input type="radio" class="rs-ck-dir" name="' +
    dirName + '" value="dist"' + (main ? '' : ' checked') +
    '> 配力筋方向 (長辺・内側の段)</label></div>' +
    '<div class="row"><span class="rs-uplab">' +
    (single ? '配筋' : '上端筋') + '</span> ' + rsBarHtml('up', c.bar_up) +
    '　かぶり ' + rsNum('rs-ck-cup', c.cover_up === undefined ? 30 : c.cover_up,
                     36) + 'mm' +
    '<span class="rs-dn"' + (single ? ' style="display:none"' : '') + '>' +
    '　　下端筋 ' + rsBarHtml('dn', c.bar_dn) +
    '　かぶり ' + rsNum('rs-ck-cdn', c.cover_dn === undefined ? 30 : c.cover_dn,
                     36) + 'mm</span></div>';
}

function rsCheckVals(fs) {
  const v = cls => { const e = fs.querySelector('.' + cls); return e ? e.value.trim() : ''; };
  return {
    enabled: fs.querySelector('.rs-ck-on').checked,
    dir: (fs.querySelector('.rs-ck-dir:checked') || {}).value || 'dist',
    single: v('rs-ck-single') === 'シングル配筋',
    Fc: v('rs-ck-fc'), ctype: v('rs-ck-ctype'), sd: v('rs-ck-sd'),
    t: v('rs-ck-t'),
    bar_up: v('rs-ck-upd') + '@' + v('rs-ck-upp'), cover_up: v('rs-ck-cup'),
    bar_dn: v('rs-ck-dnd') + '@' + v('rs-ck-dnp'), cover_dn: v('rs-ck-cdn'),
  };
}

document.addEventListener('change', ev => {
  if (ev.target.classList && ev.target.classList.contains('rs-ck-single')) {
    const fs = ev.target.closest('.rs-sec');
    const single = ev.target.value === 'シングル配筋';
    fs.querySelector('.rs-dn').style.display = single ? 'none' : '';
    fs.querySelector('.rs-uplab').textContent = single ? '配筋' : '上端筋';
  }
});

function rsPct(x) {
  if (x === null || x === undefined) return '-';
  const s = (x * 100).toFixed(1) + '%';
  return x > 1 ? '<b style="color:#b91c1c">' + s + '</b>' : s;
}

async function rsCheck() {
  const secs = rsSections();
  if (!secs.length) {
    setMsg('rs_msg', '先に取得またはデータの読み込みをしてください。', 'err');
    return;
  }
  try {
    const j = await api('/api/rcslab_check', rsBody({sections: secs}));
    if (!j.rows.length) {
      setMsg('rs_msg', '「検定ページを載せる」断面がありません。', 'err');
      return;
    }
    let h = '<table class="res" style="width:auto"><thead><tr><th>断面</th>' +
      '<th>期間</th><th>M+</th><th>M−</th><th>Q</th><th>正曲げ</th>' +
      '<th>負曲げ</th><th>せん断</th><th>構造規定</th><th>判定</th></tr>' +
      '</thead><tbody>';
    j.rows.forEach(r => {
      [['長期', r.long], ['短期', r.short]].forEach(([lab, c]) => {
        if (!c) return;
        h += '<tr><td class="name">' + esc(r.name) + '</td><td>' + lab +
          '</td><td>' +
          c['M+'].toFixed(1) + '</td><td>' + c['M-'].toFixed(1) + '</td><td>' +
          c.Q.toFixed(1) + '</td><td>' + rsPct(c.r_pos) + '</td><td>' +
          rsPct(c.r_neg) + '</td><td>' + rsPct(c.r_q) + '</td><td>' +
          (c.rules_ok ? 'OK' : '<b style="color:#b91c1c">NG</b> ' +
           esc(c.rules_ng.join('・'))) + '</td><td>' +
          (c.ok ? 'OK' : '<b style="color:#b91c1c">NG</b>') + '</td></tr>';
      });
    });
    h += '</tbody></table>';
    setMsg('rs_msg', '検定しました (検定比 = 必要量/配筋量、Q/許容せん断力)。',
           'ok');
    $('rs_out').innerHTML = h;
  } catch (e) { setMsg('rs_msg', esc(e.message), 'err'); }
}

async function rsPdf() {
  const secs = rsSections();
  if (!secs.length) {
    setMsg('rs_msg', '先に「MIDASから取得」または「前回の取得データを' +
      '読み込む」を押してください。', 'err');
    return;
  }
  try {
    const j = await api('/api/rcslab_pdf', rsBody({
      sections: secs, title: $('rs_title').value.trim(),
      file_name: $('rs_file').value.trim(), font: $('rs_font').value}));
    setMsg('rs_msg', '検討書PDFを作成しました。' + openFolderBtn(j.out_dir) +
      notesHtml(j.notes), 'ok');
    $('rs_out').innerHTML = pdfListHtml(j.pdfs);
  } catch (e) { setMsg('rs_msg', esc(e.message), 'err'); }
}

// app.js ($ など) はタブの後に読み込まれるので、DOM 構築後に初期化する
document.addEventListener('DOMContentLoaded', function rsInit() {
  rsSetCases(RS_DEFAULT_CASES);
  const btn = document.querySelector('button[data-tab="rcslab"]');
  let done = false;
  if (btn) {
    btn.addEventListener('click', () => { if (!done) { done = true; rsRestore(); } });
  }
  // mgt を差し替えたら対象の一覧を読み直す (タブを開いた後だけ)
  const mg = $('mgt_path');
  if (mg) mg.addEventListener('change', () => { if (done) rsRestore(); });
});
