import numpy as np
from .models import AnalogChunk, Analysis, DecodeSettings, DifferentialChunk, UartFrame


def aligned_chunks(positive: list[AnalogChunk], negative: list[AnalogChunk]):
    """Use the slower grid within each overlap; never extrapolate across gaps."""
    aligned = []
    i = j = 0
    while i < len(positive) and j < len(negative):
        a, b = positive[i], negative[j]
        lo = max(a.start, b.start)
        hi = min(a.end - a.period, b.end - b.period)
        period = max(a.period, b.period)
        if hi >= lo:
            count = int(np.floor((hi - lo) / period + 1e-6)) + 1
            times = lo + np.arange(count) * period
            av = np.interp(times, a.times, a.samples)
            bv = np.interp(times, b.times, b.samples)
            if (aligned and np.isclose(aligned[-1][3], period, rtol=1e-8, atol=0)
                    and abs(times[0] - (aligned[-1][0][-1] + period)) < period * 1e-5):
                old = aligned[-1]
                aligned[-1] = (np.concatenate((old[0], times)), np.concatenate((old[1], av)),
                               np.concatenate((old[2], bv)), period)
            else:
                aligned.append((times, av, bv, period))
        if a.end <= b.end:
            i += 1
        else:
            j += 1
    if not aligned:
        raise ValueError("The two channels have no overlapping samples.")
    return aligned


def reconstruct(difference, settings):
    decisive = (difference >= settings.upper_threshold) | (difference <= settings.lower_threshold)
    last = np.maximum.accumulate(np.where(decisive, np.arange(len(difference)), -1))
    logic = np.full(len(difference), -1, dtype=np.int8)
    known = last >= 0
    logic[known] = (difference[last[known]] >= settings.upper_threshold).astype(np.int8)
    if settings.invert:
        logic[known] = 1 - logic[known]
    return logic, ~decisive


def decode_uart(chunk: DifferentialChunk, settings: DecodeSettings) -> list[UartFrame]:
    t, logic = chunk.times, chunk.logic
    if len(t) < 2:
        return []
    bit = 1 / settings.baud
    parity_bits = int(settings.parity != "none")
    stop_start = 1 + settings.data_bits + parity_bits
    frame_bits = stop_start + settings.stop_bits
    end_of_data = t[-1] + chunk.period
    edges = np.flatnonzero((logic[:-1] == 1) & (logic[1:] == 0)) + 1
    rising_edges = np.flatnonzero((logic[:-1] == 0) & (logic[1:] != 0)) + 1
    frames = []
    resume = -np.inf

    def index_at(time):
        return min(len(t) - 1, int(np.searchsorted(t, time, side="right") - 1))

    for edge in edges:
        start = t[edge]
        if start < resume:
            continue
        # Reject a short low glitch before treating it as a start bit.
        middle = start + 0.5 * bit
        if middle >= end_of_data:
            frames.append(UartFrame(start, end_of_data, None, ("incomplete",)))
            break
        if logic[index_at(middle)] != 0:
            continue
        # A continuous low lasting a whole frame is a break, not a stream of zero bytes.
        rising_index = np.searchsorted(rising_edges, edge, side="right")
        low_end = t[rising_edges[rising_index]] if rising_index < len(rising_edges) else end_of_data
        if low_end - start >= frame_bits * bit:
            frames.append(UartFrame(start, low_end, None, ("break",)))
            resume = low_end
            continue
        end = start + frame_bits * bit
        if end > end_of_data + chunk.period * 1e-5:
            frames.append(UartFrame(start, end_of_data, None, ("incomplete",)))
            break
        errors = []
        centers = [start + (1.5 + k) * bit for k in range(settings.data_bits)]
        data_indices = [index_at(time) for time in centers]
        bits = [int(logic[k]) for k in data_indices]
        value = sum(v << k for k, v in enumerate(bits)) if all(v >= 0 for v in bits) else None
        checked = [index_at(middle), *data_indices]
        if parity_bits:
            pidx = index_at(start + (1.5 + settings.data_bits) * bit)
            checked.append(pidx)
            expected = sum(bits) % 2
            if settings.parity == "odd":
                expected = 1 - expected
            if logic[pidx] != expected:
                errors.append("parity")
        stop_offsets = [0.5]
        if settings.stop_bits == 1.5:
            stop_offsets.append(1.25)
        elif settings.stop_bits == 2:
            stop_offsets.append(1.5)
        for offset in stop_offsets:
            idx = index_at(start + (stop_start + offset) * bit)
            checked.append(idx)
            if logic[idx] != 1:
                errors.append("framing")
        if any(logic[k] < 0 or chunk.uncertain[k] for k in checked):
            errors.append("uncertain")
        frames.append(UartFrame(start, end, value, tuple(dict.fromkeys(errors))))
        # Allow for start edge quantization on consecutive frames.
        resume = end - chunk.period * 1.01
    return frames


def analyze(positive, negative, settings: DecodeSettings) -> Analysis:
    settings.validate()
    chunks, frames, warnings = [], [], []
    for times, av, bv, period in aligned_chunks(positive, negative):
        difference = av - bv
        logic, uncertain = reconstruct(difference, settings)
        c = DifferentialChunk(times, av, bv, period, difference, (av + bv) / 2, logic, uncertain)
        chunks.append(c)
        frames.extend(decode_uart(c, settings))
        if 1 / period / settings.baud < 16:
            warnings.append("Fewer than 16 analog samples per bit; decode timing may be unreliable.")
    if len(chunks) > 1:
        warnings.append("Capture gaps are present; decoding restarts after each gap.")
    if any(np.any(c.logic < 0) for c in chunks):
        warnings.append("Some initial samples are unresolved between the switching thresholds.")
    return Analysis(chunks, frames, list(dict.fromkeys(warnings)))
