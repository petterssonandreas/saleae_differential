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
        return cls.launch()

    def __init__(self):
        self.closed = False
        self.capture_closed = False
        self.error = None
        self.devices_list = [SimpleNamespace(device_id='PRO8', device_type=8, is_simulation=False)]

    def close(self):
        self.closed = True

    def get_devices(self, **kwargs):
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
