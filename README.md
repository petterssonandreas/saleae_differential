# Saleae differential analyzer

A desktop application for capturing two analog channels from a **Saleae Logic Pro 8**, calculating their difference, reconstructing logic states, and decoding UART bytes. Capture, export, and analysis run automatically from this application's Capture button.

The viewer shows the two inputs, their difference, optional common-mode voltage, reconstructed logic, and a timestamped UART table. Select a frame to zoom to its waveform interval. Change receiver or UART settings and click **Decode again** to reuse the original recording.

Scroll over a waveform to zoom time horizontally across the linked plots. Scroll over a plot's left axis (voltage labels) to zoom only that plot vertically.

## Install and run

Use Python 3.12 or newer on Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m saleae_differential --demo
```

The demo requires no hardware or Saleae software. It generates `Hello RS485!` followed by CR/LF with noise and a changing common-mode voltage. It uses the same binary parser, decoder, plots, and table as physical captures.

Normal startup:

```bash
python -m saleae_differential
# or, after installing the project:
saleae-differential
```

PySide6 is the default Qt dependency. The interface also works with PyQt6 via pyqtgraph's Qt compatibility layer, which was used for local validation. For an existing environment with PyQt6, running the module from this repository does not require installing PySide6.

### WSLg display startup

The application automatically resolves the WSLg Wayland socket when `WAYLAND_DISPLAY` is set but `XDG_RUNTIME_DIR` points to a directory without that socket. This changes only the application's environment. An explicitly configured `QT_QPA_PLATFORM` is respected.

If you use X11 instead of Wayland and Qt reports a missing `libxcb-cursor.so.0`, install its Ubuntu system dependency and select X11:

```bash
sudo apt install libxcb-cursor0
QT_QPA_PLATFORM=xcb python -m saleae_differential --demo
```

## Acquisition backends

### Headless (preferred)

Saleae released a headless Automation API preview supporting the Pro 8. It includes the capture server in its platform-specific Python wheel. This application starts it with `Manager.launch(headless=True)` and keeps it alive between captures.

On Linux x64, install the pinned preview:

```bash
python -m pip install -r requirements-headless-linux.txt
```

Other platforms must install their matching preview wheel from [Saleae's release announcement](https://discuss.saleae.com/t/headless-logic2-automation-support/3793). The pinned Linux wheel is **logic2-automation 1.1.0**, published August 8, 2026. This is a preview backend, and physical capture compatibility has not yet been validated here.

Install Saleae's USB drivers or Linux udev rules first, using Logic 2's normal installation instructions. This is a one-time system setup; subsequent captures require no interaction with Saleae's interface. Do not run this application as root to bypass missing USB permissions.

### Desktop launch

If the preview is unavailable or fails, select **Desktop launch** explicitly. Install the stable Automation library:

```bash
python -m pip install -e '.[automation]'
```

Set **Logic path** to your installed executable or executable AppImage. If left blank, Saleae's library searches for an installed application. This mode opens Logic 2 automatically and enables its Automation interface during launch. Its window may be visible, but capturing and exporting require no manual actions. The application shuts down the backend it launched when you exit.

### Connect to running backend

Select **Connect to running backend** to use an existing local Automation server (default port 10430). For a manually started Logic 2 GUI, enable its Automation server once in preferences. An externally started backend remains running when this application exits.

No automatic fallback occurs: a startup failure stays visible so you can choose another backend deliberately. **Refresh devices** starts/connects the selected backend and lists only physical Pro 8 devices. Capture automatically selects a sole Pro 8; multiple devices require an explicit selection.

## Capture and decoder controls

Defaults: channels **0 − 1**, **6.25 MS/s**, **100 ms**, **115200 baud**, **8N1**, switching thresholds **−0.2 V / +0.2 V**.

1. Connect both probe inputs to the two RS485 wires, referenced to an appropriate bus/system ground. Never attach a ground lead to one of the bus signal wires. Observe your Pro 8's input measurement range and grounding requirements; subtraction cannot recover clipped input samples. See [Saleae's differential signal guidance](https://www.saleae.com/support/protocol-analyzers/analyzer-user-guides/decode-differential-and-high-voltage-data).
2. Select the channels and the bus's actual baud rate and framing.
3. Click **Capture**. Startup, acquisition, export, and decode progress appear in the status area.
4. If polarity is reversed, enable **Invert reconstructed logic** and click **Decode again**. A/B labels vary among devices.

The listed sample rates are candidate Pro 8 rates, not a capability guarantee for every configuration. If the backend rejects a rate, its error is displayed; choose a supported rate. Fewer than 16 measured samples per bit produces a warning. Limit captures to 20 million samples per channel to bound processing and display memory use; the default capture is much smaller.

The threshold model switches high at the upper threshold and low at the lower threshold, holding the previous state between them. Samples before the first decisive level remain unknown. A UART bit sampled inside the threshold band is flagged **uncertain**, even if its reconstructed state is held. These editable thresholds model a receiver; they are not a precise reproduction of every RS485 transceiver's hysteresis or failsafe circuit. See [TI's explanation](https://www.ti.com/video/6107769508001).

UART decoding supports 5–9 data bits, none/even/odd parity, and 1/1.5/2 stop bits. Table statuses include parity, framing, uncertain, incomplete, and break. This is UART byte decoding; it does not identify transmitting nodes or parse Modbus messages. A capture beginning in the middle of a frame cannot reliably recover that first frame.

## Files and playback

**Save session** asks for a new folder name. Existing folders are never overwritten. Sessions contain:

- `session.json`: capture settings, decoder settings, device/backend versions, and source metadata.
- `analog_<positive>.bin` and `analog_<negative>.bin`: untouched original analog exports.

**Open session** selects one of these folders and decodes it without hardware or a backend. **Export decoded CSV** saves start/end timestamps, integer and hex values, printable characters, and error statuses. A 9-bit value is preserved as an integer rather than truncated to a byte.

Capture files remain in temporary storage until you exit. Save any recording you want to retain. Saved settings persist through Qt's standard per-user settings store. Changing decoder settings does not modify an opened recording; save to a new folder to retain the revised settings.

The parser supports [documented analog binary export versions 0 and 1](https://www.saleae.com/support/logic-software/saving-and-exporting-data/binary-and-csv-export-formats-2025-update), including downsampling metadata and separate continuous waveforms. Alignment uses the slower input grid within the overlap and interpolation only inside each continuous interval. Gaps restart the decoder. The viewer uses peak-preserving display downsampling; the decoder always uses the full aligned signal.

Captures run on demand. Export and processing happen after acquisition; this is not gapless live acquisition. Closing the application waits for any active operation before releasing its backend.

## Checks

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

Tests cover binary formats and malformed exports, channel alignment, gaps, hysteresis, polarity, UART framing and errors, saved sessions, CSV, mocked acquisition lifecycle, and the desktop demo. GUI tests need a Qt binding; the signal-processing and storage tests run without one.

Local validation: **79 checks passed**, using Python 3.12.3, NumPy 2.5.2, PyQt6 6.10.0, pyqtgraph 0.13.7, and pytest 9.1.1. Package downloads were unavailable; cached packages provided these dependencies. The Saleae Automation library was mocked because no backend or connected Pro 8 was available to validate here.

Before relying on physical captures, confirm:

- The headless backend starts and identifies your Pro 8 without opening Logic 2.
- A known UART transmission decodes correctly with both input waveforms visible.
- Polarity inversion, repeated on-demand captures, and session save/reopen work.
- Desktop launch works as the alternate backend and shuts down cleanly.
- A disconnect reports an error while keeping the previous recording visible.

## Code layout

- `backend.py`: acquisition and backend ownership; calls are serialized on a worker thread.
- `binary.py`, `processing.py`, `models.py`: binary exports, signal reconstruction, UART decoding, data models.
- `session.py`, `demo.py`: portable sessions, CSV, reproducible synthetic captures.
- `gui.py`: desktop controls, linked interactive plots, frame table, and asynchronous jobs.
