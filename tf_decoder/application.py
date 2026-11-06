from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
import math
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
class Viewport:
    """Visible coordinates in seconds and Hz; independent of analysis arrays."""
    time_range: tuple[float, float]
    frequency_range: tuple[float, float]

    def __post_init__(self):
        for name in ("time_range", "frequency_range"):
            bounds = tuple(getattr(self, name))
            if len(bounds) != 2 or not all(math.isfinite(v) for v in bounds) or bounds[0] >= bounds[1]:
                raise ValueError("视口范围必须为有限且递增的两个坐标")
            object.__setattr__(self, name, bounds)


@dataclass(frozen=True)
class ApplicationState:
    status: str = "idle"
    path: Path | None = None
    result: AnalysisResult | None = None
    message: str = "请选择 RAW 采集文件"
    viewport: Viewport | None = None
    displayed_frequency_range: tuple[float, float] | None = None


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
        self._export_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="png-export")
        from .export import ExportResult
        self._export_status = ExportResult("idle", message="尚未导出")

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
            state = ApplicationState("ready", path, result, message, _full_view(result),
                                     (0, result.parameters.sample_rate/2))
        except (OSError, ValueError) as error:
            state = ApplicationState("failed", path, message=f"{path}: {error}")
        with self._lock:
            if generation == self._generation:
                self._state = state
        return state

    @property
    def export_status(self):
        with self._lock:
            return self._export_status

    def export_png(self, directory: str | Path, *, mode="current") -> Future:
        """Export an immutable applied-result/view snapshot without changing browsing state."""
        from .export import ExportResult, write_png
        with self._lock:
            state = self._state
            if mode not in ("current", "full") or state.status != "ready":
                message = "导出模式必须为 current 或 full" if mode not in ("current", "full") else "时频图尚未就绪，无法导出"
                self._export_status = ExportResult("failed", message=message)
                future = Future()
                future.set_result(self._export_status)
                return future
            view = state.viewport if mode == "current" else Viewport(
                _full_view(state.result).time_range,
                state.displayed_frequency_range or _full_view(state.result).frequency_range)
            self._export_status = ExportResult("exporting", viewport=view, message="正在导出 PNG…")
        def work():
            exported = write_png(state.result, view, Path(directory), mode)
            with self._lock:
                self._export_status = exported
            return exported
        return self._export_executor.submit(work)

    def set_viewport(self, viewport: Viewport) -> ApplicationState:
        """Select a rectangle without recomputing PSD or file-wide color levels."""
        if not isinstance(viewport, Viewport):
            raise TypeError("viewport must be a Viewport")
        with self._lock:
            if self._state.status != "ready":
                raise ValueError("请先打开有效的 RAW 采集文件")
            self._state = replace(self._state, viewport=viewport)
            return self._state

    def zoom_viewport(self, factor: float, *, time: float, frequency: float) -> ApplicationState:
        """Scale both spans around a pointer in seconds/Hz; factor < 1 zooms in."""
        if not all(math.isfinite(v) for v in (factor, time, frequency)) or factor <= 0:
            raise ValueError("缩放比例必须为有限正数，指针坐标必须有限")
        with self._lock:
            view = self._state.viewport
            if view is None:
                raise ValueError("请先打开有效的 RAW 采集文件")
            return self.set_viewport(Viewport(
                tuple(time + (v-time)*factor for v in view.time_range),
                tuple(frequency + (v-frequency)*factor for v in view.frequency_range)))

    def pan_viewport(self, time_delta: float, frequency_delta: float) -> ApplicationState:
        """Translate the visible coordinates in seconds/Hz."""
        with self._lock:
            view = self._state.viewport
            if view is None:
                raise ValueError("请先打开有效的 RAW 采集文件")
            return self.set_viewport(Viewport(tuple(v+time_delta for v in view.time_range),
                                              tuple(v+frequency_delta for v in view.frequency_range)))

    def restore_full_view(self) -> ApplicationState:
        with self._lock:
            if self._state.result is None:
                raise ValueError("请先打开有效的 RAW 采集文件")
            return self.set_viewport(_full_view(self._state.result))

    def close(self):
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._export_executor.shutdown(wait=True, cancel_futures=True)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _full_view(result):
    return Viewport((0, result.sample_count/result.parameters.sample_rate),
                    (0, result.parameters.sample_rate/2))


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
