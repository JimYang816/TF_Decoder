"""Read-only local workload; writes aggregate metrics, never RAW or source names.

Run from the checkout: python scripts/measure_browser.py SOURCE --output REPORT.json
Requires psutil in addition to the application dependencies.
"""
import argparse
import gc
import json
import os
import platform
from pathlib import Path
import statistics
import sys
from threading import Event, Thread
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import psutil
import PySide6
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from tf_decoder import Application
from tf_decoder.gui import MainWindow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    qt = QApplication([])
    application = Application()
    window = MainWindow(application)
    window.show()
    process = psutil.Process()
    stop = Event()
    rss = []
    def sample():
        while not stop.wait(.01):
            rss.append((time.perf_counter(), process.memory_info().rss))
    sampler = Thread(target=sample, daemon=True)
    sampler.start()
    beats = []
    timer = QTimer()
    timer.timeout.connect(lambda: beats.append(time.perf_counter()))
    timer.start(10)
    def pump_until(condition, timeout=120):
        deadline = time.perf_counter() + timeout
        while not condition():
            qt.processEvents()
            if time.perf_counter() > deadline:
                raise TimeoutError('GUI workload exceeded deadline')
            time.sleep(.001)
        qt.processEvents()
    def dwell(seconds):
        end = time.perf_counter() + seconds
        pump_until(lambda: time.perf_counter() >= end)
    def describe(start, heartbeat):
        elapsed = time.perf_counter() - start
        ticks = [start] + beats[heartbeat:] + [time.perf_counter()]
        return {'seconds': elapsed, 'heartbeat_count': len(beats)-heartbeat,
                'heartbeat_max_gap_ms': max(np.diff(ticks), default=0)*1000}
    paths = [p for p in args.source.rglob('*') if p.suffix.lower() == '.raw' and p.is_file()]
    largest = max(paths, key=lambda p: p.stat().st_size)
    start, heartbeat = time.perf_counter(), len(beats)
    window.open_file(largest)
    pump_until(lambda: window.current_result is not None)
    first = describe(start, heartbeat)
    assert window.current_result.sample_count == 16777216
    largest_shape = window.current_result.psd.shape
    largest_result_bytes = sum(a.nbytes for a in (window.current_result.psd,
        window.current_result.db, window.current_result.times, window.current_result.frequencies))
    start, heartbeat = time.perf_counter(), len(beats)
    window.open_folder(args.source)
    pump_until(lambda: window.current_result is not None or application.state.status == 'failed')
    folder_first = describe(start, heartbeat)
    entries = application.browser.entries
    valid = [i for i,e in enumerate(entries) if e.path.stat().st_size]
    max_index = next(i for i,e in enumerate(entries) if e.path == largest)
    chosen = valid[:100]
    if max_index not in chosen:
        chosen[-1] = max_index
    def select(index):
        start, heartbeat = time.perf_counter(), len(beats)
        if window.file_list.currentRow() == index:
            # Move away before measuring a real switch back.
            return None
        window.file_list.setCurrentRow(index)
        pump_until(lambda: window.current_result is not None and window.current_result.path == entries[index].path)
        return describe(start, heartbeat)
    measures = []
    signatures = {}
    start = time.perf_counter()
    workload_beat = len(beats)
    for n,index in enumerate(chosen):
        measured = select(index)
        if measured:
            measures.append(measured)
        result = window.current_result
        signatures[index] = (result.sample_count, result.levels, result.voltage_range,
                             float(result.psd[0,0]), float(result.psd[-1,-1]))
        del result
        if (n+1) % 10 == 0:
            print(f'visited {n+1}/100, RSS {process.memory_info().rss / 1024**2:.1f} MiB', flush=True)
    workload = describe(start, workload_beat)
    revisit = []
    for index in chosen[:3]:
        measured = select(index)
        if measured:
            revisit.append(measured)
        result = window.current_result
        assert signatures[index] == (result.sample_count, result.levels, result.voltage_range,
                                      float(result.psd[0,0]), float(result.psd[-1,-1]))
        del result
    # Allow adjacent prefetch to finish, then measure a real switch to that neighbor.
    current = application.browser.current_index
    target = current+1 if current+1 < len(entries) else current-1
    dwell(2)
    cached = select(target)
    zoom = []
    for _ in range(20):
        start = time.perf_counter()
        box = window.plot.getViewBox()
        box.scaleBy((.95,.95))
        qt.processEvents()
        window.graphics.grab()  # force an actual image paint, not just a state update
        zoom.append((time.perf_counter()-start)*1000)
    dwell(2)
    gc.collect()
    stable_rss = process.memory_info().rss
    report = {
        'platform': platform.platform(), 'processor': platform.processor(),
        'logical_cpus': os.cpu_count(), 'ram_bytes': psutil.virtual_memory().total,
        'python': platform.python_version(), 'qt': PySide6.__version__,
        'numpy': np.__version__, 'qt_platform': os.environ['QT_QPA_PLATFORM'],
        'cache_budget_bytes': 384*1024**2, 'parameters': vars(application.applied_parameters),
        'source_raw_count': len(paths), 'empty_raw_count': sum(p.stat().st_size==0 for p in paths),
        'workload_nonempty_files': len(chosen), 'largest_raw_bytes': largest.stat().st_size,
        'largest_matrix_shape': largest_shape, 'largest_result_bytes': largest_result_bytes,
        'largest_first_display': first, 'folder_first_display': folder_first,
        'workload': workload, 'switch_median_seconds': statistics.median(m['seconds'] for m in measures),
        'switch_max_seconds': max(m['seconds'] for m in measures),
        'revisit_after_eviction': revisit, 'adjacent_switch_after_2s_prefetch_dwell': cached,
        'zoom_paint_median_ms': statistics.median(zoom), 'zoom_paint_max_ms': max(zoom),
        'peak_rss_bytes': max(r[1] for r in rss), 'settled_rss_bytes': stable_rss,
        'rss_sample_interval_ms': 10,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)
    timer.stop()
    stop.set()
    sampler.join()
    window.close()


if __name__ == '__main__':
    main()
