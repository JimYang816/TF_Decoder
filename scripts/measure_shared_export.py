"""Read-only RAW overlap benchmark; PNGs go only to the explicit output folder."""
import argparse
import json
import os
from pathlib import Path
import sys
from threading import Event, Thread
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import psutil
from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from tf_decoder import Application, AnalysisParameters
from tf_decoder.gui import MainWindow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('--png-output', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    qt = QApplication([])
    app = Application()
    window = MainWindow(app)
    window.show()
    def wait(condition):
        deadline = time.perf_counter()+120
        while not condition():
            qt.processEvents()
            time.sleep(.001)
            if time.perf_counter() > deadline:
                raise TimeoutError('overlap workload timed out')
        qt.processEvents()
    window.open_folder(args.source)
    wait(lambda: window.current_result is not None)
    largest = max(app.browser.entries, key=lambda e: e.path.stat().st_size).path
    beats, memory = [], []
    timer = QTimer()
    timer.timeout.connect(lambda: beats.append(time.perf_counter()))
    timer.start(10)
    stop = Event()
    def sample():
        process = psutil.Process()
        while not stop.wait(.01):
            memory.append(process.memory_info().rss)
    thread = Thread(target=sample, daemon=True)
    thread.start()
    def progress(state):
        if state.completed >= 3:
            app.cancel_batch_export()
    start = time.perf_counter()
    batch = app.export_batch_png(args.png_output, progress=progress)
    parameters = app.apply_parameters(AnalysisParameters(sample_rate=48000))
    wait(parameters.done)
    changed = parameters.result()
    assert changed.result.parameters.sample_rate == 48000
    window.open_file(largest)
    wait(lambda: window.current_result is not None and window.current_result.path == largest)
    foreground_seconds = time.perf_counter()-start
    assert window.current_result.parameters.sample_rate == 48000
    assert window.current_result.frequencies[-1] == 24000
    wait(batch.done)
    finished = time.perf_counter()
    result = batch.result()
    assert result.status == 'cancelled'
    assert result.completed == 3
    for entry in result.entries[:3]:
        assert entry.status in ('success', 'warning')
        image = QImage(str(entry.output))
        assert not image.isNull()
        assert 'sample_rate=96000' in image.text('Analysis parameters')
        assert image.text('Frequency range (Hz)') == '0, 48000'
    assert app.state.result.parameters.sample_rate == 48000
    timer.stop()
    stop.set()
    thread.join()
    ticks = [start]+beats+[finished]
    report = {'seconds': finished-start, 'foreground_largest_with_new_parameters_seconds': foreground_seconds,
              'heartbeat_count': len(beats),
              'heartbeat_max_gap_ms': max(b-a for a,b in zip(ticks,ticks[1:]))*1000,
              'peak_rss_bytes': max(memory), 'settled_rss_bytes': psutil.Process().memory_info().rss,
              'exported_files': result.completed, 'export_status': result.status,
              'snapshot_sample_rate': 96000, 'foreground_sample_rate': 48000,
              'snapshot_frequency_range': [0,48000],
              'remaining_uncompleted': result.summary['uncompleted'],
              'png_metadata_and_current_configuration_verified': True}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    window.close()


if __name__ == '__main__':
    main()
