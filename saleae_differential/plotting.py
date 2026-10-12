"""Peak-preserving display levels built on the worker, bounded viewport buffers."""
from dataclasses import dataclass, field
import numpy as np

ATTRIBUTES = ('positive', 'negative', 'difference', 'common_mode', 'logic')
MAX_PLOT_POINTS = 20_000


@dataclass
class PlotSeries:
    times: np.ndarray
    values: np.ndarray
    levels: dict = field(default_factory=dict)
    minimum: float = 0
    maximum: float = 0

    @classmethod
    def build(cls, times, values):
        series = cls(times, values, minimum=float(np.min(values)), maximum=float(np.max(values)))
        block = 32
        count = len(values)
        while block < count:
            groups, remainder = divmod(count, block)
            windows = values[:groups * block].reshape(groups, block)
            base = np.arange(groups) * block
            minima = base + np.argmin(windows, axis=1)
            maxima = base + np.argmax(windows, axis=1)
            # Preserve chronological order; drawing min then max unconditionally
            # would distort descending edges and spike timing.
            indices = np.column_stack((np.minimum(minima, maxima), np.maximum(minima, maxima))).ravel()
            if remainder:
                tail = values[groups * block:]
                extra = groups * block + np.sort([np.argmin(tail), np.argmax(tail)])
                indices = np.concatenate((indices, extra))
            series.levels[block] = indices
            block *= 4
        return series

    def viewport(self, low, high, budget):
        start = max(0, int(np.searchsorted(self.times, low, side='left')) - 1)
        end = min(len(self.times), int(np.searchsorted(self.times, high, side='right')) + 1)
        if end <= start:
            return self.times[:0], self.values[:0]
        length = end - start
        if length <= budget:
            return self.times[start:end], self.values[start:end]
        indices = None
        for block, level in self.levels.items():
            if 2 * (length / block + 3) <= budget:
                indices = level
                break
        if indices is None:
            # Use the coarsest cached level for exceptionally small per-chunk
            # budgets, preserving its visible extrema below if still necessary.
            indices = next(reversed(self.levels.values())) if self.levels else np.arange(start, end)
        left = max(0, int(np.searchsorted(indices, start)) - 1)
        right = min(len(indices), int(np.searchsorted(indices, end)) + 1)
        selected = indices[left:right]
        if len(selected) > budget:
            candidates = self.values[selected]
            keep = np.unique([0, len(selected) - 1, np.argmin(candidates), np.argmax(candidates)])
            selected = selected[keep]
        return self.times[selected], self.values[selected]


class PlotData:
    def __init__(self, chunks):
        self.series = {attr: [PlotSeries.build(c.times, getattr(c, attr)) for c in chunks] for attr in ATTRIBUTES}

    def bounds(self, attributes):
        selected = [s for attr in attributes for s in self.series[attr]]
        return min(s.minimum for s in selected), max(s.maximum for s in selected)

    def viewport(self, attribute, low, high):
        active = [s for s in self.series[attribute] if s.times[-1] >= low and s.times[0] <= high]
        if not active:
            return np.array([]), np.array([])
        budget = max(4, MAX_PLOT_POINTS // len(active) - 1)
        xx, yy = [], []
        for series in active:
            x, y = series.viewport(low, high, budget)
            if attribute == 'logic':
                y = np.where(y < 0, np.nan, y)
            xx.extend((x, np.array([np.nan])))
            yy.extend((y, np.array([np.nan])))
        return np.concatenate(xx), np.concatenate(yy)
