import numpy as np
import pytest
from saleae_differential.demo import synthesize
from saleae_differential.models import AnalogChunk, DecodeSettings
from saleae_differential.processing import analyze, aligned_chunks, reconstruct


@pytest.mark.parametrize('bits', range(5, 10))
@pytest.mark.parametrize('parity', ['none', 'even', 'odd'])
@pytest.mark.parametrize('stops', [1, 1.5, 2])
def test_uart_framing(bits, parity, stops):
    cfg = DecodeSettings(baud=9600, data_bits=bits, parity=parity, stop_bits=stops)
    values = [0, 1, (1 << bits) - 1, 0x15]
    a, b = synthesize(values, cfg, rate=1_250_000)
    result = analyze([a], [b], cfg)
    assert [f.value for f in result.frames] == values
    assert all(not f.errors for f in result.frames)


def test_polarity_common_mode_and_default_demo():
    cfg = DecodeSettings(invert=True)
    a, b = synthesize(b'Hello RS485!', cfg, invert=True)
    result = analyze([a], [b], cfg)
    assert bytes(f.value for f in result.frames) == b'Hello RS485!'
    assert np.ptp(result.chunks[0].common_mode) > 0.1
    assert all(not f.errors for f in result.frames)


def test_hysteresis_and_unresolved_start():
    logic, uncertain = reconstruct(np.array([0, 0.1, 0.3, 0.1, -0.1, -0.3, 0, 0.3]), DecodeSettings())
    assert logic.tolist() == [-1, -1, 1, 1, 1, 0, 0, 1]
    assert uncertain.tolist() == [True, True, False, True, True, False, True, False]


def test_errors():
    cfg = DecodeSettings(parity='even')
    a, b = synthesize([0x55], cfg, corrupt_parity=True, corrupt_stop=True)
    result = analyze([a], [b], cfg)
    assert result.frames[0].value == 0x55
    assert 'parity' in result.frames[0].errors
    assert 'framing' in result.frames[0].errors


def test_uncertain_samples_keep_logic_but_flag_frame():
    cfg = DecodeSettings()
    a, b = synthesize([0x55], cfg)
    bit_center = (3 + 1.5) / cfg.baud
    idx = round(bit_center / a.period)
    a.samples[idx - 3:idx + 4] = b.samples[idx - 3:idx + 4] + 0.05
    result = analyze([a], [b], cfg)
    assert 'uncertain' in result.frames[0].errors


def test_incomplete_and_break():
    cfg = DecodeSettings(baud=1000)
    a, b = synthesize([0x55], cfg, rate=100_000)
    a.samples = a.samples[:700]
    b.samples = b.samples[:700]
    assert analyze([a], [b], cfg).frames[0].errors == ('incomplete',)
    differential = np.r_[np.ones(300), -np.ones(1500), np.ones(300)]
    a = AnalogChunk(0, 1e-5, differential)
    b = AnalogChunk(0, 1e-5, np.zeros(len(differential)))
    frames = analyze([a], [b], cfg).frames
    assert len(frames) == 1
    assert frames[0].errors == ('break',)
    assert frames[0].value is None


def test_gap_does_not_decode_across_missing_data():
    cfg = DecodeSettings(baud=1000)
    a, b = synthesize([0x55], cfg, rate=100_000)
    # Split in the middle of the byte, leaving a missing interval.
    aa = [AnalogChunk(0, 1e-5, a.samples[:700]), AnalogChunk(0.009, 1e-5, a.samples[900:])]
    bb = [AnalogChunk(0, 1e-5, b.samples[:700]), AnalogChunk(0.009, 1e-5, b.samples[900:])]
    result = analyze(aa, bb, cfg)
    assert result.frames[0].errors == ('incomplete',)
    assert len(result.chunks) == 2
    assert any('gaps' in warning for warning in result.warnings)


def test_contiguous_export_chunks_do_not_interrupt_frames():
    cfg = DecodeSettings(baud=1000)
    a, b = synthesize([0x55], cfg, rate=100_000)
    aa = [AnalogChunk(0, 1e-5, a.samples[:700]), AnalogChunk(0.007, 1e-5, a.samples[700:])]
    bb = [AnalogChunk(0, 1e-5, b.samples[:700]), AnalogChunk(0.007, 1e-5, b.samples[700:])]
    result = analyze(aa, bb, cfg)
    assert len(result.chunks) == 1
    assert result.frames[0].value == 0x55
    assert not result.frames[0].errors


def test_align_differing_grids_without_extrapolation():
    a = AnalogChunk(10, 0.1, np.arange(10, dtype=float))
    b = AnalogChunk(10.05, 0.2, np.arange(5, dtype=float))
    times, av, bv, period = aligned_chunks([a], [b])[0]
    assert period == 0.2
    np.testing.assert_allclose(times, [10.05, 10.25, 10.45, 10.65, 10.85])
    np.testing.assert_allclose(av, [0.5, 2.5, 4.5, 6.5, 8.5])
    np.testing.assert_allclose(bv, range(5))


def test_short_low_glitch_rejected():
    diff = np.ones(1000)
    diff[100:105] = -1
    result = analyze([AnalogChunk(0, 1e-5, diff)], [AnalogChunk(0, 1e-5, np.zeros(1000))], DecodeSettings(baud=1000))
    assert not result.frames


def test_low_sample_rate_warning():
    cfg = DecodeSettings(baud=1000)
    a, b = synthesize([0x55], cfg, rate=10_000)
    result = analyze([a], [b], cfg)
    assert any('16' in warning for warning in result.warnings)


@pytest.mark.parametrize('settings', [DecodeSettings(lower_threshold=1, upper_threshold=0),
                                    DecodeSettings(baud=0), DecodeSettings(parity='invalid')])
def test_invalid_decode_configuration(settings):
    with pytest.raises(ValueError):
        settings.validate()
