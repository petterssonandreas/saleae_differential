"""Deterministic analog UART waveform generator; no Saleae hardware required."""
import numpy as np
from .binary import write_analog
from .models import AnalogChunk, CaptureSettings, DecodeSettings, Session
from .session import write_metadata


def synthesize(values, settings=DecodeSettings(), rate=6_250_000, start=0.0,
               idle_bits=3, invert=False, corrupt_parity=False, corrupt_stop=False):
    runs = [(1, float(idle_bits))]
    for value in values:
        bits = [(value >> k) & 1 for k in range(settings.data_bits)]
        runs.extend([(0, 1), *((b, 1) for b in bits)])
        if settings.parity != "none":
            parity = sum(bits) % 2 ^ int(settings.parity == "odd") ^ int(corrupt_parity)
            runs.append((parity, 1))
        runs.append((0 if corrupt_stop else 1, settings.stop_bits))
    runs.append((1, float(idle_bits)))
    boundaries = np.cumsum([duration for _, duration in runs]) / settings.baud
    count = int(np.ceil(boundaries[-1] * rate))
    times = np.arange(count) / rate
    idx = np.minimum(np.searchsorted(boundaries, times, side="right"), len(runs) - 1)
    logic = np.array([level for level, _ in runs])[idx]
    differential = np.where(logic ^ int(invert), 1.5, -1.5)
    common = 2.5 + 0.25 * np.sin(2 * np.pi * 1000 * times)
    noise = np.random.default_rng(42).normal(0, 0.01, count)
    return (AnalogChunk(start, 1 / rate, (common + differential / 2 + noise).astype(np.float32)),
            AnalogChunk(start, 1 / rate, (common - differential / 2 - noise).astype(np.float32)))


def create_demo(directory):
    cfg = CaptureSettings()
    dec = DecodeSettings()
    a, b = synthesize(b"Hello RS485!\r\n", dec, cfg.sample_rate)
    write_analog(directory / "analog_0.bin", [a])
    write_analog(directory / "analog_1.bin", [b])
    session = Session(directory, CaptureSettings(duration=a.end), dec,
                      {"source": "synthetic demo", "expected_text": "Hello RS485!\\r\\n"})
    write_metadata(session)
    return session
