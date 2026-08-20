"""manager/launcher.py の停止まわりのテスト (外部プロセスはモック)。"""
import pytest

from manager import launcher


class TestStopApp:
    def test_not_running_returns_false(self, monkeypatch):
        monkeypatch.setattr(launcher, 'port_in_use', lambda port: False)
        monkeypatch.setattr(launcher, '_pids_listening',
                            lambda port: pytest.fail('調べる必要がない'))
        assert launcher.stop_app(8765) is False

    def test_stops_python_listener_and_waits_for_port(self, monkeypatch):
        killed = []
        state = {'in_use': True}
        monkeypatch.setattr(launcher, 'port_in_use',
                            lambda port: state['in_use'])
        monkeypatch.setattr(launcher, '_pids_listening', lambda port: [123])
        monkeypatch.setattr(launcher, '_process_name',
                            lambda pid: 'python.exe')

        def terminate(pid):
            killed.append(pid)
            state['in_use'] = False
        monkeypatch.setattr(launcher, '_terminate', terminate)
        assert launcher.stop_app(8765) is True
        assert killed == [123]

    def test_refuses_to_kill_other_program(self, monkeypatch):
        # ポートを別のプログラムが使っている場合は誤終了しない
        monkeypatch.setattr(launcher, 'port_in_use', lambda port: True)
        monkeypatch.setattr(launcher, '_pids_listening', lambda port: [99])
        monkeypatch.setattr(launcher, '_process_name',
                            lambda pid: 'chrome.exe')
        killed = []
        monkeypatch.setattr(launcher, '_terminate',
                            lambda pid: killed.append(pid))
        with pytest.raises(launcher.LaunchError):
            launcher.stop_app(8765, timeout=0.3)
        assert killed == []

    def test_unknown_process_name_is_still_stopped(self, monkeypatch):
        # プログラム名を取得できない場合 (権限等) は python とみなして止める
        state = {'in_use': True}
        monkeypatch.setattr(launcher, 'port_in_use',
                            lambda port: state['in_use'])
        monkeypatch.setattr(launcher, '_pids_listening', lambda port: [55])
        monkeypatch.setattr(launcher, '_process_name', lambda pid: '')

        def terminate(pid):
            state['in_use'] = False
        monkeypatch.setattr(launcher, '_terminate', terminate)
        assert launcher.stop_app(8765) is True

    def test_raises_when_port_stays_busy(self, monkeypatch):
        monkeypatch.setattr(launcher, 'port_in_use', lambda port: True)
        monkeypatch.setattr(launcher, '_pids_listening', lambda port: [123])
        monkeypatch.setattr(launcher, '_process_name', lambda pid: 'python')
        monkeypatch.setattr(launcher, '_terminate', lambda pid: None)
        with pytest.raises(launcher.LaunchError,
                           match='終了できませんでした'):
            launcher.stop_app(8765, timeout=0.3)


class TestStopOtherBeta:
    """β版はポートが 1 つしかないので、別の版が動いていたら止めてから起動する.

    止めずに起動すると launch_app は新規起動せず**前の版の画面**を開く。
    画面に版名が出ないため、利用者は違う版を見たまま気づかない
    (フィードバックも見ていない版の提出に付く)。
    """

    def _root(self, monkeypatch, tmp_path):
        monkeypatch.setattr(launcher.paths, 'install_root',
                            lambda config=None: str(tmp_path))
        return tmp_path

    def test_nothing_running(self, monkeypatch, tmp_path):
        self._root(monkeypatch, tmp_path)
        monkeypatch.setattr(launcher, 'port_in_use', lambda port: False)
        monkeypatch.setattr(launcher, 'stop_app',
                            lambda *a, **k: pytest.fail('止める相手がいない'))
        assert launcher.stop_other_beta('v1.5-beta.1', 8766) is False

    def test_same_version_is_left_running(self, monkeypatch, tmp_path):
        self._root(monkeypatch, tmp_path)
        launcher.remember_beta('v1.5-beta.1')
        monkeypatch.setattr(launcher, 'port_in_use', lambda port: True)
        monkeypatch.setattr(launcher, 'stop_app',
                            lambda *a, **k: pytest.fail('同じ版は止めない'))
        assert launcher.stop_other_beta('v1.5-beta.1', 8766) is False

    def test_other_version_is_stopped(self, monkeypatch, tmp_path):
        self._root(monkeypatch, tmp_path)
        launcher.remember_beta('v1.4-beta.2')
        monkeypatch.setattr(launcher, 'port_in_use', lambda port: True)
        stopped = []
        monkeypatch.setattr(launcher, 'stop_app',
                            lambda port, **k: (stopped.append(port), True)[1])
        assert launcher.stop_other_beta('v1.5-beta.1', 8766) is True
        assert stopped == [8766]
        # 止めたので控えも消える (次の判断を誤らせない)
        assert launcher.running_beta() is None

    def test_unknown_version_is_stopped(self, monkeypatch, tmp_path):
        """どの版が動いているか分からないときは止める (取り違えるより安全)."""
        self._root(monkeypatch, tmp_path)
        monkeypatch.setattr(launcher, 'port_in_use', lambda port: True)
        stopped = []
        monkeypatch.setattr(launcher, 'stop_app',
                            lambda port, **k: (stopped.append(port), True)[1])
        assert launcher.stop_other_beta('v1.5-beta.1', 8766) is True
        assert stopped == [8766]

    def test_remember_and_forget(self, monkeypatch, tmp_path):
        self._root(monkeypatch, tmp_path)
        assert launcher.running_beta() is None      # まだ試していない
        launcher.remember_beta('v1.5-beta.1')
        assert launcher.running_beta() == 'v1.5-beta.1'
        launcher.remember_beta(None)
        assert launcher.running_beta() is None
        launcher.remember_beta(None)                # 二度目でも落ちない

    def test_broken_record_is_treated_as_unknown(self, monkeypatch,
                                                 tmp_path):
        root = self._root(monkeypatch, tmp_path)
        (root / 'beta').mkdir()
        (root / 'beta' / launcher.RUNNING_BETA_NAME).write_text(
            'これは JSON ではない', encoding='utf-8')
        assert launcher.running_beta() is None

    def test_the_record_does_not_confuse_the_tidy_up(self, monkeypatch,
                                                     tmp_path):
        """控え (ファイル) をβ版の置き場と間違えて片付けないこと."""
        from manager import updater
        root = self._root(monkeypatch, tmp_path)
        monkeypatch.setattr(updater.paths, 'install_root',
                            lambda config=None: str(root))
        (root / 'beta' / 'v1.5-beta.1' / 'mgtkit').mkdir(parents=True)
        launcher.remember_beta('v1.5-beta.1')
        assert updater.prune_betas([]) == ['v1.5-beta.1']
        assert launcher.running_beta() == 'v1.5-beta.1'

