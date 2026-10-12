"""Read the documented Saleae analog binary export formats, not .sal internals."""
from pathlib import Path
import math
import struct
import numpy as np
from .models import AnalogChunk


def read_analog(path: Path) -> list[AnalogChunk]:
    path = Path(path)
    size = path.stat().st_size
    chunks = []
    with path.open("rb") as stream:
        def unpack(fmt):
            length = struct.calcsize(fmt)
            data = stream.read(length)
            if len(data) != length:
                raise ValueError(f"Truncated analog export: {path.name}")
            return struct.unpack(fmt, data)

        if stream.read(8) != b"<SALEAE>":
            raise ValueError(f"Not a Saleae binary export: {path.name}")
        version, kind = unpack("<ii")
        if kind != 1 or version not in (0, 1):
            raise ValueError(f"Expected analog export version 0 or 1; got version={version}, type={kind}.")
        count = unpack("<Q")[0] if version == 1 else 1
        if count > (size - stream.tell()) // (40 if version else 32):
            raise ValueError("Invalid waveform count in analog export.")
        for _ in range(count):
            if version == 0:
                start, rate, downsample, length = unpack("<dQQQ")
                trigger = None
            else:
                start, trigger, rate, downsample, length = unpack("<dddqQ")
            if (not math.isfinite(start) or not math.isfinite(rate) or rate <= 0 or downsample <= 0
                    or (trigger is not None and not math.isfinite(trigger))):
                raise ValueError("Invalid timing metadata in analog export.")
            if length > (size - stream.tell()) // 4:
                raise ValueError("Truncated analog sample data.")
            samples = np.fromfile(stream, dtype="<f4", count=length)
            if not np.all(np.isfinite(samples)):
                raise ValueError("Analog export contains non-finite voltage samples.")
            if length:
                chunks.append(AnalogChunk(start, downsample / rate, samples, trigger))
        if stream.tell() != size:
            raise ValueError("Unexpected trailing bytes in analog export.")
    chunks.sort(key=lambda c: c.start)
    for previous, current in zip(chunks, chunks[1:]):
        if current.start < previous.end - previous.period * 1e-5:
            raise ValueError("Overlapping analog waveforms cannot be aligned unambiguously.")
    if not chunks:
        raise ValueError("Analog export contains no samples.")
    return chunks


def write_analog(path: Path, chunks: list[AnalogChunk]):
    """Write version 1 exports for demo captures and deterministic fixtures."""
    with Path(path).open("wb") as stream:
        stream.write(b"<SALEAE>" + struct.pack("<iiQ", 1, 1, len(chunks)))
        for c in chunks:
            stream.write(struct.pack("<dddqQ", c.start, c.trigger if c.trigger is not None else c.start,
                                     1 / c.period, 1, len(c.samples)))
            stream.write(np.asarray(c.samples, dtype="<f4").tobytes())
