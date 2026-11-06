from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
import math
import os
import re
from pathlib import Path
from threading import Event, RLock
from collections import OrderedDict
from .scheduling import AnalysisExecutor
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


@dataclass(frozen=True)
class BatchExportEntry:
    path: Path
    relative_path: Path
    status: str = 'pending'
    output: Path | None = None
    message: str = '尚未导出'


@dataclass(frozen=True)
class BatchExportState:
    status: str = 'idle'
    entries: tuple[BatchExportEntry, ...] = ()
    parameters: AnalysisParameters | None = None
    displayed_frequency_range: tuple[float, float] | None = None
    message: str = '尚未批量导出'

    @property
    def completed(self):
        return sum(e.status in ('success', 'warning', 'failed') for e in self.entries)

    @property
    def summary(self):
        return {status: sum(e.status == status for e in self.entries)
                for status in ('success', 'warning', 'failed', 'uncompleted')}


class Application:
    """Asynchronous, Qt-independent entry point. Future results are state snapshots.

    Only the most recent request may update ``state``. Results and arrays are
    immutable; callbacks run on the worker and UI clients must marshal to Qt.
    """
    def __init__(self, *, cache_budget_bytes=384 * 1024**2):
        if isinstance(cache_budget_bytes, bool) or not isinstance(cache_budget_bytes, int) or cache_budget_bytes < 0:
            raise ValueError("缓存预算必须为非负整数字节数")
        self._executor = AnalysisExecutor()
        self._cache_budget = cache_budget_bytes
        self._cache = OrderedDict()
        self._cache_bytes = 0
        self._closed = False
        self._lock = RLock()
        self._generation = 0
        self._state = ApplicationState()
        self._export_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="png-export")
        from .export import ExportResult
        self._export_status = ExportResult("idle", message="尚未导出")
        self._browser = BrowserState()
        self._batch = 0
        self._views = {}
        self._parameters = AnalysisParameters()
        self._display_range = (0, 48000)
        self._configuration = 0
        self._batch_export_status = BatchExportState()
        self._batch_cancel = Event()

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
            display_changed = bounds != self._display_range
            self._parameters = parameters
            self._display_range = bounds
            if changed:
                self._views.clear()
                self._configuration += 1
                self._clear_cache()
                self._browser = replace(self._browser, entries=tuple(replace(e, status='pending', message='参数已更新，尚未分析') for e in self._browser.entries))
            elif display_changed:
                self._views = {path: _clip_frequency(view, bounds) for path, view in self._views.items()}
            if not changed and self._state.result is not None:
                if display_changed:
                    view = _clip_frequency(self._state.viewport, bounds)
                    self._views[self._state.path] = view
                    self._state = replace(self._state, displayed_frequency_range=bounds, viewport=view)
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
            self._clear_cache()
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
            self._clear_cache()
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
            try:
                cached = self._cached(self._cache_key(path, self._parameters))
            except OSError:
                cached = None
            if cached is not None:
                # A ready result never queues behind an in-flight speculative read.
                future = Future()
                future.set_result(self._analyse(path, generation, self._batch,
                    self._parameters, self._display_range, self._configuration, cached))
                return future
            return self._executor.submit(self._analyse, path, generation, self._batch,
                                         self._parameters, self._display_range, self._configuration)

    def _mark_entry(self, path, status, message):
        self._browser = replace(self._browser, entries=tuple(
            replace(e, status=status, message=message) if e.path == path else e
            for e in self._browser.entries))

    def _analyse(self, path, generation, batch, parameters, bounds, configuration, result=None):
        try:
            if result is None:
                key = self._cache_key(path, parameters)
                with self._lock:
                    result = self._cached(key)
            if result is None:
                result = _compute(path, parameters)
                if key != self._cache_key(path, parameters):
                    raise OSError("采集文件在计算期间发生变化，请重新打开")
                with self._lock:
                    if (generation, batch, configuration) == (self._generation, self._batch, self._configuration):
                        self._remember(key, result)
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
                    view = self._views.get(path, replace(state.viewport, frequency_range=self._display_range))
                    state = replace(state, viewport=view, displayed_frequency_range=self._display_range)
                self._state = state
                if not self._closed:
                    self._prefetch_neighbors(generation, batch, parameters, configuration)
        return state

    def _clear_cache(self):
        self._cache.clear()
        self._cache_bytes = 0

    @staticmethod
    def _cache_key(path, parameters):
        stat = path.stat()
        return (path, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, parameters)

    def _cached(self, key):
        result = self._cache.get(key)
        if result is not None:
            self._cache.move_to_end(key)
        return result

    @staticmethod
    def _result_bytes(result):
        return sum(a.nbytes for a in (result.times, result.frequencies, result.psd, result.db))

    def _remember(self, key, result):
        size = self._result_bytes(result)
        if size > self._cache_budget:
            return
        if key in self._cache:
            self._cache_bytes -= self._result_bytes(self._cache.pop(key))
        while self._cache and self._cache_bytes + size > self._cache_budget:
            _, old = self._cache.popitem(last=False)
            self._cache_bytes -= self._result_bytes(old)
        self._cache[key] = result
        self._cache_bytes += size

    def _prefetch_neighbors(self, generation, batch, parameters, configuration):
        index = self._browser.current_index
        if index is None or self._cache_budget == 0:
            return
        for neighbor in (index + 1, index - 1):
            if 0 <= neighbor < len(self._browser.entries):
                path = self._browser.entries[neighbor].path
                self._executor.submit(self._prefetch, path, generation, batch, parameters,
                                      configuration, speculative=True)

    def _prefetch(self, path, generation, batch, parameters, configuration):
        with self._lock:
            if self._closed or (generation, batch, configuration) != (self._generation, self._batch, self._configuration):
                return
        try:
            key = self._cache_key(path, parameters)
            with self._lock:
                if self._cached(key) is not None:
                    return
            result = _compute(path, parameters)
            if key != self._cache_key(path, parameters):
                return
            with self._lock:
                if not self._closed and (generation, batch, configuration) == (self._generation, self._batch, self._configuration):
                    self._remember(key, result)
        except (OSError, ValueError):
            # Speculative errors are reported only when this file is requested.
            pass

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
            relative_parent = self._browser.entries[self._browser.current_index].relative_path.parent
            destination = Path(directory).resolve() / relative_parent
        def work():
            try:
                destination.mkdir(parents=True, exist_ok=True)
                exported = write_png(state.result, view, destination, mode)
            except OSError as error:
                exported = ExportResult('failed', message=f'{destination}: {error}')
            with self._lock:
                self._export_status = exported
            return exported
        return self._export_executor.submit(work)

    @property
    def batch_export_status(self):
        with self._lock:
            return self._batch_export_status

    def cancel_batch_export(self):
        """Stop at the next safe boundary; a PNG already writing completes intact."""
        with self._lock:
            self._batch_cancel.set()

    def export_batch_png(self, directory: str | Path, *, progress=None) -> Future:
        """Sequential full-time PNG export of the current entries/configuration snapshot.

        ``progress(snapshot)`` runs on the export worker, before advancing to the
        next file, so observers can deterministically cancel at file boundaries.
        Marshal widget changes to the Qt thread. Callback errors are ignored.
        """
        from .export import write_png
        with self._lock:
            if self._batch_export_status.status == 'exporting':
                raise ValueError('批量导出正在进行')
            entries = tuple(BatchExportEntry(e.path, e.relative_path) for e in self._browser.entries)
            snapshot = BatchExportState('exporting', entries, self._parameters, self._display_range,
                                        '正在批量导出 PNG…')
            self._batch_export_status = snapshot
            cancel = self._batch_cancel = Event()
        directory = Path(directory).resolve()

        def publish(state):
            with self._lock:
                self._batch_export_status = state
            if progress is not None:
                try:
                    progress(state)
                except Exception:
                    # An observer must not interrupt output or leave the job active.
                    pass

        def work():
            state = snapshot
            publish(state)
            for index, entry in enumerate(snapshot.entries):
                if cancel.is_set():
                    break
                items = list(state.entries)
                items[index] = replace(entry, status='processing', message='正在分析并导出…')
                state = replace(state, entries=tuple(items))
                publish(state)
                if cancel.is_set():
                    break
                destination = directory / entry.relative_path.parent
                result = None
                try:
                    result = _compute(entry.path, snapshot.parameters)
                    if cancel.is_set():
                        break
                    destination.mkdir(parents=True, exist_ok=True)
                    view = replace(_full_view(result), frequency_range=snapshot.displayed_frequency_range)
                    exported = write_png(result, view, destination, 'full')
                    if exported.status == 'failed':
                        raise OSError(exported.message)
                    items[index] = replace(entry, status='warning' if result.warnings else 'success',
                        output=exported.path, message='；'.join(result.warnings) or exported.message)
                except Exception as error:
                    items[index] = replace(entry, status='failed', message=f'{entry.path} → {destination}: {error}')
                finally:
                    result = None  # Release this matrix before starting the next file.
                state = replace(state, entries=tuple(items))
                publish(state)
            cancelled = cancel.is_set() and state.completed < len(state.entries)
            entries = tuple(replace(e, status='uncompleted', message='已取消，未完成')
                            if e.status in ('pending', 'processing') else e for e in state.entries)
            state = replace(state, status='cancelled' if cancelled else ('completed' if entries else 'failed'), entries=entries)
            counts = state.summary
            message = ('批量导出已取消' if cancelled else '批量导出完成') + (
                f" · 成功 {counts['success']} · 警告 {counts['warning']} · 失败 {counts['failed']} · 未完成 {counts['uncompleted']}")
            state = replace(state, message=message if entries else '没有可导出的采集文件')
            publish(state)
            return state
        return self._export_executor.submit(work)

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
        with self._lock:
            self._closed = True
        self.cancel_batch_export()
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._export_executor.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            self._clear_cache()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _full_view(result):
    return Viewport((0, result.sample_count/result.parameters.sample_rate),
                    (0, result.parameters.sample_rate/2))


def _clip_frequency(view, bounds):
    low = max(view.frequency_range[0], bounds[0])
    high = min(view.frequency_range[1], bounds[1])
    return replace(view, frequency_range=(low, high) if low < high else bounds)


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
    nonzero = bool(np.any(counts))
    del counts, records, data
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
    # Bound FFT workspace independently of the number of complete windows.
    psd = np.empty((nfft // 2 + 1, segments.shape[0]), dtype=np.float64)
    scale = fs * np.dot(window, window)
    for start in range(0, segments.shape[0], 256):
        spectrum = np.fft.rfft(segments[start:start+256] * window, n=nfft, axis=1)
        power = np.abs(spectrum)
        np.square(power, out=power)
        power /= scale
        psd[:, start:start+256] = power.T
    del spectrum, power
    psd[1:-1 if nfft % 2 == 0 else None] *= 2
    db = psd.copy()
    db += np.finfo(np.float64).eps
    np.log10(db, out=db)
    db *= 10
    floor = float(10 * np.log10(np.finfo(np.float64).eps))
    levels = (floor, max(float(db.max()), floor + 1) if not nonzero else float(db.max()))
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
