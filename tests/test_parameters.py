from dataclasses import replace
import numpy as np
import pytest
from tf_decoder import Application, AnalysisParameters
from test_application import raw_file


def test_applied_parameters_are_batch_wide_and_display_range_preserves_analysis(tmp_path):
    raw_file(tmp_path, [2097152], '1.raw')
    raw_file(tmp_path, [1048576], '2.raw')
    with Application() as app:
        initial = app.open_folder(tmp_path).result(timeout=10)
        parameters = replace(initial.result.parameters, sample_rate=48000, window_length=512,
                             overlap=128, fft_length=512)
        assert app.state.result.parameters.sample_rate == 96000
        applied = app.apply_parameters(parameters, displayed_frequency_range=(2000, 15000)).result(timeout=10)
        assert applied.result.parameters == parameters
        assert applied.viewport.frequency_range == (2000, 15000)
        cropped = app.apply_parameters(parameters, displayed_frequency_range=(4000, 10000)).result(timeout=10)
        assert cropped.result is applied.result
        assert cropped.result.levels == applied.result.levels
        next_state = app.next_file().result(timeout=10)
        assert next_state.result.parameters == parameters
        assert next_state.displayed_frequency_range == (4000, 10000)
        assert app.restore_full_view().viewport.frequency_range == (4000, 10000)


def test_causal_bandpass_matches_whole_record_oracle_across_raw_blocks(tmp_path):
    from scipy import signal
    n = np.arange(32768)
    counts = np.rint(1000000 * (np.sin(2*np.pi*6000*n/96000) + np.sin(2*np.pi*18000*n/96000))).astype(np.int64)
    path = tmp_path / 'tones.raw'
    path.write_bytes(b''.join(bytes(32) + (block & 0xffffff).astype('<u4').tobytes()
                             for block in counts.reshape(-1, 8192)))
    parameters = AnalysisParameters(filter_enabled=True, passband_low=5000, passband_high=13000)
    with Application() as app:
        original = app.open_file(path).result(timeout=10).result
        filtered = app.apply_parameters(parameters).result(timeout=10).result
        # Independent whole-record direct-form causal filter + SciPy spectrogram;
        # production uses second-order sections and carries state between RAW blocks.
        b, a = signal.iirdesign([5000, 13000], [4000, 14000], .5, 10, fs=96000, ftype='cheby1')
        volts = signal.lfilter(b, a, counts * (8.196/2**24))
        frequencies, times, expected = signal.spectrogram(volts, fs=96000,
            window=signal.windows.hann(1026)[1:-1], noverlap=512, nfft=1024, detrend=False)
        np.testing.assert_allclose(filtered.psd, expected, rtol=2e-7, atol=1e-15)
        np.testing.assert_array_equal(filtered.times, times)
        for frequency, min_db, max_db in [(6000, -.51, .01), (18000, -100, -10)]:
            row = np.argmin(abs(frequencies-frequency))
            ratio = 10*np.log10(filtered.psd[row, 8:].mean()/original.psd[row, 8:].mean())
            assert min_db < ratio < max_db
