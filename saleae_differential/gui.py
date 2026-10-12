"""Qt desktop interface. Qt binding is selected by pyqtgraph (PySide6 by default)."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path
import json
import sys
import tempfile
import traceback
import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets
from .backend import SaleaeBackend
from .demo import create_demo
from .display import configure_display
from .models import CaptureSettings, DecodeSettings, Session
from .session import analyze_session, export_csv, load_session, save_session


def prepare_session(session):
    """Prepare large plot buffers on the worker as well as the decoder output."""
    analysis = analyze_session(session)
    plot_data = {}
    for attribute in ("times", "positive", "negative", "difference", "common_mode", "logic"):
        arrays = []
        for chunk in analysis.chunks:
            values = np.asarray(getattr(chunk, attribute), dtype=float)
            if attribute == "logic":
                values = np.where(values < 0, np.nan, values)
            arrays.extend((values, np.array([np.nan])))
        plot_data[attribute] = np.concatenate(arrays)
    return session, analysis, plot_data


class TimeZoomViewBox(pg.ViewBox):
    """Wheel zoom affects time unless an axis explicitly requests vertical zoom."""

    def wheelEvent(self, event, axis=None):
        super().wheelEvent(event, axis=0 if axis is None else axis)


class Bridge(QtCore.QObject):
    completed = QtCore.Signal(str, object, object)
    progress = QtCore.Signal(str)


class FrameTable(QtCore.QAbstractTableModel):
    columns = ("Start (s)", "End (s)", "Hex", "Text", "Status")

    def __init__(self):
        super().__init__()
        self.frames = []

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.frames)

    def columnCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index, role=QtCore.Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != QtCore.Qt.ItemDataRole.DisplayRole:
            return None
        frame = self.frames[index.row()]
        return (f"{frame.start:.9f}", f"{frame.end:.9f}",
                f"0x{frame.value:02X}" if frame.value is not None else "—",
                frame.character, ", ".join(frame.errors) or "OK")[index.column()]

    def headerData(self, section, orientation, role=QtCore.Qt.ItemDataRole.DisplayRole):
        if role == QtCore.Qt.ItemDataRole.DisplayRole and orientation == QtCore.Qt.Orientation.Horizontal:
            return self.columns[section]
        return None

    def set_frames(self, frames):
        self.beginResetModel()
        self.frames = frames
        self.endResetModel()


class Window(QtWidgets.QMainWindow):
    def __init__(self, demo=False):
        super().__init__()
        self.setWindowTitle("Differential • Saleae Pro 8")
        self.resize(1280, 900)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="saleae")
        self.backend = SaleaeBackend()
        self.bridge = Bridge()
        self.bridge.completed.connect(self.finished)
        self.bridge.progress.connect(self.show_status)
        self.storage = tempfile.TemporaryDirectory(prefix="saleae-differential-")
        self.sequence = 0
        self.session = None
        self.analysis = None
        self.busy = False
        self.closing = False
        self.closed_backend = False
        self.settings = QtCore.QSettings("SaleaeDifferential", "Pro8Analyzer")
        self.build_ui()
        self.restore_settings()
        if demo:
            QtCore.QTimer.singleShot(0, self.demo)

    def build_ui(self):
        root = QtWidgets.QSplitter()
        self.setCentralWidget(root)
        controls = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(controls)
        form.setRowWrapPolicy(QtWidgets.QFormLayout.RowWrapPolicy.WrapLongRows)
        title = QtWidgets.QLabel("<h2>Differential analyzer</h2>Two analog inputs → UART bytes")
        form.addRow(title)
        self.mode = self.combo([("Headless", "headless"), ("Desktop launch", "desktop"), ("Connect to running backend", "connect")])
        self.path = QtWidgets.QLineEdit()
        self.path.setPlaceholderText("Logic executable / AppImage (desktop mode)")
        self.port = self.integer(1, 65535, 10430)
        self.device = QtWidgets.QComboBox()
        self.device.addItem("Automatic (one Pro 8)", "")
        self.positive = self.integer(0, 7, 0)
        self.negative = self.integer(0, 7, 1)
        self.rate = self.combo([(f"{r / 1e6:g} MS/s", r) for r in (625_000, 1_250_000, 3_125_000, 6_250_000, 12_500_000, 25_000_000, 50_000_000)])
        self.rate.setCurrentIndex(3)
        self.duration = self.decimal(0.000001, 60, 0.1, 6)
        self.lower = self.decimal(-100, 100, -0.2, 4)
        self.upper = self.decimal(-100, 100, 0.2, 4)
        self.invert = QtWidgets.QCheckBox("Invert reconstructed logic")
        self.baud = self.integer(1, 20_000_000, 115200)
        self.bits = self.integer(5, 9, 8)
        self.parity = self.combo([(p.title(), p) for p in ("none", "even", "odd")])
        self.stops = self.combo([(str(n), n) for n in (1.0, 1.5, 2.0)])
        self.common = QtWidgets.QCheckBox("Show common-mode voltage")
        self.common.toggled.connect(self.update_common)
        for label, widget in (("Backend", self.mode), ("Logic path", self.path), ("Automation port", self.port),
                              ("Pro 8 device", self.device), ("Positive channel", self.positive),
                              ("Negative channel", self.negative), ("Analog rate", self.rate),
                              ("Duration (s)", self.duration), ("Lower threshold (V)", self.lower),
                              ("Upper threshold (V)", self.upper), ("Polarity", self.invert),
                              ("Baud rate", self.baud), ("Data bits", self.bits),
                              ("Parity", self.parity), ("Stop bits", self.stops)):
            form.addRow(label, widget)
        form.addRow(self.common)
        self.inputs = [self.mode, self.path, self.port, self.device, self.positive, self.negative,
                       self.rate, self.duration, self.lower, self.upper, self.invert, self.baud,
                       self.bits, self.parity, self.stops]
        self.buttons = {}
        for text, callback in (("Refresh devices", self.refresh), ("Capture", self.capture),
                               ("Decode again", self.redecode), ("Load demo", self.demo),
                               ("Open session…", self.open_session), ("Save session…", self.save),
                               ("Export decoded CSV…", self.export)):
            button = QtWidgets.QPushButton(text)
            button.clicked.connect(callback)
            self.buttons[text] = button
            form.addRow(button)
        note = QtWidgets.QLabel("Thresholds model a receiver. A/B naming varies; use polarity inversion if idle decodes low.\n\nSamples are processed after each capture; this is not a continuous live stream.")
        note.setWordWrap(True)
        form.addRow(note)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(controls)
        root.addWidget(scroll)
        right = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        root.addWidget(right)
        plots = pg.GraphicsLayoutWidget()
        right.addWidget(plots)
        self.input_plot = plots.addPlot(row=0, col=0, title="Analog inputs", viewBox=TimeZoomViewBox())
        self.diff_plot = plots.addPlot(row=1, col=0, title="Difference · positive − negative", viewBox=TimeZoomViewBox())
        self.logic_plot = plots.addPlot(row=2, col=0, title="Reconstructed UART logic", viewBox=TimeZoomViewBox())
        for plot in (self.input_plot, self.diff_plot, self.logic_plot):
            plot.showGrid(x=True, y=True, alpha=0.2)
            plot.setLabel("bottom", "Time", units="s")
            plot.setClipToView(True)
            plot.setDownsampling(auto=True, mode="peak")
        self.input_plot.setLabel("left", "Voltage", units="V")
        self.diff_plot.setLabel("left", "Voltage", units="V")
        self.logic_plot.setLabel("left", "Logic")
        self.logic_plot.setYRange(-0.2, 1.2)
        self.input_plot.setXLink(self.diff_plot)
        self.logic_plot.setXLink(self.diff_plot)
        self.input_plot.addLegend()
        self.a_curve = self.input_plot.plot(pen=pg.mkPen("#64b5f6", width=1.5), name="Positive")
        self.b_curve = self.input_plot.plot(pen=pg.mkPen("#ffb74d", width=1.5), name="Negative")
        self.common_curve = self.input_plot.plot(pen=pg.mkPen("#b39ddb", width=1), name="Common mode")
        self.common_curve.hide()
        self.d_curve = self.diff_plot.plot(pen=pg.mkPen("#81c784", width=1.5))
        self.l_curve = self.logic_plot.plot(pen=pg.mkPen("#80cbc4", width=1.5), stepMode="left")
        self.threshold_lines = [pg.InfiniteLine(pos=v, angle=0, pen=pg.mkPen("#ef9a9a", style=QtCore.Qt.PenStyle.DashLine)) for v in (-0.2, 0.2)]
        for line in self.threshold_lines:
            self.diff_plot.addItem(line)
        self.selection = pg.LinearRegionItem(movable=False, brush=(100, 150, 220, 35))
        self.diff_plot.addItem(self.selection)
        self.selection.hide()
        self.table = QtWidgets.QTableView()
        self.table_model = FrameTable()
        self.table.setModel(self.table_model)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.selectionModel().currentRowChanged.connect(self.select_frame)
        right.addWidget(self.table)
        right.setSizes([600, 240])
        root.setSizes([330, 950])
        self.status = QtWidgets.QLabel("Ready. Load the demo or capture your Pro 8.")
        self.status.setWordWrap(True)
        self.statusBar().addWidget(self.status, 1)
        self.enable_controls()

    @staticmethod
    def combo(values):
        widget = QtWidgets.QComboBox()
        for label, value in values:
            widget.addItem(label, value)
        return widget

    @staticmethod
    def integer(low, high, value):
        widget = QtWidgets.QSpinBox()
        widget.setRange(low, high)
        widget.setValue(value)
        return widget

    @staticmethod
    def decimal(low, high, value, decimals):
        widget = QtWidgets.QDoubleSpinBox()
        widget.setDecimals(decimals)
        widget.setRange(low, high)
        widget.setValue(value)
        return widget

    def configuration(self):
        capture = CaptureSettings(self.positive.value(), self.negative.value(), int(self.rate.currentData()),
                                  self.duration.value(), self.device.currentData() or "")
        decode = DecodeSettings(self.lower.value(), self.upper.value(), self.invert.isChecked(),
                                self.baud.value(), self.bits.value(), self.parity.currentData(), self.stops.currentData())
        capture.validate()
        decode.validate()
        return capture, decode

    def apply_configuration(self, cfg, dec):
        self.positive.setValue(cfg.positive_channel)
        self.negative.setValue(cfg.negative_channel)
        rate_idx = self.rate.findData(cfg.sample_rate)
        if rate_idx < 0:
            self.rate.addItem(f"{cfg.sample_rate / 1e6:g} MS/s", cfg.sample_rate)
            rate_idx = self.rate.count() - 1
        self.rate.setCurrentIndex(rate_idx)
        self.duration.setValue(cfg.duration)
        device_idx = self.device.findData(cfg.device_id)
        if device_idx < 0:
            self.device.addItem(cfg.device_id, cfg.device_id)
            device_idx = self.device.count() - 1
        self.device.setCurrentIndex(device_idx)
        self.lower.setValue(dec.lower_threshold)
        self.upper.setValue(dec.upper_threshold)
        self.invert.setChecked(dec.invert)
        self.baud.setValue(dec.baud)
        self.bits.setValue(dec.data_bits)
        self.parity.setCurrentIndex(self.parity.findData(dec.parity))
        self.stops.setCurrentIndex(self.stops.findData(dec.stop_bits))

    def restore_settings(self):
        raw = self.settings.value("configuration", "")
        if not raw:
            return
        try:
            stored = json.loads(raw)
            cfg, dec = CaptureSettings(**stored["capture"]), DecodeSettings(**stored["decode"])
            cfg.validate()
            dec.validate()
            self.apply_configuration(cfg, dec)
            self.mode.setCurrentIndex(max(0, self.mode.findData(stored.get("mode", "headless"))))
            self.path.setText(stored.get("path", ""))
            self.port.setValue(stored.get("port", 10430))
            self.common.setChecked(stored.get("common_mode", False))
        except (TypeError, ValueError, KeyError):
            self.show_status("Saved settings were invalid; using defaults.")

    def persist_settings(self):
        cfg, dec = self.configuration()
        self.settings.setValue("configuration", json.dumps({"capture": asdict(cfg), "decode": asdict(dec),
                              "mode": self.mode.currentData(), "path": self.path.text(),
                              "port": self.port.value(), "common_mode": self.common.isChecked()}))

    def next_directory(self):
        self.sequence += 1
        directory = Path(self.storage.name) / str(self.sequence)
        directory.mkdir()
        return directory

    def run_job(self, name, callback):
        if self.busy:
            return
        self.busy = True
        self.enable_controls()
        self.show_status(f"{name.capitalize()}…")
        future = self.executor.submit(callback)
        def completed(f):
            try:
                result, error = f.result(), None
            except Exception as exc:
                traceback.print_exc()
                result, error = None, str(exc)
            self.bridge.completed.emit(name, result, error)
        future.add_done_callback(completed)

    def connect_options(self):
        return self.mode.currentData(), self.path.text().strip(), self.port.value()

    def refresh(self):
        options = self.connect_options()
        def job():
            self.backend.connect(*options)
            return self.backend.devices()
        self.run_job("devices", job)

    def capture(self):
        try:
            cfg, dec = self.configuration()
            self.persist_settings()
            options = self.connect_options()
            directory = self.next_directory()
        except ValueError as exc:
            self.error(str(exc))
            return
        def job():
            self.bridge.progress.emit("Starting acquisition backend…")
            self.backend.connect(*options)
            session = self.backend.capture(cfg, dec, directory, self.bridge.progress.emit)
            self.bridge.progress.emit("Reconstructing differential logic and decoding UART…")
            return prepare_session(session)
        self.run_job("capture", job)

    def redecode(self):
        if self.session is None:
            return
        try:
            _, dec = self.configuration()
            self.persist_settings()
            session = replace(self.session, decode=dec)
        except ValueError as exc:
            self.error(str(exc))
            return
        self.run_job("decode", lambda: prepare_session(session))

    def demo(self):
        directory = self.next_directory()
        def job():
            session = create_demo(directory)
            return prepare_session(session)
        self.run_job("demo", job)

    def open_session(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Open session folder")
        if folder:
            def job():
                session = load_session(folder)
                return prepare_session(session)
            self.run_job("open", job)

    def save(self):
        if self.session is None:
            return
        # A save dialog chooses a NEW directory path, not an existing directory.
        destination, _ = QtWidgets.QFileDialog.getSaveFileName(self, "New session folder name", "capture-session", "Session folder (*)")
        if destination:
            session = self.session
            self.run_job("save", lambda: save_session(session, destination))

    def export(self):
        if self.analysis is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export UART frames", "decoded.csv", "CSV (*.csv)")
        if path:
            frames = self.analysis.frames
            def job():
                export_csv(frames, path)
                return path
            self.run_job("export", job)

    def finished(self, name, result, error):
        self.busy = False
        if name == "close":
            self.closed_backend = True
            if error:
                self.show_status(f"Backend shutdown reported: {error}")
            self.close()
            return
        if error:
            if self.closing:
                self.show_status(error)
            else:
                self.error(error)
        elif name == "devices":
            previous = self.device.currentData()
            self.device.clear()
            self.device.addItem("Automatic (one Pro 8)", "")
            for device_id in result:
                self.device.addItem(device_id, device_id)
            self.device.setCurrentIndex(max(0, self.device.findData(previous)))
            self.show_status(f"Found {len(result)} Pro 8 device(s)." if result else "No Pro 8 found. Check USB and udev permissions.")
        elif name in ("capture", "demo", "open", "decode"):
            self.session, self.analysis, self.plot_data = result
            if name != "decode":
                self.apply_configuration(self.session.capture, self.session.decode)
            self.render()
        else:
            self.show_status(f"Saved: {result}")
        self.enable_controls()
        if self.closing:
            self.begin_shutdown()

    def render(self):
        chunks = self.analysis.chunks
        times = self.plot_data["times"]
        for curve, attr in ((self.a_curve, "positive"), (self.b_curve, "negative"),
                            (self.common_curve, "common_mode"), (self.d_curve, "difference"), (self.l_curve, "logic")):
            curve.setData(times, self.plot_data[attr], connect="finite")
        self.update_common()
        self.threshold_lines[0].setValue(self.session.decode.lower_threshold)
        self.threshold_lines[1].setValue(self.session.decode.upper_threshold)
        self.selection.hide()
        self.diff_plot.setXRange(chunks[0].times[0], chunks[-1].times[-1], padding=0.02)
        self.table_model.set_frames(self.analysis.frames)
        errors = sum(bool(f.errors) for f in self.analysis.frames)
        versions = self.session.metadata
        backend_info = f" · backend {versions.get('backend_version')} / API {versions.get('automation_library')}" if "backend_version" in versions else ""
        status = f"{len(self.analysis.frames)} UART frames · {errors} with errors{backend_info}"
        if self.analysis.warnings:
            status += " · " + " ".join(self.analysis.warnings)
        self.show_status(status)

    def update_common(self):
        self.common_curve.setVisible(self.common.isChecked())

    def select_frame(self, current, previous):
        if not current.isValid() or self.analysis is None:
            return
        f = self.analysis.frames[current.row()]
        self.selection.setRegion((f.start, f.end))
        self.selection.show()
        width = max(f.end - f.start, 1 / self.session.decode.baud)
        self.diff_plot.setXRange(f.start - width * 0.25, f.end + width * 0.25, padding=0)

    def enable_controls(self):
        enabled = not self.busy and not self.closing
        for widget in self.inputs:
            widget.setEnabled(enabled)
        for name, button in self.buttons.items():
            button.setEnabled(enabled and (self.session is not None or name not in ("Decode again", "Save session…", "Export decoded CSV…")))

    def show_status(self, text):
        self.status.setText(text)

    def error(self, text):
        self.show_status(text)
        QtWidgets.QMessageBox.warning(self, "Differential analyzer", text)

    def begin_shutdown(self):
        if not self.busy:
            self.run_job("close", self.backend.close)

    def closeEvent(self, event):
        if self.closed_backend:
            self.executor.shutdown(wait=False)
            self.storage.cleanup()
            event.accept()
            return
        try:
            self.persist_settings()
        except ValueError:
            pass
        self.closing = True
        self.enable_controls()
        self.show_status("Closing after the current operation finishes…")
        event.ignore()
        self.begin_shutdown()


def main():
    configure_display()
    pg.setConfigOptions(background="#17212b", foreground="#dce5ed", antialias=False)
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Saleae Differential")
    window = Window(demo="--demo" in sys.argv)
    window.show()
    sys.exit(app.exec())
