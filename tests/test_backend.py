from pathlib import Path
from types import SimpleNamespace
import pytest
from saleae_differential.backend import SaleaeBackend
from saleae_differential.demo import create_demo
from saleae_differential.models import CaptureSettings, DecodeSettings
from saleae_differential.session import analyze_session


class Manager:
    current = None

    @classmethod
    def launch(cls, *, headless=False, application_path=None, port=None, connect_timeout_seconds=None):
        cls.current = cls()
        cls.current.headless = headless
        cls.current.path = application_path
        return cls.current

    @classmethod
    def connect(cls, **kwargs):
        manager = cls.launch()
        manager.address = kwargs.get('address')
        return manager

    def __init__(self):
        self.closed = False
        self.capture_closed = False
        self.error = None
        self.devices_list = [SimpleNamespace(device_id='PRO8', device_type=8, is_simulation=False)]

    def close(self):
        self.closed = True

    def get_devices(self, **kwargs):
        self.device_query = kwargs
        return self.devices_list

    def get_app_info(self):
        return SimpleNamespace(app_version='mock-1.0')

    def start_capture(self, **kwargs):
        self.configuration = kwargs
        return SimpleNamespace(wait=self.wait, export_raw_data_binary=self.export, close=self.close_capture)

    def wait(self):
        if self.error:
            raise RuntimeError(self.error)

    def export(self, directory, **kwargs):
        create_demo(Path(directory))

    def close_capture(self):
        self.capture_closed = True


@pytest.fixture
def backend():
    api = SimpleNamespace(Manager=Manager, DeviceType=SimpleNamespace(LOGIC_PRO_8=8),
                          LogicDeviceConfiguration=lambda **kw: kw,
                          CaptureConfiguration=lambda **kw: kw, TimedCaptureMode=lambda **kw: kw)
    return SaleaeBackend(api)


def test_launch_capture_cleanup_and_reuse(backend, tmp_path):
    backend.connect()
    manager = backend.manager
    assert manager.headless
    backend.connect()
    assert backend.manager is manager
    session = backend.capture(CaptureSettings(), DecodeSettings(), tmp_path)
    assert manager.capture_closed
    assert manager.configuration['device_id'] == 'PRO8'
    assert manager.configuration['device_configuration']['enabled_analog_channels'] == [0, 1]
    assert len(analyze_session(session).frames) == 14
    assert session.metadata['backend_version'] == 'mock-1.0'
    backend.close()
    assert manager.closed
    assert backend.manager is None


def test_switch_backend_and_connect_existing(backend):
    backend.connect()
    first = backend.manager
    backend.connect('desktop', '/path/Logic')
    assert first.closed
    assert backend.manager.path == '/path/Logic'
    assert not backend.manager.headless
    backend.connect('connect')
    backend.close()


def test_capture_failure_releases_tab(backend, tmp_path):
    backend.connect()
    backend.manager.error = 'USB disconnected'
    with pytest.raises(RuntimeError, match='USB disconnected'):
        backend.capture(CaptureSettings(), DecodeSettings(), tmp_path)
    assert backend.manager.capture_closed


def test_no_hardware_and_multiple_devices(backend, tmp_path):
    backend.connect()
    backend.manager.devices_list = []
    with pytest.raises(RuntimeError, match='No Logic Pro 8'):
        backend.capture(CaptureSettings(), DecodeSettings(), tmp_path)
    device = SimpleNamespace(device_id='PRO8', device_type=8, is_simulation=False)
    backend.manager.devices_list = [device, SimpleNamespace(device_id='PRO8-2', device_type=8, is_simulation=False)]
    with pytest.raises(RuntimeError, match='Multiple'):
        backend.capture(CaptureSettings(), DecodeSettings(), tmp_path)


def test_old_library_headless_error(backend):
    class OldManager:
        @classmethod
        def launch(cls):
            pass
    backend.automation.Manager = OldManager
    with pytest.raises(RuntimeError, match='lacks headless support'):
        backend.connect()


def test_crashed_backend_is_restarted(backend):
    backend.connect()
    first = backend.manager
    def dead():
        raise RuntimeError('Server crashed')
    first.get_app_info = dead
    backend.connect()
    assert first.closed
    assert backend.manager is not first


def test_cleanup_error_does_not_hide_capture_error(backend, tmp_path):
    backend.connect()
    backend.manager.error = 'USB disconnected'
    def close_failed():
        raise RuntimeError('Close RPC failed')
    backend.manager.close_capture = close_failed
    with pytest.raises(RuntimeError, match='USB disconnected'):
        backend.capture(CaptureSettings(), DecodeSettings(), tmp_path)


def test_simulation_discovery_capture_and_metadata(backend, tmp_path):
    backend.connect('desktop')
    backend.manager.devices_list.extend([
        SimpleNamespace(device_id='DEMO8', device_type=8, is_simulation=True),
        SimpleNamespace(device_id='DEMO16', device_type=16, is_simulation=True),
    ])
    assert backend.devices() == ['PRO8']
    assert backend.devices(simulation=True) == ['DEMO8']
    assert backend.manager.device_query == {'include_simulation_devices': True}
    session = backend.capture(CaptureSettings(simulation=True), DecodeSettings(), tmp_path)
    assert backend.manager.configuration['device_id'] == 'DEMO8'
    assert session.capture.simulation
    assert session.metadata['is_simulation']
    assert session.metadata['source'] == 'Logic Pro 8 simulation'
    from saleae_differential.session import load_session
    assert load_session(tmp_path).capture.simulation


def test_missing_simulation_does_not_capture_physical_hardware(backend, tmp_path):
    backend.connect('desktop')
    with pytest.raises(RuntimeError, match='No simulated Pro 8'):
        backend.capture(CaptureSettings(simulation=True), DecodeSettings(), tmp_path)
    assert not hasattr(backend.manager, 'configuration')


def test_windows_host_and_export_path(backend, tmp_path, monkeypatch):
    backend.connect('connect', address='172.20.0.1', windows_backend=True)
    assert backend.manager.address == '172.20.0.1'
    translated = r'\\wsl.localhost\Ubuntu\tmp\capture'
    calls = []
    def translate(command, **kwargs):
        assert command == ['wslpath', '-w', str(tmp_path.resolve())]
        return SimpleNamespace(stdout=translated + '\n')
    monkeypatch.setattr('saleae_differential.backend.subprocess.run', translate)
    def export(directory, **kwargs):
        calls.append(directory)
        create_demo(tmp_path)
    backend.manager.export = export
    session = backend.capture(CaptureSettings(), DecodeSettings(), tmp_path)
    assert calls == [translated]
    assert session.metadata['backend_address'] == '172.20.0.1'
    assert session.metadata['windows_backend']
    assert len(analyze_session(session).frames) == 14


def test_changed_host_reconnects(backend):
    backend.connect('connect', address='172.20.0.1')
    first = backend.manager
    backend.connect('connect', address='172.21.0.1')
    assert first.closed
    assert backend.manager.address == '172.21.0.1'
