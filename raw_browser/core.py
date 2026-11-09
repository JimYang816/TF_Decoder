"""RAW decoding and numerical processing, independent of Qt and Matplotlib."""
from __future__ import annotations

import mmap
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryFile

import numpy as np
from scipy import fft, signal

SAMPLE_RATE = 96_000
HEADER_BYTES = 32
BLOCK_SAMPLES = 8192
SAMPLE_DTYPE = np.dtype("<u4")
BLOCK_BYTES = HEADER_BYTES + BLOCK_SAMPLES * SAMPLE_DTYPE.itemsize
WINDOW_SIZE = 1024
HOP = 512
NYQUIST = SAMPLE_RATE / 2
BATCH_SIZE = 10
FFT_BATCH_SIZE = 2048
FFT_WORKERS = max(1, min(4, (os.cpu_count() or 2) - 2))
EPS = np.finfo(np.float64).eps
VOLTS_PER_COUNT = (4.098 * 2) / (2 ** 24)
PSD_FLOOR_DB = float(10 * np.log10(EPS))
BLOCK_DTYPE = np.dtype([
    ("header", f"V{HEADER_BYTES}"),
    ("samples", SAMPLE_DTYPE, (BLOCK_SAMPLES,)),
])


class ProcessingCancelled(Exception):
    pass


class RawFormatError(ValueError):
    pass


def check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise ProcessingCancelled()


@dataclass(frozen=True)
class AnalysisSettings:
    filter_enabled: bool = False
    low_hz: float = 20_000.0
    high_hz: float = 28_000.0

    def validate(self) -> None:
        if self.filter_enabled and not (0 < self.low_hz < self.high_hz < NYQUIST):
            raise ValueError("滤波频率必须满足：0 < 下限 < 上限 < 48 kHz。")


class _SpectrumFile:
    """Keep frequency bins for each frame contiguous in a temporary file."""

    def __init__(self, shape: tuple[int, int]):
        self._shape = shape
        try:
            self._file = TemporaryFile(prefix="tf-decoder-", suffix=".psd", buffering=0)
        except OSError as error:
            raise OSError(f"无法创建临时频谱缓存：{error}") from error

    def append(self, frames: np.ndarray) -> None:
        try:
            frames.astype(np.float32).tofile(self._file)
        except OSError as error:
            raise OSError(f"无法写入临时频谱缓存：{error}") from error

    def __getitem__(self, rows: slice) -> np.ndarray:
        mapping = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        # The array owns the mapping, so readers can outlive the cache entry.
        values = np.ndarray(self._shape, dtype=np.float32, buffer=mapping, order="F")
        return values[rows]

    def close(self) -> None:
        self._file.close()


@dataclass
class Spectrum:
    path: Path
    db: np.ndarray | _SpectrumFile
    times: np.ndarray
    frequencies: np.ndarray
    sample_count: int
    settings: AnalysisSettings
    peak_db: float | None = None

    @property
    def duration(self) -> float:
        return self.sample_count / SAMPLE_RATE

    def band_data(self, band: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
        validate_display_band(band)
        start = np.searchsorted(self.frequencies, band[0], side="left")
        end = np.searchsorted(self.frequencies, band[1], side="right")
        if start == end:
            raise ValueError("显示范围内没有FFT频率点，请扩大范围（频率间隔93.75 Hz）。")
        return self.frequencies[start:end], self.db[start:end]

    def color_limits(self) -> tuple[float, float]:
        # MATLAB uses the peak of the full spectrum even when the view is cropped.
        low = PSD_FLOOR_DB
        high = self.peak_db if self.peak_db is not None else float(self.db[:].max())
        if not np.isfinite(high) or high <= low:
            return low - 1.0, low + 1.0
        return low, high


def validate_display_band(band: tuple[float, float]) -> None:
    low, high = band
    if not (0 <= low < high <= NYQUIST):
        raise ValueError("显示频率必须满足：0 ≤ 下限 < 上限 ≤ 48 kHz。")
    if np.ceil(low / (SAMPLE_RATE / WINDOW_SIZE)) > np.floor(high / (SAMPLE_RATE / WINDOW_SIZE)):
        raise ValueError("显示范围内没有FFT频率点，请扩大范围（频率间隔93.75 Hz）。")


def scan_raw_files(directory: Path, recursive: bool = False) -> list[Path]:
    directory = Path(directory).expanduser().resolve()
    if not directory.is_dir():
        raise ValueError("请选择存在且可读取的文件夹。")
    if recursive:
        def fail(error):
            raise error
        files = [Path(root) / name for root, _, names in os.walk(directory, onerror=fail)
                 for name in names if Path(name).suffix.casefold() == ".raw"]
    else:
        files = [p for p in directory.iterdir() if p.is_file() and p.suffix.casefold() == ".raw"]

    def natural_key(path):
        name = path.relative_to(directory).as_posix()
        parts = tuple((1, int(part)) if part.isdigit() else (0, part.casefold())
                      for part in re.split(r"(\d+)", name))
        return parts, name
    return sorted(files, key=natural_key)


def _decode_sample_words(words: np.ndarray) -> np.ndarray:
    # Shift away the high byte, then arithmetic-shift the signed word.
    # This avoids large boolean-indexed floating-point temporaries.
    signed = np.left_shift(words, 8).view(np.int32)
    return np.right_shift(signed, 8, out=signed)


def read_raw(path: Path, cancel: threading.Event | None = None) -> np.ndarray:
    path = Path(path)
    before = path.stat()
    if before.st_size == 0:
        raise RawFormatError("RAW文件为空。")
    blocks, remainder = divmod(before.st_size, BLOCK_BYTES)
    full_samples = blocks * BLOCK_SAMPLES
    tail_samples = max(0, remainder - HEADER_BYTES) // SAMPLE_DTYPE.itemsize
    output = np.empty(full_samples + tail_samples, dtype=np.float64)
    with path.open("rb") as stream:
        for first in range(0, blocks, 64):
            check_cancel(cancel)
            count = min(64, blocks - first)
            raw = stream.read(count * BLOCK_BYTES)
            if len(raw) != count * BLOCK_BYTES:
                raise RawFormatError("文件在读取期间被截断，请停止采集后重新扫描。")
            words = np.frombuffer(raw, dtype=BLOCK_DTYPE)["samples"]
            output[first * BLOCK_SAMPLES:(first + count) * BLOCK_SAMPLES] = _decode_sample_words(words).ravel()
        if tail_samples:
            check_cancel(cancel)
            raw = stream.read(remainder)
            if len(raw) != remainder:
                raise RawFormatError("文件在读取期间被截断，请停止采集后重新扫描。")
            words = np.frombuffer(raw, dtype=SAMPLE_DTYPE, count=tail_samples, offset=HEADER_BYTES)
            output[full_samples:] = _decode_sample_words(words)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RawFormatError("文件在读取期间发生变化，请停止采集后重新扫描。")
    check_cancel(cancel)
    return output


def matlab_hanning() -> np.ndarray:
    # MATLAB hanning(N) excludes the two zero endpoints of hann(N+2).
    return signal.windows.hann(WINDOW_SIZE + 2, sym=True)[1:-1]


def prepare_signal(samples: np.ndarray, settings: AnalysisSettings,
                   cancel: threading.Event | None = None, *, copy: bool = True) -> np.ndarray:
    settings.validate()
    check_cancel(cancel)
    x = np.asarray(samples, dtype=np.float64)
    if copy:
        x = x.copy()
    if x.size < WINDOW_SIZE:
        raise RawFormatError("有效采样不足1024点，无法绘制时频图。")
    # The decoder calibrates board counts to volts without removing DC or
    # normalizing each file's peak. Keep physical power comparable in dB/Hz.
    x *= VOLTS_PER_COUNT
    if settings.filter_enabled:
        check_cancel(cancel)
        b = signal.firwin(513, [settings.low_hz, settings.high_hz], fs=SAMPLE_RATE,
                         pass_zero=False, window="hamming", scale=True)
        if x.size > 3 * b.size:
            x = signal.filtfilt(b, [1.0], x, padtype="odd", padlen=1536, method="pad")
        else:
            x = signal.lfilter(b, [1.0], x)
    check_cancel(cancel)
    return x


def analyze_samples(samples: np.ndarray, path: Path = Path("signal.raw"),
                    settings: AnalysisSettings = AnalysisSettings(),
                    cancel: threading.Event | None = None, *, copy_samples: bool = True,
                    disk_cache: bool = False) -> Spectrum:
    x = prepare_signal(samples, settings, cancel, copy=copy_samples)
    frame_count = 1 + (x.size - WINDOW_SIZE) // HOP
    frames = np.lib.stride_tricks.sliding_window_view(x, WINDOW_SIZE)[::HOP]
    window = matlab_hanning()
    psd_scale = 1.0 / (SAMPLE_RATE * np.dot(window, window))
    peak_db = PSD_FLOOR_DB
    windowed = np.empty((min(FFT_BATCH_SIZE, frame_count), WINDOW_SIZE), dtype=np.float64)
    shape = (WINDOW_SIZE // 2 + 1, frame_count)
    db = _SpectrumFile(shape) if disk_cache else np.empty(shape, dtype=np.float32)
    try:
        for start in range(0, frame_count, FFT_BATCH_SIZE):
            check_cancel(cancel)
            end = min(start + FFT_BATCH_SIZE, frame_count)
            chunk = windowed[:end - start]
            np.multiply(frames[start:end], window, out=chunk)
            transformed = fft.rfft(chunk, n=WINDOW_SIZE, axis=-1,
                                   norm="backward", workers=FFT_WORKERS, overwrite_x=True)
            magnitude = np.abs(transformed)
            np.square(magnitude, out=magnitude)
            magnitude *= psd_scale
            magnitude[:, 1:-1] *= 2  # Single-sided PSD: preserve DC and Nyquist.
            np.add(magnitude, EPS, out=magnitude)
            np.log10(magnitude, out=magnitude)
            magnitude *= 10
            peak_db = max(peak_db, float(magnitude.max()))
            if isinstance(db, _SpectrumFile):
                db.append(magnitude)
            else:
                db[:, start:end] = magnitude.T
        check_cancel(cancel)
        times = (np.arange(frame_count, dtype=np.float64) * HOP + WINDOW_SIZE / 2) / SAMPLE_RATE
        frequencies = fft.rfftfreq(WINDOW_SIZE, 1 / SAMPLE_RATE)
        return Spectrum(Path(path), db, times, frequencies, x.size, settings, peak_db)
    except BaseException:
        if isinstance(db, _SpectrumFile):
            db.close()
        raise


def analyze_file(path: Path, settings: AnalysisSettings = AnalysisSettings(),
                 cancel: threading.Event | None = None, *, disk_cache: bool = False) -> Spectrum:
    samples = read_raw(path, cancel)
    return analyze_samples(samples, path, settings, cancel, copy_samples=False,
                           disk_cache=disk_cache)
