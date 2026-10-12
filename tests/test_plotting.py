import numpy as np
from saleae_differential.models import DifferentialChunk
from saleae_differential.plotting import MAX_PLOT_POINTS, PlotData, PlotSeries


def test_large_overview_preserves_spikes_and_zoom_restores_raw_samples():
    times = np.arange(200_000) / 1e6
    values = np.zeros(200_000)
    values[12345] = 10
    values[54321] = -9
    series = PlotSeries.build(times, values)
    x, y = series.viewport(0, times[-1], MAX_PLOT_POINTS)
    assert len(x) <= MAX_PLOT_POINTS
    assert np.max(y) == 10
    assert np.min(y) == -9
    assert x[np.argmax(y)] == times[12345]
    assert np.all(np.diff(x) >= 0)
    x, y = series.viewport(times[12340], times[12350], MAX_PLOT_POINTS)
    np.testing.assert_array_equal(y, values[12339:12352])


def test_multichunk_budget_and_gap_separators():
    chunks = []
    for i in range(30):
        times = i + np.arange(10_000) / 1e6
        values = np.sin(np.arange(10_000))
        chunks.append(DifferentialChunk(times, values, values, 1e-6, values, values,
                                        (values > 0).astype(np.int8), np.zeros(10_000, dtype=bool)))
    data = PlotData(chunks)
    x, y = data.viewport('positive', 0, 30)
    assert len(x) <= MAX_PLOT_POINTS
    assert np.isnan(x).sum() == 30
    np.testing.assert_array_equal(np.isnan(x), np.isnan(y))


def test_unknown_logic_is_displayed_as_missing():
    times = np.arange(4, dtype=float)
    values = np.zeros(4)
    chunk = DifferentialChunk(times, values, values, 1, values, values,
                              np.array([-1, -1, 0, 1]), np.zeros(4, dtype=bool))
    x, y = PlotData([chunk]).viewport('logic', 0, 3)
    assert np.isnan(y[:2]).all()
    np.testing.assert_array_equal(y[2:4], [0, 1])
