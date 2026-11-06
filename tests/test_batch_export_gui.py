import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from threading import Event
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog
from tf_decoder.gui import MainWindow
from test_application import raw_file


def test_batch_progress_cancel_and_details(qtbot, tmp_path, monkeypatch):
    for number in range(3):
        raw_file(tmp_path, [0], name=f'{number}.raw')
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *args: str(tmp_path))
    qtbot.mouseClick(window.folder_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: window.current_result is not None)
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *args: str(tmp_path / 'output'))
    # Gate an OS read for the second file; all start/progress/cancel wiring is real.
    reached, release = Event(), Event()
    original_open = Path.open
    def gated_open(path, *args, **kwargs):
        if path == tmp_path / '1.raw':
            reached.set()
            assert release.wait(10)
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', gated_open)
    window.export_button.menu().actions()[2].trigger()
    try:
        qtbot.waitUntil(reached.is_set)
        qtbot.waitUntil(lambda: window.batch_progress.value() == 1)
        assert window.cancel_export_button.isEnabled()
        qtbot.mouseClick(window.cancel_export_button, Qt.LeftButton)
    finally:
        release.set()
    qtbot.waitUntil(lambda: '未完成 2' in window.batch_status_label.text())
    assert window.application.batch_export_status.summary['success'] == 1
    assert (tmp_path / 'output' / '0.png').exists()
    assert not (tmp_path / 'output' / '1.png').exists()
    assert not window.cancel_export_button.isEnabled()
    qtbot.mouseClick(window.export_details_button, Qt.LeftButton)
    assert window.export_details.isVisible()
    assert '0.raw' in window.export_details.toPlainText()
    assert '未完成' in window.export_details.toPlainText()
    # The menu also connects batch start to the same real Application entry.
    window.export_button.menu().actions()[2].trigger()
    qtbot.waitUntil(lambda: window.application.batch_export_status.status == 'completed')
    qtbot.waitUntil(lambda: '成功 3' in window.batch_status_label.text())
    window.close()
