'use strict';
// RC定着・付着・柱梁接合部検定タブ (rcdetail)。共通ヘルパは app.js の
// ものをグローバル参照で借りる: $ / api / setMsg / esc / notesHtml /
// pdfListHtml / openFolderBtn / pickFile / CTYPE_LABEL。

let RCD = null;   // 対象読込の結果 {sections, n_joints, fc_list, cases}

// ---------------- 対象読込 ----------------

async function rdRead() {
  setMsg('rd_msg', '', '');
  $('rd_result').innerHTML = '';
  try {
    const j = await api('/api/rcdetail_read', {
      mgt_path: $('mgt_path').value,
      wall_prefix: $('rd_wall_prefix').value.trim() || 'EW',
      beam_stress_path: $('rd_beam_stress_path').value.trim()});
    RCD = j;
    let h = '<table class="res" style="max-width:720px"><thead><tr>' +
      '<th>RC梁断面</th><th>B×D [mm]</th><th>要素数</th><th>配筋</th>' +
      '<th>接合部の形状判定に算入</th><th>検定対象 (定着・通し・付着)</th>' +
      '</tr></thead><tbody>';
    j.sections.forEach(s => {
      const chk = s.has_rebar ? ' checked' : '';
      h += '<tr><td>' + esc(s.name) + '</td>' +
        '<td>' + s.b.toFixed(0) + '×' + s.D.toFixed(0) + '</td>' +
        '<td>' + s.n_ele + '</td>' +
        '<td>' + (s.has_rebar ? 'あり' : '<span class="ng">なし</span>') +
        '</td>' +
        '<td><input type="checkbox" class="rdform" value="' + s.sec +
        '"' + chk + '></td>' +
        '<td><input type="checkbox" class="rdchk" value="' + s.sec +
        '"' + chk + (s.has_rebar ? '' : ' disabled title="配筋データが' +
        '無いため検定できません"') + '></td></tr>';
    });
    h += '</tbody></table>' +
      '<div class="row"><span class="hint">配筋なしの断面 (ダミー梁や' +
      '配筋未入力の梁) は既定で両方とも対象外です。実在する梁なら' +
      '「形状判定に算入」にチェックを入れると、接合部の形式 (十字/T/ト/L)' +
      ' の判定にだけ算入されます (My=0扱い・注記が出ます)</span></div>';
    if (j.n_joints.length) {
      h += '<div class="row"><b>柱梁接合部 (自動判定)</b>: ' +
        j.n_joints.map(x => esc(x.form) + ' ' + x.n + '箇所').join('、') +
        '　<span class="hint">Fc: ' +
        j.fc_list.map(f => 'Fc' + f).join(', ') + '</span></div>';
    }
    // 耐震壁一覧 (19条: EW断面の鉛直要素。QD・t'・必要値は検定実行で算出)
    if (j.walls && j.walls.length) {
      h += '<div class="row"><b>耐震壁部材 (19条: ' +
        esc($('rd_wall_prefix').value.trim() || 'EW') +
        '断面の鉛直要素)</b> ' + j.walls.length + '本' +
        '<span class="hint">QD (強軸せん断)・必要壁厚t\'・必要断面は' +
        '検定実行で算出します。枠柱は既定で自動判定です。間違っている' +
        '場合はプルダウンから柱断面符号を選んでください (「枠柱なし」' +
        'も選べます)</span></div>' +
        '<table class="res"><thead><tr><th>符号</th><th>要素</th>' +
        '<th>方向</th><th>壁長</th><th>壁厚</th><th>高さ</th>' +
        '<th>s</th><th>z範囲</th><th>枠柱(始端)</th>' +
        '<th>枠柱(終端)</th></tr></thead><tbody>';
      j.walls.forEach(w => {
        const cell = (lst, cls) => {
          const names = lst.map(c => esc(c.name)).join('/');
          let o = '<option value="auto" selected>自動: ' +
            (names || 'なし') + '</option>' +
            '<option value="none">枠柱なし</option>';
          (j.col_secs || []).forEach(cs => {
            o += '<option value="' + cs.sec + '">' + esc(cs.name) +
              ' (' + cs.H.toFixed(0) + '×' + cs.B.toFixed(0) +
              ')</option>';
          });
          return '<td><select class="' + cls + '" data-ele="' + w.ele +
            '">' + o + '</select></td>';
        };
        h += '<tr><td>' + esc(w.name) + '</td><td>' + w.ele + '</td>' +
          '<td>' + w.axis + '</td>' +
          '<td>' + w.lw.toFixed(0) + '</td>' +
          '<td>' + w.t.toFixed(0) + '</td>' +
          '<td>' + w.h.toFixed(0) + '</td>' +
          '<td>' + w.s.toFixed(0) + '</td>' +
          '<td>' + (w.z0 / 1000).toFixed(1) + '〜' +
          (w.z1 / 1000).toFixed(1) + 'm</td>' +
          cell(w.cols_minus, 'rdwcm') + cell(w.cols_plus, 'rdwcp') +
          '</tr>';
      });
      h += '</tbody></table>';
      // 開口補強の入力 (19条5項)。l0を空欄のままにした壁は対象外
      const dSel = (cls, ele, v) => {
        return '<select class="' + cls + '" data-ele="' + ele + '">' +
          [10, 13, 16, 19, 22].map(d => '<option value="' + d + '"' +
            (d === v ? ' selected' : '') + '>D' + d + '</option>')
          .join('') + '</select>';
      };
      h += '<div class="row"><b>開口補強の入力 (19条5項)</b>' +
        '<span class="hint">開口のある壁だけ l0・h0 を入力して' +
        'ください (空欄=検定しない)。壁筋は縦横同仕様。補強筋の材種は' +
        '径から自動 (D13以下SD295、D16以上SD345扱い…D19以上SD345)' +
        '</span></div>' +
        '<table class="res"><thead><tr><th>符号</th>' +
        '<th>開口 l0×h0 [mm]</th><th>横並びnh</th><th>縦並びnv</th>' +
        '<th>単層/ピロティ最下層</th><th>壁筋</th>' +
        '<th>斜め筋</th><th>縦補強筋 Av0</th><th>横補強筋 Ah0</th>' +
        '</tr></thead><tbody>';
      j.walls.forEach(w => {
        const e = w.ele;
        h += '<tr><td>' + esc(w.name) + '</td>' +
          '<td><input type="number" class="rdol" data-ele="' + e +
          '" style="width:70px" placeholder="l0">×' +
          '<input type="number" class="rdoh" data-ele="' + e +
          '" style="width:70px" placeholder="h0"></td>' +
          '<td><input type="number" class="rdonh" data-ele="' + e +
          '" value="1" min="1" style="width:48px"></td>' +
          '<td><input type="number" class="rdonv" data-ele="' + e +
          '" value="1" min="1" style="width:48px"></td>' +
          '<td><input type="checkbox" class="rdosg" data-ele="' + e +
          '"></td>' +
          '<td>' + dSel('rdowd', e, 10) +
          '@<input type="number" class="rdows" data-ele="' + e +
          '" value="200" step="50" style="width:60px"> ' +
          '<label><input type="checkbox" class="rdowW" data-ele="' + e +
          '" checked>W</label></td>' +
          '<td><input type="number" class="rdodn" data-ele="' + e +
          '" value="2" min="0" style="width:44px">-' +
          dSel('rdodd', e, 13) + '</td>' +
          '<td><input type="number" class="rdovn" data-ele="' + e +
          '" value="2" min="0" style="width:44px">-' +
          dSel('rdovd', e, 13) + '</td>' +
          '<td><input type="number" class="rdohn" data-ele="' + e +
          '" value="2" min="0" style="width:44px">-' +
          dSel('rdohd', e, 13) + '</td></tr>';
      });
      h += '</tbody></table>';
    }
    $('rd_targets_box').innerHTML = h;
    if (j.cases) {
      let hc = '<table class="res" style="max-width:640px"><thead><tr>' +
        '<th>荷重ケース</th><th>種別</th><th>ケース名 (検討書に記載)' +
        '</th></tr></thead><tbody>';
      j.cases.forEach(c => {
        hc += '<tr><td>ケース ' + c.no + '</td><td>' +
          '<select class="rdctype" data-no="' + c.no + '">' +
          ['L', 'H', 'S', 'M'].map(t =>
            '<option value="' + t + '"' +
            (c.type === t ? ' selected' : '') + '>' +
            CTYPE_LABEL[t] + '</option>').join('') +
          '</select></td>' +
          '<td><input type="text" class="rdcname" data-no="' + c.no +
          '" value="' + esc(c.name) + '"></td></tr>';
      });
      $('rd_cases_box').innerHTML = hc + '</tbody></table>';
    } else {
      $('rd_cases_box').innerHTML = '<span class="hint">応力ファイルが' +
        '未指定のためケース一覧を表示できません。応力ファイルを指定して' +
        '「対象読込」し直してください</span>';
    }
    setMsg('rd_msg', 'RC梁 (配筋つき断面) ' + j.sections.length +
      ' 種を読み込みました。', 'msg-ok');
    if (j.notes && j.notes.length) {
      $('rd_msg').innerHTML += notesHtml(j.notes);
    }
  } catch (e) { setMsg('rd_msg', esc(e.message), 'msg-err'); }
}

function rdCollectCases() {
  const out = [];
  document.querySelectorAll('#rd_cases_box select.rdctype').forEach(s => {
    const name = document.querySelector(
      '#rd_cases_box input.rdcname[data-no="' + s.dataset.no + '"]');
    out.push({no: parseFloat(s.dataset.no), type: s.value,
              name: name ? name.value : ('C' + s.dataset.no)});
  });
  return out;
}

// ---------------- 検定実行 ----------------

function rdOxTd(ok) {
  if (ok === null || ok === undefined) return '<td>-</td>';
  return ok ? '<td>OK</td>' : '<td class="ng">NG</td>';
}

async function rdRun() {
  setMsg('rd_msg', '検定中...', 'msg-note');
  $('rd_result').innerHTML = '';
  try {
    const secOf = cls => {
      const all = document.querySelectorAll('#rd_targets_box input.' + cls);
      if (!all.length) return null;   // 対象読込前は既定 (配筋あり断面)
      return [...all].filter(cb => cb.checked)
        .map(cb => parseFloat(cb.value));
    };
    const j = await api('/api/rcdetail_run', {
      mgt_path: $('mgt_path').value,
      beam_stress_path: $('rd_beam_stress_path').value.trim(),
      case_types: rdCollectCases(),
      form_secs: secOf('rdform'),
      check_secs: secOf('rdchk'),
      project: $('rd_project').value.trim(),
      params: {
        beam_cover: parseFloat($('rd_beam_cover').value),
        anchor_offset: parseFloat($('rd_anchor_offset').value),
        anchor_offset2: ($('rd_anchor_offset2').value.trim() === ''
          ? null : parseFloat($('rd_anchor_offset2').value)),
        anchor_method: $('rd_anchor_method').value,
        alpha: parseFloat($('rd_alpha').value),
        S: parseFloat($('rd_S').value),
        ld_mode: $('rd_ld_mode').value,
        do_anchor: $('rd_do_anchor').checked,
        do_through: $('rd_do_through').checked,
        do_bond: $('rd_do_bond').checked,
        do_bond_safety: $('rd_bond_safety').checked,
        do_joint: $('rd_do_joint').checked,
        joint_hoop_factor: parseFloat($('rd_joint_hoop_f').value) || 1.5,
        do_wall: $('rd_do_wall').checked,
        wall_prefix: $('rd_wall_prefix').value.trim() || 'EW',
        wall_ft: parseFloat($('rd_wall_ft').value),
        wall_wr: parseFloat($('rd_wall_wr').value)},
      wall_open: (() => {
        const o = {};
        const val = (cls, ele) => {
          const inp = document.querySelector(
            '#rd_targets_box .' + cls + '[data-ele="' + ele + '"]');
          if (!inp) return null;
          if (inp.type === 'checkbox') return inp.checked;
          return inp.value === '' ? null : parseFloat(inp.value);
        };
        document.querySelectorAll('#rd_targets_box input.rdol')
          .forEach(inp => {
            const e = inp.dataset.ele;
            const l0 = parseFloat(inp.value);
            if (isNaN(l0) || l0 <= 0) return;
            o[e] = {l0: l0, h0: val('rdoh', e) || 0,
                    nh: val('rdonh', e) || 1, nv: val('rdonv', e) || 1,
                    single: val('rdosg', e),
                    w_di: val('rdowd', e), w_pitch: val('rdows', e),
                    w_double: val('rdowW', e),
                    d_n: val('rdodn', e), d_di: val('rdodd', e),
                    v_n: val('rdovn', e), v_di: val('rdovd', e),
                    h_n: val('rdohn', e), h_di: val('rdohd', e)};
          });
        return Object.keys(o).length ? o : null;
      })(),
      wall_cols: (() => {
        const o = {};
        document.querySelectorAll('#rd_targets_box select.rdwcm')
          .forEach(s => {
            o[s.dataset.ele] = {minus: s.value, plus: 'auto'};
          });
        document.querySelectorAll('#rd_targets_box select.rdwcp')
          .forEach(s => {
            if (o[s.dataset.ele]) o[s.dataset.ele].plus = s.value;
          });
        return Object.keys(o).length ? o : null;
      })()});
    const sm = j.summary;
    let h = '<div class="row"><b>検定結果</b>: ' +
      ['定着 ' + sm.anchor.n + '件 (NG ' + sm.anchor.ng + ')',
       '通し配筋 ' + sm.through.n + '件 (NG ' + sm.through.ng + ')',
       '付着 ' + sm.bond.n + '件 (NG ' + sm.bond.ng + ')',
       '接合部 ' + sm.joint.n + '件 (NG ' + sm.joint.ng + ')',
       '耐震壁周辺柱 ' + sm.wall.n + '壁 (NG ' + sm.wall.ng + ')',
       '開口補強 ' + sm.open.n + '件 (NG ' + sm.open.ng + ')'
      ].join('、') + '</div>';

    if (j.anchor.length) {
      const am = j.params.anchor_method;
      const amLabel = {rc: 'RC規準17.2式', k432: '告示432号',
                       proj: '0.75D細則'}[am];
      const needTh = {rc: '必要lab', k432: '必要l (告432)',
                      proj: '0.75D'}[am];
      h += '<div class="row"><b>定着 (ト形・L形、' + amLabel +
        'による)</b></div>' +
        '<table class="res"><thead><tr><th>節点</th><th>方向</th>' +
        '<th>形式</th><th>柱</th><th>D</th><th>梁</th><th>位置</th>' +
        '<th>配筋</th><th>' + needTh + '</th>' +
        '<th>確保la</th><th>判定</th></tr></thead><tbody>';
      j.anchor.forEach(r => {
        let need;
        if (am === 'rc') {
          need = r.lab.toFixed(0);
        } else if (am === 'k432') {
          need = r.l432.toFixed(0);
        } else {
          need = r.proj.toFixed(0);
        }
        h += '<tr><td>' + r.node + '</td><td>' + r.dir + '</td>' +
          '<td>' + esc(r.form) + '</td><td>' + esc(r.col) + '</td>' +
          '<td>' + r.Dc.toFixed(0) + '</td><td>' + esc(r.beam) + '</td>' +
          '<td>' + r.pos + '</td><td>' + r.n + '-D' + r.di + '</td>' +
          '<td>' + need + '</td>' +
          '<td>' + r.la.toFixed(0) +
          (r.la2 != null ? ' (2段' + r.la2.toFixed(0) + ')' : '') +
          '</td>' + rdOxTd(r.ok) + '</tr>';
      });
      h += '</tbody></table>';
    }
    if (j.through.length) {
      const ng = j.through.filter(r => !r.ok);
      h += '<div class="row"><b>通し配筋 (17.3式)</b>: ' +
        j.through.length + '件中 NG ' + ng.length + '件</div>';
      if (ng.length) {
        h += '<table class="res"><thead><tr><th>節点</th><th>方向</th>' +
          '<th>柱</th><th>D</th><th>梁</th><th>位置</th><th>db</th>' +
          '<th>上限db</th></tr></thead><tbody>';
        ng.forEach(r => {
          h += '<tr><td>' + r.node + '</td><td>' + r.dir +
            '</td><td>' + esc(r.col) + '</td><td>' + r.Dc.toFixed(0) +
            '</td><td>' + esc(r.beam) + '</td><td>' + r.pos + '</td>' +
            '<td>D' + r.di + '</td><td class="ng">' +
            r.limit.toFixed(1) + '</td></tr>';
        });
        h += '</tbody></table>';
      }
    }
    if (j.bond.length) {
      h += '<div class="row"><b>付着 (断面代表)</b></div>' +
        '<table class="res"><thead><tr><th>符号</th><th>位置</th>' +
        '<th>配筋</th><th>QL</th><th>τL</th><th>Lfa</th><th>判定</th>' +
        '<th>Qs</th><th>τs</th><th>sfa</th><th>判定</th>' +
        '<th>τy</th><th>K・fb</th><th>判定</th></tr></thead><tbody>';
      j.bond.forEach(r => {
        h += '<tr><td>' +
          esc(r.beam) + '</td><td>' + r.pos + '</td>' +
          '<td>' + r.n + '-D' + r.di + '</td>' +
          '<td>' + r.ql.toFixed(1) + '</td>' +
          '<td>' + r.tau_l.toFixed(2) + '</td>' +
          '<td>' + r.fa_l.toFixed(2) + '</td>' + rdOxTd(r.ok_l) +
          '<td>' + r.qs.toFixed(1) + '</td>' +
          '<td>' + r.tau_s.toFixed(2) + '</td>' +
          '<td>' + r.fa_s.toFixed(2) + '</td>' + rdOxTd(r.ok_s) +
          '<td>' + (r.tau_y == null ? '-' : r.tau_y.toFixed(2)) + '</td>' +
          '<td>' + (r.kfb == null ? '-' : r.kfb.toFixed(2)) + '</td>' +
          rdOxTd(r.ok_y) + '</tr>';
      });
      h += '</tbody></table>';
    }
    if (j.joints.length) {
      h += '<div class="row"><b>柱梁接合部</b></div>' +
        '<table class="res"><thead><tr><th>節点</th><th>方向</th>' +
        '<th>形式</th><th>柱</th><th>D×b</th><th>梁</th><th>bj</th>' +
        '<th>QAj</th><th>QDj</th><th>QDj/QAj</th><th>判定</th>' +
        '</tr></thead><tbody>';
      j.joints.forEach(r => {
        h += '<tr><td>' + r.node +
          '</td><td>' + r.dir + '</td><td>' + esc(r.form) + '</td>' +
          '<td>' + esc(r.col) + '</td>' +
          '<td>' + r.Dc.toFixed(0) + '×' + r.bc.toFixed(0) + '</td>' +
          '<td>' + esc(r.beams) + '</td>' +
          '<td>' + r.bj.toFixed(0) + '</td>' +
          '<td>' + r.QAj.toFixed(1) + '</td>' +
          '<td>' + r.QDj.toFixed(1) + '</td>' +
          '<td' + (r.ok ? '' : ' class="ng"') + '>' + r.ratio.toFixed(2) + '</td>' +
          rdOxTd(r.ok) + '</tr>';
      });
      h += '</tbody></table>';
    }
    // 耐震壁周辺柱の判定
    if (j.walls && j.walls.length) {
      h += '<div class="row"><b>耐震壁周辺柱の断面 (19条)</b></div>' +
        '<table class="res"><thead><tr><th>符号</th><th>要素</th>' +
        '<th>壁長</th><th>s</th><th>QD (kN)</th><th>ケース</th>' +
        '<th>t\'</th><th>必要A</th><th>必要Dmin</th></tr></thead><tbody>';
      j.walls.forEach(w => {
        h += '<tr><td>' + esc(w.name) + '</td><td>' + w.ele + '</td>' +
          '<td>' + w.lw.toFixed(0) + '</td>' +
          '<td>' + w.s.toFixed(0) + '</td>' +
          '<td>' + (w.qd == null ? '-' : w.qd.toFixed(1)) + '</td>' +
          '<td>' + (w.qd_case ? esc(w.qd_case) : '-') + '</td>' +
          '<td>' + w.tp.toFixed(1) + '</td>' +
          '<td>' + (w.a_req / 1000).toFixed(0) + '×10³mm²</td>' +
          '<td>' + w.d_req.toFixed(0) + 'mm</td></tr>';
      });
      h += '</tbody></table>' +
        '<table class="res"><thead><tr><th>壁符号</th><th>端</th>' +
        '<th>柱</th><th>B×D</th><th>A</th><th>Dmin</th>' +
        '<th>必要A</th><th>必要Dmin</th><th>判定</th></tr></thead><tbody>';
      j.walls.forEach(w => {
        w.cols.forEach(c => {
          h += '<tr><td>' + esc(w.name) +
            (w.manual ? ' (手動)' : '') + '</td>' +
            '<td>' + (c.side === 'minus' ? '始端' : '終端') + '</td>' +
            '<td>' + esc(c.name) + '</td>' +
            '<td>' + (c.B == null ? '-' :
              c.B.toFixed(0) + '×' + c.H.toFixed(0)) + '</td>' +
            '<td>' + (c.A == null ? '-' :
              (c.A / 1000).toFixed(0) + '×10³') + '</td>' +
            '<td>' + (c.dmin == null ? '-' : c.dmin.toFixed(0)) + '</td>' +
            '<td>' + (w.a_req / 1000).toFixed(0) + '×10³</td>' +
            '<td>' + w.d_req.toFixed(0) + '</td>' +
            rdOxTd(c.ok) + '</tr>';
        });
      });
      h += '</tbody></table>';
    }
    // 開口補強の判定
    if (j.openings && j.openings.length) {
      h += '<div class="row"><b>開口補強 (19条5項)</b>' +
        '<span class="hint">各欄は 左辺/右辺 (19.14: kN、19.15・16: ' +
        'kN・m)</span></div>' +
        '<table class="res"><thead><tr><th>符号</th><th>開口</th>' +
        '<th>QD</th><th>斜め</th><th>縦</th><th>横</th>' +
        '<th>19.14</th><th>判定</th><th>19.15</th><th>判定</th>' +
        '<th>19.16</th><th>判定</th></tr></thead><tbody>';
      j.openings.forEach(r => {
        h += '<tr><td>' + esc(r.name) + '</td>' +
          '<td>' + r.l0.toFixed(0) + '×' + r.h0.toFixed(0) + '</td>' +
          '<td>' + r.qd.toFixed(1) + '</td>' +
          '<td>' + esc(r.d_txt) + '</td><td>' + esc(r.v_txt) + '</td>' +
          '<td>' + esc(r.h_txt) + '</td>' +
          '<td>' + r.lhs14.toFixed(0) + '/' + r.rhs14.toFixed(0) +
          '</td>' + rdOxTd(r.ok14) +
          '<td>' + r.lhs15.toFixed(0) + '/' + r.rhs15.toFixed(0) +
          '</td>' + rdOxTd(r.ok15) +
          '<td>' + r.lhs16.toFixed(0) + '/' + r.rhs16.toFixed(0) +
          '</td>' + rdOxTd(r.ok16) + '</tr>';
      });
      h += '</tbody></table>';
    }
    // TeXソース (計算書組込み用、3ファイル別)
    h += '<div class="row"><b>TeXソース (計算書組込み用)</b>: ' +
      j.tex_files.map(f => '<a href="' + f.url + '" download="' + f.name +
        '">' + esc(f.name) + '</a>').join('　') + '</div>';
    if (j.pdf) {
      h += pdfListHtml([j.pdf]);
    }
    h += openFolderBtn(j.out_dir);
    // コンパイル結果のプレビュー画像
    if (j.preview_pngs && j.preview_pngs.length) {
      h += '<div class="row"><b>プレビュー (コンパイル結果)</b></div>';
      j.preview_pngs.forEach(u => {
        h += '<img src="' + u + '" style="max-width:100%;border:1px ' +
          'solid #bbb;margin:4px 0;display:block">';
      });
    }
    $('rd_result').innerHTML = h;
    setMsg('rd_msg', '検定が完了しました。', 'msg-ok');
    if (j.notes && j.notes.length) {
      $('rd_msg').innerHTML += notesHtml(j.notes);
    }
  } catch (e) { setMsg('rd_msg', esc(e.message), 'msg-err'); }
}
