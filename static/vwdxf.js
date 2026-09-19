'use strict';

// DXF(VW10J) タブ (mgtkit/vwdxf/)。app.js の共通関数 ($ / api / setMsg / esc /
// checkAll / checkedVals / notesHtml / openFolderBtn) を借りる。app.js には手を入れず、
// このファイルは _tab_vwdxf.html から読み込む。mgt ファイルは共通欄 mgt_path。

let VDX_LEVELS = [];
let VDX_FRAMES = [];
let VDX_LISTCATS = [];
let VDX_PREV_PAGE = 1;
let VDX_LOADED_KEY = null;   // 読み込み済みの mgt パスとダミー材下限
let VDX_LOAD_SEQ = 0;        // 読み込みが重なったとき、最後のものだけ画面に出す

// ---- 自動読み込み (枠 1 の「読み直す」は vdxAutoLoad(true)) ----
// 共通欄の mgt ファイルが決まったら (手入力・参照・ドロップ・共通の読み込み)、このタブを
// 読み直す。タブが閉じていれば、開いたときに読む。app.js には手を入れず、ページの準備が
// できたところで loadMgt を包み、mgt_path の change とタブのボタンに聞き耳を立てる。
function vdxKey() {
  return ($('mgt_path').value || '').trim() + '|' + $('vdx_limit').value;
}

function vdxTabOpen() {
  const t = $('tab-vwdxf');
  return !!(t && t.classList.contains('active'));
}

function vdxAutoLoad(force) {
  if (!($('mgt_path').value || '').trim()) {
    if (force && vdxTabOpen()) setMsg('vdx_info_msg', 'mgt ファイルを指定してください。', 'msg-err');
    return;
  }
  if (force) VDX_LOADED_KEY = null;
  if (VDX_LOADED_KEY === vdxKey()) return;
  if (vdxTabOpen()) vdxLoadInfo();
}

window.addEventListener('DOMContentLoaded', () => {
  if (typeof loadMgt === 'function') {
    const orig = loadMgt;
    loadMgt = async function (...args) {   // eslint-disable-line no-global-assign
      const r = await orig.apply(this, args);
      vdxAutoLoad(true);
      return r;
    };
  }
  $('mgt_path').addEventListener('change', () => vdxAutoLoad(true));
  const btn = document.querySelector('nav button[data-tab="vwdxf"]');
  if (btn) btn.addEventListener('click', () => vdxAutoLoad(false));
  vdxAutoLoad(false);
});

function vdxRefreshPrevSel() {
  const sel = $('vdx_prev_sel');
  if (!sel) return;
  const prev = sel.value;
  const groups = [];
  const axis = VDX_FRAMES.map(f =>
    '<option value="axis:' + esc(f.key) + '">軸組 ' + esc(f.label) + '</option>');
  if (axis.length) groups.push('<optgroup label="軸組図">' + axis.join('') + '</optgroup>');
  const wood = vdxWood();
  const plan = [];
  VDX_LEVELS.forEach(pl => {
    if (wood && pl.top === false) {
      plan.push('<option value="plan_beam:' + esc(pl.key) + '">梁伏図 ' +
                esc(pl.label) + '</option>');
      plan.push('<option value="plan_column:' + esc(pl.key) + '">柱伏図 ' +
                esc(pl.label) + '</option>');
    } else {
      plan.push('<option value="plan:' + esc(pl.key) + '">伏図 ' +
                esc(pl.label) + '</option>');
    }
  });
  if (plan.length) groups.push('<optgroup label="伏図">' + plan.join('') + '</optgroup>');
  const cats = checkedVals('vdxlistcat');
  const lists = VDX_LISTCATS.filter(c => cats.includes(c.key)).map(c =>
    '<option value="list:' + esc(c.key) + '">' + esc(c.label) + '</option>');
  if (lists.length) groups.push('<optgroup label="部材リスト">' + lists.join('') + '</optgroup>');
  sel.innerHTML = groups.join('');
  if ([...sel.options].some(o => o.value === prev)) sel.value = prev;
  VDX_PREV_PAGE = 1;
}

async function vdxLoadInfo() {
  const seq = ++VDX_LOAD_SEQ;
  const key = vdxKey();
  setMsg('vdx_info_msg', '読み込み中…', '');
  try {
    const j = await api('/api/vwdxf_info',
      {mgt_path: $('mgt_path').value,
       limit_sec_no: +$('vdx_limit').value});
    if (seq !== VDX_LOAD_SEQ) return;   // 後から始めた読み込みがある
    VDX_LOADED_KEY = key;
    VDX_LEVELS = j.plans ||
      (j.levels || []).map(z => ({key: String(z),
                                  label: (+z).toFixed(3) + 'm'}));
    VDX_FRAMES = j.frames || [];
    VDX_LISTCATS = j.list_categories || [];
    // 区分ごとのチェック。「その他」は配筋も形状情報も少ないので既定で外す
    $('vdx_listcats_box').innerHTML = VDX_LISTCATS.map(c =>
      '<label><input type="checkbox" class="vdxlistcat" value="' + esc(c.key) + '"' +
      (c.key !== 'OTHER' ? ' checked' : '') + ' onchange="vdxRefreshPrevSel()"> ' +
      esc(c.label) + ' <span class="hint">' + c.n + '断面</span></label>').join(' ') ||
      '<span class="hint">載せる断面がありません</span>';
    $('vdx_list_limit').textContent = $('vdx_limit').value;
    $('vdx_axes_box').innerHTML = VDX_FRAMES.map(f =>
      '<label><input type="checkbox" class="vdxframe" value="' +
      esc(f.key) + '"' + (f.n_col >= 2 ? ' checked' : '') + '> ' +
      esc(f.label) + ' <span class="hint">柱' + f.n_col + '</span></label>'
      ).join(' ') || '<span class="hint">柱が見つかりません</span>';
    $('vdx_grids_box').innerHTML = VDX_FRAMES.map(f =>
      '<label><input type="checkbox" class="vdxgrid" value="' +
      esc(f.key) + '"' + (f.n_col >= 2 ? ' checked' : '') + '> ' +
      esc(f.label) + '</label>'
      ).join(' ') || '<span class="hint">通りの候補がありません</span>';
    $('vdx_lvlines_box').innerHTML = VDX_LEVELS.map(pl =>
      '<label><input type="checkbox" class="vdxlvline" value="' +
      esc(pl.key) + '"' + (pl.flat ? ' checked' : '') + '> ' + esc(pl.label) +
      (pl.z != null ? ' <span class="hint">' + (+pl.z).toFixed(3) + 'm</span>' : '') +
      '</label>').join(' ') ||
      '<span class="hint">フロアの候補が見つかりません</span>';
    $('vdx_levels_box').innerHTML = VDX_LEVELS.map(pl =>
      '<label><input type="checkbox" class="vdxlevel" value="' +
      esc(pl.key) + '" checked> ' + esc(pl.label) + '</label>').join(' ') ||
      '<span class="hint">伏図の候補が見つかりません</span>';
    setMsg('vdx_info_msg', '部材 ' + j.n_members + ' (柱' + j.kinds.column +
           '/梁' + j.kinds.beam + '/斜材' + j.kinds.brace + ')・ピン端あり ' +
           j.n_pin + ' 部材・伏図 ' + VDX_LEVELS.length + ' 件',
           'msg-ok');
    $('vdx_info_msg').innerHTML += notesHtml(j.notes);
    vdxRefreshPrevSel();
  } catch (e) {
    if (seq === VDX_LOAD_SEQ) setMsg('vdx_info_msg', esc(e.message), 'msg-err');
  }
}

function vdxWood() {
  const r = document.querySelector('input[name="vdx_mode"]:checked');
  return !!r && r.value === 'wood';
}

function vdxCommon() {
  return {mgt_path: $('mgt_path').value,
    paper: $('vdx_paper').value,
    scale: +$('vdx_scale').value,
    list_scale: +$('vdx_list_scale').value,
    text_pt: +$('vdx_text_pt').value,
    grid_pt: +$('vdx_grid_pt').value,
    grids: checkedVals('vdxgrid'),
    wood: vdxWood(),
    level_lines: checkedVals('vdxlvline'),
    list_categories: checkedVals('vdxlistcat'),
    limit_sec_no: +$('vdx_limit').value};
}

async function vdxPreview() {
  setMsg('vdx_msg', '', '');
  try {
    const v = $('vdx_prev_sel').value;
    if (!v) { setMsg('vdx_msg', 'プレビュー対象がありません。mgt ファイルを読み込んでください。', 'msg-err'); return; }
    const [kind, key] = [v.slice(0, v.indexOf(':')),
                         v.slice(v.indexOf(':') + 1)];
    const req = vdxCommon();
    req.kind = kind.startsWith('plan') ? 'plan' : kind; req.key = key;
    req.sheet = kind.startsWith('plan_') ? kind.slice(5) : null;
    req.page = kind === 'list' ? VDX_PREV_PAGE : 1;
    const j = await api('/api/vwdxf_preview', req);
    let pageNav = '';
    if (kind === 'list' && j.pages) {
      VDX_PREV_PAGE = j.page || 1;
      pageNav = ' ／ ' + VDX_PREV_PAGE + '/' + j.pages + '枚目' +
        (VDX_PREV_PAGE > 1 ? ' <button class="sub" onclick="vdxPrevPage(-1)">前の図</button>' : '') +
        (VDX_PREV_PAGE < j.pages ? ' <button class="sub" onclick="vdxPrevPage(1)">次の図</button>' : '');
    }
    setMsg('vdx_msg', 'プレビュー: 縮尺 1/' + j.scale + pageNav, 'msg-ok');
    $('vdx_msg').innerHTML += notesHtml(j.notes);
    // 同じ秒に続けて描くと URL が同じになり古い画像が出るので、要求ごとに変える
    $('vdx_prev').innerHTML = '<img src="' + j.png_url + '&r=' + Date.now() +
      '" style="width:100%;border:1px solid #ccc">';
  } catch (e) { setMsg('vdx_msg', esc(e.message), 'msg-err'); }
}

function vdxPrevPage(d) {
  VDX_PREV_PAGE = Math.max(1, VDX_PREV_PAGE + d);
  vdxPreview();
}

async function vdxRunDxf() {
  setMsg('vdx_msg', '', ''); $('vdx_files').innerHTML = '';
  try {
    const req = vdxCommon();
    req.axes = checkedVals('vdxframe');
    req.levels = checkedVals('vdxlevel');
    if ((!req.axes || !req.axes.length) &&
        (!req.levels || !req.levels.length) &&
        (!req.list_categories || !req.list_categories.length)) {
      setMsg('vdx_msg', '軸組図の通り・伏図のフロア・部材リストのいずれかを選択してください。',
             'msg-err');
      return;
    }
    const j = await api('/api/vwdxf_dxf', req);
    let h = 'DXF ' + j.files.length + ' 件を生成しました → ' +
            esc(j.out_dir) + openFolderBtn(j.out_dir);
    if (j.info && j.info.length) {
      h += '<br>' + j.info.map(i => esc(i.title)).join(' / ');
    }
    setMsg('vdx_msg', h, 'msg-ok');
    $('vdx_msg').innerHTML += notesHtml(j.notes);
    $('vdx_files').innerHTML = j.files.map(f =>
      '<div class="pdfblock"><b>' + esc(f.name) + '</b>' +
      '<a class="dl" href="' + f.url + '">ダウンロード</a></div>').join('');
  } catch (e) { setMsg('vdx_msg', esc(e.message), 'msg-err'); }
}

