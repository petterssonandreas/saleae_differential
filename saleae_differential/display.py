"""Resolve a stale WSLg Wayland socket path before Qt initializes its platform."""
import os
from pathlib import Path


def configure_display():
    # An explicit Qt platform is a user choice (including offscreen for checks).
    if os.environ.get("QT_QPA_PLATFORM"):
        return
    display = os.environ.get("WAYLAND_DISPLAY")
    if not display or Path(display).is_absolute():
        return
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", "/run/user/unknown"))
    if (runtime / display).is_socket():
        return
    # WSLg exposes the actual socket here even when shell/runtime setup points
    # XDG_RUNTIME_DIR elsewhere. Absolute WAYLAND_DISPLAY paths are supported
    # by libwayland; changing only this process avoids altering shell settings.
    wslg_socket = Path("/mnt/wslg/runtime-dir") / display
    if wslg_socket.is_socket():
        os.environ["WAYLAND_DISPLAY"] = str(wslg_socket)
