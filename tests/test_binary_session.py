import struct
import numpy as np
import pytest
from saleae_differential.binary import read_analog, write_analog
from saleae_differential.demo import create_demo
from saleae_differential.models import AnalogChunk
from saleae_differential.session import analyze_session, export_csv, load_session, save_session


def test_v0_timing_and_volts(tmp_path):
    path = tmp_path / 'analog.bin'
    path.write_bytes(b'<SALEAE>' + struct.pack('<ii', 0, 1) + struct.pack('<dQQQ', 2.5, 100, 4, 3)
                     + struct.pack('<fff', -1, 0, 1))
    chunk, = read_analog(path)
    np.testing.assert_allclose(chunk.times, [2.5, 2.54, 2.58])
    np.testing.assert_array_equal(chunk.samples, [-1, 0, 1])


def test_v1_multiple_waveforms(tmp_path):
    path = tmp_path / 'analog.bin'
    chunks = [AnalogChunk(1, 0.01, np.arange(5), 1.02), AnalogChunk(2, 0.01, np.arange(3), 2.02)]
    write_analog(path, chunks)
    loaded = read_analog(path)
    assert len(loaded) == 2
    assert loaded[1].start == 2
    assert loaded[0].trigger == 1.02
    np.testing.assert_array_equal(loaded[0].samples, np.arange(5))


@pytest.mark.parametrize('data', [b'bad', b'<SALEAE>',
    b'<SALEAE>' + struct.pack('<ii', 4, 1), b'<SALEAE>' + struct.pack('<ii', 0, 0),
    b'<SALEAE>' + struct.pack('<ii', 0, 1) + struct.pack('<dQQQ', 0, 100, 1, 100),
    b'<SALEAE>' + struct.pack('<ii', 1, 1) + struct.pack('<Q', 100)])
def test_invalid_or_truncated_file(tmp_path, data):
    path = tmp_path / 'bad.bin'
    path.write_bytes(data)
    with pytest.raises(ValueError):
        read_analog(path)


def test_nonfinite_and_overlapping_chunks(tmp_path):
    path = tmp_path / 'bad.bin'
    write_analog(path, [AnalogChunk(0, 1, np.array([np.nan]))])
    with pytest.raises(ValueError, match='non-finite'):
        read_analog(path)
    write_analog(path, [AnalogChunk(0, 1, np.array([1, 2])), AnalogChunk(1, 1, np.array([3]))])
    with pytest.raises(ValueError, match='Overlapping'):
        read_analog(path)


def test_session_round_trip_and_csv(tmp_path):
    original = tmp_path / 'original'
    original.mkdir()
    session = create_demo(original)
    first = analyze_session(session)
    saved = save_session(session, tmp_path / 'saved')
    reopened = load_session(saved)
    second = analyze_session(reopened)
    assert first.frames == second.frames
    assert bytes(f.value for f in second.frames) == b'Hello RS485!\r\n'
    csv_path = tmp_path / 'decoded.csv'
    export_csv(second.frames, csv_path)
    assert '48,H,' in csv_path.read_text()
    with pytest.raises(ValueError, match='never overwritten'):
        save_session(session, saved)
