from pathlib import Path
import struct
import numpy as np
from tf_decoder import Application


def raw_file(tmp_path, counts, name="采集.raw", tail=b""):
    path = tmp_path / name
    values = np.resize(np.asarray(counts, dtype=np.int64), 8192)
    # Deliberately nonzero header/high byte: neither belongs to the ADC sample.
    words = ((values & 0xffffff) | 0xa5000000).astype("<u4")
    path.write_bytes(bytes(range(32)) + words.tobytes() + tail)
    return path


def test_single_raw_dc_has_calibrated_psd_and_complete_window_coordinates(tmp_path):
    path = raw_file(tmp_path, [2097152])  # exactly 1.0245 volts
    with Application() as app:
        state = app.open_file(path).result(timeout=10)
        assert state.status == "ready"
        result = state.result
        assert result.path == path.resolve()
        assert result.sample_count == 8192
        assert result.voltage_range == (1.0245, 1.0245)
        assert result.psd.shape == (513, 15)
        np.testing.assert_allclose(result.times[[0, -1]], [0.005333333333333333, 0.08], atol=1e-15)
        np.testing.assert_allclose(result.frequencies[[0, 64, -1]], [0, 6000, 48000], atol=1e-12)
        # Independently worked DC energy: sum(w)=512.5, sum(w²)=384.375.
        np.testing.assert_allclose(result.psd[0], 0.007471112890625, rtol=1e-13)
        assert result.levels[0] == -156.53559774527022
        assert result.levels[1] == float(result.db.max())
        assert result.parameters.sample_rate == 96000
        assert result.warnings == ()



def test_signed_adc_boundaries_ignore_high_byte_and_preserve_source(tmp_path):
    import hashlib
    for count, volts in [(0, 0.0), (1, 0.0000004885196685791016), (-1, -0.0000004885196685791016), (8388607, 4.097999511480331), (-8388608, -4.098)]:
        path = raw_file(tmp_path, [count])
        original = hashlib.sha256(path.read_bytes()).hexdigest()
        with Application() as app:
            state = app.open_file(path).result(timeout=10)
            assert state.status == "ready"
            np.testing.assert_allclose(state.result.voltage_range, [volts, volts], rtol=1e-15, atol=1e-18)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == original


def test_known_tone_has_one_sided_energy_without_detrending(tmp_path):
    # 64 cycles per FFT, amplitude exactly 2^21 ADC counts, rounded to ADC codes.
    samples = np.rint(2097152 * np.sin(2*np.pi*np.arange(8192)/16)).astype(np.int64)
    path = raw_file(tmp_path, samples)
    with Application() as app:
        result = app.open_file(path).result(timeout=10).result
        assert result.frequencies[result.psd[:, 0].argmax()] == 6000
        # Continuous tone mean square is A²/2 = .524800125 V²;
        # quantisation error < 0.5 ADC count bounds numerical tolerance.
        np.testing.assert_allclose(np.sum(result.psd, axis=0) * 93.75, 0.524800125, atol=2e-7, rtol=0)
        np.testing.assert_allclose(result.psd[64], 0.0037355556944, atol=3e-9, rtol=0)


def test_zero_tail_and_failures_are_visible_and_recoverable(tmp_path):
    with Application() as app:
        for name, data in [("empty.raw", b""), ("short.raw", b"x"*32799)]:
            path = tmp_path / name
            path.write_bytes(data)
            state = app.open_file(path).result(timeout=10)
            assert state.status == "failed"
            assert str(path) in state.message and "32800" in state.message
        missing = tmp_path / "missing.raw"
        assert app.open_file(missing).result(timeout=10).status == "failed"
        state = app.open_file(raw_file(tmp_path, [0], tail=b"truncated")).result(timeout=10)
        assert state.status == "ready"
        assert state.result.warnings == ("已跳过残缺尾块：9 字节",)
        assert np.all(state.result.db == -156.53559774527022)
        assert state.result.levels[1] > state.result.levels[0]
        assert app.state == state
