'use strict';

// 荷重分布図タブ。app.js の共通関数 ($ / api / setMsg / notesHtml /
// pdfListHtml / checkAll / checkedVals / esc / openFolderBtn) を借りる。
// app.js には手を入れず、このファイルは _tab_loadmap.html から読み込む。

// 用紙[mm] (縦向き)。loadmap/draw.py の PAPERS と対応
const LM_PAPERS = {2: [420, 594], 3: [297, 420], 4: [210, 297], 5: [148, 210]};
// 1ページの図数 -> [列, 行]。loadmap/draw.py の GRID と対応
const LM_GRID = {1: [1, 1], 2: [1, 2], 3: [1, 3], 4: [2, 2], 6: [2, 3],
                 8: [2, 4], 9: [3, 3]};

function lmNum(id, dflt) {
  const el = $(id);
  const v = el && el.value !== '' ? parseFloat(el.value) : NaN;
  return isNaN(v) ? dflt : v;
}

function lmGrid() {
  const c = lmNum('lm_cols', 0), r = lmNum('lm_rows', 0);
  if (c > 0 || r > 0) return [Math.max(1, c || 1), Math.max(1, r || 1)];
  return LM_GRID[String($('lm_perpage').value)] || [2, 2];
}

// 枠の実寸を出す。用紙・図数を変えたときに小さすぎる指定へ気づけるようにする
// (判定そのものはサーバ側 draw.py も行う)
function loadmapCell() {
  let [pw, ph] = LM_PAPERS[String($('lm_paper').value)] || [210, 297];
  if ($('lm_landscape').checked) { const t = pw; pw = ph; ph = t; }
  const [cols, rows] = lmGrid();
  const mg = lmNum('lm_margin', 8), gap = lmNum('lm_gap', 4);
  const cw = (pw - 2 * mg - (cols - 1) * gap) / cols;
  const ch = (ph - 2 * mg - (rows - 1) * gap) / rows;
  const box = $('lm_cell');
  if (!box) return;
  if (cw <= 20 || ch <= 20) {
    box.innerHTML = '枠 ' + cw.toFixed(0) + '×' + ch.toFixed(0) +
      'mm — 小さすぎます。用紙を大きくするか図数を減らしてください';
    box.style.color = '#b45309';
  } else {
    box.innerHTML = pw + '×' + ph + 'mm ／ ' + cols + '列×' + rows + '行 = ' +
      (cols * rows) + '図/ページ　→ 枠 ' + cw.toFixed(1) + '×' +
      ch.toFixed(1) + 'mm';
    box.style.color = '';
  }
}

// スライダと数値欄を互いに追従させる
function loadmapAngle(src) {
  const pair = src.startsWith('az') ? ['lm_az', 'lm_az_n'] : ['lm_el', 'lm_el_n'];
  const from = src.endsWith('_n') ? pair[1] : pair[0];
  const to = from === pair[0] ? pair[1] : pair[0];
  $(to).value = $(from).value;
}

async function loadmapCases() {
  const mgt = $('mgt_path').value.trim();
  if (!mgt) { setMsg('lm_msg', 'mgtファイルのパスを入力してください。', 'err'); return; }
  try {
    const j = await api('/api/loadmap_cases', {mgt_path: mgt});
    const box = $('lm_cases_box');
    if (!j.cases || !j.cases.length) {
      box.innerHTML = '<span class="hint">荷重が入力された荷重ケースが' +
        'ありません</span>';
      return;
    }
    box.innerHTML = j.cases.map(c => {
      const d = [];
      if (c.floor) d.push('床' + c.floor + '面');
      if (c.pressure) d.push('面' + c.pressure + '要素');
      if (c.nodal) d.push('節点' + c.nodal + '点');
      return '<label><input type="checkbox" class="lmcase" value="' +
        esc(c.name) + '" checked> ' + esc(c.name) +
        ' <span class="hint">(' + d.join('・') + ')</span></label>';
    }).join('');
    setMsg('lm_msg', j.cases.length + ' 件の荷重ケースを読み込みました。', 'ok');
    loadmapCell();
  } catch (e) { setMsg('lm_msg', esc(e.message), 'err'); }
}

function loadmapBody() {
  const cases = checkedVals('lmcase');
  const [cols, rows] = lmGrid();
  const manual = lmNum('lm_cols', 0) > 0 || lmNum('lm_rows', 0) > 0;
  return {
    mgt_path: $('mgt_path').value.trim(),
    cases: cases,
    paper_size: parseInt($('lm_paper').value, 10),
    landscape: $('lm_landscape').checked,
    per_page: parseInt($('lm_perpage').value, 10),
    cols: manual ? cols : null,
    rows: manual ? rows : null,
    margin: lmNum('lm_margin', 8),
    gap: lmNum('lm_gap', 4),
    azimuth: lmNum('lm_az_n', -40),
    elevation: lmNum('lm_el_n', 24),
    arrows: $('lm_arrows').checked,
  };
}

// PDF を作る前の確認。紙面の計算はサーバ側と同じなので、
// ここに出た絵がそのまま PDF になる
let LM_PAGE = 1;

async function loadmapPreview(page) {
  const body = loadmapBody();
  if (!body.mgt_path) {
    setMsg('lm_msg', 'mgtファイルのパスを入力してください。', 'err'); return;
  }
  if (!body.cases.length) {
    setMsg('lm_msg', '荷重ケースを1つ以上選択してください。' +
      '（「荷重ケースを読み込み」を押してから選びます）', 'err');
    return;
  }
  body.page = page || LM_PAGE;
  try {
    const j = await api('/api/loadmap_preview', body);
    LM_PAGE = j.page;
    const nav = j.pages > 1
      ? '<button class="sub" onclick="loadmapPreview(' + (j.page - 1) +
        ')"' + (j.page <= 1 ? ' disabled' : '') + '>◀ 前のページ</button>' +
        ' <b>' + j.page + ' / ' + j.pages + ' ページ</b> ' +
        '<button class="sub" onclick="loadmapPreview(' + (j.page + 1) +
        ')"' + (j.page >= j.pages ? ' disabled' : '') + '>次のページ ▶</button>'
      : '<b>1 / 1 ページ</b>';
    $('lm_preview').innerHTML =
      '<div class="row">' + nav + '<span class="hint">' +
      j.cases.map(esc).join('・') + '</span></div>' +
      '<img src="' + j.url + '&_=' + Date.now() + '" alt="荷重分布図プレビュー"' +
      ' style="max-width:100%;border:1px solid #ccd;background:#fff">';
    setMsg('lm_msg', 'プレビューを更新しました。この紙面のままPDFになります。' +
      notesHtml(j.notes), 'ok');
  } catch (e) {
    $('lm_preview').innerHTML = '';
    setMsg('lm_msg', esc(e.message), 'err');
  }
}

async function plotLoadmap() {
  const body = loadmapBody();
  if (!body.mgt_path) {
    setMsg('lm_msg', 'mgtファイルのパスを入力してください。', 'err'); return;
  }
  if (!body.cases.length) {
    setMsg('lm_msg', '荷重ケースを1つ以上選択してください。' +
      '（「荷重ケースを読み込み」を押してから選びます）', 'err');
    return;
  }
  try {
    const j = await api('/api/plot_loadmap', body);
    setMsg('lm_msg', '荷重分布図を作成しました (' + body.cases.length + '図)。' +
      openFolderBtn(j.out_dir) + notesHtml(j.notes) + pdfListHtml(j.pdfs), 'ok');
  } catch (e) { setMsg('lm_msg', esc(e.message), 'err'); }
}

async function loadmapViewSheet() {
  const body = loadmapBody();
  if (!body.mgt_path || !body.cases.length) {
    setMsg('lm_msg', 'mgtファイルと荷重ケースを選んでください。', 'err'); return;
  }
  try {
    const j = await api('/api/loadmap_view_sheet', body);
    setMsg('lm_msg', '視点の比較シートを作成しました (' + esc(body.cases[0]) +
      ')。良い角度を見つけて方位角・見下ろし角の欄に入れてください。' +
      openFolderBtn(j.out_dir) + notesHtml(j.notes) + pdfListHtml(j.pdfs), 'ok');
  } catch (e) { setMsg('lm_msg', esc(e.message), 'err'); }
}

// 共通関数 ($ など) を持つ app.js はこのファイルより後で読み込まれるので、
// 初回の枠寸法表示は DOM 構築の完了まで待つ
window.addEventListener('DOMContentLoaded', loadmapCell);
