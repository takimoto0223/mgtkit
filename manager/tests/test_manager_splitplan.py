"""manager/splitplan.py (タブごとに分ける案) と submit.finalize_split のテスト。

ローカルの bare リポジトリを origin に見立てて git 操作を実際に行い、
gh CLI (ユーザー名取得・PR 作成・本文の更新) はモックする。
"""
import json
import subprocess
import zipfile

import pytest

from manager import splitplan, submit
from manager.gitcli import run_git


def _git(args, cwd):
    subprocess.run(['git'] + args, cwd=cwd, check=True,
                   capture_output=True, text=True)


BASE = {
    'app.py': 'print("app")\n',
    'util.py': 'def old():\n    return 1\n',
    'mgt.py': 'def read():\n    return []\n',
    'templates/index.html': '<html></html>\n',
    'static/app.js': 'function copyOld(){}\n',
    'loadmap/__init__.py': '',
    'loadmap/routes.py': 'def make_blueprint(host):\n    return None\n',
    'templates/_tab_loadmap.html':
        '{# tab: id="loadmap" label="荷重分布図" order="100" #}\n<section/>\n',
    'static/loadmap.js': 'function lm(){}\n',
    'tests/test_x.py': 'def test():\n    pass\n',
}


def _tab(x, label, body='def make_blueprint(host):\n    return None\n'):
    return {
        '%s/__init__.py' % x: '',
        '%s/routes.py' % x: body,
        'templates/_tab_%s.html' % x:
            '{# tab: id="%s" label="%s" #}\n<section id="tab-%s"/>\n'
            % (x, label, x),
        'static/%s.js' % x: 'function %sInit(){}\n' % x,
    }


@pytest.fixture()
def repo_env(tmp_path):
    origin = tmp_path / 'origin.git'
    _git(['init', '--bare', '-b', 'main', str(origin)], cwd=tmp_path)
    seed = tmp_path / 'seed'
    seed.mkdir()
    _git(['init', '-b', 'main', '.'], cwd=seed)
    for rel, content in BASE.items():
        p = seed / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding='utf-8')
    _git(['add', '-A'], cwd=seed)
    _git(['-c', 'user.name=t', '-c', 'user.email=t@example.com',
          'commit', '-m', 'base'], cwd=seed)
    base_sha = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=seed,
                              check=True, capture_output=True,
                              text=True).stdout.strip()
    _git(['remote', 'add', 'origin', str(origin)], cwd=seed)
    _git(['push', 'origin', 'main'], cwd=seed)
    workrepo = tmp_path / 'workrepo'
    _git(['clone', str(origin), str(workrepo)], cwd=tmp_path)
    return {'origin': str(origin), 'workrepo': str(workrepo),
            'base_sha': base_sha, 'tmp': tmp_path}


def _zip(env, overrides, name='sub.zip'):
    files = {r: c for r, c in BASE.items() if not r.startswith('tests/')}
    files['version.json'] = json.dumps(
        {'version': 'v2.1', 'commit': env['base_sha'],
         'distributed_at': '2026-10-08'})
    for rel, content in overrides.items():
        if content is None:
            files.pop(rel, None)
        else:
            files[rel] = content
    path = env['tmp'] / name
    with zipfile.ZipFile(path, 'w') as zf:
        for rel, content in files.items():
            zf.writestr(rel, content)
    return str(path)


def _plan(env, overrides):
    prep = submit.prepare_submission(_zip(env, overrides), {},
                                     env['workrepo'])
    try:
        return splitplan.plan(env['workrepo'], env['base_sha'],
                              prep['extract_dir'], prep['changes']), prep
    finally:
        submit.cleanup(prep)


def _by_key(result):
    return {u['key']: u for u in result['units']}


class TestPlan:
    def test_single_new_tab_is_not_split(self, repo_env):
        result, prep = _plan(repo_env, _tab('rcslab', 'RCスラブ'))
        assert not result['splittable']
        assert prep['split'] is None
        assert [u['label'] for u in result['units']] == ['新しいタブ「RCスラブ」']

    def test_two_tabs_and_a_root_fix_become_three(self, repo_env):
        ov = {}
        ov.update(_tab('rcslab', 'RCスラブ'))
        ov.update(_tab('colbase', 'S露出柱脚'))
        ov['mgt.py'] = 'def read():\n    return [1]\n'
        result, prep = _plan(repo_env, ov)
        assert result['splittable']
        units = _by_key(result)
        assert set(units) == {'common', 'tab:rcslab', 'tab:colbase'}
        assert units['common']['files'] == ['mgt.py']
        assert units['tab:rcslab']['files'] == sorted(_tab('rcslab', 'x'))
        # 新しいタブは共通部分の修正 (計算結果を変えうる) を使っていない
        assert units['tab:rcslab']['shared'] == []
        assert units['tab:rcslab']['label'] == '新しいタブ「RCスラブ」'
        # 準備の段階で案ができている (確認画面に出す)
        assert prep['split'] is not None
        assert result['units'][0]['kind'] == 'common'

    def test_tab_using_a_new_shared_function_carries_that_change(
            self, repo_env):
        ov = _tab('rcslab', 'RCスラブ', body=(
            'from mgtkit.util import new_helper\n'
            'def make_blueprint(host):\n    return new_helper\n'))
        ov.update(_tab('colbase', 'S露出柱脚', body=(
            'from mgtkit.util import old\n'
            'def make_blueprint(host):\n    return old\n')))
        ov['util.py'] = ('def old():\n    return 2\n'
                         'def new_helper():\n    return 3\n')
        result, _ = _plan(repo_env, ov)
        units = _by_key(result)
        assert units['tab:rcslab']['shared'] == ['util.py']
        # 前からある関数だけを使うタブには入れない
        assert units['tab:colbase']['shared'] == []
        assert units['common']['files'] == ['util.py']

    def test_module_alias_attributes_are_checked(self, repo_env):
        ov = _tab('rcslab', 'RCスラブ', body=(
            'import mgtkit.util as u\n'
            'def make_blueprint(host):\n    return u.old, u.__file__\n'))
        ov.update(_tab('colbase', 'S露出柱脚'))
        ov['util.py'] = 'def old():\n    return 2\n'
        units = _by_key(_plan(repo_env, ov)[0])
        assert units['tab:rcslab']['shared'] == []

    def test_tabs_that_need_each_other_are_one_submission(self, repo_env):
        ov = _tab('jointchk', '木造梁接合部')
        ov['jointchk/report.py'] = 'def paged():\n    return 1\n'
        ov.update(_tab('diaphragm', '木造水平構面', body=(
            'from mgtkit.jointchk.report import paged\n'
            'def make_blueprint(host):\n    return paged\n')))
        ov.update(_tab('rcslab', 'RCスラブ'))
        result, _ = _plan(repo_env, ov)
        labels = [u['label'] for u in result['units']]
        assert '新しいタブ「木造水平構面」「木造梁接合部」' in labels
        assert '新しいタブ「RCスラブ」' in labels
        assert len(labels) == 2

    def test_tab_using_existing_parts_of_another_tab_stays_separate(
            self, repo_env):
        ov = {'loadmap/routes.py': (
            'def make_blueprint(host):\n    return None\n'
            'def extra():\n    return 1\n')}
        ov.update(_tab('rcslab', 'RCスラブ', body=(
            'from mgtkit.loadmap.routes import make_blueprint as mb\n'
            'def make_blueprint(host):\n    return mb\n')))
        units = _by_key(_plan(repo_env, ov)[0])
        assert set(units) == {'tab:loadmap', 'tab:rcslab'}
        assert units['tab:loadmap']['label'] == 'タブ「荷重分布図」の改良'

    def test_tab_calling_a_new_screen_function_carries_app_js(
            self, repo_env):
        ov = _tab('rcslab', 'RCスラブ')
        ov['static/rcslab.js'] = 'function go(){ copyEleList([]); }\n'
        ov['static/app.js'] = ('function copyOld(){}\n'
                               'function copyEleList(x){}\n')
        ov.update(_tab('colbase', 'S露出柱脚'))
        units = _by_key(_plan(repo_env, ov)[0])
        assert units['tab:rcslab']['shared'] == ['static/app.js']
        assert units['tab:colbase']['shared'] == []

    def test_new_data_used_only_by_a_tab_moves_with_it(self, repo_env):
        ov = _tab('jointchk', '木造梁接合部', body=(
            "DB = 'joint_fittings.json'\n"
            'def make_blueprint(host):\n    return DB\n'))
        ov['data/joint_fittings.json'] = '{}\n'
        ov.update(_tab('rcslab', 'RCスラブ'))
        result, _ = _plan(repo_env, ov)
        units = _by_key(result)
        assert 'common' not in units          # 共通部分には何も残らない
        assert units['tab:jointchk']['shared'] == ['data/joint_fittings.json']

    def test_hub_files_are_never_copied_into_tabs(self, repo_env):
        ov = _tab('rcslab', 'RCスラブ')
        ov['templates/_tab_rcslab.html'] = (
            '{# tab: id="rcslab" label="RCスラブ" #}\n'
            '<!-- index.html から include される -->\n')
        ov['templates/index.html'] = '<html><body></body></html>\n'
        units = _by_key(_plan(repo_env, ov)[0])
        assert units['tab:rcslab']['shared'] == []
        assert units['common']['files'] == ['templates/index.html']

    def test_standalone_folders_are_one_submission(self, repo_env):
        ov = {'wood_joint/calc.html': '<html/>\n',
              'snow/factor.html': '<html/>\n',
              'loadtex/loadtex.py': 'print(1)\n'}
        ov.update(_tab('rcslab', 'RCスラブ'))
        units = _by_key(_plan(repo_env, ov)[0])
        assert units['tools']['label'] == \
            '単独のツール (フォルダ loadtex・snow・wood_joint)'
        assert len(units['tools']['files']) == 3

    def test_deletion_only_tab_goes_to_common(self, repo_env):
        ov = {'static/loadmap.js': None}
        ov.update(_tab('rcslab', 'RCスラブ'))
        ov['mgt.py'] = 'def read():\n    return [2]\n'
        units = _by_key(_plan(repo_env, ov)[0])
        assert 'tab:loadmap' not in units
        assert units['common']['deleted'] == ['static/loadmap.js']

    def test_requirements_change_rides_with_python_tabs(self, repo_env):
        ov = {'requirements.txt': 'flask\nnumpy\n'}
        ov.update(_tab('rcslab', 'RCスラブ'))
        ov.update(_tab('colbase', 'S露出柱脚'))
        units = _by_key(_plan(repo_env, ov)[0])
        assert units['tab:rcslab']['shared'] == ['requirements.txt']


@pytest.fixture()
def gh_mock(monkeypatch):
    calls = []
    counter = {'n': 200}

    def fake_run_gh(args, timeout=60):
        calls.append(args)
        if args[:2] == ['api', 'user']:
            return 'kunie\n'
        if args[:2] == ['pr', 'create']:
            counter['n'] += 1
            return 'https://github.com/o/r/pull/%d\n' % counter['n']
        if args[:2] == ['pr', 'edit']:
            return ''
        raise AssertionError('unexpected gh call: %r' % args)

    monkeypatch.setattr(submit.ghcli, 'run_gh', fake_run_gh)
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    return calls


def _three_way(env):
    ov = _tab('rcslab', 'RCスラブ', body=(
        'from mgtkit.util import new_helper\n'
        'def make_blueprint(host):\n    return new_helper\n'))
    ov.update(_tab('colbase', 'S露出柱脚'))
    ov['util.py'] = ('def old():\n    return 2\n'
                     'def new_helper():\n    return 3\n')
    ov['app.py'] = None                          # 意図的な削除
    return submit.prepare_submission(_zip(env, ov), {}, env['workrepo'])


class TestFinalizeSplit:
    def test_each_unit_becomes_its_own_submission(self, repo_env, gh_mock):
        prep = _three_way(repo_env)
        units = prep['split']['units']
        result = submit.finalize_split(prep, units, ['app.py'],
                                       '- 新しいタブ 2 つ', {})
        assert [p['label'] for p in result['prs']] == [
            '既存の機能の修正・共通部分', '新しいタブ「RCスラブ」',
            '新しいタブ「S露出柱脚」']
        creates = [c for c in gh_mock if c[:2] == ['pr', 'create']]
        assert len(creates) == 3
        wr = repo_env['workrepo']
        branches = [p['branch'] for p in result['prs']]
        assert branches[0].endswith('-1') and branches[2].endswith('-3')

        def tree(b):
            return set(run_git(['ls-tree', '-r', '--name-only', b],
                               cwd=wr).split())
        # 共通部分: util の修正と意図的な削除
        assert 'app.py' not in tree(branches[0])
        assert 'rcslab/routes.py' not in tree(branches[0])
        # RCスラブ: タブ + 使っている util の新しい関数 (同じ変更を複製)
        t1 = tree(branches[1])
        assert 'rcslab/routes.py' in t1 and 'colbase/routes.py' not in t1
        assert 'new_helper' in run_git(['show', '%s:util.py' % branches[1]],
                                       cwd=wr)
        assert 'app.py' in t1                    # 削除は共通部分の提出だけ
        # S露出柱脚: util は基点のまま
        assert 'new_helper' not in run_git(
            ['show', '%s:util.py' % branches[2]], cwd=wr)

        body = creates[1][creates[1].index('--body') + 1]
        assert body.startswith('<!-- mgtkit-base ')
        info = submit.split_from_body(body)
        assert info == {'bundle': branches[0], 'k': 2, 'n': 3}
        assert '# 分けて出した提出' in body
        assert 'util.py' in body.split('# 分けて出した提出')[1]
        # 手書きの更新内容は全部に付き、タイトルでどのタブか分かる
        title = creates[1][creates[1].index('--title') + 1]
        assert title.startswith('新しいタブ「RCスラブ」')
        # 送ったあとで、本文の一覧に提出番号を書き足す
        edits = [c for c in gh_mock if c[:2] == ['pr', 'edit']]
        assert len(edits) == 3
        edited = edits[0][edits[0].index('--body') + 1]
        assert '(#202)' in edited and '(#203)' in edited

    def test_units_merge_in_any_order_without_conflicts(self, repo_env,
                                                        gh_mock):
        prep = _three_way(repo_env)
        extract = dict()
        for rel in prep['changes']['added'] + prep['changes']['modified']:
            with open(prep['extract_dir'] + '/' + rel, encoding='utf-8') as f:
                extract[rel] = f.read()
        result = submit.finalize_split(prep, prep['split']['units'],
                                       ['app.py'], 'msg', {})
        wr = repo_env['workrepo']
        branches = [p['branch'] for p in result['prs']]
        for order in (branches, list(reversed(branches))):
            run_git(['checkout', '-q', '-B', 'trial', repo_env['base_sha']],
                    cwd=wr)
            for b in order:
                run_git(['-c', 'user.name=t', '-c', 'user.email=t@e',
                         'merge', '--no-edit', '-q', b], cwd=wr)
            for rel, text in extract.items():
                assert run_git(['show', 'trial:%s' % rel], cwd=wr) \
                    .rstrip('\n') == text.rstrip('\n')
            names = run_git(['ls-tree', '-r', '--name-only', 'trial'],
                            cwd=wr).split()
            assert 'app.py' not in names

    def test_ai_drafts_are_reviewed_one_by_one(self, repo_env, gh_mock,
                                               monkeypatch):
        seen = []
        monkeypatch.setattr(
            submit.claude_helper, 'generate_pr_body',
            lambda summary, *a, **k: '# T\n\n## 更新内容\n\n- %s\n'
            % summary.splitlines()[0])

        def review(title, update, limits, part):
            seen.append(part)
            return (title + str(part['k']), update, limits)
        prep = _three_way(repo_env)
        submit.finalize_split(prep, prep['split']['units'], [], '', {},
                              use_ai=True, on_review=review)
        assert [p['k'] for p in seen] == [1, 2, 3]
        assert all(p['n'] == 3 for p in seen)
        titles = [c[c.index('--title') + 1] for c in gh_mock
                  if c[:2] == ['pr', 'create']]
        assert titles == ['T1', 'T2', 'T3']

    def test_cancel_midway_sends_nothing(self, repo_env, gh_mock,
                                         monkeypatch):
        monkeypatch.setattr(submit.claude_helper, 'generate_pr_body',
                            lambda *a, **k: '# T\n\n## 更新内容\n\n- x\n')
        answers = iter([('T', '- x', ''), None])
        prep = _three_way(repo_env)
        with pytest.raises(submit.SubmitCancelled):
            submit.finalize_split(prep, prep['split']['units'], [], '', {},
                                  use_ai=True,
                                  on_review=lambda *a: next(answers))
        assert not [c for c in gh_mock if c[:2] == ['pr', 'create']]
        heads = run_git(['ls-remote', '--heads', repo_env['origin']])
        assert 'feature/' not in heads

    def test_push_failure_reports_what_was_sent(self, repo_env, gh_mock,
                                                monkeypatch):
        prep = _three_way(repo_env)
        real = submit.ghcli.run_gh
        count = {'n': 0}

        def flaky(args, timeout=60):
            if args[:2] == ['pr', 'create']:
                count['n'] += 1
                if count['n'] == 2:
                    raise submit.ghcli.GhError('通信が切れました')
            return real(args, timeout)
        monkeypatch.setattr(submit.ghcli, 'run_gh', flaky)
        with pytest.raises(submit.SubmitError) as e:
            submit.finalize_split(prep, prep['split']['units'], [], 'm', {})
        msg = str(e.value)
        assert '2 本目' in msg and 'ここまでに提出できたもの' in msg
        assert '/pull/201' in msg
