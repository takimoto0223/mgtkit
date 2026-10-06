"""manager/tabconv.py (古い書き方のタブ追加を提出時に直す) のテスト.

ローカルの bare リポジトリを origin に見立て、
  基点 (古い版)  = 見出し・自動登録より前の書き方の小さな本体
  最新版 (main)  = 見出し・自動登録・data-sync・イベントのある本体
を用意して、古い書き方でタブを足した ZIP を提出の入口
(prepare_submission → finalize_submission) に通す。gh はモックする。
最後に、この版の本物の app.py / index.html / app.js でも読み取れることを確かめる
(つなぎ込みの書き方を変えたら、ここが落ちて変換側の追従漏れに気づける)。
"""
import json
import logging
import shutil
import subprocess
import zipfile

from pathlib import Path

import pytest

from manager import submit, tabconv
from manager.gitcli import run_git

ROOT = Path(__file__).resolve().parents[2]


def _git(args, cwd):
    return subprocess.run(['git'] + args, cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


# ---- 基点 (古い書き方の版) ------------------------------------------------
OLD = {
    'app.py': (
        "import sys\n"
        "from flask import Flask\n"
        "from mgtkit.loadmap.routes import make_blueprint as _loadmap_bp\n"
        "\n"
        "app = Flask(__name__)\n"
        "\n"
        "# 荷重分布図タブ (mgtkit/loadmap/)\n"
        "app.register_blueprint(_loadmap_bp(sys.modules[__name__]))\n"
        "\n"
        "\n"
        "def helper():\n"
        "    return 1\n"),
    'templates/index.html': (
        "<nav>\n"
        "  <button data-tab=\"model\" class=\"active\">モデル</button>\n"
        "  <button data-tab=\"quantity\">数量集計</button>\n"
        "  <button data-tab=\"loadmap\">荷重分布図</button>\n"
        "  <button data-tab=\"nvalue\">N値計算</button>\n"
        "</nav>\n"
        "<main>\n"
        "  <span class=\"hint\">生成物はタブ別フォルダ\n"
        "    (model / stress / qr) に保存されます。</span>\n"
        "<section class=\"tab active\" id=\"tab-model\"></section>\n"
        "<section class=\"tab\" id=\"tab-quantity\"></section>\n"
        "\n"
        "{% include '_tab_loadmap.html' %}\n"
        "{% include '_tab_nvalue.html' %}\n"
        "</main>\n"),
    'static/app.js': (
        "'use strict';\n"
        "const $ = id => document.getElementById(id);\n"
        "\n"
        "const SYNC_GROUPS = [\n"
        "  ['beam_stress_path', 'c_beam_stress_path'],\n"
        "  ['plate_stress_path', 'c_plate_stress_path'],\n"
        "];\n"
        "\n"
        "['mgt_path', 'mgt_out_base', 'beam_stress_path',\n"
        " 'plate_stress_path'].forEach(id => {\n"
        "  const el = $(id);\n"
        "  el.value = localStorage.getItem('mgtkit_' + id) || '';\n"
        "});\n"
        "\n"
        "function clearCaseTable() {\n"
        "  const box = $('check_cases_box');\n"
        "  if (box && box.innerHTML.trim() !== '') {\n"
        "    box.innerHTML = '<span class=\"hint\">読み直してください</span>';\n"
        "  }\n"
        "}\n"
        "\n"
        "async function api(url, body) {\n"
        "  if (body && typeof body === 'object' && !('path_hints' in body)) {\n"
        "    body.path_hints = ['beam_stress_path',\n"
        "      'plate_stress_path']\n"
        "      .map(id => $(id) ? $(id).value.trim() : '').filter(v => v);\n"
        "  }\n"
        "  return fetch(url, {method: 'POST', body: JSON.stringify(body)});\n"
        "}\n"
        "\n"
        "async function loadMgt() {\n"
        "  try {\n"
        "    const j = await api('/api/mgt_info', {mgt_path: $('mgt_path').value});\n"
        "    groupChecks(j.groups, 'vertical', 'axgroup', 'axes_box', true);\n"
        "    let hmsg = '';\n"
        "    setMsg('mgt_msg', 'グループ ' + j.groups.length + ' 件' + hmsg);\n"
        "  } catch (e) { setMsg('mgt_msg', e.message, 'msg-err'); }\n"
        "}\n"),
    'templates/_tab_loadmap.html': '<section class="tab" id="tab-loadmap"></section>\n',
    'templates/_tab_nvalue.html': '<section class="tab" id="tab-nvalue"></section>\n',
    'loadmap/__init__.py': '',
    'loadmap/routes.py': 'def make_blueprint(host):\n    return None\n',
}

# ---- 最新版 (見出し・自動登録のある版) ----------------------------------------
NEW = dict(OLD)
NEW.update({
    'tabreg.py': 'def discover_blueprints(app, host, base):\n    pass\n\n\n'
                 'def collect_tabs(templates_dir, builtin=()):\n    pass\n',
    'app.py': (
        "import sys\n"
        "from mgtkit import tabreg as _tabreg\n"
        "\n"
        "app = _tabreg.TabFlask(__name__)\n"
        "_TAB_PACKAGES, _TAB_ERRORS = _tabreg.discover_blueprints(\n"
        "    app, sys.modules[__name__], '.')\n"
        "BUILTIN_TABS = [\n"
        "    {'id': 'model', 'label': 'モデル', 'order': 10, 'active': True},\n"
        "    {'id': 'quantity', 'label': '数量集計', 'order': 90},\n"
        "]\n"
        "\n"
        "\n"
        "def helper():\n"
        "    return 1\n"),
    'templates/index.html': (
        "<nav>\n"
        "{%- for t in tabs if t.nav %}\n"
        "  <button data-tab=\"{{ t.id }}\"{% if t.active %} class=\"active\"{% endif %}>{{ t.label }}</button>\n"
        "{%- endfor %}\n"
        "</nav>\n"
        "<main>\n"
        "  <span class=\"hint\">生成物はタブ別フォルダ\n"
        "    (model / stress / qr{% for t in tabs if t.out %} / {{ t.out }}{% endfor %}) に保存されます。</span>\n"
        "<section class=\"tab active\" id=\"tab-model\"></section>\n"
        "<section class=\"tab\" id=\"tab-quantity\"></section>\n"
        "{%- for t in tabs if t.include %}\n"
        "{% include t.template %}\n"
        "{%- endfor %}\n"
        "</main>\n"),
    'static/app.js': OLD['static/app.js']
    .replace("const SYNC_GROUPS = [\n"
             "  ['beam_stress_path', 'c_beam_stress_path'],\n"
             "  ['plate_stress_path', 'c_plate_stress_path'],\n"
             "];\n",
             "const SYNC_GROUPS = {\n"
             "  beam: ['beam_stress_path', 'c_beam_stress_path'],\n"
             "  plate: ['plate_stress_path', 'c_plate_stress_path'],\n"
             "};\n")
    .replace("['mgt_path', 'mgt_out_base', 'beam_stress_path',\n"
             " 'plate_stress_path'].forEach(id => {",
             "const PATH_IDS = ['mgt_path', 'mgt_out_base', 'beam_stress_path',\n"
             " 'plate_stress_path'];\n"
             "PATH_IDS.forEach(id => {")
    .replace("    body.path_hints = ['beam_stress_path',\n"
             "      'plate_stress_path']\n",
             "    body.path_hints = PATH_IDS\n")
    .replace("    box.innerHTML = '<span class=\"hint\">読み直してください</span>';\n"
             "  }\n",
             "    box.innerHTML = '<span class=\"hint\">読み直してください</span>';\n"
             "  }\n"
             "  document.dispatchEvent(new CustomEvent('mgtkit:stress-changed'));\n")
    .replace("    groupChecks(j.groups, 'vertical', 'axgroup', 'axes_box', true);\n",
             "    groupChecks(j.groups, 'vertical', 'axgroup', 'axes_box', true);\n"
             "    document.dispatchEvent(new CustomEvent('mgtkit:mgt-loaded', {detail: j}));\n"),
    'templates/_tab_loadmap.html': '{# tab: id="loadmap" label="荷重分布図" order="100" #}\n'
                                   + OLD['templates/_tab_loadmap.html'],
    'templates/_tab_nvalue.html': '{# tab: id="nvalue" label="N値計算" order="210" #}\n'
                                  + OLD['templates/_tab_nvalue.html'],
})

# ---- 古い書き方で足したタブ (木造梁接合部) -----------------------------------
TAB_FILES = {
    'jointchk/__init__.py': '',
    'jointchk/routes.py': 'def make_blueprint(host):\n    return None\n',
    'templates/_tab_jointchk.html': (
        '<!-- タブ: 木造梁接合部 -->\n'
        '<section class="tab" id="tab-joint">\n'
        '  <input type="text" id="j_beam_stress_path" class="path">\n'
        '  <div id="j_cases_box"></div>\n'
        '</section>\n'
        '<script src="/static/jointchk.js"></script>\n'),
    'static/jointchk.js': ("'use strict';\n"
                           "function jointGroups(groups) {\n"
                           "  return groups.length;\n"
                           "}\n"),
}


def _old_style_on_old_base(files):
    """OLD を基点に、古い書き方で木造梁接合部タブを足した中身."""
    f = dict(files)
    f.update(TAB_FILES)
    f['app.py'] = f['app.py'].replace(
        "from mgtkit.loadmap.routes import make_blueprint as _loadmap_bp\n",
        "from mgtkit.loadmap.routes import make_blueprint as _loadmap_bp\n"
        "from mgtkit.jointchk.routes import make_blueprint as _jointchk_bp\n"
    ).replace(
        "app.register_blueprint(_loadmap_bp(sys.modules[__name__]))\n",
        "app.register_blueprint(_loadmap_bp(sys.modules[__name__]))\n"
        "\n"
        "# 木造梁接合部タブ (mgtkit/jointchk/)。同じ作法で登録する\n"
        "app.register_blueprint(_jointchk_bp(sys.modules[__name__]))\n")
    f['templates/index.html'] = f['templates/index.html'].replace(
        "  <button data-tab=\"loadmap\">荷重分布図</button>\n",
        "  <button data-tab=\"loadmap\">荷重分布図</button>\n"
        "  <button data-tab=\"joint\">木造梁接合部</button>\n"
    ).replace(
        "{% include '_tab_loadmap.html' %}\n",
        "{% include '_tab_loadmap.html' %}\n"
        "\n"
        "{% include '_tab_jointchk.html' %}\n"
    ).replace("(model / stress / qr)", "(model / stress / qr /\n    jointchk)")
    f['static/app.js'] = f['static/app.js'].replace(
        "  ['beam_stress_path', 'c_beam_stress_path'],\n",
        "  ['beam_stress_path', 'c_beam_stress_path',\n   'j_beam_stress_path'],\n"
    ).replace(
        " 'plate_stress_path'].forEach(id => {",
        " 'plate_stress_path', 'j_beam_stress_path'].forEach(id => {"
    ).replace(
        "      'plate_stress_path']\n",
        "      'plate_stress_path', 'j_beam_stress_path']\n"
    ).replace(
        "    box.innerHTML = '<span class=\"hint\">読み直してください</span>';\n"
        "  }\n",
        "    box.innerHTML = '<span class=\"hint\">読み直してください</span>';\n"
        "  }\n"
        "  // 梁接合部タブのケース表も読み直しを促す\n"
        "  const jbox = $('j_cases_box');\n"
        "  if (jbox) {\n"
        "    jbox.innerHTML = '';\n"
        "  }\n"
    ).replace(
        "    groupChecks(j.groups, 'vertical', 'axgroup', 'axes_box', true);\n",
        "    groupChecks(j.groups, 'vertical', 'axgroup', 'axes_box', true);\n"
        "    if (typeof jointGroups === 'function') jointGroups(j.groups);\n")
    return f


def _old_style_on_new_base(files):
    """NEW を基点に、古い書き方 (nav の末尾にボタン等) でタブを足した中身."""
    f = dict(files)
    f.update(TAB_FILES)
    f['app.py'] = f['app.py'].replace(
        "from mgtkit import tabreg as _tabreg\n",
        "from mgtkit import tabreg as _tabreg\n"
        "from mgtkit.jointchk.routes import make_blueprint as _jointchk_bp\n"
    ).replace(
        "    app, sys.modules[__name__], '.')\n",
        "    app, sys.modules[__name__], '.')\n"
        "app.register_blueprint(_jointchk_bp(sys.modules[__name__]))\n")
    f['templates/index.html'] = f['templates/index.html'].replace(
        "{%- endfor %}\n</nav>\n",
        "{%- endfor %}\n  <button data-tab=\"joint\">木造梁接合部</button>\n</nav>\n"
    ).replace(
        "{% include t.template %}\n{%- endfor %}\n",
        "{% include t.template %}\n{%- endfor %}\n"
        "{% include '_tab_jointchk.html' %}\n")
    f['static/app.js'] = f['static/app.js'].replace(
        "  beam: ['beam_stress_path', 'c_beam_stress_path'],\n",
        "  beam: ['beam_stress_path', 'c_beam_stress_path', 'j_beam_stress_path'],\n")
    return f


@pytest.fixture()
def env(tmp_path):
    """origin (bare) の main = 最新版。その 1 つ前のコミット = 古い版."""
    origin = tmp_path / 'origin.git'
    _git(['init', '--bare', '-b', 'main', str(origin)], cwd=tmp_path)
    seed = tmp_path / 'seed'
    seed.mkdir()
    _git(['init', '-b', 'main', '.'], cwd=seed)

    def commit(files, msg):
        for p in seed.iterdir():
            if p.name != '.git':
                shutil.rmtree(p) if p.is_dir() else p.unlink()
        for rel, content in files.items():
            p = seed / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content.encode('utf-8'))
        _git(['add', '-A'], cwd=seed)
        _git(['-c', 'user.name=t', '-c', 'user.email=t@example.com',
              'commit', '-q', '-m', msg], cwd=seed)
        return _git(['rev-parse', 'HEAD'], cwd=seed).strip()

    old_sha = commit(OLD, 'old')
    new_sha = commit(NEW, 'new way')
    _git(['remote', 'add', 'origin', str(origin)], cwd=seed)
    _git(['push', '-q', 'origin', 'main'], cwd=seed)
    workrepo = tmp_path / 'workrepo'
    _git(['clone', '-q', str(origin), str(workrepo)], cwd=tmp_path)
    return {'tmp': tmp_path, 'workrepo': str(workrepo), 'origin': str(origin),
            'old': old_sha, 'new': new_sha, 'commit': commit}


def _zip(tmp_path, files, base_sha, name='submission.zip'):
    z = tmp_path / name
    with zipfile.ZipFile(z, 'w') as zf:
        for rel, content in files.items():
            zf.writestr(rel, content)
        zf.writestr('version.json', json.dumps({'version': 'v1.12',
                                                'commit': base_sha}))
    return str(z)


def _read(prep, rel):
    return (Path(prep['extract_dir']) / rel).read_text(encoding='utf-8')


@pytest.fixture()
def gh(monkeypatch):
    calls = []

    def fake_run_gh(args, timeout=60):
        calls.append(args)
        if args[:2] == ['api', 'user']:
            return 'y-kunie\n'
        if args[:2] == ['pr', 'create']:
            return 'https://github.com/o/r/pull/7\n'
        raise AssertionError('unexpected gh call: %r' % args)

    monkeypatch.setattr(submit.ghcli, 'run_gh', fake_run_gh)
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    return calls


def _body(calls):
    c = next(c for c in calls if c[:2] == ['pr', 'create'])
    return c[c.index('--body') + 1]


class TestOldBase:
    """見出しより前の版を基点にした、古い書き方の提出."""

    def test_rewritten_into_the_tab_files(self, env):
        z = _zip(env['tmp'], _old_style_on_old_base(OLD), env['old'])
        prep = submit.prepare_submission(z, {}, env['workrepo'])
        try:
            conv = prep['tabconv']
            assert conv['status'] == 'converted', conv
            assert conv['needs_merge'] is True
            # つなぎ込みの 3 ファイルは送らない (基点のまま)
            ch = prep['changes']
            assert not set(tabconv.HUB_FILES) & set(ch['added'] + ch['modified'])
            for rel in tabconv.HUB_FILES:
                assert _read(prep, rel) == OLD[rel]
            # 見出し: 荷重分布図 (100) と N値計算 (210) の間 → きりのよい 150
            tpl = _read(prep, 'templates/_tab_jointchk.html')
            assert tpl.split('\n')[0] == (
                '{# tab: id="joint" label="木造梁接合部" order="150" '
                'out="jointchk" #}')
            # 一覧の id → 入力欄の属性 (同期グループ名は最新版の beam)
            assert ('<input type="text" id="j_beam_stress_path" '
                    'data-sync="beam" data-persist class="path">') in tpl
            # 関数に足した処理 → タブの JS でイベントを受け取る
            js = _read(prep, 'static/jointchk.js')
            assert js.startswith(TAB_FILES['static/jointchk.js'])
            assert ("document.addEventListener('mgtkit:mgt-loaded', e => {\n"
                    "  const j = e.detail;\n"
                    "  if (typeof jointGroups === 'function') "
                    "jointGroups(j.groups);\n});") in js
            assert ("document.addEventListener('mgtkit:stress-changed', () => {\n"
                    "  // 梁接合部タブのケース表も読み直しを促す\n"
                    "  const jbox = $('j_cases_box');\n"
                    "  if (jbox) {\n"
                    "    jbox.innerHTML = '';\n"
                    "  }\n});") in js
            assert prep['safety']['warnings'] == []
            assert subprocess.run(['node', '--check', str(
                Path(prep['extract_dir']) / 'static/jointchk.js')]
            ).returncode == 0 if shutil.which('node') else True
        finally:
            submit.cleanup(prep)

    def test_submission_does_not_collide_with_latest(self, env, gh):
        z = _zip(env['tmp'], _old_style_on_old_base(OLD), env['old'])
        prep = submit.prepare_submission(z, {}, env['workrepo'])
        result = submit.finalize_submission(prep, [], 'タブを追加', {})
        w = env['workrepo']
        br = result['branch']
        # 提出者の変更の記録 (基点の直後) は 3 ファイルに触れない
        first = run_git(['rev-list', '--first-parent', '--reverse',
                         '%s..%s' % (env['old'], br)], cwd=w).split()[0]
        touched = run_git(['diff', '--name-only', env['old'], first],
                          cwd=w).split()
        assert not set(tabconv.HUB_FILES) & set(touched)
        assert 'templates/_tab_jointchk.html' in touched
        # 最新版を取り込み済み (β版の画面に見出しのタブが出る)
        assert tabconv.has_auto_registration(w, br)
        assert run_git(['show', '%s:templates/index.html' % br], cwd=w) == \
            NEW['templates/index.html']
        # 最新版へ統合してもぶつからない
        _git(['checkout', '-q', '-B', 'trial', 'origin/main'], cwd=w)
        r = subprocess.run(['git', '-c', 'user.name=t', '-c',
                            'user.email=t@example.com', 'merge', '--no-edit',
                            br], cwd=w, capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        # 承認する人向けに、直した箇所を PR 本文に残す
        body = _body(gh)
        assert '# マネージャーが直した箇所 (古い書き方のタブの登録)' in body
        assert 'order="150"' in body
        assert '# 提出時の警告' not in body

    def test_hint_for_an_existing_tab_is_reported_not_dropped_silently(
            self, env):
        f = _old_style_on_old_base(OLD)
        f['templates/index.html'] = f['templates/index.html'].replace(
            '(model / stress / qr /\n    jointchk)',
            '(model / stress / qr / loadmap / jointchk)')
        z = _zip(env['tmp'], f, env['old'])
        prep = submit.prepare_submission(z, {}, env['workrepo'])
        try:
            assert prep['tabconv']['status'] == 'converted'
            [w] = prep['safety']['warnings']
            assert '「loadmap」' in w and '荷重分布図' in w
        finally:
            submit.cleanup(prep)


class TestNewBase:
    def test_button_after_the_loop_goes_right_end_without_order(self, env, gh):
        z = _zip(env['tmp'], _old_style_on_new_base(NEW), env['new'])
        prep = submit.prepare_submission(z, {}, env['workrepo'])
        conv = prep['tabconv']
        assert conv['status'] == 'converted', conv
        assert conv['needs_merge'] is False
        tpl = _read(prep, 'templates/_tab_jointchk.html')
        assert tpl.split('\n')[0] == '{# tab: id="joint" label="木造梁接合部" #}'
        assert 'data-sync="beam"' in tpl and 'data-persist' not in tpl
        result = submit.finalize_submission(prep, [], 'タブを追加', {})
        # 最新版の取り込みは要らない (基点に見出しの仕組みがある)
        assert run_git(['rev-list', '--count', '%s..%s' % (
            env['new'], result['branch'])], cwd=env['workrepo']).strip() == '1'


class TestNotConverted:
    def test_unreadable_change_is_submitted_as_is_with_a_warning(
            self, env, gh, caplog):
        f = _old_style_on_old_base(OLD)
        f['app.py'] = f['app.py'].replace('    return 1\n', '    return 2\n')
        z = _zip(env['tmp'], f, env['old'])
        with caplog.at_level(logging.WARNING, logger='manager.tabconv'):
            prep = submit.prepare_submission(z, {}, env['workrepo'])
        conv = prep['tabconv']
        assert conv['status'] == 'kept'
        # 何も捨てずにそのまま (3 ファイルとも提出の中身のまま)
        assert set(tabconv.HUB_FILES) <= set(prep['changes']['modified'])
        assert _read(prep, 'app.py') == f['app.py']
        assert _read(prep, 'templates/_tab_jointchk.html') == \
            TAB_FILES['templates/_tab_jointchk.html']
        # 画面 (確認ダイアログの警告) とログで知らせる
        [w] = prep['safety']['warnings']
        assert 'そのまま提出します' in w and 'app.py 16 行目' in w
        assert 'return 1' in caplog.text
        submit.finalize_submission(prep, [], 'タブを追加', {})
        body = _body(gh)
        assert '# 提出時の警告 (承認時に確認)' in body and 'app.py 16 行目' in body
        assert '# マネージャーが直した箇所' not in body

    def test_plain_change_of_a_hub_file_is_left_alone(self, env):
        f = dict(OLD)
        f['app.py'] = f['app.py'].replace('    return 1\n', '    return 2\n')
        z = _zip(env['tmp'], f, env['old'])
        prep = submit.prepare_submission(z, {}, env['workrepo'])
        try:
            assert prep['tabconv']['status'] == 'none'
            assert prep['safety']['warnings'] == []
            assert prep['changes']['modified'] == ['app.py']
        finally:
            submit.cleanup(prep)

    def test_latest_without_the_new_way_is_left_alone(self, env):
        env['commit'](OLD, 'back to the old way')
        _git(['push', '-q', '-f', 'origin', 'HEAD:main'], cwd=env['tmp'] / 'seed')
        _git(['fetch', '-q', 'origin'], cwd=env['workrepo'])
        z = _zip(env['tmp'], _old_style_on_old_base(OLD), env['old'])
        prep = submit.prepare_submission(z, {}, env['workrepo'])
        try:
            assert prep['tabconv']['status'] == 'none'
            assert 'app.py' in prep['changes']['modified']
        finally:
            submit.cleanup(prep)

    @pytest.mark.parametrize('edit, where', [
        # 関数の中の変数 (hmsg) を使う処理は、移すと動かない
        (lambda f: f.replace(
            "    setMsg('mgt_msg', 'グループ ' + j.groups.length + ' 件' + hmsg);\n",
            "    hmsg += jointGroups(j.groups);\n"
            "    setMsg('mgt_msg', 'グループ ' + j.groups.length + ' 件' + hmsg);\n"),
         'static/app.js'),
        # どのタブの入力欄でもない id
        (lambda f: f.replace("   'j_beam_stress_path'],\n",
                             "   'j_beam_stress_path', 'zz_path'],\n"),
         'static/app.js'),
        # 一覧に id を足すのではなく、グループを丸ごと足した
        (lambda f: f.replace("];\n\n['mgt_path'",
                             "  ['x_path', 'y_path'],\n];\n\n['mgt_path'"),
         'static/app.js'),
    ])
    def test_js_that_cannot_be_moved_is_kept(self, env, edit, where):
        f = _old_style_on_old_base(OLD)
        f['static/app.js'] = edit(f['static/app.js'])
        z = _zip(env['tmp'], f, env['old'])
        prep = submit.prepare_submission(z, {}, env['workrepo'])
        try:
            assert prep['tabconv']['status'] == 'kept'
            assert where in prep['safety']['warnings'][0]
            assert _read(prep, 'static/app.js') == f['static/app.js']
        finally:
            submit.cleanup(prep)

    def test_converter_failure_does_not_stop_the_submission(
            self, env, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError('bug')
        monkeypatch.setattr(tabconv, 'plan', boom)
        z = _zip(env['tmp'], _old_style_on_old_base(OLD), env['old'])
        prep = submit.prepare_submission(z, {}, env['workrepo'])
        try:
            assert prep['tabconv']['status'] == 'kept'
            assert 'manager.log' in prep['safety']['warnings'][0]
            assert 'app.py' in prep['changes']['modified']
        finally:
            submit.cleanup(prep)


class _FakeTarget:
    def __init__(self, orders):
        self.orders = orders

    def finite_orders(self):
        return [v for v in self.orders.values() if v != float('inf')]


@pytest.mark.parametrize('nav, files, expect', [
    # 間に 1 つ: 90 と 100 の間 → 95
    ([('tab', 'quantity'), ('new', 'a', ''), ('tab', 'loadmap')],
     {'a': '_tab_a.html'}, {'a': '95'}),
    # 間に 5 つ: 100 と 200 の間を 10 刻み (#187 の並び)
    ([('tab', 'loadmap')] + [('new', t, '') for t in 'abcde'] + [('tab', 'wallqty')],
     {t: '_tab_%s.html' % t for t in 'abcde'},
     {'a': '110', 'b': '120', 'c': '130', 'd': '140', 'e': '150'}),
    # 左端
    ([('new', 'a', ''), ('tab', 'model')], {'a': '_tab_a.html'}, {'a': '0'}),
    # 右端 (見出しの order を省略)
    ([('loop',), ('new', 'a', '')], {'a': '_tab_a.html'}, {'a': None}),
    # 右端に 2 つ・ファイル名順が nav と違う → 値を書く
    ([('loop',), ('new', 'z', ''), ('new', 'a', '')],
     {'z': '_tab_z.html', 'a': '_tab_a.html'}, {'z': '220', 'a': '230'}),
    # 最新版に無いタブは飛ばして、その先の隣で決める
    ([('tab', 'quantity'), ('tab', 'gone'), ('new', 'a', ''), ('tab', 'loadmap')],
     {'a': '_tab_a.html'}, {'a': '95'}),
])
def test_order_between_the_neighbours(nav, files, expect):
    target = _FakeTarget({'model': 10, 'quantity': 90, 'loadmap': 100,
                          'wallqty': 200, 'nvalue': 210})
    assert tabconv._assign_orders(nav, target, files) == expect


def test_real_hub_files_of_this_version_are_readable(tmp_path):
    """この版の本物の app.py / index.html / app.js に古い書き方で足しても読める.

    つなぎ込みの 3 ファイルの書き方 (loadMgt の形・一覧の名前・nav の
    ループ) を変えたときに、変換が黙って効かなくなるのを防ぐ。
    この版にまだ見出し・自動登録の仕組み (tabreg.py) が無いときは、
    本物の 3 ファイルに古い書き方で足しても何も直さないこと (ガード) を確かめる。
    """
    if not (ROOT / 'tabreg.py').exists():
        _real_hub_files_without_the_new_way_are_left_alone(tmp_path)
        return
    rels = ['tabreg.py', 'app.py', 'templates/index.html', 'static/app.js']
    # 見本のタブ (TAB_FILES の木造梁接合部) がこの版に本当にあるときは、
    # 本物のほうを外して「まだ無いタブを古い書き方で足す」形にする
    rels += ['templates/' + p.name for p in (ROOT / 'templates').glob('_tab_*.html')
             if 'templates/' + p.name not in TAB_FILES]
    files = {rel: (ROOT / rel).read_text(encoding='utf-8') for rel in rels}
    seed = tmp_path / 'seed'
    for rel, text in files.items():
        p = seed / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode('utf-8'))
    _git(['init', '-q', '-b', 'main', '.'], cwd=seed)
    _git(['add', '-A'], cwd=seed)
    _git(['-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit',
          '-q', '-m', 'this version'], cwd=seed)
    sha = _git(['rev-parse', 'HEAD'], cwd=seed).strip()
    sub = tmp_path / 'sub'
    shutil.copytree(seed, sub, ignore=shutil.ignore_patterns('.git'))
    for rel, text in TAB_FILES.items():
        p = sub / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8')

    def edit(rel, old, new):
        p = sub / rel
        s = p.read_text(encoding='utf-8')
        assert s.count(old) == 1, (rel, old)
        p.write_bytes(s.replace(old, new).encode('utf-8'))
    edit('app.py', 'from mgtkit import tabreg as _tabreg\n',
         'from mgtkit import tabreg as _tabreg\n'
         'from mgtkit.jointchk.routes import make_blueprint as _jointchk_bp\n')
    edit('app.py', 'app = _tabreg.TabFlask(__name__)\n',
         'app = _tabreg.TabFlask(__name__)\n'
         'app.register_blueprint(_jointchk_bp(sys.modules[__name__]))\n')
    edit('templates/index.html', '{%- endfor %}\n</nav>\n',
         '{%- endfor %}\n  <button data-tab="joint">木造梁接合部</button>\n</nav>\n')
    edit('templates/index.html', '{% include t.template %}\n{%- endfor %}\n',
         "{% include t.template %}\n{%- endfor %}\n{% include '_tab_jointchk.html' %}\n")
    edit('templates/index.html', '{{ t.out }}{% endfor %})',
         '{{ t.out }}{% endfor %} / jointchk)')
    edit('static/app.js', "  beam: ['beam_stress_path', ",
         "  beam: ['j_beam_stress_path', 'beam_stress_path', ")
    edit('static/app.js', "const PATH_IDS = ['mgt_path', 'mgt_out_base', ",
         "const PATH_IDS = ['mgt_path', 'mgt_out_base', 'j_beam_stress_path', ")
    edit('static/app.js', '    refreshStructPrevSel();\n    // タブ側は',
         '    refreshStructPrevSel();\n'
         "    if (typeof jointGroups === 'function') jointGroups(j.groups);\n"
         '    // タブ側は')
    edit('static/app.js',
         "  document.dispatchEvent(new CustomEvent('mgtkit:stress-changed'));\n",
         "  const jbox = $('j_cases_box');\n"
         "  if (jbox) jbox.innerHTML = '';\n"
         "  document.dispatchEvent(new CustomEvent('mgtkit:stress-changed'));\n")
    changes = submit.compute_changes(str(seed), sha, str(sub))
    result = tabconv.plan(str(seed), sha, str(sub), changes, 'HEAD')
    assert result['status'] == 'converted', result['problems']
    for rel in tabconv.HUB_FILES:
        assert result['writes'][rel] == files[rel].encode('utf-8')
    tpl = result['writes']['templates/_tab_jointchk.html'].decode('utf-8')
    assert tpl.startswith('{# tab: id="joint" label="木造梁接合部" '
                          'out="jointchk" #}\n')
    assert 'data-sync="beam" data-persist' in tpl
    js = result['writes']['static/jointchk.js'].decode('utf-8')
    assert "addEventListener('mgtkit:mgt-loaded'" in js
    assert "addEventListener('mgtkit:stress-changed'" in js


def _real_hub_files_without_the_new_way_are_left_alone(tmp_path):
    """見出しの仕組みより前の版 (main 単独) では、変換は何もしない."""
    rels = ['app.py', 'templates/index.html', 'static/app.js']
    files = {rel: (ROOT / rel).read_text(encoding='utf-8') for rel in rels}
    seed = tmp_path / 'seed'
    for rel, text in files.items():
        p = seed / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode('utf-8'))
    _git(['init', '-q', '-b', 'main', '.'], cwd=seed)
    _git(['add', '-A'], cwd=seed)
    _git(['-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit',
          '-q', '-m', 'this version'], cwd=seed)
    sha = _git(['rev-parse', 'HEAD'], cwd=seed).strip()
    sub = tmp_path / 'sub'
    shutil.copytree(seed, sub, ignore=shutil.ignore_patterns('.git'))
    for rel, text in TAB_FILES.items():
        p = sub / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8')
    app = sub / 'app.py'
    app.write_bytes((app.read_text(encoding='utf-8')
                     + '\nfrom mgtkit.jointchk.routes import make_blueprint'
                       ' as _jointchk_bp\n'
                       'app.register_blueprint(_jointchk_bp(sys.modules'
                       '[__name__]))\n').encode('utf-8'))
    index = sub / 'templates' / 'index.html'
    s = index.read_text(encoding='utf-8')
    assert s.count('</nav>') == 1
    index.write_bytes(s.replace(
        '</nav>', '  <button data-tab="joint">木造梁接合部</button>\n</nav>'
    ).encode('utf-8'))
    changes = submit.compute_changes(str(seed), sha, str(sub))
    assert 'app.py' in changes['modified']
    result = tabconv.plan(str(seed), sha, str(sub), changes, 'HEAD')
    assert result['status'] == 'none'
    assert result['writes'] == {} and result['warnings'] == []
    assert not tabconv.has_auto_registration(str(seed), 'HEAD')
