'use strict';

// ---------------- 共通 ----------------
const $ = id => document.getElementById(id);

document.querySelectorAll('nav button').forEach(b => {
  b.onclick = () => {
    document.querySelectorAll('nav button').forEach(x =>
      x.classList.toggle('active', x === b));
    document.querySelectorAll('.tab').forEach(t =>
      t.classList.toggle('active', t.id === 'tab-' + b.dataset.tab));
  };
});

// 同種の応力ファイル欄はタブ間で同期する (どこで読み込んでも全タブに反映)
const SYNC_GROUPS = [
  ['beam_stress_path', 'c_beam_stress_path', 'q_beam_stress_path'],
  ['truss_stress_path', 'c_truss_stress_path', 'q_truss_stress_path'],
  ['plate_stress_path', 'c_plate_stress_path', 'q_plate_stress_path'],
  ['c_wall_stress_path', 'q_wall_stress_path'],
];
function syncPathGroup(srcId, val) {
  for (const g of SYNC_GROUPS) {
    if (!g.includes(srcId)) continue;
    for (const id of g) {
      if (id === srcId) continue;
      const el = $(id);
      if (el && el.value !== val) {
        el.value = val;
        localStorage.setItem('mgtkit_' + id, val);
      }
    }
  }
}

['mgt_path', 'mgt_out_base', 'beam_stress_path', 'truss_stress_path',
 'plate_stress_path',
 'c_beam_stress_path', 'c_truss_stress_path', 'c_plate_stress_path',
 'c_wall_stress_path', 'c_pc_stress_path', 'c_pc_cable_path',
 'c_pc_slab_path', 'q_beam_stress_path', 'q_truss_stress_path',
 'q_wall_stress_path', 'q_plate_stress_path', 'q_reaction_path',
 'q_deformation_path'].forEach(id => {
  const el = $(id);
  el.value = localStorage.getItem('mgtkit_' + id) || '';
  el.addEventListener('change', () => {
    localStorage.setItem('mgtkit_' + id, el.value);
    syncPathGroup(id, el.value);
    if (id !== 'mgt_path') clearCaseTable();
  });
});

// 応力ファイルが変わったら古いケース表を消す (別ファイルのケースで実行するのを防ぐ)
function clearCaseTable() {
  const box = $('check_cases_box');
  if (box && box.innerHTML.trim() !== '') {
    box.innerHTML = '<span class="hint">応力ファイルが変わりました。' +
      '「ケース読込 (種別指定)」を押し直してください。' +
      '(未読込のまま検定するとケース数からの自動判定になります)</span>';
  }
}

function overlay(on) { $('overlay').classList.toggle('show', on); }

async function api(url, body) {
  overlay(true);
  try {
    // 出力先フォルダ(共通欄)を全リクエストへ付与
    if (body && typeof body === 'object' && !('out_base' in body)) {
      const ob = $('mgt_out_base');
      if (ob && ob.value.trim()) body.out_base = ob.value.trim();
    }
    // アップロードmgt使用時の出力先自動推定用に、UIの全パス欄の値を添付
    if (body && typeof body === 'object' && !('path_hints' in body)) {
      body.path_hints = ['beam_stress_path', 'truss_stress_path',
        'plate_stress_path',
        'c_beam_stress_path', 'c_truss_stress_path', 'c_plate_stress_path',
        'c_wall_stress_path', 'c_pc_stress_path', 'c_pc_cable_path',
        'c_pc_slab_path', 'q_beam_stress_path', 'q_truss_stress_path',
        'q_wall_stress_path', 'q_plate_stress_path', 'q_reaction_path',
        'q_deformation_path']
        .map(id => $(id) ? $(id).value.trim() : '').filter(v => v);
    }
    const r = await fetch(url, {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)});
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
    return j;
  } finally { overlay(false); }
}

function setMsg(id, html, cls) {
  $(id).innerHTML = html ? '<div class="' + cls + '">' + html + '</div>' : '';
}
function esc(s) {
  // "や'もエスケープする: mgtの引用符付きグループ名(例: "構造グループ 35")は
  // 引用符ごと名前として保持される(MATLAB版mgtopen_group.mと同一挙動)ため、
  // 属性値に埋め込む際にエスケープしないと value="" となり空名になる
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
                  .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
                  .replace(/'/g, '&#39;');
}
function notesHtml(notes) {
  if (!notes || !notes.length) return '';
  return '<div class="msg-note">注記:\n' + notes.map(esc).join('\n') + '</div>';
}
function openFolderBtn(dir) {
  return ' <button class="sub open-folder" data-dir="' +
         esc(dir).replace(/"/g, '&quot;') + '">フォルダを開く</button>';
}
async function openFolder(dir) {
  try {
    await api('/api/open_folder', dir ? {path: dir} : {target: 'uploads'});
  } catch (e) { alert('フォルダを開けませんでした: ' + e.message); }
}
document.addEventListener('click', ev => {
  const b = ev.target.closest ? ev.target.closest('.open-folder') : null;
  if (b) openFolder(b.dataset.dir || '');
});
const BUNDLE = {};  // kind ('model'|'stress') -> 生成PDFの絶対パス一覧

function mgtBase() {
  const n = ($('mgt_path').value || '').split(/[\\/]/).pop();
  return n.replace(/\.[^.]+$/, '') || 'mgtkit';
}

function pdfEmbedHtml(p, open) {
  // 折りたたみ式プレビュー。閉じている間はPDFを読み込まず、
  // 開いたときに data-src を src へ移して読み込む
  return '<details class="pdfblock pdfdet"' + (open ? ' open' : '') +
    ' ontoggle="pdfDetToggle(this)">' +
    '<summary>' + esc(p.name) +
    '<a class="dl" href="' + p.url +
    '&dl=1" onclick="event.stopPropagation()">ダウンロード</a>' +
    '<span class="hint" style="margin-left:8px">クリックでプレビュー開閉</span>' +
    '</summary>' +
    '<embed class="pdf"' + (open ? ' src="' + p.url + '"' : '') +
    ' data-src="' + p.url + '" type="application/pdf"></details>';
}

function pdfDetToggle(det) {
  if (!det.open) return;
  const e = det.querySelector('embed');
  if (e && !e.getAttribute('src')) e.setAttribute('src', e.dataset.src);
}

function pdfListHtml(pdfs, kind) {
  let h = '';
  if (kind) {
    BUNDLE[kind] = pdfs.map(p => p.path);
    // 前回選択した向きを復元 (再生成でリストが再描画されても維持する)
    const savedOrient =
      localStorage.getItem('mgtkit_bundle_orient') || 'mixed';
    h += '<div class="pdfblock"><b>一括ダウンロード (' + pdfs.length +
      '件): </b>' +
      '<label style="margin-right:10px">PDFの向き ' +
      '<select id="bundle_orient_' + kind + '">' +
      '<option value="mixed"' + (savedOrient === 'mixed' ? ' selected' : '') +
      '>縦横混在(そのまま)</option>' +
      '<option value="portrait"' +
      (savedOrient === 'portrait' ? ' selected' : '') +
      '>全部縦向き</option></select></label>' +
      '<button class="act" onclick="bundleDl(\'' + kind +
      '\',\'zip\')">ZIP一括ダウンロード</button> ' +
      '<button class="act" onclick="bundleDl(\'' + kind +
      '\',\'merge\')">結合PDF</button>' +
      '<span id="bundle_msg_' + kind +
      '" class="hint" style="margin-left:10px"></span></div>';
  }
  const rows = pdfs.map(p => pdfEmbedHtml(p, pdfs.length === 1)).join('');
  if (pdfs.length > 3) {
    // 多数のときは1つの折りたたみにまとめて格納 (プレビューは遅延読込)
    h += '<details class="pdflist"><summary>生成PDF ' + pdfs.length +
      ' 件の一覧・プレビュー</summary>' + rows + '</details>';
  } else {
    h += rows;
  }
  return h;
}

async function bundleDl(kind, mode) {
  const files = BUNDLE[kind];
  if (!files || !files.length) return;
  const msg = $('bundle_msg_' + kind);
  const sel = $('bundle_orient_' + kind);
  if (!sel) staleWarn();  // 旧版キャッシュのHTML/JS混在を検出
  const orient = sel ? sel.value : 'mixed';
  localStorage.setItem('mgtkit_bundle_orient', orient);
  msg.textContent = (mode === 'zip' ? 'zip作成中...' : 'PDF結合中...');
  try {
    const j = await api('/api/bundle', {
      files: files, mode: mode, orient: orient,
      prefix: mgtBase() + '_' + kind});
    let extra = '';
    if (orient === 'portrait') {
      if (j.rotated === undefined) {
        // サーバ側が旧版のまま動いている (orient未対応) 場合の検出
        extra = ' <b style="color:#a12b1e">注意: サーバが旧版のまま動作' +
          'しています。mgtkit(起動.bat)を再起動してください' +
          '(縦向き統一は未適用)。</b>';
      } else {
        extra = ' / 横→縦回転 ' + j.rotated + 'ページ';
      }
    }
    msg.innerHTML = esc(j.name) + ' (' + j.n_files +
      '件) を作成しました' + extra +
      ' <a href="' + j.url + '">再ダウンロード</a>';
    const a = document.createElement('a');
    a.href = j.url;
    document.body.appendChild(a); a.click(); a.remove();
  } catch (e) { msg.textContent = 'エラー: ' + e.message; }
}
function checkAll(cls, on) {
  document.querySelectorAll('input.' + cls).forEach(c => c.checked = on);
}
function checkedVals(cls) {
  return [...document.querySelectorAll('input.' + cls + ':checked')]
         .map(c => c.value);
}

async function uploadFileTo(file, targetId, after) {
  const fd = new FormData();
  fd.append('file', file);
  overlay(true);
  try {
    const r = await fetch('/api/upload', {method: 'POST', body: fd});
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || 'アップロード失敗');
    $(targetId).value = j.path;
    localStorage.setItem('mgtkit_' + targetId, j.path);
    syncPathGroup(targetId, j.path);
    if (targetId !== 'mgt_path') clearCaseTable();
    if (after) after();
  } catch (e) { alert(e.message); }
  finally { overlay(false); }
}

// ---------------- ネイティブのファイル/フォルダ選択 ----------------
// サーバー側でWindows標準ダイアログを開き実パスを取得する (自己完結運用。
// アップロード方式と違い一時フォルダへのコピーが発生しない)
function setPathField(id, val) {
  const el = $(id);
  if (!el) return;
  el.value = val;
  localStorage.setItem('mgtkit_' + id, val);
  syncPathGroup(id, val);
}

async function pickFile(targetId, after, filter) {
  try {
    const j = await api('/api/pick_path', {kind: 'file',
      filter: filter || (targetId === 'mgt_path' ? 'mgt' : 'txt'),
      initial: $(targetId).value});
    if (!j.path) return;  // キャンセル
    setPathField(targetId, j.path);
    if (after) after();
  } catch (e) { alert('ファイル選択エラー: ' + e.message); }
}

async function pickDir(targetId) {
  try {
    const j = await api('/api/pick_path', {kind: 'dir',
      initial: $(targetId).value});
    if (!j.path) return;
    setPathField(targetId, j.path);
  } catch (e) { alert('フォルダ選択エラー: ' + e.message); }
}

// 事例フォルダを選んで mgt・応力ファイル群を全欄に一括セット
async function pickCaseDir() {
  try {
    const j = await api('/api/pick_path', {kind: 'dir',
      initial: $('mgt_path').value});
    if (!j.path) return;
    const r = await api('/api/scan_case_dir', {dir: j.path});
    const map = {mgt_path: r.mgt_path,
                 beam_stress_path: r.beam_stress_path,
                 truss_stress_path: r.truss_stress_path,
                 plate_stress_path: r.plate_stress_path,
                 c_wall_stress_path: r.wall_stress_path,
                 q_reaction_path: r.reaction_path,
                 q_deformation_path: r.deformation_path};
    const found = [];
    for (const [id, v] of Object.entries(map)) {
      if (v) { setPathField(id, v); found.push(v.split(/[\\/]/).pop()); }
    }
    setMsg('mgt_msg', found.length
      ? '一括セット: ' + found.map(esc).join(', ')
      : 'フォルダ内に mgt・応力ファイルが見つかりませんでした',
      found.length ? 'msg-ok' : 'msg-err');
    if (r.mgt_path) await loadMgt();
    if (r.beam_stress_path) loadCases();
  } catch (e) { alert('一括セットエラー: ' + e.message); }
}

async function uploadTo(input, targetId, after) {
  if (!input.files.length) return;
  await uploadFileTo(input.files[0], targetId, after);
}

// ---------------- ドラッグ&ドロップ読み込み ----------------
// 各パス欄にファイルをドロップするとその欄へ、ページの他の場所に
// .mgt をドロップすると mgt 欄へアップロード読み込みする。
const _DROP_AFTER = {mgt_path: () => loadMgt(),
                     beam_stress_path: () => loadCases()};

function _setupDragDrop() {
  document.querySelectorAll('input.path').forEach(el => {
    if (el.id === 'mgt_out_base') return;  // フォルダ欄はドロップ対象外
    el.addEventListener('dragover', ev => {
      ev.preventDefault(); ev.stopPropagation();
      el.classList.add('dragover');
    });
    el.addEventListener('dragleave', () => el.classList.remove('dragover'));
    el.addEventListener('drop', ev => {
      ev.preventDefault(); ev.stopPropagation();
      el.classList.remove('dragover');
      const f = ev.dataTransfer.files && ev.dataTransfer.files[0];
      if (f) uploadFileTo(f, el.id, _DROP_AFTER[el.id]);
    });
  });
  // ページ全体: .mgt なら mgt欄へ。それ以外はブラウザ既定動作(ファイルを
  // 開いて画面遷移)だけ抑止する
  window.addEventListener('dragover', ev => ev.preventDefault());
  window.addEventListener('drop', ev => {
    ev.preventDefault();
    const f = ev.dataTransfer.files && ev.dataTransfer.files[0];
    if (f && /\.mgt$/i.test(f.name)) {
      uploadFileTo(f, 'mgt_path', _DROP_AFTER.mgt_path);
    }
  });
}
_setupDragDrop();

// ---------------- mgt読み込み (グループ一覧) ----------------
function staleWarn() {
  if (window.__staleWarned) return;
  window.__staleWarned = true;
  alert('画面が古いバージョンのままです。Ctrl+F5 で再読み込みしてください。');
}
function groupChecks(groups, kind, cls, boxId, checked) {
  const box = $(boxId);
  if (!box) { staleWarn(); return; }
  const list = groups.filter(g => g.kind === kind);
  if (!list.length) {
    box.innerHTML = '<span class="hint">該当グループなし</span>';
    return;
  }
  box.innerHTML = list.map(g =>
    '<label><input type="checkbox" class="' + cls + '" value="' +
    esc(g.name) + '"' + (checked ? ' checked' : '') + '> ' + esc(g.name) +
    ' <span class="hint">(' + g.n_nodes + '節点)</span></label>').join('');
}

function fmtLevel(z) {
  return String(Math.round(z * 1000) / 1000);
}

function heightChecks(levels, cls, boxId) {
  const box = $(boxId);
  if (!box) { staleWarn(); return; }
  if (!levels || !levels.length) {
    box.innerHTML = '<span class="hint">高さ候補なし</span>';
    return;
  }
  box.innerHTML = levels.map(l =>
    '<label><input type="checkbox" class="' + cls + '" value="' + l.z +
    '" data-col="' + (l.col ? 1 : 0) + '" checked> ' + fmtLevel(l.z) +
    (l.fl ? ' <span class="hint">' + esc(l.fl) + '</span>' : '') +
    (l.col ? '' : '<span class="hint">*</span>') + '</label>').join('');
}

function presetColumns(cls) {
  document.querySelectorAll('input.' + cls).forEach(c =>
    c.checked = c.dataset.col === '1');
}

// 全選択(または未表示)なら null=従来動作(構面上の全節点レベル)を返す
function selectedHeights(cls) {
  const all = document.querySelectorAll('input.' + cls);
  const sel = [...document.querySelectorAll('input.' + cls + ':checked')];
  if (!all.length || !sel.length || sel.length === all.length) return null;
  return sel.map(c => +c.value);
}

async function loadMgt() {
  setMsg('mgt_msg', '', '');
  try {
    const j = await api('/api/mgt_info', {mgt_path: $('mgt_path').value});
    groupChecks(j.groups, 'vertical', 'axgroup', 'axes_box', true);
    groupChecks(j.groups, 'floor', 'flgroup', 'floors_box', false);
    groupChecks(j.groups, 'vertical', 'stgroup', 'staxes_box', true);
    groupChecks(j.groups, 'vertical', 'symgroup', 'symbols_box', true);
    groupChecks(j.groups, 'vertical', 'ssymgroup', 'ssymbols_box', true);
    heightChecks(j.heights, 'hlevel', 'heights_box');
    heightChecks(j.heights, 'shlevel', 'sheights_box');
    groupChecks(j.groups, 'vertical', 'rgroup', 'raxes_box', true);
    groupChecks(j.groups, 'vertical', 'rsymgroup', 'rsymbols_box', true);
    heightChecks(j.heights, 'rhlevel', 'rheights_box');
    groupChecks(j.groups, 'vertical', 'qgroup', 'qaxes_box', true);
    groupChecks(j.groups, 'vertical', 'qcalcgroup', 'qcalc_box', false);
    refreshStructPrevSel();
    let hmsg = '';
    if (j.heights && j.heights.length) {
      hmsg = ' 高さ候補: 全節点 ' + j.n_z_all + ' レベル / 柱端点 ' +
             j.n_z_col + ' レベル。';
    }
    setMsg('mgt_msg',
           'グループ ' + j.groups.length + ' 件を読み込みました。' + hmsg,
           'msg-ok');
    if (j.note) $('mgt_msg').innerHTML +=
      '<div class="msg-note">' + esc(j.note) + '</div>';
  } catch (e) { setMsg('mgt_msg', esc(e.message), 'msg-err'); }
}

// ---------------- 3D表示 ----------------
let V3 = null;

function init3D() {
  const box = $('viewer3d');
  const W = box.clientWidth, H = box.clientHeight;
  const renderer = new THREE.WebGLRenderer({antialias: true});
  renderer.setSize(W, H);
  renderer.setClearColor(0xfdfdfe);
  box.insertBefore(renderer.domElement, box.firstChild);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, W / H, 0.01, 100000);
  camera.up.set(0, 0, 1);
  const controls = new THREE.OrbitControls(camera, renderer.domElement);
  const raycaster = new THREE.Raycaster();
  const mouse = new THREE.Vector2();
  V3 = {scene, camera, renderer, controls, raycaster, mouse,
        lineObj: null, data: null};

  renderer.domElement.addEventListener('mousemove', ev => {
    if (!V3.lineObj) return;
    const rect = renderer.domElement.getBoundingClientRect();
    mouse.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
    mouse.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(mouse, camera);
    const hits = raycaster.intersectObject(V3.lineObj);
    const tip = $('tooltip3d');
    if (hits.length) {
      const i = Math.floor(hits[0].index / 2);
      tip.textContent = '要素 ' + V3.data.ele_nos[i] + ' | 断面: ' +
                        V3.data.ele_secs[i];
      tip.style.display = 'block';
      tip.style.left = (ev.clientX - rect.left + 14) + 'px';
      tip.style.top = (ev.clientY - rect.top + 8) + 'px';
    } else { tip.style.display = 'none'; }
  });
  (function animate() {
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
  })();
}

async function show3D() {
  setMsg('model3d_msg', '', '');
  if (typeof THREE === 'undefined') {
    setMsg('model3d_msg', 'three.js のCDN読み込みに失敗しました。' +
           'インターネット接続を確認してください。', 'msg-err');
    return;
  }
  try {
    const j = await api('/api/model3d', {mgt_path: $('mgt_path').value});
    if (!V3) init3D();
    while (V3.scene.children.length) V3.scene.remove(V3.scene.children[0]);
    V3.data = j;
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position',
      new THREE.Float32BufferAttribute(j.lines, 3));
    const mat = new THREE.LineBasicMaterial({color: 0x2b5f8f});
    V3.lineObj = new THREE.LineSegments(geo, mat);
    V3.scene.add(V3.lineObj);
    if (j.plate_lines.length) {
      const g2 = new THREE.BufferGeometry();
      g2.setAttribute('position',
        new THREE.Float32BufferAttribute(j.plate_lines, 3));
      V3.scene.add(new THREE.LineSegments(g2,
        new THREE.LineBasicMaterial({color: 0xbbc4cc})));
    }
    geo.computeBoundingSphere();
    const c = geo.boundingSphere.center, r = geo.boundingSphere.radius || 10;
    V3.raycaster.params.Line = {threshold: r / 150};
    V3.controls.target.copy(c);
    V3.camera.position.set(c.x + r * 1.3, c.y - r * 1.5, c.z + r * 0.9);
    V3.camera.near = r / 1000; V3.camera.far = r * 100;
    V3.camera.updateProjectionMatrix();
    setMsg('model3d_msg', '節点 ' + j.n_node + ' / 線要素 ' +
           j.ele_nos.length + ' を表示しました。', 'msg-ok');
  } catch (e) { setMsg('model3d_msg', esc(e.message), 'msg-err'); }
}

// ---------------- モデル図PDF ----------------
async function runPlotModel() {
  setMsg('model_msg', '', ''); $('model_pdfs').innerHTML = '';
  try {
    const j = await api('/api/plot_model', {
      mgt_path: $('mgt_path').value,
      axes: checkedVals('axgroup'),
      floors: checkedVals('flgroup'),
      heights: selectedHeights('hlevel'),
      symbols: checkedVals('symgroup'),
      element_onoff: $('m_element').checked,
      end_onoff: $('m_end').checked,
      node_onoff: $('m_node').checked,
      paper_size: +$('m_paper').value,
      limit_sec_no: +$('m_limit').value,
      f_d: +$('m_f_d').value,
      f_a: +$('m_f_a').value,
      f_t: +$('m_f_t').value,
      line_location: +$('m_line_loc').value,
      axisname_location: +$('m_axis_loc').value,
      mg_l: +$('m_mg_l').value,
      mg_r: +$('m_mg_r').value,
      mg_t: +$('m_mg_t').value,
      mg_b: +$('m_mg_b').value,
      fig_format: $('m_format').value});
    const mTex = $('m_format').value === 'tex';
    setMsg('model_msg', (mTex ? 'TeX図 ' : 'PDF ') + j.pdfs.length +
           ' 件を生成しました → ' + esc(j.out_dir) +
           openFolderBtn(j.out_dir), 'msg-ok');
    $('model_msg').innerHTML += notesHtml(j.notes);
    if (mTex) {
      $('model_pdfs').innerHTML = '<div class="pdfblock">' +
        j.pdfs.map(f => '<div><b>' + esc(f.name) + '</b>' +
          '<a class="dl" href="' + f.url + '&dl=1">ダウンロード</a></div>'
        ).join('') +
        '<div class="hint">09modelplot.tex は自己完結の1ファイルです。' +
        '計算書フォルダへコピーして \\input{09modelplot.tex} で全図が' +
        '入ります (要 \\usepackage{pgf})</div></div>';
    } else {
      $('model_pdfs').innerHTML = pdfListHtml(j.pdfs, 'model');
    }
  } catch (e) { setMsg('model_msg', esc(e.message), 'msg-err'); }
}

// ---------------- 応力図 ----------------
async function loadCases() {
  try {
    const j = await api('/api/load_cases',
      {beam_stress_path: $('beam_stress_path').value});
    $('cases_box').innerHTML = j.cases.map(c =>
      '<label class="inline"><input type="checkbox" class="lcase" value="' +
      c.no + '" checked> ケース' + c.no +
      ' <input type="text" class="lcname" data-no="' + c.no +
      '" value="' + esc(c.name) + '" style="width:7em"' +
      ' title="図タイトル・ファイル名に使う名前"></label>').join(' ');
  } catch (e) { setMsg('stress_msg', esc(e.message), 'msg-err'); }
}

async function runPlotStress() {
  setMsg('stress_msg', '', ''); $('stress_pdfs').innerHTML = '';
  const comps = [];
  if ($('s_N').checked) comps.push('N');
  if ($('s_M').checked) comps.push('M');
  if ($('s_Q').checked) comps.push('Q');
  const cases = checkedVals('lcase').map(Number);
  const caseNames = {};
  document.querySelectorAll('#cases_box input.lcname').forEach(el => {
    if (el.value.trim()) caseNames[el.dataset.no] = el.value.trim();
  });
  try {
    const j = await api('/api/plot_stress', {
      mgt_path: $('mgt_path').value,
      beam_stress_path: $('beam_stress_path').value,
      truss_stress_path: $('truss_stress_path').value,
      plate_stress_path: $('plate_stress_path').value,
      case_names: caseNames,
      axes: checkedVals('stgroup'),
      heights: selectedHeights('shlevel'),
      symbols: checkedVals('ssymgroup'),
      cases: cases.length ? cases : null,
      components: comps,
      truss_Lplot: $('s_trussL').checked,
      unit_merge: $('s_unit').checked,
      mscale: +$('s_mscale').value,
      paper_size: +$('s_paper').value,
      f_s: +$('s_f_s').value,
      f_d: +$('s_f_d').value,
      f_a: +$('s_f_a').value,
      f_t: +$('s_f_t').value,
      f_r: +$('s_f_r').value,
      line_location: +$('s_line_loc').value,
      axisname_location: +$('s_axis_loc').value,
      mg_l: +$('s_mg_l').value,
      mg_r: +$('s_mg_r').value,
      mg_t: +$('s_mg_t').value,
      mg_b: +$('s_mg_b').value,
      fig_format: $('s_format').value});
    const sTex = $('s_format').value === 'tex';
    setMsg('stress_msg', (sTex ? 'TeX図 ' : 'PDF ') + j.pdfs.length +
           ' 件を生成しました → ' + esc(j.out_dir) +
           openFolderBtn(j.out_dir), 'msg-ok');
    $('stress_msg').innerHTML += notesHtml(j.notes);
    if (sTex) {
      $('stress_pdfs').innerHTML = '<div class="pdfblock">' +
        j.pdfs.map((f, i) => '<div>' + (i === 0 ? '<b>一式: ' : '') +
          esc(f.name) + (i === 0 ? '</b>' : '') +
          '<a class="dl" href="' + f.url + '&dl=1">ダウンロード</a></div>'
        ).join('') +
        '<div class="hint">09stressplot.tex は自己完結の1ファイルです。' +
        '計算書フォルダへコピーして \\input{09stressplot.tex} で全図が' +
        '入ります (要 \\usepackage{pgf})</div></div>';
    } else {
      $('stress_pdfs').innerHTML = pdfListHtml(j.pdfs, 'stress');
    }
  } catch (e) { setMsg('stress_msg', esc(e.message), 'msg-err'); }
}

// ---------------- 断面検定 ----------------
function ratioClass(v) {
  if (v === null || v === undefined) return 'na';
  if (v > 1.0) return 'ng';
  if (v > 0.9) return 'warn';
  return '';
}
function fmtRatio(v) {
  return (v === null || v === undefined) ? '-' : v.toFixed(2);
}

let CHECK = null;

// ケース読込: 応力ファイルの荷重ケース一覧と種別既定値(自動判定)を表示
const CTYPE_LABEL = {
  L: '長期',
  H: '水平のみ (長期と組合せて TL±H を生成)',
  S: '短期 (長期と組合せ済み・そのまま検定)'
};

async function loadCheckCases() {
  setMsg('check_msg', '', '');
  const bs = $('c_beam_stress_path').value.trim() ||
             $('beam_stress_path').value.trim();
  const ts = $('c_truss_stress_path').value.trim() ||
             $('truss_stress_path').value.trim();
  try {
    const j = await api('/api/check_cases',
      {beam_stress_path: bs, truss_stress_path: ts,
       mgt_path: $('mgt_path').value});
    let h = '<table class="res"><thead><tr><th>荷重ケース</th>' +
            '<th>種別</th><th>ケース名</th></tr></thead><tbody>';
    j.cases.forEach((c, i) => {
      h += '<tr><td>ケース ' + c.no + '</td><td>' +
        '<select class="ctype" data-no="' + c.no + '" id="ctype' + i + '">' +
        ['L', 'H', 'S'].map(t =>
          '<option value="' + t + '"' + (c.type === t ? ' selected' : '') +
          '>' + CTYPE_LABEL[t] + '</option>').join('') +
        '</select></td>' +
        '<td><input type="text" class="cname" id="cname' + i +
        '" value="' + esc(c.name).replace(/"/g, '&quot;') + '"></td></tr>';
    });
    h += '</tbody></table>' +
      '<div class="hint">※ 引張専用材等の収束計算済みの応力' +
      '（ケースに長期＋水平荷重が合算済み）は「短期 (組合せ済み)」を' +
      '選んでください。長期を二重加算せず、そのケースの応力をそのまま' +
      '短期許容応力度 (長期の1.5倍) で検定します。' +
      '応力ファイルを変更した場合は再度「ケース読込」してください。</div>';
    $('check_cases_box').innerHTML = h;
    renderWoodMaterials(j);
    renderRcSections(j);
  } catch (e) { setMsg('check_msg', esc(e.message), 'msg-err'); }
}

// ===== RC配筋設定 (RC断面検出時のみ表示) =====
// mgtに配筋(*REBAR-BEAM/*REBAR-COLUMN)の無いRC断面は、断面名の符号
// (先頭の"_"より前, 例: RW20) 単位で種別(壁/梁/柱)と配筋を指定する。
// MATLAB版 MIDASratioplot_fig.m のlistdlg分類+RCG/RCC/RCW_input相当。
// 設定は localStorage (mgtkit_rcw_settings / mgtkit_rc_globals) に保存。
const RC_DI_OPTIONS = [10, 13, 16, 19, 22];                    // 壁 縦・横筋
const RCG_MAIN_DI = [13, 16, 19, 22, 25, 29, 32, 35, 38, 41];  // 梁主筋
const RCG_STRP_DI = [10, 13, 16, 38, 41, 51];                  // 梁あばら筋
const RCG_STRP_LB = ['D10', 'D13', 'D16',
                     'U9(ウルボン)', 'U10(ウルボン)', 'U13(ウルボン)'];
const RCC_MAIN_DI = [13, 16, 19, 22, 25, 29, 32];              // 柱主筋
const RCC_HOOP_DI = [10, 13, 16];                              // 柱HOOP

function rcwStore() {
  try { return JSON.parse(localStorage.getItem('mgtkit_rcw_settings')) || {}; }
  catch (e) { return {}; }
}
function rcGlobalStore() {
  try { return JSON.parse(localStorage.getItem('mgtkit_rc_globals')) || {}; }
  catch (e) { return {}; }
}

function diSelectOpts(id, val, opts, labels) {
  let h = '<select id="' + id + '">';
  opts.forEach((d, k) => {
    h += '<option value="' + d + '"' + (+val === d ? ' selected' : '') +
         '>' + (labels ? labels[k] : 'D' + d) + '</option>';
  });
  return h + '</select>';
}
function diSelect(id, val) { return diSelectOpts(id, val, RC_DI_OPTIONS); }

let RC_SECTIONS = null;  // check_cases の rc_sections (norebar/beams/columns)
let RC_SAVED = {};       // 符号ごとの保存設定 (localStorageと同期)

// 符号から種別(壁/梁/柱)の初期推定 (あくまで初期値。表でユーザーが確認する)
function guessRcKind(pf) {
  const s = String(pf).toUpperCase();
  if (/^(FG|RG|WG|CG|SG)/.test(s)) return 'beam';
  if (/^(RW|EW|CW|W)/.test(s)) return 'wall';
  if (/^C/.test(s)) return 'column';
  if (/^(G|B|F)/.test(s)) return 'beam';
  return 'wall';
}

// 保存データを取得 (旧形式=壁の設定のみ{v_di,...} は新形式へ変換)
// 新形式: {kind:'wall'|'beam'|'column', wall:{...}, beam:{...}, col:{...}}
function rcSavedEntry(pf) {
  let s = RC_SAVED[pf];
  if (!s) s = RC_SAVED[pf] = {};
  if (!s.kind) {
    if (s.v_di !== undefined || s.mode !== undefined) {
      RC_SAVED[pf] = s = {kind: 'wall', wall: s};
    } else {
      s.kind = guessRcKind(pf);
    }
  }
  if (!s.wall) s.wall = {};
  if (!s.beam) s.beam = {};
  if (!s.col) s.col = {};
  return s;
}

// 種別ごとの配筋入力セルHTML
function rcKindCellHtml(i, s) {
  if (s.kind === 'wall') {
    const w = s.wall;
    return '縦筋 ' + diSelect('rcw_vdi' + i, w.v_di || 10) +
      '@<input type="number" id="rcw_vp' + i + '" value="' +
      (w.v_pitch || 200) + '" style="width:60px"> ' +
      '横筋 ' + diSelect('rcw_hdi' + i, w.h_di || 10) +
      '@<input type="number" id="rcw_hp' + i + '" value="' +
      (w.h_pitch || 200) + '" style="width:60px"> ' +
      '<select id="rcw_sd' + i + '">' +
      '<option value="2"' + ((w.sd || 2) === 2 ? ' selected' : '') +
      '>ダブル配筋</option>' +
      '<option value="1"' + (w.sd === 1 ? ' selected' : '') +
      '>シングル配筋</option></select> ' +
      '<select id="rcw_mode' + i + '">' +
      '<option value="1"' + ((w.mode || 1) === 1 ? ' selected' : '') +
      '>端部: 径とピッチから自動</option>' +
      '<option value="2"' + (w.mode === 2 ? ' selected' : '') +
      '>端部補強筋を指定</option></select> ' +
      '端部片側 <input type="number" id="rcw_nr' + i + '" value="' +
      (w.num_rebar || 2) + '" style="width:50px">本';
  }
  if (s.kind === 'beam') {
    const b = s.beam;
    const udan = b.u_dan || 1;
    const ddan = b.d_dan || 1;
    const same = b.same !== false;  // 既定: 下端筋は上端筋と同じ
    let h = '<span class="rcglab">上端筋</span><select id="rcg_udan' + i +
      '" onchange="rcgDanChange(' + i + ')">';
    [1, 2, 3].forEach(d => {
      h += '<option value="' + d + '"' + (udan === d ? ' selected' : '') +
           '>' + d + '段</option>';
    });
    h += '</select> ';
    for (let k = 0; k < 3; k++) {
      h += '<span class="rcgpair">' +
        diSelectOpts('rcg_udi' + i + '_' + k,
                     (b.u_di || [])[k] || 25, RCG_MAIN_DI) +
        'x<input type="number" id="rcg_un' + i + '_' + k + '" value="' +
        ((b.u_num || [])[k] || (k === 0 ? 4 : 0)) +
        '" style="width:45px">本</span> ';
    }
    h += '<br><span class="rcglab">下端筋 <label class="inline">' +
      '<input type="checkbox" ' +
      'id="rcg_same' + i + '"' + (same ? ' checked' : '') +
      ' onchange="rcgDanChange(' + i + ')">同上</label></span>' +
      '<select id="rcg_ddan' + i + '" onchange="rcgDanChange(' + i + ')">';
    [1, 2, 3].forEach(d => {
      h += '<option value="' + d + '"' + (ddan === d ? ' selected' : '') +
           '>' + d + '段</option>';
    });
    h += '</select> ';
    for (let k = 0; k < 3; k++) {
      h += '<span class="rcgpair">' +
        diSelectOpts('rcg_ddi' + i + '_' + k,
                     (b.d_di || [])[k] || 25, RCG_MAIN_DI) +
        'x<input type="number" id="rcg_dn' + i + '_' + k + '" value="' +
        ((b.d_num || [])[k] || (k === 0 ? 4 : 0)) +
        '" style="width:45px">本</span> ';
    }
    h += '<br><span class="rcglab">あばら筋(STRP)</span>' +
      diSelectOpts('rcg_sdi' + i, b.s_di || 10, RCG_STRP_DI, RCG_STRP_LB) +
      '@<input type="number" id="rcg_sp' + i + '" value="' +
      (b.s_pitch || 200) + '" style="width:60px"> ' +
      '<input type="number" id="rcg_sn' + i + '" value="' +
      (b.s_num || 2) + '" style="width:45px">本 ' +
      '<label class="inline"><input type="checkbox" id="rcg_l43_' + i +
      '"' + (b.l43 ? ' checked' : '') +
      '>引張鉄筋比の割増(4/3)対象</label>';
    return h;
  }
  const c = s.col;
  return '主筋 ' + diSelectOpts('rcc_mdi' + i, c.m_di || 22, RCC_MAIN_DI) +
    'x<input type="number" id="rcc_mn' + i + '" value="' +
    (c.m_num || 8) + '" step="4" style="width:50px">本' +
    '<span class="hint">(4の倍数)</span> ' +
    'HOOP ' + diSelectOpts('rcc_hdi' + i, c.h_di || 10, RCC_HOOP_DI) +
    '@<input type="number" id="rcc_hp' + i + '" value="' +
    (c.h_pitch || 200) + '" style="width:60px"> ' +
    '<input type="number" id="rcc_hn' + i + '" value="' + (c.h_num || 2) +
    '" style="width:45px">本(XY各)';
}

// 梁: 段数に応じて2・3段目を無効化。「上端と同じ」なら下端筋を無効化
function rcgDanChange(i) {
  if (!$('rcg_udan' + i)) return;
  const udan = +$('rcg_udan' + i).value;
  const same = $('rcg_same' + i).checked;
  const ddan = +$('rcg_ddan' + i).value;
  for (let k = 0; k < 3; k++) {
    $('rcg_udi' + i + '_' + k).disabled = k >= udan;
    $('rcg_un' + i + '_' + k).disabled = k >= udan;
    $('rcg_ddi' + i + '_' + k).disabled = same || k >= ddan;
    $('rcg_dn' + i + '_' + k).disabled = same || k >= ddan;
  }
  $('rcg_ddan' + i).disabled = same;
}

// 現在のセル入力値を RC_SAVED へ取り込む (種別切替・検定実行時)
function harvestRcRow(tr) {
  const i = +tr.dataset.idx;
  const pf = tr.dataset.prefix;
  const s = rcSavedEntry(pf);
  if (s.kind === 'wall' && $('rcw_vdi' + i)) {
    s.wall = {mode: +$('rcw_mode' + i).value, sd: +$('rcw_sd' + i).value,
              v_di: +$('rcw_vdi' + i).value, v_pitch: +$('rcw_vp' + i).value,
              h_di: +$('rcw_hdi' + i).value, h_pitch: +$('rcw_hp' + i).value,
              num_rebar: +$('rcw_nr' + i).value};
  } else if (s.kind === 'beam' && $('rcg_udan' + i)) {
    const rd = (p, k) => +$(p + i + '_' + k).value;
    s.beam = {
      u_dan: +$('rcg_udan' + i).value,
      u_di: [rd('rcg_udi', 0), rd('rcg_udi', 1), rd('rcg_udi', 2)],
      u_num: [rd('rcg_un', 0), rd('rcg_un', 1), rd('rcg_un', 2)],
      same: $('rcg_same' + i).checked,
      d_dan: +$('rcg_ddan' + i).value,
      d_di: [rd('rcg_ddi', 0), rd('rcg_ddi', 1), rd('rcg_ddi', 2)],
      d_num: [rd('rcg_dn', 0), rd('rcg_dn', 1), rd('rcg_dn', 2)],
      s_di: +$('rcg_sdi' + i).value, s_pitch: +$('rcg_sp' + i).value,
      s_num: +$('rcg_sn' + i).value,
      l43: $('rcg_l43_' + i).checked};
  } else if (s.kind === 'column' && $('rcc_mdi' + i)) {
    s.col = {m_di: +$('rcc_mdi' + i).value, m_num: +$('rcc_mn' + i).value,
             h_di: +$('rcc_hdi' + i).value, h_pitch: +$('rcc_hp' + i).value,
             h_num: +$('rcc_hn' + i).value};
  }
}

// 種別ドロップダウン変更 → 入力欄を種別に合わせて描き直す
function rcKindChange(i) {
  const tr = document.querySelector(
    '#check_rc_box tr.rcrow[data-idx="' + i + '"]');
  if (!tr) return;
  harvestRcRow(tr);  // 旧種別の入力値を保持してから切替
  const s = rcSavedEntry(tr.dataset.prefix);
  s.kind = $('rc_kind' + i).value;
  $('rc_cell' + i).innerHTML = rcKindCellHtml(i, s);
  if (s.kind === 'beam') rcgDanChange(i);
  rcColumnWarn();
}

// RC柱の注意表示: 角形(UCH物件)・丸形(Tsune物件)とも照合済み(2026-07-11)の
// ため通常は表示なし。TAPER柱のみ未移植(検定対象になると明示エラー)
function rcColumnWarn() {
  const box = $('rc_col_warn');
  if (box) box.innerHTML = '';
}

function renderRcSections(j) {
  RC_SECTIONS = j.rc_sections || null;
  const hasNR = !!(RC_SECTIONS && RC_SECTIONS.norebar &&
                   RC_SECTIONS.norebar.length);
  const hasBeams = !!(RC_SECTIONS && RC_SECTIONS.beams &&
                      RC_SECTIONS.beams.length);
  const hasCols = !!(RC_SECTIONS && RC_SECTIONS.columns &&
                     RC_SECTIONS.columns.length);
  const hasSrc = !!(RC_SECTIONS && RC_SECTIONS.has_src);
  if (!hasNR && !hasBeams && !hasCols && !hasSrc) {
    $('check_rc_box').innerHTML = '';
    return;
  }
  RC_SAVED = rcwStore();
  const g = rcGlobalStore();
  let h = '';
  if (hasNR) {
  // 符号(prefix)ごとにまとめる
  const groups = {};
  RC_SECTIONS.norebar.forEach(w => {
    (groups[w.prefix] = groups[w.prefix] || []).push(w);
  });
  h += '<div class="row"><b>RC配筋設定</b> <span class="hint">' +
    '(RC材料でmgtに配筋の無い断面。符号ごとに種別(壁/梁/柱)を確認のうえ' +
    '配筋を指定します。設定はブラウザに保存され再利用されます。' +
    'mgt側で入力する場合はMIDASの *REBAR-BEAM (梁) / *REBAR-COLUMN (柱)' +
    'に配筋を設定してください)</span></div>' +
    '<table class="res"><thead><tr><th>符号</th><th>断面数</th>' +
    '<th>寸法 [mm]</th><th>種別</th><th>配筋</th>' +
    '</tr></thead><tbody>';
  // 既定例: 符号ごとの標準的な壁配筋 (保存済み設定が無い場合の初期値)
  const RC_SEED = {RW20: {v_di: 10, h_di: 10}, RW20A: {v_di: 13, h_di: 13},
                   RW45: {v_di: 13, h_di: 13}};
  Object.keys(groups).forEach((pf, i) => {
    const ws = groups[pf];
    if (!RC_SAVED[pf] && RC_SEED[pf]) {
      RC_SAVED[pf] = {kind: 'wall', wall: RC_SEED[pf]};
    }
    const s = rcSavedEntry(pf);
    const dims = ws.map(w => (w.t != null ? w.t + 'x' + w.D : '-'));
    const dimLabel = ws.length === 1 ? dims[0] :
      (dims[0] + ' 他' + (ws.length - 1) + '断面');
    h += '<tr data-prefix="' + esc(pf) + '" data-idx="' + i +
      '" class="rcrow">' +
      '<td class="name">' + esc(pf) + '</td><td>' + ws.length + '</td>' +
      '<td title="' + esc(dims.join(', ')) + '">' + esc(dimLabel) + '</td>' +
      '<td><select class="rckind" id="rc_kind' + i +
      '" onchange="rcKindChange(' + i + ')">' +
      '<option value="wall"' + (s.kind === 'wall' ? ' selected' : '') +
      '>壁</option>' +
      '<option value="beam"' + (s.kind === 'beam' ? ' selected' : '') +
      '>梁</option>' +
      '<option value="column"' + (s.kind === 'column' ? ' selected' : '') +
      '>柱</option></select></td>' +
      '<td class="rcgcell" id="rc_cell' + i + '">' + rcKindCellHtml(i, s) + '</td></tr>';
  });
  h += '</tbody></table>' +
    '<div class="hint">※ 種別は断面名の符号からの推定初期値です。' +
    '必ず確認してください。壁の「径とピッチから自動」はMATLAB版RCW_input' +
    'と同じく端部補強筋 (縦筋の一段太径・片側4本相当) を自動配置し、' +
    '縦筋本数を壁長から算定します。「端部補強筋を指定」は端部片側本数と' +
    '縦筋欄の径を端部補強筋として使います。' +
    '梁のi端・中央・j端は同一配筋で、主筋SD値は径から自動' +
    ' (D18以下=SD295 / D19〜D28=SD345 / D29以上=SD390、ウルボン=1275)。' +
    '</div>';
  }  // end if (hasNR)
  h += '<div id="rc_col_warn"></div>';
  // 全体設定 (配筋未指定断面が無くてもRC梁・SRCがあれば表示)
  h += '<div class="row"><b>検定の全体設定 (せん断割増ルート等)</b> ' +
    '<label class="inline">面内曲げの鉄筋 (壁式のみ)' +
    ' <select id="rc_method"><option value="3"' +
    ((g.method_rcw || 3) == 3 ? ' selected' : '') +
    '>端部補強筋+縦筋</option><option value="1"' +
    (g.method_rcw == 1 ? ' selected' : '') + '>端部補強筋</option>' +
    '<option value="2"' + (g.method_rcw == 2 ? ' selected' : '') +
    '>縦筋</option></select></label>' +
    '<label class="inline">せん断設計 RCQ <select id="rc_RCQ">' +
    '<option value="1"' + ((g.RCQ || 1) == 1 ? ' selected' : '') +
    '>1: 応力割増による</option>' +
    '<option value="2"' + (g.RCQ == 2 ? ' selected' : '') +
    '>2: 部材端終局モーメント時のせん断による</option>' +
    '<option value="3"' + (g.RCQ == 3 ? ' selected' : '') +
    '>3: 両者の小さい値による</option></select></label>' +
    '<label class="inline">壁かぶり <input type="number" id="rc_wcover" ' +
    'value="' + (g.wall_cover || 40) + '" style="width:60px">mm</label>' +
    '<label class="inline">梁かぶり <input type="number" id="rc_bcover" ' +
    'value="' + (g.beam_cover || 40) + '" style="width:60px">mm</label>' +
    '<label class="inline">柱かぶり <input type="number" id="rc_ccover" ' +
    'value="' + (g.column_cover || 40) + '" style="width:60px">mm</label>' +
    '</div>' +
    '<div class="row"><label class="inline">せん断力の割増 (RCルート) ' +
    '<select id="rc_qup"><option value="route1"' +
    ((g.qup_mode || 'route1') === 'route1' ? ' selected' : '') +
    '>ルート1 (柱梁1.5 壁2.0 SRC1.0)</option>' +
    '<option value="route2"' + (g.qup_mode === 'route2' ? ' selected' : '') +
    '>ルート2-1 2-2 (柱梁2.0 壁2.0 SRC2.0)</option>' +
    '<option value="route3"' + (g.qup_mode === 'route3' ? ' selected' : '') +
    '>ルート3 (柱梁1.5 壁1.0 SRC1.0)</option>' +
    '<option value="custom"' + (g.qup_mode === 'custom' ? ' selected' : '') +
    '>数値指定</option>' +
    '<option value="none"' + (g.qup_mode === 'none' ? ' selected' : '') +
    '>割増なし</option></select></label>' +
    '<label class="inline">数値指定: 柱梁 <input type="number" ' +
    'id="rc_qup_beam" value="' + (g.qup_beam || 2.0) +
    '" step="0.1" style="width:60px"></label>' +
    '<label class="inline">壁 <input type="number" id="rc_qup_wall" ' +
    'value="' + (g.qup_wall || 2.0) + '" step="0.1" style="width:60px">' +
    '</label>' +
    '<label class="inline">SRC <input type="number" id="rc_qup_src" ' +
    'value="' + (g.qup_src || 2.0) + '" step="0.1" style="width:60px">' +
    '</label>' +
    '<label class="inline">部材長の扱い <select id="rc_select_unit">' +
    '<option value=""' + (!g.select_unit_zero ? ' selected' : '') +
    '>要素単位</option>' +
    '<option value="0"' + (g.select_unit_zero ? ' selected' : '') +
    '>結合部材単位 (一括表示相当)</option></select></label>' +
    '</div>';
  if (hasBeams) {
    const l43g = g.l43_mgt || {};
    h += '<div class="row"><b>引張鉄筋比の割増(4/3)対象とする梁</b> ' +
      '<span class="hint">mgtの梁配筋 (*REBAR-BEAM) で検定されるRC梁です。' +
      '割増対象とする梁はチェックしてください (MATLAB版の割増対象選択' +
      'ダイアログ相当)。</span></div><div class="row">';
    RC_SECTIONS.beams.forEach(b => {
      h += '<label class="inline"><input type="checkbox" class="l43mgt" ' +
        'data-no="' + b.no + '"' + (l43g[b.no] ? ' checked' : '') + '>' +
        esc(b.name.split('_')[0]) + ' (' + b.no + ')</label> ';
    });
    h += '</div>';
  }
  if (hasCols) {
    h += '<div class="hint">RC柱断面 (' +
      RC_SECTIONS.columns.map(c => esc(c.name.split('_')[0])).join(', ') +
      ') はmgtの柱配筋 (*REBAR-COLUMN) を使用します。</div>';
  }
  $('check_rc_box').innerHTML = h;
  // 描画後の初期状態反映 (段数・下端筋同上の有効/無効、柱注意)
  document.querySelectorAll('#check_rc_box tr.rcrow').forEach(tr => {
    rcgDanChange(+tr.dataset.idx);
  });
  rcColumnWarn();
}

// 数値入力の必須チェック (空欄は +'' === 0 になり、割増0倍・かぶり0mm等の
// 危険な値として黙って通ってしまうため明示エラーにする) 2026-07-10
function _reqPos(v, label) {
  if (!isFinite(v) || v <= 0) {
    throw new Error(label + ' は正の数値を入力してください (空欄・0以下は不可)');
  }
}

function collectRcParams() {
  const hasNR = !!(RC_SECTIONS && RC_SECTIONS.norebar &&
                   RC_SECTIONS.norebar.length);
  const hasBeams = !!(RC_SECTIONS && RC_SECTIONS.beams &&
                      RC_SECTIONS.beams.length);
  const hasCols = !!(RC_SECTIONS && RC_SECTIONS.columns &&
                     RC_SECTIONS.columns.length);
  const hasSrc = !!(RC_SECTIONS && RC_SECTIONS.has_src);
  if (!hasNR && !hasBeams && !hasCols && !hasSrc) return null;
  if (!$('rc_qup')) return null;  // 設定ボックス未描画
  const rcw = [], rcg = [], rcc = [], L_43 = [];
  document.querySelectorAll('#check_rc_box tr.rcrow').forEach(tr => {
    const pf = tr.dataset.prefix;
    harvestRcRow(tr);
    const s = rcSavedEntry(pf);
    const secs = RC_SECTIONS.norebar.filter(w => w.prefix === pf);
    if (s.kind === 'wall') {
      const w = s.wall;
      _reqPos(w.v_di, pf + ' 縦筋径'); _reqPos(w.v_pitch, pf + ' 縦筋ピッチ');
      _reqPos(w.h_di, pf + ' 横筋径'); _reqPos(w.h_pitch, pf + ' 横筋ピッチ');
      secs.forEach(sec => rcw.push(Object.assign({no: sec.no}, w)));
    } else if (s.kind === 'beam') {
      const b = s.beam;
      for (let k = 0; k < b.u_dan; k++) {
        _reqPos(b.u_di[k], pf + ' 上端筋' + (k + 1) + '段目の径');
        _reqPos(b.u_num[k], pf + ' 上端筋' + (k + 1) + '段目の本数');
      }
      if (!b.same) {
        for (let k = 0; k < b.d_dan; k++) {
          _reqPos(b.d_di[k], pf + ' 下端筋' + (k + 1) + '段目の径');
          _reqPos(b.d_num[k], pf + ' 下端筋' + (k + 1) + '段目の本数');
        }
      }
      _reqPos(b.s_di, pf + ' STRP径');
      _reqPos(b.s_pitch, pf + ' STRPピッチ');
      _reqPos(b.s_num, pf + ' STRP本数');
      const row = {
        u_dan: b.u_dan, u_di: b.u_di, u_num: b.u_num,
        d_dan: b.same ? b.u_dan : b.d_dan,
        d_di: b.same ? b.u_di : b.d_di,
        d_num: b.same ? b.u_num : b.d_num,
        s_di: b.s_di, s_pitch: b.s_pitch, s_num: b.s_num};
      secs.forEach(sec => {
        rcg.push(Object.assign({no: sec.no}, row));
        if (b.l43) L_43.push(sec.no);
      });
    } else {
      const c = s.col;
      _reqPos(c.m_di, pf + ' 主筋径');
      _reqPos(c.m_num, pf + ' 主筋本数');
      if (c.m_num % 4 !== 0) {
        throw new Error(pf + ' の主筋本数は4の倍数を入力してください ' +
                        '(入力値: ' + c.m_num + ')');
      }
      _reqPos(c.h_di, pf + ' HOOP径');
      _reqPos(c.h_pitch, pf + ' HOOPピッチ');
      _reqPos(c.h_num, pf + ' HOOP本数');
      secs.forEach(sec => rcc.push(Object.assign({no: sec.no}, c)));
    }
  });
  // mgt配筋のRC梁の引張鉄筋比割増(4/3)対象
  const l43_mgt = {};
  document.querySelectorAll('#check_rc_box input.l43mgt').forEach(cb => {
    if (cb.checked) {
      L_43.push(+cb.dataset.no);
      l43_mgt[cb.dataset.no] = true;
    }
  });
  const g = {
    method_rcw: +$('rc_method').value, RCQ: +$('rc_RCQ').value,
    wall_cover: +$('rc_wcover').value, beam_cover: +$('rc_bcover').value,
    column_cover: +$('rc_ccover').value,
    qup_mode: $('rc_qup').value,
    qup_beam: +$('rc_qup_beam').value, qup_wall: +$('rc_qup_wall').value,
    qup_src: +$('rc_qup_src').value,
    l43_mgt: l43_mgt,
    select_unit_zero: $('rc_select_unit').value === '0'};
  if (g.qup_mode === 'custom') {
    _reqPos(g.qup_beam, 'せん断割増係数(柱・梁)');
    _reqPos(g.qup_wall, 'せん断割増係数(壁)');
    _reqPos(g.qup_src, 'せん断割増係数(SRC)');
  }
  _reqPos(g.wall_cover, 'RC壁のかぶり厚');
  _reqPos(g.beam_cover, 'RC梁のかぶり厚');
  _reqPos(g.column_cover, 'RC柱のかぶり厚');
  try {
    localStorage.setItem('mgtkit_rcw_settings', JSON.stringify(RC_SAVED));
    localStorage.setItem('mgtkit_rc_globals', JSON.stringify(g));
  } catch (e) { /* localStorage不可でも検定は続行 */ }
  return {rcw: rcw, rcg: rcg, rcc: rcc, L_43: L_43,
          method_rcw: g.method_rcw, RCQ: g.RCQ,
          wall_cover: g.wall_cover, beam_cover: g.beam_cover,
          column_cover: g.column_cover,
          qup_mode: g.qup_mode, qup_beam: g.qup_beam, qup_wall: g.qup_wall,
          qup_src: g.qup_src,
          select_unit: $('rc_select_unit').value};
}

// 木材料の W_AL 行 (木材種別) 選択ドロップダウン
// 材料名から自動対応できた材料は既定選択済み、できない材料は要選択
function renderWoodMaterials(j) {
  if (!j.wood_materials || !j.wood_materials.length) {
    $('check_wood_box').innerHTML = '';
    return;
  }
  const menu = j.w_al_menu || [];
  const names = j.w_al_names || [];
  const SP_GROUPS = ['からまつ・べいまつ系', 'おうしゅうあかまつ系',
                     'すぎ系'];
  // 行番号 → 属する集成材項目の樹種群 (初期選択の判定用)
  const idx2sp = {};
  menu.forEach(g => g.items.forEach(it => {
    if (it.sp) Object.entries(it.sp).forEach(([sp, v]) => idx2sp[v] = sp);
  }));
  let h = '<div class="row"><b>木材料の許容応力度 (W_AL表の行選択)</b></div>' +
    '<table class="res"><thead><tr><th>材料番号</th><th>材料名 (mgt)</th>' +
    '<th>木材種別 (W_AL)</th><th>集成材の樹種</th></tr></thead><tbody>';
  j.wood_materials.forEach((w, i) => {
    const auto = w.w_index > 0;
    const curSp = idx2sp[w.w_index] || SP_GROUPS[0];
    h += '<tr><td>' + w.no + '</td><td class="name">' + esc(w.name) +
      '</td><td><select class="wsel" data-no="' + w.no + '" id="wsel' + i +
      '"' + (auto ? '' : ' style="border:2px solid #c00"') + '>';
    if (!auto) {
      h += '<option value="0" selected>-- 自動対応不可: 選択してください --' +
           '</option>';
    }
    menu.forEach(g => {
      h += '<optgroup label="' + esc(g.label) + '">';
      g.items.forEach(it => {
        if (it.sp) {
          const v = it.sp[curSp] !== undefined
            ? it.sp[curSp] : Object.values(it.sp)[0];
          const isSel = Object.values(it.sp).includes(w.w_index);
          h += '<option value="' + v + '" data-sp=\'' +
            JSON.stringify(it.sp) + '\'' + (isSel ? ' selected' : '') +
            '>' + esc(it.label) + '</option>';
        } else {
          h += '<option value="' + it.value + '"' +
            (w.w_index === it.value ? ' selected' : '') + '>' +
            esc(it.label) + '</option>';
        }
      });
      h += '</optgroup>';
    });
    h += '</select></td><td><select class="wspsel" data-idx="' + i + '">' +
      SP_GROUPS.map(sp => '<option value="' + esc(sp) + '"' +
        (sp === curSp ? ' selected' : '') + '>' + esc(sp) +
        '</option>').join('') +
      '</select></td></tr>';
  });
  h += '</tbody></table>' +
    '<div class="hint">※ 材料名から自動対応できた行は選択済みです。' +
    '赤枠の材料は選択してください (未選択のまま検定するとその材料の' +
    '部材でエラーになります)。集成材は左で区分・等級を、右の' +
    '「集成材の樹種」で樹種群 (せん断強度) を選びます。</div>';
  $('check_wood_box').innerHTML = h;
  // 樹種セレクト変更 → 集成材項目の行番号を差し替え (選択は維持)
  document.querySelectorAll('#check_wood_box select.wspsel').forEach(sel => {
    sel.addEventListener('change', () => {
      const wsel = $('wsel' + sel.dataset.idx);
      [...wsel.options].forEach(o => {
        if (!o.dataset.sp) return;
        const sp = JSON.parse(o.dataset.sp);
        if (sp[sel.value] !== undefined) o.value = sp[sel.value];
      });
    });
  });
  // 行セレクト変更 → 集成材なら樹種セレクトを追従
  document.querySelectorAll('#check_wood_box select.wsel').forEach(sel => {
    sel.addEventListener('change', () => {
      const i = sel.id.replace('wsel', '');
      const sp = document.querySelector(
        '#check_wood_box select.wspsel[data-idx="' + i + '"]');
      const opt = sel.selectedOptions[0];
      if (!sp || !opt || !opt.dataset.sp) return;
      const m = JSON.parse(opt.dataset.sp);
      for (const [g, v] of Object.entries(m)) {
        if (String(v) === String(sel.value)) { sp.value = g; break; }
      }
    });
  });
}

function collectWoodMaterialMap() {
  const sels = [...document.querySelectorAll('#check_wood_box select.wsel')];
  if (!sels.length) return null;  // 未読込 → 材料名からの自動対応に任せる
  return sels.map(s => ({no: +s.dataset.no, w_index: +s.value}));
}

function collectCaseTypes() {
  const sels = [...document.querySelectorAll('#check_cases_box select.ctype')];
  if (!sels.length) return null;  // 未読込 → サーバ側で自動判定
  return sels.map((s, i) => ({no: +s.dataset.no, type: s.value,
                              name: $('cname' + i).value.trim()}));
}

// ---------------- 構造図DXF (新規版) ----------------
let SDX_LEVELS = [];
let SDX_FRAMES = [];

function refreshStructPrevSel() {
  const sel = $('sdx_prev_sel');
  if (!sel) return;
  const opts = [];
  SDX_FRAMES.forEach(f =>
    opts.push('<option value="axis:' + esc(f.key) + '">軸組 ' +
              esc(f.label) + '</option>'));
  SDX_LEVELS.forEach(pl =>
    opts.push('<option value="plan:' + esc(pl.key) + '">伏図 ' +
              esc(pl.label) + '</option>'));
  sel.innerHTML = opts.join('');
}

async function loadStructInfo() {
  setMsg('sdx_info_msg', '', '');
  try {
    const j = await api('/api/struct_info',
      {mgt_path: $('mgt_path').value});
    SDX_LEVELS = j.plans ||
      (j.levels || []).map(z => ({key: String(z),
                                  label: (+z).toFixed(3) + 'm'}));
    SDX_FRAMES = j.frames || [];
    $('sdx_axes_box').innerHTML = SDX_FRAMES.map(f =>
      '<label><input type="checkbox" class="sdxframe" value="' +
      esc(f.key) + '"' + (f.n_col >= 2 ? ' checked' : '') + '> ' +
      esc(f.label) + ' <span class="hint">柱' + f.n_col + '</span></label>'
      ).join(' ') || '<span class="hint">柱が見つかりません</span>';
    $('sdx_levels_box').innerHTML = SDX_LEVELS.map(pl =>
      '<label><input type="checkbox" class="sdxlevel" value="' +
      esc(pl.key) + '"> ' + esc(pl.label) + '</label>').join(' ') ||
      '<span class="hint">伏図の候補が見つかりません</span>';
    setMsg('sdx_info_msg', '部材 ' + j.n_members + ' (柱' + j.kinds.column +
           '/梁' + j.kinds.beam + '/斜材' + j.kinds.brace + ')・ピン端あり ' +
           j.n_pin + ' 部材・伏図 ' + SDX_LEVELS.length + ' 件',
           'msg-ok');
    $('sdx_info_msg').innerHTML += notesHtml(j.notes);
    refreshStructPrevSel();
  } catch (e) { setMsg('sdx_info_msg', esc(e.message), 'msg-err'); }
}

function structCommon() {
  return {mgt_path: $('mgt_path').value,
    paper: $('sdx_paper').value,
    scale: $('sdx_scale').value || null,
    pin_mm: +$('sdx_pin').value,
    text_mm: +$('sdx_text').value,
    limit_sec_no: +($('m_limit') ? $('m_limit').value : 9000)};
}

async function runStructPreview() {
  setMsg('sdx_msg', '', '');
  try {
    const v = $('sdx_prev_sel').value;
    if (!v) { setMsg('sdx_msg', 'プレビュー対象がありません。mgt読み込みと' +
                     'レベル読込を先に実行してください。', 'msg-err'); return; }
    const [kind, key] = [v.slice(0, v.indexOf(':')),
                         v.slice(v.indexOf(':') + 1)];
    const req = structCommon();
    req.kind = kind; req.key = key;
    const j = await api('/api/struct_preview', req);
    setMsg('sdx_msg', 'プレビュー: 縮尺 1/' + j.scale +
           ' (自動選定の場合はここで確認して固定できます)', 'msg-ok');
    $('sdx_msg').innerHTML += notesHtml(j.notes);
    $('sdx_prev').innerHTML = '<img src="' + j.png_url +
      '" style="width:100%;border:1px solid #ccc">';
  } catch (e) { setMsg('sdx_msg', esc(e.message), 'msg-err'); }
}

async function runStructDxf() {
  setMsg('sdx_msg', '', ''); $('sdx_files').innerHTML = '';
  try {
    const req = structCommon();
    req.axes = checkedVals('sdxframe');
    req.levels = checkedVals('sdxlevel');
    req.one_file = $('sdx_onefile').value === '1';
    if ((!req.axes || !req.axes.length) &&
        (!req.levels || !req.levels.length)) {
      setMsg('sdx_msg', '構面またはレベルを選択してください。', 'msg-err');
      return;
    }
    const j = await api('/api/struct_dxf', req);
    let h = 'DXF ' + j.files.length + ' 件を生成しました → ' +
            esc(j.out_dir) + openFolderBtn(j.out_dir);
    if (j.info && j.info.length) {
      h += '<br>' + j.info.map(i => esc(i.title)).join(' / ');
    }
    setMsg('sdx_msg', h, 'msg-ok');
    $('sdx_msg').innerHTML += notesHtml(j.notes);
    $('sdx_files').innerHTML = j.files.map(f =>
      '<div class="pdfblock"><b>' + esc(f.name) + '</b>' +
      '<a class="dl" href="' + f.url + '">ダウンロード</a></div>').join('');
  } catch (e) { setMsg('sdx_msg', esc(e.message), 'msg-err'); }
}

// ---------------- QR図 (反力・せん断分担・偏心率) ----------------
let QR_INFO = null;

function qrPaths() {
  return {
    mgt_path: $('mgt_path').value,
    beam_stress_path: $('q_beam_stress_path').value.trim() ||
                      $('beam_stress_path').value.trim(),
    truss_stress_path: $('q_truss_stress_path').value.trim() ||
                       $('truss_stress_path').value.trim(),
    wall_stress_path: $('q_wall_stress_path').value.trim(),
    plate_stress_path: $('q_plate_stress_path').value.trim(),
    plate_up: +$('q_plate_up').value,
    reaction_path: $('q_reaction_path').value.trim(),
    deformation_path: $('q_deformation_path').value.trim(),
    limit_sec_no: +$('q_limit').value};
}

function qrCommonReq() {
  const req = qrPaths();
  req.case_names = QR_INFO ? QR_INFO.cases.map((c, i) => ({
    no: c, name: ($('q_cn_' + i) ? $('q_cn_' + i).value.trim() : '') ||
        ('CASE' + c)})) : [];
  // 層構成: 層番号 -> レベル値リスト
  const stories = {};
  if (QR_INFO) QR_INFO.z_points.forEach((z, i) => {
    const v = $('q_lv_' + i) ? $('q_lv_' + i).value.trim() : '';
    if (v !== '' && !isNaN(+v)) {
      (stories[+v] = stories[+v] || []).push(z);
    }
  });
  req.stories = Object.keys(stories).map(Number).sort((a, b) => a - b)
    .map(k => stories[k]);
  // 断面分類
  const cls = {};
  if (QR_INFO) QR_INFO.sections.forEach((s, i) => {
    if ($('q_sc_' + i)) cls[s.no] = $('q_sc_' + i).value;
  });
  req.sec_class = cls;
  req.axes = checkedVals('qgroup');
  const scope = document.querySelector('input[name="q_scope"]:checked');
  req.scope = scope ? scope.value : 'all';
  req.calc_groups = checkedVals('qcalcgroup');
  req.f_s = +$('q_f_s').value; req.f_d = +$('q_f_d').value;
  req.f_a = +$('q_f_a').value; req.f_t = +$('q_f_t').value;
  req.f_re = +$('q_f_re').value;
  req.line_location = +$('q_line_loc').value;
  req.axisname_location = +$('q_axis_loc').value;
  req.mg_l = +$('q_mg_l').value; req.mg_r = +$('q_mg_r').value;
  req.mg_t = +$('q_mg_t').value; req.mg_b = +$('q_mg_b').value;
  req.paper_size = +$('q_paper').value;
  req.paper_orient = +$('q_orient').value;
  return req;
}

async function loadQrInfo() {
  setMsg('qr_info_msg', '', '');
  try {
    const j = await api('/api/qr_info', qrPaths());
    QR_INFO = j;
    // ケース名テーブル
    $('qr_cases_box').innerHTML = j.cases.map((c, i) =>
      '<label class="inline">ケース' + c + ' <input type="text" id="q_cn_' +
      i + '" value="' + esc(j.default_names[i] || ('CASE' + c)) +
      '" style="width:6em"></label>').join(' ');
    // レベル→層番号テーブル
    $('qr_levels_box').innerHTML = j.z_points.map((z, i) =>
      '<label class="inline">' + z.toFixed(3) + 'm → 層 ' +
      '<input type="text" id="q_lv_' + i + '" value="' +
      (j.story_guess[i] === null ? '' : j.story_guess[i]) +
      '" style="width:3em"></label>').join(' ');
    // 断面分類
    const CLS = [['g', '梁'], ['c', '柱'], ['w', '壁'], ['v', 'ブレース'],
                 ['x', '対象外']];
    $('qr_secs_box').innerHTML = j.sections.map((s, i) =>
      '<label class="inline">' + s.no + ' ' + esc(s.name) +
      ' <select id="q_sc_' + i + '">' + CLS.map(c =>
        '<option value="' + c[0] + '"' + (s.guess === c[0] ? ' selected' : '')
        + '>' + c[1] + '</option>').join('') +
      '</select></label>').join(' ');
    // ケース選択欄 (各機能)
    const caseChecks = (cls) => j.cases.map((c, i) =>
      '<label><input type="checkbox" class="' + cls + '" value="' + c +
      '"> ' + esc(j.default_names[i] || ('CASE' + c)) + '</label>').join(' ');
    $('q_re_l_box').innerHTML = caseChecks('qrel');
    $('q_re_s_box').innerHTML = caseChecks('qres');
    $('q_cb_case_box').innerHTML = caseChecks('qcb');
    $('q_cb_z_box').innerHTML = j.z_points.map(z =>
      '<label><input type="checkbox" class="qcbz" value="' + z + '"> ' +
      z.toFixed(3) + 'm</label>').join(' ');
    const DIRS = [[1, 'X'], [2, 'Y'], [3, '45度'], [4, '135度']];
    $('q_sh_case_box').innerHTML = j.cases.map((c, i) =>
      '<label class="inline"><input type="checkbox" class="qsh" value="' + c +
      '"> ' + esc(j.default_names[i] || ('CASE' + c)) + ' 方向:<select ' +
      'id="q_shd_' + c + '">' + DIRS.map(d =>
        '<option value="' + d[0] + '">' + d[1] + '</option>').join('') +
      '</select></label>').join(' ');
    const nCases = (j.defo_cases && j.defo_cases.length)
      ? j.defo_cases : j.cases;
    const defoOpts = (nCases || []).map(c =>
      '<option value="' + c + '">' + c + '</option>').join('');
    $('q_dr_case_box').innerHTML = (j.defo_cases || []).map(c =>
      '<label><input type="checkbox" class="qdr" value="' + c + '"> ケース' +
      c + '</label>').join(' ');
    ['q_ce_n', 'q_ce_kx', 'q_ce_ky'].forEach(id =>
      $(id).innerHTML = defoOpts);
    if ($('q_ce_kx').options.length > 1) $('q_ce_kx').selectedIndex = 1;
    if ($('q_ce_ky').options.length > 2) $('q_ce_ky').selectedIndex = 2;
    setMsg('qr_info_msg', 'ケース ' + j.cases.length + ' / レベル ' +
           j.z_points.length + ' / 断面 ' + j.sections.length +
           ' 件を読み込みました。', 'msg-ok');
    $('qr_info_msg').innerHTML += notesHtml(j.notes);
  } catch (e) { setMsg('qr_info_msg', esc(e.message), 'msg-err'); }
}

async function runQrReaction() {
  setMsg('qr_re_msg', '', ''); $('qr_re_pdfs').innerHTML = '';
  try {
    const req = qrCommonReq();
    req.case_L = checkedVals('qrel').map(Number);
    req.case_S = checkedVals('qres').map(Number);
    req.case_kind = $('q_re_kind').value;
    const j = await api('/api/qr_reaction', req);
    setMsg('qr_re_msg', 'PDF ' + j.pdfs.length + ' 件を生成しました → ' +
           esc(j.out_dir) + openFolderBtn(j.out_dir), 'msg-ok');
    $('qr_re_msg').innerHTML += notesHtml(j.notes);
    $('qr_re_pdfs').innerHTML = pdfListHtml(j.pdfs, 'qre');
  } catch (e) { setMsg('qr_re_msg', esc(e.message), 'msg-err'); }
}

async function runQrCB() {
  setMsg('qr_cb_msg', '', ''); $('qr_cb_pdfs').innerHTML = '';
  try {
    const req = qrCommonReq();
    req.case_CB = checkedVals('qcb').map(Number);
    req.cb_levels = checkedVals('qcbz').map(Number);
    const j = await api('/api/qr_cb', req);
    let h = '柱脚データを生成しました → ' + esc(j.out_dir) +
            openFolderBtn(j.out_dir);
    if (j.xlsx_url) h += '　<a href="' + j.xlsx_url + '">' +
                         esc(j.xlsx_name) + ' をダウンロード</a>';
    setMsg('qr_cb_msg', h, 'msg-ok');
    $('qr_cb_msg').innerHTML += notesHtml(j.notes);
    if (j.pdfs) $('qr_cb_pdfs').innerHTML = pdfListHtml(j.pdfs, 'qcbp');
  } catch (e) { setMsg('qr_cb_msg', esc(e.message), 'msg-err'); }
}

async function runQrShear() {
  setMsg('qr_sh_msg', '', ''); $('qr_sh_pdfs').innerHTML = '';
  $('qr_sh_tex').innerHTML = '';
  try {
    const req = qrCommonReq();
    const cs = checkedVals('qsh').map(Number);
    req.case_S = cs;
    req.directions = {};
    cs.forEach(c => req.directions[c] = +$('q_shd_' + c).value);
    const j = await api('/api/qr_shear', req);
    setMsg('qr_sh_msg', 'PDF ' + j.pdfs.length + ' 件を生成しました → ' +
           esc(j.out_dir) + openFolderBtn(j.out_dir), 'msg-ok');
    $('qr_sh_msg').innerHTML += notesHtml(j.notes);
    $('qr_sh_pdfs').innerHTML = pdfListHtml(j.pdfs, 'qsh');
    if (j.tex_lines && j.tex_lines.length) {
      $('qr_sh_tex').innerHTML = '<h3>層せん断・分担率 TeX表ソース</h3>' +
        '<pre class="detailtext">' + esc(j.tex_lines.join('\n')) + '</pre>';
    }
  } catch (e) { setMsg('qr_sh_msg', esc(e.message), 'msg-err'); }
}

async function runQrDrift() {
  setMsg('qr_dr_msg', '', ''); $('qr_dr_pdfs').innerHTML = '';
  try {
    const req = qrCommonReq();
    req.delta_case = checkedVals('qdr').map(Number);
    const j = await api('/api/qr_drift', req);
    setMsg('qr_dr_msg', 'PDF ' + j.pdfs.length + ' 件を生成しました → ' +
           esc(j.out_dir) + openFolderBtn(j.out_dir), 'msg-ok');
    $('qr_dr_msg').innerHTML += notesHtml(j.notes);
    $('qr_dr_pdfs').innerHTML = pdfListHtml(j.pdfs, 'qdr');
  } catch (e) { setMsg('qr_dr_msg', esc(e.message), 'msg-err'); }
}

async function runQrCenter() {
  setMsg('qr_ce_msg', '', ''); $('qr_ce_pdfs').innerHTML = '';
  $('qr_ce_table').innerHTML = ''; $('qr_ce_tex').innerHTML = '';
  try {
    const req = qrCommonReq();
    req.N_case = +$('q_ce_n').value;
    req.KX_case = +$('q_ce_kx').value;
    req.KY_case = +$('q_ce_ky').value;
    req.k_mode = $('q_ce_mode').value;
    req.brace_baisu = $('q_ce_brace').value;
    const j = await api('/api/qr_center', req);
    setMsg('qr_ce_msg', 'PDF ' + j.pdfs.length + ' 件を生成しました → ' +
           esc(j.out_dir) + openFolderBtn(j.out_dir), 'msg-ok');
    $('qr_ce_msg').innerHTML += notesHtml(j.notes);
    $('qr_ce_pdfs').innerHTML = pdfListHtml(j.pdfs, 'qce');
    if (j.table && j.table.length) {
      let h = '<h3>重心・剛心・偏心率</h3><table class="res"><thead><tr>' +
        ['層', '重心gx', '重心gy', '剛心lx', '剛心ly', 'ねじり剛性KR',
         '弾力半径rex', 'rey', 'Rex', 'Rey', 'Fex', 'Fey'].map(x =>
          '<th>' + x + '</th>').join('') + '</tr></thead><tbody>';
      j.table.forEach(r => {
        h += '<tr>' + r.map(v => '<td>' + esc(v) + '</td>').join('') +
             '</tr>';
      });
      h += '</tbody></table>';
      $('qr_ce_table').innerHTML = h;
    }
    if (j.tex_lines && j.tex_lines.length) {
      $('qr_ce_tex').innerHTML = '<h3>偏心率 TeX表ソース</h3>' +
        '<pre class="detailtext">' + esc(j.tex_lines.join('\n')) + '</pre>';
    }
  } catch (e) { setMsg('qr_ce_msg', esc(e.message), 'msg-err'); }
}

function buildCheckReq() {
  // 検定実行と検定比図で同一の検定条件を送るための共通リクエスト組立て
  const bs = $('c_beam_stress_path').value.trim() ||
             $('beam_stress_path').value.trim();
  const ts = $('c_truss_stress_path').value.trim() ||
             $('truss_stress_path').value.trim();
  const req = {
    mgt_path: $('mgt_path').value,
    beam_stress_path: bs,
    truss_stress_path: ts,
    select_length: +$('c_select_length').value,
    judge_H: +$('c_judge_H').value,
    H_beam_RCsupport: +$('c_H_beam_RCsupport').value,
    limit_sec_no: +$('c_limit').value,
    c_up_STKR: +$('c_up_STKR').value,
    c_up_BCR: +$('c_up_BCR').value,
    c_up_BCP: +$('c_up_BCP').value,
    c_up_BSH: +$('c_up_BSH').value,
    case_types: collectCaseTypes(),
    wal_up: +$('c_wal_up').value,
    w_material_map: collectWoodMaterialMap(),
    plate_stress_path: ($('c_plate_use').value === 'matlab')
      ? $('c_plate_stress_path').value.trim() : '',
    plywood_stress_path: ($('c_plate_use').value === 'plywood')
      ? $('c_plate_stress_path').value.trim() : '',
    pw_qa: +$('pw_qa').value,
    wall_stress_path: $('c_wall_stress_path').value.trim(),
    plate_up: +$('c_plate_up').value,
    pl_long: +$('c_pl_long').value,
    pile_cover: +$('c_pile_cover').value,
    src_cover: +$('c_src_cover').value};
  _reqPos(req.src_cover, 'SRCコンクリートかぶり厚');
  _reqPos(req.pile_cover, 'RC杭かぶり厚');
  const pcp = collectPcParams();
  if (pcp) req.pc = pcp;
  const rc = collectRcParams();
  if (rc) {
    req.rc = rc;
    req.qup_mode = rc.qup_mode;
    req.qup_beam = rc.qup_beam;
    req.qup_wall = rc.qup_wall;
    req.qup_src = rc.qup_src;
    req.select_unit = rc.select_unit;
  }
  return req;
}

function collectPcParams() {
  // PC設定: スキップ指定か、いずれかの入力があるときのみ送る
  // (PC材料が無いモデルでは null のままでよい。PC材料があるのに未入力の
  //  場合はサーバ側がPC情報の入力を促すエラーを返す)
  if ($('pc_skip').checked) return {skip: true};
  const stress = $('c_pc_stress_path').value.trim();
  const cable = $('c_pc_cable_path').value.trim();
  const slab = $('c_pc_slab_path').value.trim();
  if (!stress && !cable && !slab) return null;
  return {
    PC_eff: +$('pc_eff').value,
    PC_type: +$('pc_type').value,
    DL_ratio: +$('pc_dl').value,
    pc_stress_path: stress,
    pc_cable_path: cable,
    pc_slab_path: slab};
}

async function runCheck() {
  setMsg('check_msg', '', ''); $('check_result').innerHTML = '';
  try {
    const req = buildCheckReq();
    const j = await api('/api/steel_check', req);
    CHECK = j;
    renderCheck(j);
  } catch (e) { setMsg('check_msg', esc(e.message), 'msg-err'); return; }
  // 検定値一覧表と検定詳細のTeXも続けて自動生成 (失敗しても検定結果は残す)
  await runRatioTex();
  await runDetailTex();
}

// ---------------- 検定詳細TeX (10detail) ----------------
async function runDetailTex() {
  if (!CHECK || !$('ctex_detail')) return;
  $('ctex_detail').innerHTML = '検定詳細を生成中…';
  try {
    const req = buildCheckReq();
    req.mode = $('dtex_mode') ? $('dtex_mode').value : 'all';
    const j = await api('/api/ratio_detail_tex', req);
    $('ctex_detail').innerHTML = '検定詳細 ' + j.files.map(f =>
      '<a class="dl" href="' + f.url + '&dl=1">' + esc(f.name) + '</a>'
      ).join(' ');
  } catch (e) {
    $('ctex_detail').innerHTML = '<span class="msg-err">検定詳細: ' +
      esc(e.message) + '</span>';
  }
}

// ---------------- 検定比図 ----------------
async function runPlotRatio() {
  setMsg('ratio_msg', '', ''); $('ratio_pdfs').innerHTML = '';
  try {
    if (!CHECK) {
      // 未実行なら先に検定を実行してサーバ側の結果を作る
      await runCheck();
      if (!CHECK) {
        setMsg('ratio_msg',
               '検定が完了していません (検定実行のエラーを確認してください)',
               'msg-err');
        return;
      }
    }
    const req = buildCheckReq();
    req.axes = checkedVals('rgroup');
    req.heights = selectedHeights('rhlevel');
    req.symbols = checkedVals('rsymgroup');
    const cs = checkedVals('rcase').map(Number);
    req.cases = cs.length ? cs : null;
    req.fig_unit = $('r_unit').checked;
    req.paper_size = +$('r_paper').value;
    req.f_s = +$('r_f_s').value;
    req.f_d = +$('r_f_d').value;
    req.f_a = +$('r_f_a').value;
    req.f_t = +$('r_f_t').value;
    req.line_location = +$('r_line_loc').value;
    req.axisname_location = +$('r_axis_loc').value;
    req.mg_l = +$('r_mg_l').value;
    req.mg_r = +$('r_mg_r').value;
    req.mg_t = +$('r_mg_t').value;
    req.mg_b = +$('r_mg_b').value;
    req.fig_format = $('r_format').value;
    const j = await api('/api/plot_ratio', req);
    const isTex = req.fig_format === 'tex';
    setMsg('ratio_msg', (isTex ? 'TeX図 ' : 'PDF ') + j.pdfs.length +
           ' 件を生成しました → ' + esc(j.out_dir) +
           openFolderBtn(j.out_dir), 'msg-ok');
    $('ratio_msg').innerHTML += notesHtml(j.notes);
    if (isTex) {
      $('ratio_pdfs').innerHTML = '<div class="pdfblock">' +
        j.pdfs.map((f, i) => '<div>' + (i === 0 ? '<b>一式: ' : '') +
          esc(f.name) + (i === 0 ? '</b>' : '') +
          '<a class="dl" href="' + f.url + '&dl=1">ダウンロード</a></div>'
        ).join('') +
        '<div class="hint">10ratioplot.tex は自己完結の1ファイルです。' +
        'これだけを計算書のフォルダへコピーし、本文で' +
        ' \\input{10ratioplot.tex} すると全図が入ります (1図1ページ。' +
        'プリアンブルに \\usepackage{pgf} が必要)</div></div>';
    } else {
      $('ratio_pdfs').innerHTML = pdfListHtml(j.pdfs, 'ratio');
    }
  } catch (e) { setMsg('ratio_msg', esc(e.message), 'msg-err'); }
}

async function runRatioTex() {
  if (!CHECK || !$('ctex_rtable')) return;
  try {
    const req = buildCheckReq();
    const cs = checkedVals('rcase').map(Number);
    req.cases = cs.length ? cs : null;
    req.fig_unit = $('r_unit').checked;
    const j = await api('/api/ratio_table_tex', req);
    $('ctex_rtable').innerHTML = '検定値一覧表 ' + j.files.map(f =>
      '<a class="dl" href="' + f.url + '&dl=1">' + esc(f.name) + '</a>'
      ).join(' ');
  } catch (e) {
    $('ctex_rtable').innerHTML = '<span class="msg-err">検定値一覧表: ' +
      esc(e.message) + '</span>';
  }
}

function renderCheck(j) {
  // 検定比図のケース選択欄を充填
  if (j.case_nos && $('rcases_box')) {
    $('rcases_box').innerHTML = j.case_nos.map((no, i) =>
      '<label><input type="checkbox" class="rcase" value="' + no +
      '" checked> ' + esc(j.case_names[i]) + '</label>').join('');
  }
  const nc = j.case_names.length;
  let h = '<div class="msg-ok">検定完了: 断面 ' + j.sections.length +
    ' 種 / 検定ケース ' + nc + ' (' + j.case_names.map(esc).join(', ') +
    ')　<a href="' + j.xlsx_url + '">' + esc(j.xlsx_name) +
    ' をダウンロード</a>' +
    (j.full_ratio_url ? '　<a href="' + j.full_ratio_url +
     '">要素別全結果 (xlsx)</a>' : '') +
    (j.plywood_csv ? '　<a href="' + j.plywood_csv.url + '">木合板検定 (' +
     esc(j.plywood_csv.name) + ')</a>' : '') +
    (j.out_dir ? openFolderBtn(j.out_dir) : '') + '</div>';
  // 計算書TeX (10ratiotable.tex / 10detail.tex) のダウンロード行
  // (検定実行後に runRatioTex / runDetailTex が中身を埋める)
  h += '<div id="ctex_slot" class="msg-ok" style="margin-top:2px">' +
    '計算書TeX:　<span id="ctex_rtable">検定値一覧表を生成中…</span>　' +
    '<span id="ctex_detail">検定詳細を生成中…</span>　' +
    '<label class="inline">検定詳細の出力ケース ' +
    '<select id="dtex_mode" onchange="runDetailTex()">' +
    '<option value="all" selected>全検定ケース</option>' +
    '<option value="pick">長期＋短期最大の2つ</option></select></label>' +
    '　<span class="hint">本文へ \\input{10ratiotable.tex} /' +
    ' \\input{10detail.tex} で組込み (要 longtable,multirow,booktabs,' +
    'array / amsmath,amssymb)</span></div>';
  h += notesHtml(j.notes);

  h += '<table class="res"><thead><tr><th></th><th>断面番号</th>' +
       '<th>断面名</th>';
  j.case_names.forEach(n => h += '<th>' + esc(n) + '</th>');
  h += '<th>最大</th><th>検定詳細文</th></tr></thead><tbody>';
  j.sections.forEach((s, si) => {
    h += '<tr><td><button class="sub" onclick="toggleDetail(' + si +
         ')">展開</button></td><td>' + s.no + '</td><td class="name">' +
         esc(s.name) + '</td>';
    s.cells.forEach(c => {
      if (!c) { h += '<td class="na">-</td>'; return; }
      h += '<td class="' + ratioClass(c.ratio) + '">' + fmtRatio(c.ratio) +
           '<span class="cell-ele">要素 ' + c.ele + '</span></td>';
    });
    h += '<td class="' + ratioClass(s.max) + '">' + fmtRatio(s.max) +
         '</td><td><button class="sub" onclick="showText(' + si +
         ')">表示</button></td></tr>';
    h += '<tr id="det' + si + '" style="display:none"><td></td>' +
         '<td colspan="' + (nc + 4) + '">';
    if (s.details.length) {
      h += '<table class="res"><thead><tr><th>要素番号</th>';
      j.case_names.forEach(n => h += '<th>' + esc(n) + '</th>');
      h += '</tr></thead><tbody>';
      s.details.forEach(d => {
        h += '<tr><td>' + d.ele + '</td>';
        d.vals.forEach(v => h += '<td class="' + ratioClass(v) + '">' +
                                 fmtRatio(v) + '</td>');
        h += '</tr>';
      });
      h += '</tbody></table>';
    } else { h += '<span class="hint">要素別データなし</span>'; }
    h += '</td></tr>';
  });
  h += '</tbody></table>';

  if (j.thick_table && j.thick_table.length) {
    h += '<h3>板・壁要素の断面(厚さ)別最大 <span class="hint">' +
         '(未検証経路: 検証データが無いため値の妥当性は設計者が確認して' +
         'ください)</span></h3>';
    h += '<table class="res"><thead><tr><th>区分</th><th>厚さ番号</th>' +
         '<th>厚さ</th>';
    j.case_names.forEach(n => h += '<th>' + esc(n) + '</th>');
    h += '<th>最大</th><th>検定詳細文</th></tr></thead><tbody>';
    j.thick_table.forEach((s, ti) => {
      h += '<tr><td>' + esc(s.kind) + '</td><td>' + s.no +
           '</td><td class="name">' + esc(s.name) + '</td>';
      s.cells.forEach(c => {
        if (!c) { h += '<td class="na">-</td>'; return; }
        h += '<td class="' + ratioClass(c.ratio) + '">' + fmtRatio(c.ratio) +
             '<span class="cell-ele">要素 ' + c.ele + '</span></td>';
      });
      h += '<td class="' + ratioClass(s.max) + '">' + fmtRatio(s.max) +
           '</td><td><button class="sub" onclick="showThickText(' + ti +
           ')">表示</button></td></tr>';
    });
    h += '</tbody></table>';
  }

  if (j.skipped.length) {
    h += '<h3>未対応(今後対応)の要素</h3>' +
      '<div class="msg-note">以下の要素はRC/SRC等スコープ外のため' +
      '検定していません。</div>' +
      '<table class="res"><thead><tr><th>要素番号</th><th>材料番号</th>' +
      '<th>材料種別</th><th>断面番号</th><th>区分</th></tr></thead><tbody>';
    j.skipped.forEach(s => {
      h += '<tr><td>' + esc(s.element_no) + '</td><td>' +
           esc(s.material_no) + '</td><td>' + esc(s.material_type) +
           '</td><td>' + esc(s.section_no) + '</td><td>' + esc(s.kind) +
           '</td></tr>';
    });
    h += '</tbody></table>';
  }
  h += '<h3>検定詳細文 (maxratios_text)</h3>' +
       '<div id="check_text"><span class="hint">断面行の「表示」ボタンで' +
       '検定詳細文を表示します</span></div>';
  $('check_result').innerHTML = h;
}

function toggleDetail(si) {
  const tr = $('det' + si);
  tr.style.display = tr.style.display === 'none' ? '' : 'none';
}
function showThickText(ti) {
  const s = CHECK.thick_table[ti];
  let t = '';
  CHECK.case_names.forEach((cn, ci) => {
    const lines = (s.texts[ci] || []);
    if (lines.length) {
      t += '===== ' + s.kind + '要素 厚さ番号' + s.no + ' ' + s.name +
           ' [' + cn + '] =====\n' + lines.join('\n') + '\n\n';
    }
  });
  if (!t) t = '(この厚さの検定詳細文はありません)';
  $('check_text').innerHTML = '<pre class="detailtext">' + esc(t) + '</pre>';
  $('check_text').scrollIntoView({behavior: 'smooth'});
}
function showText(si) {
  const s = CHECK.sections[si];
  let t = '';
  CHECK.case_names.forEach((cn, ci) => {
    const lines = (s.texts[ci] || []);
    if (lines.length) {
      t += '===== ' + s.name + ' [' + cn + '] =====\n' +
           lines.join('\n') + '\n\n';
    }
  });
  if (!t) t = '(この断面の検定詳細文はありません)';
  $('check_text').innerHTML = '<pre class="detailtext">' + esc(t) + '</pre>';
  $('check_text').scrollIntoView({behavior: 'smooth'});
}

// ---------------- TeX ----------------
async function runTex() {
  setMsg('tex_msg', '', ''); $('tex_out').innerHTML = '';
  try {
    const j = await api('/api/export_tex', {
      mgt_path: $('mgt_path').value, limit_sec_no: +$('t_limit').value});
    setMsg('tex_msg', '生成しました: ' + esc(j.name) +
      '　<a href="' + j.url + '&dl=1">ダウンロード</a>' +
      openFolderBtn(j.out_dir), 'msg-ok');
    $('tex_msg').innerHTML += notesHtml(j.notes);
    $('tex_out').innerHTML = '<pre class="texbox">' + esc(j.text) + '</pre>';
  } catch (e) { setMsg('tex_msg', esc(e.message), 'msg-err'); }
}

// ---------------- DXF ----------------
async function runDxf() {
  setMsg('dxf_msg', '', ''); $('dxf_out').innerHTML = '';
  try {
    const j = await api('/api/export_dxf', {
      mgt_path: $('mgt_path').value,
      fontsize: +$('d_fontsize').value,
      limit_sec_no: +$('d_limit').value});
    setMsg('dxf_msg', 'DXF ' + j.files.length + ' 件を生成しました → ' +
           esc(j.out_dir) + openFolderBtn(j.out_dir), 'msg-ok');
    $('dxf_msg').innerHTML += notesHtml(j.notes);
    $('dxf_out').innerHTML = j.files.map(f =>
      '<div class="pdfblock"><b>' + esc(f.name) + '</b>' +
      '<a class="dl" href="' + f.url + '">ダウンロード</a></div>').join('');
  } catch (e) { setMsg('dxf_msg', esc(e.message), 'msg-err'); }
}

// ---------------- 数量集計 ----------------
function fmtNum(v, d) {
  if (v === null || v === undefined) return '';
  return Number(v).toLocaleString('ja-JP',
    {minimumFractionDigits: d, maximumFractionDigits: d});
}

// 板要素壁(*REBAR-WALLで配筋定義されたWALL要素)の厚みごとの単配筋/複配筋。
// 径・ピッチはmgtから取得済みなので、ここでは配筋の層数だけ厚みID単位で
// 指定する。設定は localStorage (mgtkit_qty_wall_thick_layer) に保存する。
let QTY_WALL_THICKNESSES = null;

function qtyWallThickLayerStore() {
  try {
    return JSON.parse(localStorage.getItem('mgtkit_qty_wall_thick_layer')) || {};
  } catch (e) { return {}; }
}

async function loadQtyWallThicknesses() {
  setMsg('qty_wallthick_msg', ''); $('qty_wallthick_box').innerHTML = '';
  try {
    const j = await api('/api/quantity_wall_thicknesses',
      {mgt_path: $('mgt_path').value});
    QTY_WALL_THICKNESSES = j.thicknesses || [];
    if (!QTY_WALL_THICKNESSES.length) {
      setMsg('qty_wallthick_msg', '配筋定義(*REBAR-WALL)のある板要素壁は' +
        'ありませんでした。', 'msg-note');
      return;
    }
    const saved = qtyWallThickLayerStore();
    let h = '<table class="res"><thead><tr><th>厚みID</th><th>厚み(mm)</th>' +
      '<th>壁パネル数</th><th>配筋</th></tr></thead><tbody>';
    QTY_WALL_THICKNESSES.forEach(t => {
      const layer = saved[t.thick_id] || 'double';
      h += '<tr class="qtytrow" data-id="' + t.thick_id + '">' +
        '<td>' + t.thick_id + '</td>' +
        '<td>' + fmtNum(t.thickness_mm, 0) + '</td>' +
        '<td>' + t.n_walls + '</td>' +
        '<td class="rcgcell"><select id="qtyt_layer' + t.thick_id + '">' +
        '<option value="double"' + (layer === 'double' ? ' selected' : '') +
        '>複配筋 (両面x2)</option>' +
        '<option value="single"' + (layer === 'single' ? ' selected' : '') +
        '>単配筋 (片面)</option></select></td></tr>';
    });
    h += '</tbody></table>';
    $('qty_wallthick_box').innerHTML = h;
  } catch (e) { setMsg('qty_wallthick_msg', esc(e.message), 'msg-err'); }
}

function harvestQtyWallThicknesses() {
  const out = {};
  if (!QTY_WALL_THICKNESSES) return out;
  const saved = qtyWallThickLayerStore();
  document.querySelectorAll('#qty_wallthick_box tr.qtytrow').forEach(tr => {
    const tid = tr.dataset.id;
    const sel = $('qtyt_layer' + tid);
    if (!sel) return;
    out[tid] = sel.value;
    saved[tid] = sel.value;
  });
  localStorage.setItem('mgtkit_qty_wall_thick_layer', JSON.stringify(saved));
  return out;
}

// 線材壁パネル(断面名に"W"を含み配筋定義の無いRC断面)の配筋入力。
// 断面検定タブの「配筋の無いRC断面」入力(mgtkit_rcw_settings)と符号(prefix)
// 単位で設定を共有する (RC_SAVED/rcSavedEntry/diSelect は断面検定タブ用に
// 定義済みのものを流用)。
let QTY_WALL_SECTIONS = null;

function qtyWallCellHtml(i, w) {
  return '縦筋 ' + diSelect('qtyw_vdi' + i, w.v_di || 10) +
    '@<input type="number" id="qtyw_vp' + i + '" value="' +
    (w.v_pitch || 200) + '" style="width:60px"> ' +
    '横筋 ' + diSelect('qtyw_hdi' + i, w.h_di || 10) +
    '@<input type="number" id="qtyw_hp' + i + '" value="' +
    (w.h_pitch || 200) + '" style="width:60px"> ' +
    '<select id="qtyw_sd' + i + '">' +
    '<option value="2"' + ((w.sd || 2) === 2 ? ' selected' : '') +
    '>ダブル配筋</option>' +
    '<option value="1"' + (w.sd === 1 ? ' selected' : '') +
    '>シングル配筋</option></select> ' +
    '端部片側 <input type="number" id="qtyw_nr' + i + '" value="' +
    (w.num_rebar || 0) + '" style="width:50px">本';
}

async function loadQtyWallSections() {
  setMsg('qty_wallsec_msg', '', ''); $('qty_wallsec_box').innerHTML = '';
  try {
    const j = await api('/api/quantity_wall_sections',
      {mgt_path: $('mgt_path').value});
    QTY_WALL_SECTIONS = j.sections || [];
    RC_SAVED = rcwStore();  // 断面検定タブの保存設定を読み込む(未保存なら空)
    if (!QTY_WALL_SECTIONS.length) {
      setMsg('qty_wallsec_msg', '断面名に"W"を含み配筋定義(*REBAR-BEAM/' +
        'COLUMN)の無いRC断面はありませんでした。', 'msg-note');
      return;
    }
    const prefixes = [...new Set(QTY_WALL_SECTIONS.map(s => s.prefix))];
    let h = '<table class="res"><thead><tr><th>符号</th><th>断面数</th>' +
      '<th>配筋</th></tr></thead><tbody>';
    prefixes.forEach((pf, i) => {
      const secs = QTY_WALL_SECTIONS.filter(s => s.prefix === pf);
      const s = rcSavedEntry(pf);
      h += '<tr class="qtywrow" data-idx="' + i + '" data-prefix="' +
        esc(pf) + '"><td class="name">' + esc(pf) +
        '<span class="cell-ele">' +
        secs.map(x => esc(x.name)).join(', ') + '</span></td>' +
        '<td>' + secs.length + '</td>' +
        '<td class="rcgcell" id="qtyw_cell' + i + '">' +
        qtyWallCellHtml(i, s.wall) + '</td></tr>';
    });
    h += '</tbody></table>';
    $('qty_wallsec_box').innerHTML = h;
  } catch (e) { setMsg('qty_wallsec_msg', esc(e.message), 'msg-err'); }
}

// 表の入力値を断面番号ごとの配筋設定へ展開し、断面検定タブとの共有設定
// (mgtkit_rcw_settings) にも保存する
function harvestQtyWallSections() {
  const out = {};
  if (!QTY_WALL_SECTIONS) return out;
  document.querySelectorAll('#qty_wallsec_box tr.qtywrow').forEach(tr => {
    const i = +tr.dataset.idx;
    const pf = tr.dataset.prefix;
    if (!$('qtyw_vdi' + i)) return;
    const sd = +$('qtyw_sd' + i).value;
    const w = {v_dia: +$('qtyw_vdi' + i).value, v_pitch: +$('qtyw_vp' + i).value,
              h_dia: +$('qtyw_hdi' + i).value, h_pitch: +$('qtyw_hp' + i).value,
              layer: (sd === 1 ? 'single' : 'double'),
              edge_num: +$('qtyw_nr' + i).value};
    const s = rcSavedEntry(pf);
    s.kind = 'wall';
    s.wall = Object.assign({}, s.wall, {
      v_di: w.v_dia, v_pitch: w.v_pitch, h_di: w.h_dia, h_pitch: w.h_pitch,
      sd: sd, num_rebar: w.edge_num});
    QTY_WALL_SECTIONS.filter(x => x.prefix === pf).forEach(x => {
      out[x.no] = w;
    });
  });
  localStorage.setItem('mgtkit_rcw_settings', JSON.stringify(RC_SAVED));
  return out;
}

// 板要素スラブ(PLATE)の厚みごとX/Y方向筋・上端筋のみ/上下端筋入力。
// 設定は localStorage (mgtkit_qty_slab_thick) に厚みID単位で保存する。
let QTY_SLAB_THICKNESSES = null;

function qtySlabThickStore() {
  try {
    return JSON.parse(localStorage.getItem('mgtkit_qty_slab_thick')) || {};
  } catch (e) { return {}; }
}

// 配筋一段分(X/Y方向筋)の入力HTML。id接頭辞(例 'qtys_top9011_')を渡す
function qtySlabDirHtml(prefix, w) {
  w = w || {};
  return 'X方向 ' + diSelect(prefix + 'xdi', w.x_dia || 10) +
    '@<input type="number" id="' + prefix + 'xp" value="' +
    (w.x_pitch || 200) + '" style="width:60px"> ' +
    'Y方向 ' + diSelect(prefix + 'ydi', w.y_dia || 10) +
    '@<input type="number" id="' + prefix + 'yp" value="' +
    (w.y_pitch || 200) + '" style="width:60px">';
}

function qtySlabCellHtml(tid, s) {
  const layer = s.layer || 'double';
  const isDouble = layer === 'double';
  return '<select id="qtys_layer' + tid + '" onchange="qtySlabLayerChange(' +
    tid + ')">' +
    '<option value="double"' + (isDouble ? ' selected' : '') +
    '>ダブル配筋</option>' +
    '<option value="single"' + (!isDouble ? ' selected' : '') +
    '>シングル配筋</option></select>' +
    '<div><span id="qtys_toplabel' + tid + '">' +
    (isDouble ? '上端筋' : '配筋') + '</span> ' +
    qtySlabDirHtml('qtys_top' + tid + '_', s.top) + '</div>' +
    '<div id="qtys_bottomrow' + tid + '"' +
    (isDouble ? '' : ' style="display:none"') + '>下端筋 ' +
    qtySlabDirHtml('qtys_bottom' + tid + '_', s.bottom) + '</div>';
}

function qtySlabLayerChange(tid) {
  const isDouble = $('qtys_layer' + tid).value === 'double';
  $('qtys_bottomrow' + tid).style.display = isDouble ? '' : 'none';
  $('qtys_toplabel' + tid).textContent = isDouble ? '上端筋' : '配筋';
}

async function loadQtySlabThicknesses() {
  setMsg('qty_slabthick_msg', ''); $('qty_slabthick_box').innerHTML = '';
  try {
    const j = await api('/api/quantity_slab_thicknesses',
      {mgt_path: $('mgt_path').value});
    QTY_SLAB_THICKNESSES = j.thicknesses || [];
    if (!QTY_SLAB_THICKNESSES.length) {
      setMsg('qty_slabthick_msg', 'RC材料のスラブ用板要素はありませんでした。',
        'msg-note');
      return;
    }
    const saved = qtySlabThickStore();
    let h = '<table class="res"><thead><tr><th>厚みID</th><th>名称</th>' +
      '<th>厚み(mm)</th><th>要素数</th><th>面積(m2)</th><th>配筋</th>' +
      '</tr></thead><tbody>';
    QTY_SLAB_THICKNESSES.forEach(t => {
      const s = saved[t.thick_id] || {};
      h += '<tr class="qtystrow" data-id="' + t.thick_id + '">' +
        '<td>' + t.thick_id + '</td>' +
        '<td>' + esc(t.name || '') + '</td>' +
        '<td>' + fmtNum(t.thickness_mm, 0) + '</td>' +
        '<td>' + t.n_elements + '</td>' +
        '<td>' + fmtNum(t.area_m2, 1) + '</td>' +
        '<td class="rcgcell">' + qtySlabCellHtml(t.thick_id, s) + '</td></tr>';
    });
    h += '</tbody></table>';
    $('qty_slabthick_box').innerHTML = h;
  } catch (e) { setMsg('qty_slabthick_msg', esc(e.message), 'msg-err'); }
}

function harvestQtySlabThicknesses() {
  const out = {};
  if (!QTY_SLAB_THICKNESSES) return out;
  const saved = qtySlabThickStore();
  const rd = prefix => ({x_dia: +$(prefix + 'xdi').value,
                         x_pitch: +$(prefix + 'xp').value,
                         y_dia: +$(prefix + 'ydi').value,
                         y_pitch: +$(prefix + 'yp').value});
  document.querySelectorAll('#qty_slabthick_box tr.qtystrow').forEach(tr => {
    const tid = tr.dataset.id;
    if (!$('qtys_layer' + tid)) return;
    const layer = $('qtys_layer' + tid).value;
    const cfg = {layer: layer, top: rd('qtys_top' + tid + '_')};
    if (layer === 'double') cfg.bottom = rd('qtys_bottom' + tid + '_');
    out[tid] = cfg;
    saved[tid] = cfg;
  });
  localStorage.setItem('mgtkit_qty_slab_thick', JSON.stringify(saved));
  return out;
}

async function runQuantity() {
  setMsg('qty_msg', '', ''); $('qty_out').innerHTML = '';
  try {
    const j = await api('/api/quantity', {
      mgt_path: $('mgt_path').value,
      beam_cover: +$('qty_cover_beam').value,
      column_cover: +$('qty_cover_column').value,
      wall_cover: +$('qty_cover_wall').value,
      wall_layer_by_thickness: harvestQtyWallThicknesses(),
      wall_line_rebar: harvestQtyWallSections(),
      slab_rebar_by_thickness: harvestQtySlabThicknesses()});

    let h = '<table class="res"><thead><tr><th>径</th>' +
      '<th>梁(kg)</th><th>柱(kg)</th><th>壁(kg)</th><th>スラブ(kg)</th>' +
      '<th>合計(kg)</th><th>合計(m)</th></tr></thead><tbody>';
    let tKg = 0, tM = 0;
    j.diameters.forEach(r => {
      h += '<tr><td>D' + r.dia + '</td>' +
        '<td>' + fmtNum(r.beam_kg, 1) + '</td>' +
        '<td>' + fmtNum(r.column_kg, 1) + '</td>' +
        '<td>' + fmtNum(r.wall_kg, 1) + '</td>' +
        '<td>' + fmtNum(r.slab_kg, 1) + '</td>' +
        '<td><b>' + fmtNum(r.total_kg, 1) + '</b></td>' +
        '<td>' + fmtNum(r.total_m, 1) + '</td></tr>';
      tKg += r.total_kg; tM += r.total_m;
    });
    h += '<tr><td class="name"><b>合計</b></td><td></td><td></td><td></td>' +
      '<td></td><td><b>' + fmtNum(tKg, 1) + '</b></td><td>' + fmtNum(tM, 1) +
      '</td></tr></tbody></table>';

    h += '<table class="res" style="margin-top:10px"><thead><tr>' +
      '<th>部材</th><th>コンクリート体積(m3)</th><th>集計対象要素数</th>' +
      '</tr></thead><tbody>' +
      '<tr><td class="name">梁</td><td>' + fmtNum(j.concrete_m3.beam, 3) +
      '</td><td>' + j.n_beam + '</td></tr>' +
      '<tr><td class="name">柱</td><td>' + fmtNum(j.concrete_m3.column, 3) +
      '</td><td>' + j.n_column + '</td></tr>' +
      '<tr><td class="name">壁</td><td>' + fmtNum(j.concrete_m3.wall, 3) +
      '</td><td>' + j.n_wall + '</td></tr>' +
      '<tr><td class="name">スラブ</td><td>' + fmtNum(j.concrete_m3.slab, 3) +
      '</td><td>' + j.n_slab + '</td></tr>' +
      '<tr><td class="name"><b>合計</b></td><td><b>' +
      fmtNum(j.concrete_m3.total, 3) + '</b></td><td></td></tr>' +
      '</tbody></table>';

    $('qty_out').innerHTML = h;
    setMsg('qty_msg', '集計しました。 ' +
      '<a href="' + j.xlsx.url + '">' + esc(j.xlsx.name) + ' をダウンロード</a>' +
      openFolderBtn(j.out_dir), 'msg-ok');
    if (j.skipped && j.skipped.length) {
      const lines = j.skipped.map(s =>
        (s.ele !== undefined ? '要素' + s.ele : '壁ID' + s.wall_id) +
        ' (断面' + (s.sec_no || '') + '): ' + s.reason);
      $('qty_msg').innerHTML += notesHtml(lines);
    }
  } catch (e) { setMsg('qty_msg', esc(e.message), 'msg-err'); }
}
