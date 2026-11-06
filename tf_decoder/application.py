from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
import numpy as np


@dataclass(frozen=True)
class AnalysisParameters:
    sample_rate: float = 96000
    window_length: int = 1024
    overlap: int = 512
    fft_length: int = 1024


@dataclass(frozen=True)
class AnalysisResult:
    path: Path
    parameters: AnalysisParameters
    sample_count: int
    voltage_range: tuple[float, float]
    times: np.ndarray
    frequencies: np.ndarray
    psd: np.ndarray
    db: np.ndarray
    levels: tuple[float, float]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ApplicationState:
    status: str = "idle"
    path: Path | None = None
    result: AnalysisResult | None = None
    message: str = "请选择 RAW 采集文件"


class Application:
    """Asynchronous, Qt-independent entry point. Future results are state snapshots.

    Only the most recent request may update ``state``. Results and arrays are
    immutable; callbacks run on the worker and UI clients must marshal to Qt.
    """
    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="raw-analysis")
        self._lock = RLock()
        self._generation = 0
        self._state = ApplicationState()

    @property
    def state(self):
        with self._lock:
            return self._state

    def open_file(self, path: str | Path) -> Future:
        path = Path(path).resolve()
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._state = ApplicationState("computing", path, message="正在读取并计算时频图…")
        return self._executor.submit(self._analyse, path, generation)

    def _analyse(self, path, generation):
        try:
            result = _compute(path, AnalysisParameters())
            message = "；".join(result.warnings) if result.warnings else "计算完成 · 未滤波"
            state = ApplicationState("ready", path, result, message)
        except (OSError, ValueError) as error:
            state = ApplicationState("failed", path, message=f"{path}: {error}")
        with self._lock:
            if generation == self._generation:
                self._state = state
        return state

    def close(self):
        self._executor.shutdown(wait=True, cancel_futures=True)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _compute(path, parameters):
    block_size = 32800
    with path.open("rb") as stream:
        data = stream.read()
    blocks, remainder = divmod(len(data), block_size)
    if not blocks:
        raise ValueError("空文件或无完整数据块（每块需 32800 字节）")
    records = np.frombuffer(data, dtype=np.dtype([("header", "u1", 32), ("samples", "<u4", 8192)]), count=blocks)
    counts = (records["samples"].reshape(-1) & 0xffffff).astype(np.int32)
    counts[counts >= 0x800000] -= 0x1000000
    volts = counts.astype(np.float64) * (8.196 / 2**24)
    n, overlap, nfft, fs = parameters.window_length, parameters.overlap, parameters.fft_length, parameters.sample_rate
    window = np.hanning(n + 2)[1:-1]
    segments = np.lib.stride_tricks.sliding_window_view(volts, n)[::n-overlap]
    spectrum = np.fft.rfft(segments * window, n=nfft, axis=1)
    psd = (np.abs(spectrum)**2 / (fs * np.dot(window, window))).T
    psd[1:-1] *= 2
    db = 10 * np.log10(psd + np.finfo(np.float64).eps)
    floor = float(10 * np.log10(np.finfo(np.float64).eps))
    levels = (floor, max(float(db.max()), floor + 1) if not np.any(counts) else float(db.max()))
    times = (np.arange(segments.shape[0]) * (n-overlap) + n/2) / fs
    frequencies = np.fft.rfftfreq(nfft, 1/fs)
    for array in (times, frequencies, psd, db):
        array.flags.writeable = False
    warnings = (f"已跳过残缺尾块：{remainder} 字节",) if remainder else ()
    return AnalysisResult(path, parameters, len(volts), (float(volts.min()), float(volts.max())), times, frequencies, psd, db, levels, warnings)
