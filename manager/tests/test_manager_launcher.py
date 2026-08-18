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
