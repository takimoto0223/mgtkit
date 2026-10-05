'use strict';

// 断面符号図 (MIDAS風) タブ。app.js の共通関数 ($ / api / setMsg / notesHtml /
// pdfListHtml / checkAll / checkedVals / esc / openFolderBtn / groupChecks)
// を借りる。グループ一覧は app.js の loadMgt() から capfigGroups() で埋める。

function capfigGroups(groups) {
  groupChecks(groups, 'floor', 'cffloor', 'cf_floors_box', false);
  groupChecks(groups, 'vertical', 'cfaxis', 'cf_axes_box', false);
  groupChecks(groups, 'vertical', 'cfgrid', 'cf_grids_box', true);
  groupChecks(groups, 'floor', 'cflevel', 'cf_levels_box', true);
  cfLevelsRestore();
  capfigSections();
}

// 軸組図に書く階レベル (水平グループ) の選択を mgt ごとに記憶する
const CF_LEVEL_KEY = 'mgtkit_capfig_levels';

function cfLevelsRestore() {
  const mgt = $('mgt_path').value.trim();
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(CF_LEVEL_KEY) || '{}')[mgt]; }
  catch (e) { saved = null; }
  if (!Array.isArray(saved)) return;  // 未保存なら既定 (全選択) のまま
  const on = new Set(saved);
  document.querySelectorAll('input.cflevel')
    .forEach(cb => { cb.checked = on.has(cb.value); });
}

function cfLevelsSave() {
  const mgt = $('mgt_path').value.trim();
  if (!mgt) return;
  try {
    const st = JSON.parse(localStorage.getItem(CF_LEVEL_KEY) || '{}');
    st[mgt] = checkedVals('cflevel');
    localStorage.setItem(CF_LEVEL_KEY, JSON.stringify(st));
  } catch (e) { /* 保存できなくても選択はそのまま使える */ }
}

// ---- 「_」以降も書く断面の表 ----------------------------------------
// 1行1断面。チェックセル (class="cfcell", data-row) を縦ドラッグで範囲一括、
// Shift+クリックで直前にクリックしたセルからの範囲を同じ状態にする。
// 選択は mgt のパスごとに localStorage へ保存 (表示の便利機能。無くても動く)。
const CF_FULL_KEY = 'mgtkit_capfig_fullsecs';
let CF_DRAG = null;
let CF_LAST = null;

function cfStoreGet() {
  try { return JSON.parse(localStorage.getItem(CF_FULL_KEY) || '{}'); }
  catch (e) { return {}; }
}

function cfSave() {
  const mgt = $('mgt_path').value.trim();
  if (!mgt) return;
  try {
    const st = cfStoreGet();
    st[mgt] = capfigFullSecs();
    localStorage.setItem(CF_FULL_KEY, JSON.stringify(st));
  } catch (e) { /* 保存できなくても選択はそのまま使える */ }
}

function capfigFullSecs() {
  return Array.from(document.querySelectorAll('#cf_secs_box input.cffull'))
    .filter(cb => cb.checked).map(cb => parseInt(cb.value, 10));
}

// 1行の表記例を今のチェック状態に合わせて更新
function cfRowPreview(tr) {
  const cb = tr.querySelector('input.cffull');
  const out = tr.querySelector('.cfout');
  const shortOn = $('cf_short').checked;
  out.textContent = (!shortOn || cb.checked) ? tr.dataset.name : tr.dataset.short;
  tr.style.color = (shortOn && !cb.checked) ? '' : '#1d4e89';
}

function capfigSecState() {
  const shortOn = $('cf_short').checked;
  document.querySelectorAll('#cf_secs_box tbody tr').forEach(cfRowPreview);
  const box = $('cf_secs_box');
  if (box) box.style.opacity = shortOn ? '' : '0.55';
}

function capfigFullAll(on) {
  document.querySelectorAll('#cf_secs_box input.cffull')
    .forEach(cb => { cb.checked = on; });
  capfigSecState();
  cfSave();
}

async function capfigSections() {
  const mgt = $('mgt_path').value.trim();
  const box = $('cf_secs_box');
  if (!mgt || !box) return;
  try {
    const r = await fetch('/api/capfig_sections', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mgt_path: mgt})});
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || r.statusText);
    const saved = new Set((cfStoreGet()[mgt] || []).map(Number));
    if (!j.sections.length) {
      box.innerHTML = '<span class="hint">線材の断面がありません</span>';
      return;
    }
    let h = '<table class="res" style="max-width:720px;user-select:none">' +
      '<thead><tr><th>番号</th><th>断面名 (mgt)</th><th>要素数</th>' +
      '<th>「_」以降も書く</th><th>図での表記</th></tr></thead><tbody>';
    j.sections.forEach((s, i) => {
      const noUs = s.name === s.short;
      h += '<tr data-name="' + esc(s.name) + '" data-short="' +
        esc(s.short) + '"><td>' + s.no + '</td><td class="name">' +
        esc(s.name) + '</td><td>' + s.n_elems + '</td>' +
        '<td class="cfcell" data-row="' + i + '" style="cursor:pointer"' +
        (noUs ? ' title="名前に「_」がありません"' : '') + '>' +
        '<input type="checkbox" class="cffull" value="' + s.no + '"' +
        (saved.has(s.no) ? ' checked' : '') +
        ' style="pointer-events:none"></td>' +
        '<td class="name cfout"></td></tr>';
    });
    box.innerHTML = h + '</tbody></table>';
    CF_LAST = null;
    capfigSecState();
  } catch (e) {
    box.innerHTML = '<span class="hint">断面一覧を読めませんでした: ' +
      esc(e.message) + '</span>';
  }
}

function cfHighlight() {
  document.querySelectorAll('#cf_secs_box .cfcell').forEach(td => {
    let on = false;
    if (CF_DRAG) {
      const r = +td.dataset.row;
      on = r >= Math.min(CF_DRAG.a, CF_DRAG.b) &&
           r <= Math.max(CF_DRAG.a, CF_DRAG.b);
    }
    td.style.background = on ? '#dbe6f4' : '';
  });
}

function cfApplyRange(a, b, target) {
  const lo = Math.min(a, b), hi = Math.max(a, b);
  document.querySelectorAll('#cf_secs_box .cfcell').forEach(td => {
    const r = +td.dataset.row;
    if (r < lo || r > hi) return;
    td.querySelector('input').checked = target;
    cfRowPreview(td.parentElement);
  });
  cfSave();
}

window.addEventListener('DOMContentLoaded', () => {
  const lb = $('cf_levels_box');
  if (lb) lb.addEventListener('change', cfLevelsSave);
  const box = $('cf_secs_box');
  if (!box) return;
  box.addEventListener('mousedown', ev => {
    const td = ev.target.closest('.cfcell');
    if (!td) return;
    ev.preventDefault();
    const row = +td.dataset.row;
    const cb = td.querySelector('input');
    if (ev.shiftKey && CF_LAST !== null) {
      // 直前にクリックしたセルと同じ状態で範囲を塗る
      const last = box.querySelector('.cfcell[data-row="' + CF_LAST + '"] input');
      cfApplyRange(CF_LAST, row, last ? last.checked : true);
      CF_LAST = row;
      return;
    }
    CF_DRAG = {a: row, b: row, target: !cb.checked};
    cfHighlight();
  });
  box.addEventListener('mouseover', ev => {
    if (!CF_DRAG) return;
    const td = ev.target.closest('.cfcell');
    if (!td) return;
    CF_DRAG.b = +td.dataset.row;
    cfHighlight();
  });
  document.addEventListener('mouseup', () => {
    if (!CF_DRAG) return;
    const {a, b, target} = CF_DRAG;
    CF_DRAG = null;
    cfApplyRange(a, b, target);
    CF_LAST = b;
    cfHighlight();
  });
});

function capfigBody() {
  const pt = parseFloat($('cf_pt').value);
  const lim = parseFloat($('cf_limit').value);
  return {
    mgt_path: $('mgt_path').value.trim(),
    floors: checkedVals('cffloor'),
    axes: checkedVals('cfaxis'),
    grids: checkedVals('cfgrid'),
    columns: $('cf_columns').checked,
    hplates: $('cf_hplates').checked,
    hplate_nolabel: $('cf_hplate_nolabel').checked,
    levels: $('cf_levels').checked,
    level_names: checkedVals('cflevel'),
    grid: $('cf_grid').checked,
    nodes: $('cf_nodes').checked,
    solid: $('cf_solid').checked,
    legend: $('cf_legend').checked,
    footer: $('cf_footer').checked,
    short_name: $('cf_short').checked,
    full_secs: capfigFullSecs(),
    merge: $('cf_merge').checked,
    mono: $('cf_mono').checked,
    paper_size: parseInt($('cf_paper').value, 10),
    orient: $('cf_orient').value,
    label_pt: isNaN(pt) ? 6 : pt,
    limit_sec_no: isNaN(lim) ? 9000 : lim,
  };
}

function capfigReady(body) {
  if (!body.mgt_path) {
    setMsg('cf_msg', 'mgtファイルのパスを入力してください。', 'msg-err');
    return false;
  }
  if (!body.floors.length && !body.axes.length) {
    setMsg('cf_msg', '伏図または軸組図のグループを1つ以上選択してください。' +
      '（共通欄の「読み込み」を押すと一覧が出ます）', 'msg-err');
    return false;
  }
  return true;
}

let CF_PAGE = 1;

async function capfigPreview(page) {
  const body = capfigBody();
  if (!capfigReady(body)) return;
  body.page = page || CF_PAGE;
  try {
    const j = await api('/api/capfig_preview', body);
    CF_PAGE = j.page;
    const nav = j.pages > 1
      ? '<button class="sub" onclick="capfigPreview(' + (j.page - 1) + ')"' +
        (j.page <= 1 ? ' disabled' : '') + '>◀ 前の図</button>' +
        ' <b>' + j.page + ' / ' + j.pages + '</b> ' +
        '<button class="sub" onclick="capfigPreview(' + (j.page + 1) + ')"' +
        (j.page >= j.pages ? ' disabled' : '') + '>次の図 ▶</button>'
      : '<b>1 / 1</b>';
    $('cf_preview').innerHTML =
      '<div class="row">' + nav + ' <span class="hint">' + esc(j.title) +
      '</span></div>' +
      '<img src="' + j.url + '&_=' + Date.now() + '" alt="断面符号図プレビュー"' +
      ' style="max-width:100%;border:1px solid #ccd;background:#fff">';
    setMsg('cf_msg', 'プレビューを更新しました。' + notesHtml(j.notes), 'msg-ok');
  } catch (e) {
    $('cf_preview').innerHTML = '';
    setMsg('cf_msg', esc(e.message), 'msg-err');
  }
}

async function plotCapfig() {
  const body = capfigBody();
  if (!capfigReady(body)) return;
  try {
    const j = await api('/api/capfig_plot', body);
    setMsg('cf_msg', '断面符号図を作成しました (伏図 ' + body.floors.length +
      '・軸組図 ' + body.axes.length + ')。' + openFolderBtn(j.out_dir) +
      notesHtml(j.notes) + pdfListHtml(j.pdfs), 'msg-ok');
  } catch (e) { setMsg('cf_msg', esc(e.message), 'msg-err'); }
}

// 共通の mgt 読み込みの結果からグループ一覧を作る (app.js の loadMgt が通知)
document.addEventListener('mgtkit:mgt-loaded', e => capfigGroups(e.detail.groups));
