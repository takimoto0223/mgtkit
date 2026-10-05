'use strict';
// 梁接合部検定タブ (jointchk)。共通ヘルパは app.js のものをグローバル参照で
// 借りる: $ / api / setMsg / esc / notesHtml / pdfListHtml / openFolderBtn /
// checkAll / checkedVals / pickFile / CTYPE_LABEL。
// (このファイルは app.js より前に読み込まれるが、参照はすべて
//  イベントハンドラ内なので問題ない)

let JOINT = null;   // 対象読込の結果 {members, sections, cases, fittings_db}
let JOINT_NG = [];  // 直近の検定で NG になった要素番号 (コピー用)

// NG要素番号のタブ区切りテキストをクリップボードへコピー
// (実体は app.js の copyEleList。読み込み順の都合で呼び出し時に参照する)
function jointCopyNG(btn) { copyEleList(btn, JOINT_NG); }

// 物件名欄の復元と保存 (このスクリプトはタブのHTMLの直後に読まれる)
(() => {
  const jp = document.getElementById('j_project');
  if (jp) {
    jp.value = localStorage.getItem('mgtkit_j_project') || '';
    jp.addEventListener('input', () =>
      localStorage.setItem('mgtkit_j_project', jp.value));
  }
  const jc = document.getElementById('j_comp');
  if (jc) jc.checked = localStorage.getItem('mgtkit_j_comp') === '1';
  const jh = document.getElementById('j_hidejt');
  if (jh) {
    jh.checked = localStorage.getItem('mgtkit_j_hidejt') === '1';
    document.getElementById('tab-joint')
      .classList.toggle('jthide', jh.checked);
  }
})();

// 「接合種別を表示しない」の状態 (表示のみの切替。検定の耐力選択は不変)
function jointHideJtOn() {
  const cb = document.getElementById('j_hidejt');
  return !!(cb && cb.checked);
}

function jointHideJtChanged() {
  localStorage.setItem('mgtkit_j_hidejt', jointHideJtOn() ? '1' : '');
  // 読込済みの表の種別表記は CSS クラスで即時切替 (再実行は不要)。
  // 検定結果の表と検討書PDFは次回の「検定実行」から反映される
  document.getElementById('tab-joint')
    .classList.toggle('jthide', jointHideJtOn());
}

// 「圧縮も検定する」の状態 (圧縮耐力の入力欄と検定の有無を切り替える)
function jointCompOn() {
  const cb = document.getElementById('j_comp');
  return !!(cb && cb.checked);
}

function jointCompChanged() {
  localStorage.setItem('mgtkit_j_comp', jointCompOn() ? '1' : '');
  if (JOINT) jointFittings();
}

// 金物の指定は符号名をキーに localStorage へ保存する
// (モデルの断面番号が変わっても符号名で引き継げる)
const JFIT_KEY = 'mgtkit_joint_fittings2';

function jointFitStore() {
  try { return JSON.parse(localStorage.getItem(JFIT_KEY) || '{}'); }
  catch (e) { return {}; }
}

function jointFitSave(name, vals) {
  const st = jointFitStore();
  st[name] = vals;
  localStorage.setItem(JFIT_KEY, JSON.stringify(st));
}

// ---------------- 対象読込 ----------------

function jointTypesLabel(types) {
  return Object.keys(types).map(t => t + esc(String(types[t]))).join(', ');
}

async function jointRead() {
  setMsg('j_msg', '', '');
  $('j_result').innerHTML = '';
  JG_DRAG = null;  // ドラッグ中に読込が割り込んだ場合の残留を防ぐ
  try {
    const j = await api('/api/jointchk_read', {
      mgt_path: $('mgt_path').value,
      beam_stress_path: $('j_beam_stress_path').value.trim()});
    JOINT = j;
    // 断面 (符号) の選択表 (1行1符号。接合種別の内訳つき。
    // グループの扱いと同じくセルの縦ドラッグで範囲一括チェック)
    if (!j.sections.length) {
      $('j_secs_box').innerHTML = '<span class="hint">ピン接合端をもつ' +
        '梁要素が見つかりませんでした (*FRAME-RLS を確認してください)</span>';
    } else {
      let h = '<table class="res" style="max-width:680px;user-select:none">' +
        '<thead><tr><th>符号</th><th>部材/ピン端の内訳</th>' +
        '<th>検討する</th></tr></thead><tbody>';
      j.sections.forEach((s, i) => {
        h += '<tr><td class="name">' + esc(s.name) + '</td>' +
          '<td>部材' + s.n_members + '・ピン端' + s.n_ends +
          '<span class="jtl">: ' + jointTypesLabel(s.types) + '</span></td>' +
          '<td class="jgcell" data-col="sec" data-row="' + i +
          '" style="cursor:pointer"><input type="checkbox" class="jsec"' +
          ' value="' + s.sec + '" checked style="pointer-events:none">' +
          '</td></tr>';
      });
      $('j_secs_box').innerHTML = h + '</tbody></table>';
    }
    // グループの扱い表 (1行1グループ。並びは mgt のグループ定義順のまま。
    // セルのドラッグで範囲一括チェック)
    if (j.groups && j.groups.length) {
      const gs = j.groups;
      const cell = (col, i, name) =>
        '<td class="jgcell" data-col="' + col + '" data-row="' + i +
        '" style="cursor:pointer"><input type="checkbox" class="jg' + col +
        '" data-g="' + esc(name) + '" style="pointer-events:none"></td>';
      let h = '<table class="res" style="max-width:680px;user-select:none">' +
        '<thead><tr><th>グループ</th><th>対象部材/要素</th>' +
        '<th>耐力をグループで入力</th><th>検定対象外</th></tr></thead><tbody>';
      gs.forEach((g, i) => {
        h += '<tr><td class="name">' + esc(g.name) + '</td>' +
          '<td>' + (g.n_targets ? g.n_targets + '本' : '-') + ' / ' +
          g.n_eles + '</td>' + cell('fit', i, g.name) +
          cell('excl', i, g.name) + '</tr>';
      });
      $('j_groups_box').innerHTML = h + '</tbody></table>';
    } else {
      $('j_groups_box').innerHTML = '<span class="hint">mgt にグループ' +
        ' (*GROUP) がありません</span>';
    }
    // 荷重ケースの種別 (断面算定タブと同じ表)
    if (j.cases) {
      let h = '<table class="res" style="max-width:640px"><thead><tr>' +
              '<th>荷重ケース</th><th>種別</th><th>ケース名</th></tr>' +
              '</thead><tbody>';
      j.cases.forEach((c, i) => {
        h += '<tr><td>ケース ' + c.no + '</td><td>' +
          '<select class="jctype" data-no="' + c.no + '">' +
          ['L', 'H', 'S', 'M'].map(t =>
            '<option value="' + t + '"' + (c.type === t ? ' selected' : '') +
            '>' + CTYPE_LABEL[t] + '</option>').join('') +
          '</select></td>' +
          '<td><input type="text" class="jcname" value="' +
          esc(c.name) + '"></td></tr>';
      });
      $('j_cases_box').innerHTML = h + '</tbody></table>';
    } else {
      $('j_cases_box').innerHTML = '<span class="hint">応力ファイルが' +
        '未指定のためケース一覧を表示できません。応力ファイルを指定して' +
        '「対象読込」し直してください</span>';
    }
    if (j.fittings_db) {
      $('j_species_box').innerHTML = '<span class="hint">出典: ' +
        esc(j.fittings_db.source) + '。金物と樹種を符号ごとに選ぶと、' +
        '各ピン端の接合種別 (柱-梁/梁-梁) に応じた基準耐力が自動で' +
        '使われます。長期引張は短期基準引張の1.1/2倍 (切捨て) です</span>';
    }
    jointFittings();
    setMsg('j_msg', 'ピン接合端をもつ梁要素 ' + j.members.length +
      ' 本 (断面 ' + j.sections.length + ' 種) を読み込みました。', 'msg-ok');
    if (j.notes && j.notes.length) {
      $('j_msg').innerHTML += notesHtml(j.notes);
    }
  } catch (e) { setMsg('j_msg', esc(e.message), 'msg-err'); }
}

// ---------------- 金物の指定 (符号ごと) ----------------

function jointFloor1(x) {
  return Math.floor(x * 10 + 1e-9) / 10;
}

// 金物×樹種×接合種別の耐力を引く (判定不可は柱-梁/梁-梁の小さい方)
function jointDbVals(fdef, sp, jt) {
  const spv = fdef && fdef.values[sp];
  if (!spv) return null;
  if (jt === '判定不可') {
    const mn = (key) => {
      const a = spv['柱-梁'] ? spv['柱-梁'][key] : null;
      const b = spv['梁-梁'] ? spv['梁-梁'][key] : null;
      return (a === null || a === undefined || b === null || b === undefined)
        ? null : Math.min(a, b);
    };
    return {ta_s: mn('ta_s'), qa_l: mn('qa_l'), qa_s: mn('qa_s'),
            qr_s: mn('qr_s')};
  }
  return spv[jt] || null;
}

// グループ役割の収集 (表のチェック状態から。表の並び順を保持)
function jointGroupRoles() {
  const roles = {excl: [], fit: []};
  document.querySelectorAll('#j_groups_box input.jgfit:checked')
    .forEach(cb => roles.fit.push(cb.dataset.g));
  document.querySelectorAll('#j_groups_box input.jgexcl:checked')
    .forEach(cb => roles.excl.push(cb.dataset.g));
  return roles;
}

// ---- 表の Excel 風ドラッグ選択 (同一列のセル範囲を一括チェック) --------
// グループの扱い表 (#j_groups_box) と断面選択表 (#j_secs_box) で共用。
// チェックセルは class="jgcell" + data-col/data-row を持つ。
let JG_DRAG = null;

function jgHighlight() {
  if (!JG_DRAG) {
    document.querySelectorAll('.jgcell').forEach(td => {
      td.style.background = '';
    });
    return;
  }
  JG_DRAG.box.querySelectorAll('.jgcell').forEach(td => {
    let on = false;
    if (td.dataset.col === JG_DRAG.col) {
      const r = +td.dataset.row;
      const lo = Math.min(JG_DRAG.a, JG_DRAG.b);
      const hi = Math.max(JG_DRAG.a, JG_DRAG.b);
      on = r >= lo && r <= hi;
    }
    td.style.background = on ? '#dbe6f4' : '';
  });
}

function jointDragify(boxId) {
  const box = document.getElementById(boxId);
  if (!box) return;
  box.addEventListener('mousedown', ev => {
    const td = ev.target.closest('.jgcell');
    if (!td || !box.contains(td)) return;
    ev.preventDefault();
    const cb = td.querySelector('input');
    JG_DRAG = {box: box, col: td.dataset.col,
               a: +td.dataset.row, b: +td.dataset.row,
               target: !cb.checked};
    jgHighlight();
  });
  box.addEventListener('mouseover', ev => {
    if (!JG_DRAG || JG_DRAG.box !== box) return;
    const td = ev.target.closest('.jgcell');
    if (!td || td.dataset.col !== JG_DRAG.col) return;
    JG_DRAG.b = +td.dataset.row;
    jgHighlight();
  });
}

(() => {
  jointDragify('j_groups_box');
  jointDragify('j_secs_box');
  document.addEventListener('mouseup', () => {
    if (!JG_DRAG) return;
    const {box, col, a, b, target} = JG_DRAG;
    const lo = Math.min(a, b);
    const hi = Math.max(a, b);
    box.querySelectorAll('.jgcell[data-col="' + col + '"]').forEach(td => {
      const r = +td.dataset.row;
      if (r < lo || r > hi) return;
      td.querySelector('input').checked = target;
      if (target && (col === 'fit' || col === 'excl')) {
        // 「耐力をグループで入力」と「検定対象外」は同時に選べない
        const other = td.parentElement.querySelector(
          '.jgcell[data-col="' + (col === 'fit' ? 'excl' : 'fit') +
          '"] input');
        if (other) other.checked = false;
      }
    });
    JG_DRAG = null;
    jgHighlight();
    jointFittings();
  });
})();

// 金物の割当単位 (耐力入力グループに属せばグループ、他は符号) を組む
function jointUnits() {
  const selSecs = new Set(checkedVals('jsec').map(Number));
  const roles = jointGroupRoles();
  const exclSet = new Set(roles.excl);
  const units = [];
  const byKey = {};
  for (const m of (JOINT ? JOINT.members : [])) {
    if (!selSecs.has(m.sec)) continue;
    if ((m.groups || []).some(g => exclSet.has(g))) continue;  // 対象外
    const g = roles.fit.find(x => (m.groups || []).includes(x));
    const key = g ? 'g|' + g : 's|' + m.sec;
    let r = byKey[key];
    if (!r) {
      r = {key: key, label: g || m.sec_name, grp: !!g,
           sec: m.sec, gidx: g ? roles.fit.indexOf(g) : -1,
           types: {}, secs: {}, n_members: 0};
      byKey[key] = r;
      units.push(r);
    }
    r.n_members++;
    r.secs[m.sec] = m.sec_name;
    for (const e of m.ends) {
      r.types[e.jtype] = (r.types[e.jtype] || 0) + 1;
    }
  }
  // 並び: 符号 (断面ID昇順) → グループ (グループ表の並び = mgt登録順)
  units.sort((a, b) => (a.grp - b.grp) ||
                       (a.grp ? a.gidx - b.gidx : a.sec - b.sec));
  return units;
}

function jointUnitTypes(u) {
  const order = ['柱-梁', '梁-梁', '判定不可'];
  return order.filter(t => u.types[t]);
}

// リスト選択時の耐力プレビュー (接合種別ごと)
function jointPreviewHtml(u, fname, sp) {
  const db = JOINT.fittings_db;
  const fdef = db && db.fittings.find(f => f.name === fname);
  if (!fdef) return '<span class="hint">金物がありません</span>';
  const fmt = v => (v === null || v === undefined) ? '—' : v.toFixed(1);
  const lines = jointUnitTypes(u).map(jt => {
    const v = jointDbVals(fdef, sp, jt);
    const pre = '<span class="jtl">' + esc(jt) + ': </span>';
    if (!v) return pre + '耐力データなし';
    const ta_l = (v.ta_s === null || v.ta_s === undefined)
      ? null : jointFloor1(v.ta_s * 0.55);
    return pre + '引張 ' + fmt(ta_l) + ' / ' + fmt(v.ta_s) +
      '、せん断 ' + fmt(v.qa_l) + ' / ' + fmt(v.qa_s) +
      '、逆せん断 —/' + fmt(v.qr_s);
  });
  return '<span class="hint">' + lines.join('<br>') +
    ' <span style="white-space:nowrap">[kN] (長期/短期)</span></span>';
}

// 耐力入力欄1つぶんの input (手入力・圧縮耐力で共用)
function jointNumInput(attr, f, val) {
  return '<input type="text" inputmode="decimal" class="jfit" ' +
    'style="width:68px"' + attr + ' data-f="' + f + '" value="' +
    esc(val === undefined ? '' : val) +
    '" oninput="jointFitChanged(this)">';
}

// 手入力時の入力欄 (金物名の既定はグループ名/符号名。応力ごとに1行)
function jointManualHtml(attr, v, defName) {
  const num = (f, val) => jointNumInput(attr, f, val);
  const td = 'style="border:none;padding:1px 6px 1px 0;text-align:left"';
  return '<table style="border-collapse:collapse"><tbody>' +
    '<tr><td ' + td + '>金物名</td>' +
    '<td ' + td + ' colspan="2"><input type="text" class="jfit" ' +
    'style="width:150px"' + attr + ' data-f="name" value="' +
    esc(v.name || defName || '') +
    '" oninput="jointFitChanged(this)"></td></tr>' +
    '<tr><td ' + td + '></td>' +
    '<td ' + td + ' class="hint">長期</td>' +
    '<td ' + td + ' class="hint">短期</td></tr>' +
    '<tr><td ' + td + '>引張</td>' +
    '<td ' + td + '>' + num('ta_l', v.ta_l) + '</td>' +
    '<td ' + td + '>' + num('ta_s', v.ta_s) + '</td></tr>' +
    (jointCompOn()
      ? '<tr><td ' + td + '>圧縮</td>' +
        '<td ' + td + '>' + num('ca_l', v.ca_l) + '</td>' +
        '<td ' + td + '>' + num('ca_s', v.ca_s) + '</td></tr>'
      : '') +
    '<tr><td ' + td + '>せん断</td>' +
    '<td ' + td + '>' + num('qa_l', v.qa_l) + '</td>' +
    '<td ' + td + '>' + num('qa_s', v.qa_s) + '</td></tr>' +
    '<tr><td ' + td + '>逆せん断</td>' +
    '<td ' + td + '><span class="hint">— (長期は生じない仮定)</span></td>' +
    '<td ' + td + '>' + num('qr_s', v.qr_s) + '</td></tr>' +
    '</tbody></table>' +
    '<div class="hint">耐力 [kN]。全接合種別のピン端に共通で使われます。' +
    '「-」と入力すると相手方の期から 1.1/2.0 の比率で自動計算します ' +
    '(例: 引張 短期=20・長期=- → 長期11.0)。' +
    '空欄の検定は「未検定」になります</div>';
}

// リスト金物選択時の圧縮耐力入力欄 (「圧縮も検定する」のときのみ。
// カタログに圧縮耐力の記載がないためリスト選択でも手入力)
function jointCompHtml(attr, v) {
  if (!jointCompOn()) return '';
  return '<div style="margin-top:2px">圧縮耐力 (手入力): 長期 ' +
    jointNumInput(attr, 'ca_l', v.ca_l) + ' 短期 ' +
    jointNumInput(attr, 'ca_s', v.ca_s) +
    ' <span class="hint">[kN] めり込み等の検討値。「-」で相手方から' +
    '1.1/2換算</span></div>';
}

let JOINT_UNITS = [];  // 直近に描画した金物割当単位 (警告表示用)

function jointFittings() {
  if (!JOINT) return;
  const units = jointUnits();
  JOINT_UNITS = units;
  if (!units.length) {
    $('j_fit_box').innerHTML = '<span class="hint">断面が選択されて' +
      'いません (またはすべて対象外です)</span>';
    return;
  }
  const db = JOINT.fittings_db;
  const st = jointFitStore();
  let h = '<table class="res" style="max-width:1100px"><thead><tr>' +
    '<th>符号/グループ</th><th>ピン端の内訳</th><th>金物</th><th>樹種</th>' +
    '<th>耐力 (接合種別に応じて自動選択)</th>' +
    '</tr></thead><tbody>';
  for (const u of units) {
    const v = st[u.label] || {};
    const sp = v.species || (db ? db.species[1] || db.species[0] : '');
    const attr = ' data-key="' + esc(u.key) + '" data-name="' +
                 esc(u.label) + '"';
    const spOpts = db ? db.species.map(x =>
      '<option value="' + esc(x) + '"' + (x === sp ? ' selected' : '') +
      '>' + esc(x) + '</option>').join('') : '';
    const fitOpts = '<option value="">(手入力)</option>' +
      (db ? db.fittings.map(f =>
        '<option value="' + esc(f.name) + '"' +
        (f.name === (v.sel || '') ? ' selected' : '') + '>' + esc(f.name) +
        (f.depth ? ' (梁せい' + esc(f.depth) + ')' : '') +
        '</option>').join('') : '');
    const secList = Object.values(u.secs);
    // グループ行は表示名 (既定「グループ名(特記)」) を編集できる
    const nameCell = u.grp
      ? '<input type="text" class="jdisp"' + attr + ' style="width:150px"' +
        ' value="' + esc(v.disp || (u.label + '(特記)')) +
        '" oninput="jointFitChanged(this)">' +
        ' <span class="hint">(グループ)</span>'
      : esc(u.label);
    const nEnds = Object.values(u.types).reduce((a, b) => a + b, 0);
    h += '<tr><td class="name">' + nameCell + '</td>' +
      '<td><span class="jtl">' + jointTypesLabel(u.types) + '</span>' +
      '<span class="jtl-off">ピン端' + nEnds + '</span>' +
      (u.grp ? '<br><span class="hint">部材' + u.n_members + '本: ' +
        esc(secList.join(', ')) + '</span>' : '') + '</td>' +
      '<td><select class="jfitsel"' + attr +
      ' onchange="jointFitChanged(this)">' + fitOpts + '</select></td>' +
      // 手入力のときは樹種の指定は不要 (リスト金物の耐力参照にのみ使う)
      '<td><select class="jsp"' + attr + (v.sel ? '' : ' disabled') +
      ' title="' + (v.sel ? '' : '手入力では樹種の指定は不要です') +
      '" onchange="jointFitChanged(this)">' +
      (v.sel ? spOpts : '<option value="">(不要)</option>') +
      '</select></td>' +
      '<td style="text-align:left">' +
      (v.sel ? jointPreviewHtml(u, v.sel, sp) + jointCompHtml(attr, v)
             : jointManualHtml(attr, v, u.label)) +
      '</td></tr>';
  }
  $('j_fit_box').innerHTML = h + '</tbody></table>' +
    '<div id="j_fit_warn"></div>';
  // 手入力→リスト選択に切り替えた直後は樹種が未保存 (手入力中は樹種
  // セレクトが無効なため)。表示中の既定樹種を保存して整合させる
  const st2 = jointFitStore();
  document.querySelectorAll('#j_fit_box select.jfitsel').forEach(sel => {
    const v2 = st2[sel.dataset.name];
    if (sel.value && (!v2 || !v2.species)) jointFitChanged(sel);
  });
  jointFitWarnings();
}

function jointFitChanged(el) {
  const name = el.dataset.name;
  const st = jointFitStore();
  const v = st[name] || {};
  const q = '[data-name="' + name.replace(/"/g, '\\"') + '"]';
  const spSel = document.querySelector('select.jsp' + q);
  const fitSel = document.querySelector('select.jfitsel' + q);
  const dispEl = document.querySelector('input.jdisp' + q);
  // 手入力時は樹種セレクトが無効 ((不要) 表示) なので保存値を保持する
  v.species = (spSel && !spSel.disabled) ? spSel.value : (v.species || '');
  v.sel = fitSel ? fitSel.value : (v.sel || '');
  if (dispEl) v.disp = dispEl.value;
  document.querySelectorAll('input.jfit' + q).forEach(i => {
    v[i.dataset.f] = i.value;
  });
  jointFitSave(name, v);
  // 樹種・金物の変更はプレビュー/入力欄の切替を伴うので表を描き直す。
  // 数値・名称の手入力中は再描画しない (入力フォーカスが失われるため)
  if (el.tagName === 'SELECT') jointFittings();
}

// 金物表の下の警告: 対応梁せいの照合 (グループ行は含む全符号) と、
// チェックしたのに入力欄が出ないグループの理由表示
function jointFitWarnings() {
  const box = $('j_fit_warn');
  if (!box || !JOINT) return;
  const db = JOINT.fittings_db;
  const depth = {};
  JOINT.sections.forEach(s => { depth[s.sec] = s.depth_mm; });
  const warns = [];

  // 「耐力をグループで入力」なのに入力欄が無いグループの理由
  const roles = jointGroupRoles();
  const selSecs = new Set(checkedVals('jsec').map(Number));
  const exclSet = new Set(roles.excl);
  const unitKeys = new Set(JOINT_UNITS.map(u => u.key));
  for (const g of roles.fit) {
    if (unitKeys.has('g|' + g)) continue;
    const mem = (JOINT.members || []).filter(m =>
      (m.groups || []).includes(g));
    const reasons = [];
    for (const m of mem) {
      if (!selSecs.has(m.sec)) {
        reasons.push('要素' + m.ele + ' は断面 ' + m.sec_name + ' が未選択');
      } else if ((m.groups || []).some(x => exclSet.has(x))) {
        reasons.push('要素' + m.ele + ' は検定対象外グループに所属');
      } else {
        const taken = roles.fit.find(x => (m.groups || []).includes(x));
        if (taken && taken !== g) {
          reasons.push('要素' + m.ele + ' は先に並ぶグループ ' + taken +
            ' に割り当て済み');
        }
      }
    }
    if (!mem.length) {
      const sp = (JOINT.splices || []).filter(s =>
        (s.groups || []).includes(g));
      reasons.push(sp.length
        ? '所属するピン端はすべて部材の継手として対象外'
        : 'ピン接合の対象部材が属していません');
    }
    warns.push(g + ': 入力欄がありません — ' +
      [...new Set(reasons)].join('、'));
  }
  document.querySelectorAll('#j_fit_box select.jfitsel').forEach(sel => {
    if (!sel.value || !db) return;
    const u = JOINT_UNITS.find(x => x.key === sel.dataset.key);
    const fdef = db.fittings.find(f => f.name === sel.value);
    if (!u || !fdef || !fdef.depth) return;
    const m = String(fdef.depth).match(/^(\d+)(?:〜(\d+)?)?/);
    if (!m) return;
    const lo = +m[1], hi = m[2] ? +m[2] : Infinity;
    for (const sec of Object.keys(u.secs)) {
      const d = depth[Number(sec)];
      if (!d) continue;
      if (d < lo - 0.5 || d > hi + 0.5) {
        warns.push(u.label + ': ' + u.secs[sec] + ' (梁せい' +
          Math.round(d) + ') に対して ' + sel.value +
          ' の対応梁せいは ' + fdef.depth + ' です');
      }
    }
  });
  box.innerHTML = warns.length
    ? '<div class="msg-note">確認してください: \n' +
      warns.map(esc).join('\n') + '</div>' : '';
}

function jointCollectFittings() {
  const out = {};
  const st = jointFitStore();
  document.querySelectorAll('#j_fit_box select.jfitsel').forEach(sel => {
    const name = sel.dataset.name;
    const v = st[name] || {};
    const item = {fitting: sel.value,
                  species: sel.value ? (v.species || '') : ''};
    // 手入力欄の値を集める (リスト選択時も圧縮耐力 ca_l/ca_s の欄がある)
    const q = '[data-name="' + name.replace(/"/g, '\\"') + '"]';
    document.querySelectorAll('input.jfit' + q).forEach(i => {
      item[i.dataset.f] = i.value.trim();
    });
    out[sel.dataset.key] = item;
  });
  return out;
}

function jointCollectCases() {
  const sels = [...document.querySelectorAll('#j_cases_box select.jctype')];
  if (!sels.length) return null;
  const names = [...document.querySelectorAll('#j_cases_box input.jcname')];
  return sels.map((s, i) => ({no: +s.dataset.no, type: s.value,
                              name: names[i].value.trim()}));
}

// ---------------- 検定実行 ----------------

function jointRatioCell(r, key) {
  const v = r[key];
  if (v !== null && v !== undefined) {
    // 逆せん断で決まった検定比には (逆) を付す
    const rev = (key === 'rs' && r.q_rev) ? ' (逆)' : '';
    return '<td' + (v > 1.0 ? ' class="ng"' : '') + '>' + v.toFixed(2) +
           rev + '</td>';
  }
  // 「圧縮のため引張検定を省略」は設計上の意味を持つので通常色で示す
  if (key === 'rt' && !r.tension) return '<td>(圧縮)</td>';
  if (key === 'rc' && !r.compression) return '<td>(引張)</td>';
  return '<td class="na">-</td>';
}

// 判定セル (NG > 未検定 > OK)
function jointStatusCell(ng, nc) {
  if (ng) return '<td class="ng">NG</td>';
  if (nc) return '<td class="warn">未検定</td>';
  return '<td>OK</td>';
}

function jointDetailTable(rows, comp, hideJt) {
  let h = '<div style="overflow-x:auto">' +
    '<table class="res"><thead><tr><th>符号</th><th>要素</th>' +
    '<th>端</th><th>節点</th>' +
    (hideJt ? '' : '<th>接合種別</th>') +
    '<th>軸力N [kN]</th><th>決定ケース</th><th>引張検定比</th>' +
    (comp ? '<th>圧縮N [kN]</th><th>決定ケース</th><th>圧縮検定比</th>'
          : '') +
    '<th>せん断Q [kN]</th><th>決定ケース</th>' +
    '<th>せん断検定比</th><th>判定</th></tr></thead><tbody>';
  for (const r of rows) {
    h += '<tr><td class="name">' + esc(r.sec_name) + '</td>' +
      '<td>' + r.ele + '</td><td>' + r.end + '</td><td>' + r.node + '</td>' +
      (hideJt ? '' : '<td>' + esc(r.jtype) + '</td>') +
      '<td>' + r.n.toFixed(1) + '</td><td>' + esc(r.n_case) + '</td>' +
      jointRatioCell(r, 'rt') +
      (comp ? '<td>' + r.cn.toFixed(1) + '</td><td>' + esc(r.cn_case) +
              '</td>' + jointRatioCell(r, 'rc')
            : '') +
      '<td>' + r.q.toFixed(1) + '</td><td>' + esc(r.q_case) + '</td>' +
      jointRatioCell(r, 'rs') +
      jointStatusCell(r.ng, r.nc) + '</tr>';
  }
  return h + '</tbody></table></div>';
}

async function jointRun() {
  setMsg('j_msg', '', '');
  $('j_result').innerHTML = '';
  if (!JOINT) {
    setMsg('j_msg', '先に「対象読込」を押してください。', 'msg-err');
    return;
  }
  const sel = checkedVals('jsec').map(Number);
  const cases = jointCollectCases();
  try {
    const j = await api('/api/jointchk_run', {
      mgt_path: $('mgt_path').value,
      beam_stress_path: $('j_beam_stress_path').value.trim(),
      sections: sel,
      fittings: jointCollectFittings(),
      case_types: cases,
      exclude_groups: jointGroupRoles().excl,
      fit_groups: jointGroupRoles().fit,
      unit_labels: (() => {
        const out = {};
        document.querySelectorAll('#j_fit_box input.jdisp').forEach(i => {
          if (i.value.trim()) out[i.dataset.key] = i.value.trim();
        });
        return out;
      })(),
      check_compression: jointCompOn(),
      hide_jtype: jointHideJtOn(),
      project: $('j_project') ? $('j_project').value.trim() : ''});
    const comp = !!j.check_compression;
    const hideJt = !!j.hide_jtype;
    const ngAll = j.summary.some(s => s.ng);
    const ncAll = j.summary.some(s => s.nc);
    if (ngAll) {
      setMsg('j_msg', '検定しました (対象 ' + j.n_members + ' 本): ' +
        '<b>NGの接合部があります</b>', 'msg-err');
    } else if (ncAll) {
      setMsg('j_msg', '検定しました (対象 ' + j.n_members + ' 本): ' +
        'NGはありませんが、<b>耐力未入力で未検定の端部があります</b>' +
        ' (下の表の「未検定」行と注記を確認してください)', 'msg-note');
    } else {
      setMsg('j_msg', '検定しました (対象 ' + j.n_members + ' 本): ' +
        'すべてOKです', 'msg-ok');
    }

    // NG要素の一覧 (画面のみ。検討書PDFには載せない)
    JOINT_NG = [...new Set(j.rows.filter(r => r.ng).map(r => r.ele))]
      .sort((a, b) => a - b);
    let h = '';
    if (JOINT_NG.length) {
      h += '<div class="msg-err"><b>NG要素 (' + JOINT_NG.length + '件): </b>' +
        esc(JOINT_NG.join(', ')) +
        ' <button class="sub" onclick="jointCopyNG(this)">タブ区切りで' +
        'コピー</button>' +
        '<span class="hint" style="margin-left:8px">MIDASの要素選択などに' +
        '貼り付けられます</span></div>';
    }
    h += '<h4>符号/グループ' + (hideJt ? '' : '・接合種別') +
      'ごとの最大検定比</h4>' +
      '<div style="overflow-x:auto">' +
      '<table class="res" style="max-width:1100px"><thead>' +
      '<tr><th rowspan="2">符号/グループ</th>' +
      (hideJt ? '' : '<th rowspan="2">接合種別</th>') +
      '<th rowspan="2">接合金物</th>' +
      '<th colspan="3">引張 (最大)</th>' +
      (comp ? '<th colspan="3">圧縮 (最大)</th>' : '') +
      '<th colspan="3">せん断 (最大)</th>' +
      '<th rowspan="2">判定</th></tr>' +
      '<tr><th>検定比</th><th>要素(端)</th><th>決定ケース</th>' +
      (comp ? '<th>検定比</th><th>要素(端)</th><th>決定ケース</th>' : '') +
      '<th>検定比</th><th>要素(端)</th><th>決定ケース</th></tr>' +
      '</thead><tbody>';
    for (const s of j.summary) {
      const rt = s.max_rt_at, rq = s.max_rc_at, rs = s.max_rs_at;
      const rc = (v, rev) => (v === null || v === undefined)
        ? '<td class="na">-</td>'
        : '<td' + (v > 1.0 ? ' class="ng"' : '') + '>' + v.toFixed(2) +
          (rev ? ' (逆)' : '') + '</td>';
      const at = (a) => '<td>' + (a ? a.ele + '(' + a.end + ')' : '-') +
        '</td><td>' + (a ? esc(a.case) : '-') + '</td>';
      h += '<tr><td class="name">' + esc(s.name) + '</td>' +
        (hideJt ? '' : '<td>' + esc(s.jtype) + '</td>') +
        '<td>' + esc(s.fit || '-') +
        (s.species ? ' <span class="hint">(' + esc(s.species) + ')</span>'
                   : '') + '</td>' +
        rc(s.max_rt) + at(rt) +
        (comp ? rc(s.max_rc) + at(rq) : '') +
        rc(s.max_rs, rs && rs.rev) + at(rs) +
        jointStatusCell(s.ng, s.nc) + '</tr>';
    }
    h += '</tbody></table></div>';

    // 継手部の軸力 (参考表示。検定対象外・検討書には載せない)
    if (j.splice_summary && j.splice_summary.length) {
      const cell = (b) => b
        ? '<td>' + b.n.toFixed(1) + (b.n <= 0 ? ' (圧縮)' : '') + '</td>' +
          '<td>' + esc(b.case) + '</td><td>' + b.ele + '(' + b.end + ')</td>'
        : '<td class="na">-</td><td class="na">-</td><td class="na">-</td>';
      const hasMid = j.splice_summary.some(s => s.mid);
      h += '<h4>継手部の軸力 (参考)</h4>' +
        '<div class="hint">検定対象外にした継手ピン端の軸力の最大値 (引張が正)。' +
        '継手の設計用の参考値で、検討書PDFには載りません。' +
        '断面の選択・グループ除外によらず、mgt内の全ての継手を表示します</div>' +
        '<div style="overflow-x:auto">' +
        '<table class="res" style="max-width:1100px"><thead>' +
        '<tr><th rowspan="2">符号</th><th rowspan="2">継手端数</th>' +
        '<th colspan="3">長期 最大軸力</th>' +
        '<th colspan="3">短期 最大軸力</th>' +
        (hasMid ? '<th colspan="3">中短期 最大軸力</th>' : '') + '</tr>' +
        '<tr><th>N [kN]</th><th>決定ケース</th><th>要素(端)</th>' +
        '<th>N [kN]</th><th>決定ケース</th><th>要素(端)</th>' +
        (hasMid ? '<th>N [kN]</th><th>決定ケース</th><th>要素(端)</th>'
                : '') + '</tr>' +
        '</thead><tbody>' +
        j.splice_summary.map(s =>
          '<tr><td class="name">' + esc(s.name) + '</td>' +
          '<td>' + s.n_ends +
          (s.n_missing ? ' <span class="hint">(応力なし' + s.n_missing +
            ')</span>' : '') + '</td>' +
          cell(s.long) + cell(s.short) +
          (hasMid ? cell(s.mid) : '') + '</tr>').join('') +
        '</tbody></table></div>';
    }

    for (const [term, label] of [['long', '長期'], ['short', '短期'],
                                 ['mid', '中短期']]) {
      const rows = j.rows.filter(r => r.term === term);
      if (!rows.length) continue;
      h += '<details class="adv"><summary>全ピン端の検定表 (' + label +
        ') — ' + rows.length + ' 端 (画面のみ。検討書PDFは符号×ケースの' +
        '最大のみ)</summary>' + jointDetailTable(rows, comp, hideJt) +
        '</details>';
    }
    h += notesHtml(j.notes) +
      '<div class="row">' + openFolderBtn(j.out_dir) + '</div>' +
      pdfListHtml([j.pdf]);
    $('j_result').innerHTML = h;
  } catch (e) { setMsg('j_msg', esc(e.message), 'msg-err'); }
}
