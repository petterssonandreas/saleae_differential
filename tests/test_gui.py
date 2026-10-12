import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import time
import pytest
pg = pytest.importorskip('pyqtgraph')
from pyqtgraph.Qt import QtCore, QtWidgets
from saleae_differential.gui import Window
from saleae_differential.session import load_session, analyze_session


@pytest.fixture
def window(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    # Keep test preferences away from the user's actual application preferences.
    QtCore.QSettings.setPath(QtCore.QSettings.Format.IniFormat, QtCore.QSettings.Scope.UserScope, str(tmp_path))
    QtCore.QSettings.setDefaultFormat(QtCore.QSettings.Format.IniFormat)
    w = Window()
    yield w, app
    w.close()
    wait_idle(w, app, closed=True)


def wait_idle(w, app, closed=False):
    limit = time.monotonic() + 5
    while time.monotonic() < limit:
        app.processEvents()
        if (w.closed_backend if closed else not w.busy):
            return
        time.sleep(0.005)
    pytest.fail('GUI job did not complete')


def test_demo_table_plot_and_selection(window):
    w, app = window
    w.demo()
    wait_idle(w, app)
    assert len(w.analysis.frames) == 14
    assert w.table_model.rowCount() == 14
    assert w.a_curve.xData is not None
    assert w.l_curve.yData is not None
    assert '14 UART frames' in w.status.text()
    w.common.setChecked(True)
    assert w.common_curve.isVisible()
    w.select_frame(w.table_model.index(0, 0), QtCore.QModelIndex())
    assert w.selection.isVisible()


def test_redecode_keeps_raw_data_and_changes_result(window):
    w, app = window
    w.demo()
    wait_idle(w, app)
    directory = w.session.directory
    source = (directory / 'analog_0.bin').read_bytes()
    w.invert.setChecked(True)
    w.redecode()
    wait_idle(w, app)
    assert w.session.decode.invert
    assert (directory / 'analog_0.bin').read_bytes() == source
    assert [f.value for f in w.analysis.frames] != list(b'Hello RS485!\r\n')


def test_failure_preserves_previous_result(window):
    w, app = window
    w.demo()
    wait_idle(w, app)
    previous = w.analysis
    errors = []
    w.error = errors.append
    def fail():
        raise RuntimeError('USB failure')
    w.run_job('capture', fail)
    wait_idle(w, app)
    assert w.analysis is previous
    assert errors == ['USB failure']
    assert w.buttons['Capture'].isEnabled()


def test_close_when_backend_shutdown_fails(window):
    w, app = window
    def fail():
        raise RuntimeError('Already stopped')
    w.backend.close = fail
    w.close()
    wait_idle(w, app, closed=True)
    assert w.closed_backend


def test_simulation_controls_and_device_labels(window):
    w, app = window
    w.device.addItem('Physical', 'PRO8')
    w.device.setCurrentIndex(1)
    w.simulation.setChecked(True)
    assert w.device.currentData() == ''
    assert w.configuration()[0].simulation
    w.finished('devices', ['DEMO8'], None)
    assert 'Logic simulation' in w.device.itemText(1)
    w.device.setCurrentIndex(1)
    w.simulation.setChecked(False)
    assert w.device.currentData() == ''
    assert not w.configuration()[0].simulation
