from dataclasses import dataclass, field
from pathlib import Path
import math
import numpy as np


@dataclass(frozen=True)
class CaptureSettings:
    positive_channel: int = 0
    negative_channel: int = 1
    sample_rate: int = 6_250_000
    duration: float = 0.1
    device_id: str = ""
    simulation: bool = False

    def validate(self):
        if not all(isinstance(c, int) and 0 <= c < 8 for c in (self.positive_channel, self.negative_channel)):
            raise ValueError("Pro 8 channel numbers must be between 0 and 7.")
        if self.positive_channel == self.negative_channel:
            raise ValueError("Choose two different analog channels.")
        if self.sample_rate <= 0 or not math.isfinite(self.duration) or self.duration <= 0:
            raise ValueError("Sample rate and duration must be positive.")
        if self.sample_rate * self.duration > 20_000_000:
            raise ValueError("Limit captures to 20 million samples per channel; shorten the duration or lower the rate.")


@dataclass(frozen=True)
class DecodeSettings:
    lower_threshold: float = -0.2
    upper_threshold: float = 0.2
    invert: bool = False
    baud: int = 115_200
    data_bits: int = 8
    parity: str = "none"
    stop_bits: float = 1.0

    def validate(self):
        if not all(math.isfinite(v) for v in (self.lower_threshold, self.upper_threshold)):
            raise ValueError("Thresholds must be finite voltages.")
        if self.lower_threshold >= self.upper_threshold:
            raise ValueError("The lower threshold must be below the upper threshold.")
        if self.baud <= 0 or self.data_bits not in range(5, 10):
            raise ValueError("Choose a positive baud rate and 5–9 data bits.")
        if self.parity not in ("none", "even", "odd") or self.stop_bits not in (1, 1.5, 2):
            raise ValueError("Unsupported UART framing.")


@dataclass
class AnalogChunk:
    start: float
    period: float
    samples: np.ndarray
    trigger: float | None = None

    @property
    def times(self):
        return self.start + np.arange(len(self.samples), dtype=np.float64) * self.period

    @property
    def end(self):
        return self.start + len(self.samples) * self.period


@dataclass
class DifferentialChunk:
    times: np.ndarray
    positive: np.ndarray
    negative: np.ndarray
    period: float
    difference: np.ndarray
    common_mode: np.ndarray
    logic: np.ndarray
    uncertain: np.ndarray


@dataclass(frozen=True)
class UartFrame:
    start: float
    end: float
    value: int | None
    errors: tuple[str, ...] = ()

    @property
    def character(self):
        return chr(self.value) if self.value is not None and 32 <= self.value <= 126 else ""


@dataclass
class Session:
    directory: Path
    capture: CaptureSettings
    decode: DecodeSettings
    metadata: dict = field(default_factory=dict)


@dataclass
class Analysis:
    chunks: list[DifferentialChunk]
    frames: list[UartFrame]
    warnings: list[str]
