'use strict';
// 木造水平構面検定タブ (diaphragm)。共通ヘルパは app.js のものをグローバル
// 参照で借りる: $ / api / setMsg / esc / notesHtml / pdfListHtml /
// openFolderBtn / checkAll / pickFile / CTYPE_LABEL。
// (このファイルは app.js より前に読み込まれるが、参照はすべて
//  イベントハンドラ内なので問題ない)

let DIA = null;   // 対象読込の結果 {groups, cases}

// 床倍率1あたりの短期許容せん断耐力 [kN/m] (木合板壁の検定と同じ基準値)
const DIA_QA_BASE = 1.96;

// ---------------- 対象読込 ----------------

async function diaRead() {
  setMsg('d_msg', '', '');
  $('d_result').innerHTML = '';
  try {
    const j = await api('/api/diaphragm_read', {
      mgt_path: $('mgt_path').value,
      plate_stress_path: $('d_plate_stress_path').value.trim()});
    DIA = j;
    // 床倍率グループの一覧 (チェックで検討対象を選ぶ。既定は全選択)
    let h = '<table class="res" style="max-width:560px"><thead><tr>' +
      '<th>検討する</th><th>床倍率</th><th>対象要素数</th>' +
      '<th>Z範囲 [m]</th></tr></thead><tbody>';
    j.groups.forEach(g => {
      h += '<tr><td><input type="checkbox" class="dbeta" value="' +
        g.beta + '" checked></td>' +
        '<td>' + g.beta + '</td><td>' + g.n_eles + '</td>' +
        '<td>' + g.zmin.toFixed(2) + '〜' + g.zmax.toFixed(2) +
        '</td></tr>';
    });
    $('d_groups_box').innerHTML = h + '</tbody></table>';
    // 荷重ケース (短期判定のケースに既定チェック。Sが無ければHに付ける)
    const defType = j.cases.some(c => c.type === 'S') ? 'S' : 'H';
    h = '<table class="res" style="max-width:640px"><thead><tr>' +
      '<th>検討する</th><th>荷重ケース</th><th>種別の目安</th>' +
      '<th>ケース名 (検討書に記載)</th></tr></thead><tbody>';
    j.cases.forEach(c => {
      h += '<tr><td><input type="checkbox" class="dcase" value="' + c.no +
        '"' + (c.type === defType ? ' checked' : '') + '></td>' +
        '<td>ケース ' + c.no + '</td>' +
        '<td>' + esc(CTYPE_LABEL[c.type] || c.type) + '</td>' +
        '<td><input type="text" class="dcname" data-no="' + c.no +
        '" value="' + esc(c.name) + '"></td></tr>';
    });
    $('d_cases_box').innerHTML = h + '</tbody></table>';
    setMsg('d_msg', '水平構面の板要素を読み込みました (床倍率 ' +
      j.groups.length + ' 種、要素 ' +
      j.groups.reduce((a, g) => a + g.n_eles, 0) + ' 件)。', 'msg-ok');
    if (defType === 'H') {
      $('d_msg').innerHTML += '<div class="msg-note">短期 (組合せ済み) と' +
        '判定されたケースが無いため、「水平のみ」判定のケースに既定' +
        'チェックを付けました。組合せ前の生ケース (長期未合算) の場合は' +
        'そのままの検定は適さないことがあります。検討対象を確認して' +
        'ください。</div>';
    }
    if (j.notes && j.notes.length) {
      $('d_msg').innerHTML += notesHtml(j.notes);
    }
  } catch (e) { setMsg('d_msg', esc(e.message), 'msg-err'); }
}

function diaCollectCases() {
  const out = [];
  document.querySelectorAll('#d_cases_box input.dcase:checked')
    .forEach(cb => {
      const name = document.querySelector(
        '#d_cases_box input.dcname[data-no="' + cb.value + '"]');
      const c = DIA && DIA.cases
        ? DIA.cases.find(x => +x.no === +cb.value) : null;
      out.push({no: +cb.value, name: name ? name.value.trim() : '',
                type: c ? c.type : ''});
    });
  return out;
}

// ---------------- 検定実行 ----------------

async function diaRun() {
  setMsg('d_msg', '', '');
  $('d_result').innerHTML = '';
  if (!DIA) {
    setMsg('d_msg', '先に「対象読込」を押してください。', 'msg-err');
    return;
  }
  const cases = diaCollectCases();
  if (!cases.length) {
    setMsg('d_msg', '検討対象の荷重ケースが選択されていません。', 'msg-err');
    return;
  }
  const betas = checkedVals('dbeta').map(Number);
  if (!betas.length) {
    setMsg('d_msg', '検討対象の床倍率が選択されていません。', 'msg-err');
    return;
  }
  try {
    const j = await api('/api/diaphragm_run', {
      mgt_path: $('mgt_path').value,
      plate_stress_path: $('d_plate_stress_path').value.trim(),
      cases: cases,
      betas: betas,
      qa_base: DIA_QA_BASE});
    const ngAll = j.groups.some(g => g.cases.some(r => !r.ok));
    if (ngAll) {
      setMsg('d_msg', '検定しました (床倍率 ' + j.groups.length + ' 種): ' +
        '<b>NGの構面があります</b>', 'msg-err');
    } else {
      setMsg('d_msg', '検定しました (床倍率 ' + j.groups.length + ' 種): ' +
        'すべてOKです', 'msg-ok');
    }
    // 総括 (床倍率ごとの最大)
    let h = '<h4>床倍率ごとの最大検定比</h4>' +
      '<table class="res" style="max-width:800px"><thead><tr>' +
      '<th>床倍率</th><th>要素数</th>' +
      '<th>許容 qa [kN/m]</th><th>|平均Fxy| [kN/m]</th>' +
      '<th>決定ケース</th><th>決定要素</th>' +
      '<th>検定比</th><th>判定</th></tr></thead><tbody>';
    for (const g of j.groups) {
      const r = g.cases.find(x => x.case === g.worst_case);
      h += '<tr><td>' + g.beta + '</td>' +
        '<td>' + g.n_eles + '</td>' +
        '<td>' + g.qa.toFixed(2) + '</td>' +
        '<td>' + (r ? Math.abs(r.mean).toFixed(2) : '-') + '</td>' +
        '<td>' + esc(g.worst_label || '-') + '</td>' +
        '<td>' + (r ? r.ele : '-') + '</td>' +
        '<td' + (g.worst_ratio > 1.0 ? ' class="ng"' : '') + '>' +
        g.worst_ratio.toFixed(2) + '</td>' +
        '<td' + (r && !r.ok ? ' class="ng">NG' : '>OK') + '</td></tr>';
    }
    h += '</tbody></table>';
    // 床倍率×ケースの内訳 (画面のみ。検討書は総括+図)
    h += '<h4>床倍率×ケースの内訳</h4>' +
      '<div class="hint">検定比 = 板要素ごとの節点平均 |平均Fxy| ÷ qa。' +
      '各行はそのケースで検定比が最大となる要素 (決定要素) の値です</div>' +
      '<div style="overflow-x:auto">' +
      '<table class="res" style="max-width:760px"><thead><tr>' +
      '<th>床倍率</th><th>ケース</th>' +
      '<th>平均 Fxy [kN/m]</th><th>決定要素</th>' +
      '<th>検定比</th><th>判定</th></tr></thead><tbody>';
    for (const g of j.groups) {
      for (const r of g.cases) {
        h += '<tr><td>' + g.beta + '</td>' +
          '<td>' + esc(r.label) + '</td>' +
          '<td>' + r.mean.toFixed(2) + '</td>' +
          '<td>' + r.ele + '</td>' +
          '<td' + (r.ratio > 1.0 ? ' class="ng"' : '') + '>' +
          r.ratio.toFixed(2) + '</td>' +
          '<td' + (r.ok ? '>OK' : ' class="ng">NG') + '</td></tr>';
      }
    }
    h += '</tbody></table></div>';
    h += notesHtml(j.notes) +
      '<div class="row">' + openFolderBtn(j.out_dir) +
      ' <a href="' + j.csv.url + '">' + esc(j.csv.name) +
      ' をダウンロード</a></div>' +
      pdfListHtml([j.pdf]);
    $('d_result').innerHTML = h;
  } catch (e) { setMsg('d_msg', esc(e.message), 'msg-err'); }
}
