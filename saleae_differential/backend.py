"""All backend calls are serialized by the GUI worker, never made on its UI thread."""
from dataclasses import asdict
from importlib import metadata
from pathlib import Path
import inspect
from datetime import datetime, timezone
from .models import Session
from .session import write_metadata


class SaleaeBackend:
    def __init__(self, automation=None):
        self.automation = automation
        self.manager = None
        self.connection = None

    def connect(self, mode="headless", application_path="", port=10430):
        options = (mode, application_path, port)
        if self.manager is not None and self.connection == options:
            try:
                self.manager.get_app_info()
                return
            except Exception:
                # A dead server must not keep subsequent captures tied to a stale connection.
                try:
                    self.close()
                except Exception:
                    pass
        self.close()
        if self.automation is None:
            try:
                from saleae import automation
                self.automation = automation
            except ImportError as exc:
                raise RuntimeError("Install Saleae's logic2-automation package; see README for the headless preview wheel.") from exc
        api = self.automation
        try:
            if mode == "headless":
                if "headless" not in inspect.signature(api.Manager.launch).parameters:
                    raise RuntimeError("This Automation library lacks headless support. Install the 1.1.0 preview wheel or select Desktop launch.")
                self.manager = api.Manager.launch(headless=True, port=port, connect_timeout_seconds=20)
            elif mode == "desktop":
                self.manager = api.Manager.launch(application_path=application_path or None,
                                                  port=port, connect_timeout_seconds=20)
            elif mode == "connect":
                self.manager = api.Manager.connect(port=port, connect_timeout_seconds=5)
            else:
                raise ValueError("Unknown backend mode.")
            self.connection = options
        except Exception as exc:
            raise RuntimeError(f"Cannot start/connect to the {mode} backend: {exc}\nCheck the installation, USB permissions, and backend settings.") from exc

    def devices(self):
        if self.manager is None:
            raise RuntimeError("Backend is not connected.")
        api = self.automation
        return [d.device_id for d in self.manager.get_devices(include_simulation_devices=False)
                if d.device_type == api.DeviceType.LOGIC_PRO_8 and not d.is_simulation]

    def capture(self, cfg, dec, directory, progress=lambda message: None):
        cfg.validate()
        dec.validate()
        devices = self.devices()
        if not devices:
            raise RuntimeError("No Logic Pro 8 is connected. Check USB connection and udev rules.")
        device_id = cfg.device_id
        if not device_id:
            if len(devices) != 1:
                raise RuntimeError("Multiple Pro 8 devices are connected. Refresh devices and select one.")
            device_id = devices[0]
        if device_id not in devices:
            raise RuntimeError("The selected Pro 8 is no longer connected. Refresh devices.")
        api = self.automation
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        config = api.LogicDeviceConfiguration(enabled_analog_channels=[cfg.positive_channel, cfg.negative_channel],
                                              enabled_digital_channels=[], analog_sample_rate=cfg.sample_rate)
        capture_config = api.CaptureConfiguration(capture_mode=api.TimedCaptureMode(duration_seconds=cfg.duration))
        progress("Capturing analog channels…")
        capture = self.manager.start_capture(device_id=device_id, device_configuration=config,
                                             capture_configuration=capture_config)
        capture_error = None
        try:
            capture.wait()
            progress("Exporting full-resolution samples…")
            capture.export_raw_data_binary(directory=str(directory),
                                           analog_channels=[cfg.positive_channel, cfg.negative_channel],
                                           digital_channels=[], analog_downsample_ratio=1)
        except BaseException as exc:
            capture_error = exc
            raise
        finally:
            try:
                capture.close()
            except Exception:
                if capture_error is None:
                    raise
        info = self.manager.get_app_info()
        try:
            library_version = metadata.version("logic2-automation")
        except metadata.PackageNotFoundError:
            library_version = "unknown"
        details = {"source": "Saleae Logic Pro 8", "device_id": device_id, "backend_mode": self.connection[0],
                   "backend_version": info.app_version, "automation_library": library_version,
                   "captured_at_utc": datetime.now(timezone.utc).isoformat(), "requested_capture": asdict(cfg)}
        session = Session(directory, cfg, dec, details)
        write_metadata(session)
        return session

    def close(self):
        manager, self.manager = self.manager, None
        self.connection = None
        if manager is not None:
            manager.close()
