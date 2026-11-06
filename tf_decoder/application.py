from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
import math
import os
import re
from pathlib import Path
from threading import RLock
import numpy as np
from scipy import signal


@dataclass(frozen=True)
class AnalysisParameters:
    sample_rate: float = 96000
    window_length: int = 1024
    overlap: int = 512
    fft_length: int = 1024
    filter_enabled: bool = False
    passband_low: float = 20000
    passband_high: float = 28000
    transition_width: float = 1000


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


@dataclass(frozen=True)
class BrowserEntry:
    path: Path
    relative_path: Path
    status: str = 'pending'
    message: str = '尚未分析'


@dataclass(frozen=True)
class BrowserState:
    root: Path | None = None
    entries: tuple[BrowserEntry, ...] = ()
    current_index: int | None = None
    message: str = ''

    @property
    def summary(self):
        return {status: sum(e.status == status for e in self.entries)
                for status in ('pending', 'computing', 'success', 'warning', 'failed')}


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
        self._browser = BrowserState()
        self._batch = 0
        self._views = {}
        self._parameters = AnalysisParameters()
        self._display_range = (0, 48000)
        self._configuration = 0

    @property
    def applied_parameters(self):
        with self._lock:
            return self._parameters

    def apply_parameters(self, parameters, *, displayed_frequency_range=None):
        if not isinstance(parameters, AnalysisParameters):
            raise TypeError('parameters must be AnalysisParameters')
        _validate_parameters(parameters)
        bounds = tuple(displayed_frequency_range) if displayed_frequency_range is not None else (0, parameters.sample_rate/2)
        if len(bounds) != 2 or not all(math.isfinite(v) for v in bounds) or not 0 <= bounds[0] < bounds[1] <= parameters.sample_rate/2:
            raise ValueError('显示频率范围必须在 0 Hz 至 Nyquist 频率之间且下限小于上限')
        with self._lock:
            changed = parameters != self._parameters
            self._parameters = parameters
            self._display_range = bounds
            self._views.clear()
            if changed:
                self._configuration += 1
                self._browser = replace(self._browser, entries=tuple(replace(e, status='pending', message='参数已更新，尚未分析') for e in self._browser.entries))
            if not changed and self._state.result is not None:
                self._state = replace(self._state, displayed_frequency_range=bounds,
                    viewport=Viewport(self._state.viewport.time_range, bounds))
            elif self._browser.current_index is not None:
                return self._request(self._browser.entries[self._browser.current_index].path)
            future = Future()
            future.set_result(self._state)
            return future

    @property
    def state(self):
        with self._lock:
            return self._state

    def open_file(self, path: str | Path) -> Future:
        path = Path(path).resolve()
        with self._lock:
            self._batch += 1
            self._views.clear()
            self._browser = BrowserState(path.parent, (BrowserEntry(path, Path(path.name)),), 0)
            return self._request(path)

    @property
    def browser(self):
        with self._lock:
            return self._browser

    def open_folder(self, path: str | Path) -> Future:
        path = Path(path).resolve()
        with self._lock:
            self._batch += 1
            self._generation += 1
            batch = self._batch
            self._views.clear()
            self._browser = BrowserState(path, message='正在扫描文件夹…')
            self._state = ApplicationState('scanning', path, message='正在扫描文件夹…')
        return self._executor.submit(self._scan_folder, path, batch)

    def _scan_folder(self, root, batch):
        errors = []
        paths = []
        try:
            if not root.is_dir():
                raise OSError('文件夹不存在或无法访问')
            for directory, _, names in os.walk(root, onerror=lambda error: errors.append(str(error))):
                paths.extend(Path(directory) / name for name in names if Path(name).suffix.lower() == '.raw')
        except OSError as error:
            errors.append(f'{root}: {error}')
        def natural(path):
            return tuple((0, int(part)) if part.isdigit() else (1, part.casefold())
                         for part in re.split(r'(\d+)', path.relative_to(root).as_posix()))
        paths.sort(key=lambda p: (natural(p), p.relative_to(root).as_posix()))
        message = '；'.join(errors)
        with self._lock:
            if batch != self._batch:
                return ApplicationState('idle', root, message='文件夹请求已过期')
            self._browser = BrowserState(root, tuple(BrowserEntry(p, p.relative_to(root)) for p in paths),
                                         0 if paths else None, message)
            if not paths:
                self._state = ApplicationState('failed' if errors else 'empty', root,
                    message=message or '文件夹及子文件夹中没有 RAW 采集文件')
                return self._state
            self._generation += 1
            generation = self._generation
            self._state = ApplicationState('computing', paths[0], message='正在读取并计算时频图…')
            self._mark_entry(paths[0], 'computing', self._state.message)
            parameters, bounds, configuration = self._parameters, self._display_range, self._configuration
        return self._analyse(paths[0], generation, batch, parameters, bounds, configuration)

    def select_file(self, index: int) -> Future:
        with self._lock:
            if not 0 <= index < len(self._browser.entries):
                raise IndexError('文件序号超出当前批次')
            self._browser = replace(self._browser, current_index=index)
            return self._request(self._browser.entries[index].path)

    def previous_file(self) -> Future:
        return self._step(-1)

    def next_file(self) -> Future:
        return self._step(1)

    def _step(self, delta):
        with self._lock:
            index = self._browser.current_index
            if index is None or not 0 <= index + delta < len(self._browser.entries):
                future = Future()
                future.set_result(self._state)
                return future
            return self.select_file(index + delta)

    def _request(self, path):
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._state = ApplicationState("computing", path, message="正在读取并计算时频图…")
            self._mark_entry(path, 'computing', self._state.message)
            return self._executor.submit(self._analyse, path, generation, self._batch,
                                         self._parameters, self._display_range, self._configuration)

    def _mark_entry(self, path, status, message):
        self._browser = replace(self._browser, entries=tuple(
            replace(e, status=status, message=message) if e.path == path else e
            for e in self._browser.entries))

    def _analyse(self, path, generation, batch, parameters, bounds, configuration):
        try:
            result = _compute(path, parameters)
            message = ('计算完成 · Chebyshev I 带通滤波' if parameters.filter_enabled else '计算完成 · 未滤波')
            if result.warnings:
                message += ' · ' + '；'.join(result.warnings)
            state = ApplicationState("ready", path, result, message, replace(_full_view(result), frequency_range=bounds), bounds)
        except (OSError, ValueError) as error:
            state = ApplicationState("failed", path, message=f"{path}: {error}")
        with self._lock:
            if batch == self._batch and configuration == self._configuration:
                self._mark_entry(path, 'failed' if state.status == 'failed' else
                                 ('warning' if result.warnings else 'success'), state.message)
            if generation == self._generation:
                if state.result is not None:
                    state = replace(state, viewport=self._views.get(path, state.viewport))
                self._state = state
        return state

    def set_viewport(self, viewport: Viewport) -> ApplicationState:
        """Select a rectangle without recomputing PSD or file-wide color levels."""
        if not isinstance(viewport, Viewport):
            raise TypeError("viewport must be a Viewport")
        with self._lock:
            if self._state.status != "ready":
                raise ValueError("请先打开有效的 RAW 采集文件")
            self._state = replace(self._state, viewport=viewport)
            self._views[self._state.path] = viewport
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
            return self.set_viewport(replace(_full_view(self._state.result), frequency_range=self._display_range))

    def close(self):
        self._executor.shutdown(wait=True, cancel_futures=True)

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
    if parameters.filter_enabled:
        low, high, width = parameters.passband_low, parameters.passband_high, parameters.transition_width
        sos = signal.iirdesign([low, high], [low-width, high+width], .5, 10,
                               fs=parameters.sample_rate, ftype='cheby1', output='sos')
        state = np.zeros((len(sos), 2))
        for start in range(0, len(volts), 8192):
            volts[start:start+8192], state = signal.sosfilt(sos, volts[start:start+8192], zi=state)
    n, overlap, nfft, fs = parameters.window_length, parameters.overlap, parameters.fft_length, parameters.sample_rate
    if len(volts) < n:
        raise ValueError('采集文件采样点少于窗长，无法形成完整时频窗')
    window = np.hanning(n + 2)[1:-1]
    segments = np.lib.stride_tricks.sliding_window_view(volts, n)[::n-overlap]
    spectrum = np.fft.rfft(segments * window, n=nfft, axis=1)
    psd = (np.abs(spectrum)**2 / (fs * np.dot(window, window))).T
    psd[1:-1 if nfft % 2 == 0 else None] *= 2
    db = 10 * np.log10(psd + np.finfo(np.float64).eps)
    floor = float(10 * np.log10(np.finfo(np.float64).eps))
    levels = (floor, max(float(db.max()), floor + 1) if not np.any(counts) else float(db.max()))
    times = (np.arange(segments.shape[0]) * (n-overlap) + n/2) / fs
    frequencies = np.fft.rfftfreq(nfft, 1/fs)
    for array in (times, frequencies, psd, db):
        array.flags.writeable = False
    warnings = (f"已跳过残缺尾块：{remainder} 字节",) if remainder else ()
    return AnalysisResult(path, parameters, len(volts), (float(volts.min()), float(volts.max())), times, frequencies, psd, db, levels, warnings)


def _validate_parameters(parameters):
    if not math.isfinite(parameters.sample_rate) or parameters.sample_rate <= 0:
        raise ValueError('采样率必须为有限正数（Hz）')
    for name, label in [('window_length', '窗长'), ('overlap', '重叠量'), ('fft_length', 'FFT 长度')]:
        value = getattr(parameters, name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f'{label}必须为整数')
    if parameters.window_length < 2:
        raise ValueError('窗长必须至少为 2')
    if not 0 <= parameters.overlap < parameters.window_length:
        raise ValueError('重叠量必须非负且小于窗长')
    if parameters.fft_length < parameters.window_length:
        raise ValueError('FFT 长度必须不小于窗长')
    if not isinstance(parameters.filter_enabled, bool):
        raise ValueError('滤波开关必须为布尔值')
    if parameters.filter_enabled:
        low, high, width = parameters.passband_low, parameters.passband_high, parameters.transition_width
        if not all(math.isfinite(v) for v in (low, high, width)) or not 0 < low-width < low < high < high+width < parameters.sample_rate/2:
            raise ValueError('滤波通阻带必须满足 0 < 下限−过渡带 < 下限 < 上限 < 上限+过渡带 < Nyquist 频率')
