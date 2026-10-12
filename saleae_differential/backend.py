"""All backend calls are serialized by the GUI worker, never made on its UI thread."""
from dataclasses import asdict
from importlib import metadata
from pathlib import Path
import inspect
import subprocess
from datetime import datetime, timezone
from .models import Session
from .session import write_metadata


class SaleaeBackend:
    def __init__(self, automation=None):
        self.automation = automation
        self.manager = None
        self.connection = None

    def connect(self, mode="headless", application_path="", port=10430, address="127.0.0.1", windows_backend=False):
        address = address.strip() or "127.0.0.1"
        options = (mode, application_path, port, address, windows_backend and mode == "connect")
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
                self.manager = api.Manager.connect(address=address, port=port, connect_timeout_seconds=5)
            else:
                raise ValueError("Unknown backend mode.")
            self.connection = options
        except Exception as exc:
            hint = (f"Enable Logic's Automation server and check host {address}, port {port}. "
                    "For Logic on Windows from WSL, use the Windows host IP unless mirrored networking is enabled."
                    if mode == "connect" else "Check the installation and backend settings.")
            raise RuntimeError(f"Cannot start/connect to the {mode} backend: {exc}\n{hint}") from exc

    def export_directory(self, directory):
        if not self.connection[4]:
            return str(directory)
        try:
            result = subprocess.run(["wslpath", "-w", str(directory)], check=True,
                                    capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("Cannot translate the capture folder for Windows. Windows backend (WSL) requires wslpath.") from exc
        path = result.stdout.strip()
        if not path:
            raise RuntimeError("wslpath returned an empty Windows export path.")
        return path

    def device_details(self, simulation=False):
        if self.manager is None:
            raise RuntimeError("Backend is not connected.")
        api = self.automation
        return [d for d in self.manager.get_devices(include_simulation_devices=simulation)
                if d.device_type == api.DeviceType.LOGIC_PRO_8 and d.is_simulation == simulation]

    def devices(self, simulation=False):
        return [d.device_id for d in self.device_details(simulation)]

    def capture(self, cfg, dec, directory, progress=lambda message: None):
        cfg.validate()
        dec.validate()
        devices = self.devices(cfg.simulation)
        if not devices:
            if cfg.simulation:
                raise RuntimeError("No simulated Pro 8 is available. Select Desktop launch or connect to Logic 2, then refresh devices.")
            raise RuntimeError("No Logic Pro 8 is connected. Check USB connection and udev rules.")
        device_id = cfg.device_id
        if not device_id:
            if len(devices) != 1:
                raise RuntimeError("Multiple Pro 8 devices are available. Refresh devices and select one.")
            device_id = devices[0]
        if device_id not in devices:
            raise RuntimeError("The selected Pro 8 is no longer available. Refresh devices.")
        api = self.automation
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        export_directory = self.export_directory(directory)
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
            capture.export_raw_data_binary(directory=export_directory,
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
        details = {"source": "Logic Pro 8 simulation" if cfg.simulation else "Saleae Logic Pro 8",
                   "is_simulation": cfg.simulation, "device_id": device_id, "backend_mode": self.connection[0],
                   "backend_address": self.connection[3], "windows_backend": self.connection[4],
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
