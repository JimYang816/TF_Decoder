import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from threading import Event
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog
from tf_decoder import Application
from tf_decoder.gui import MainWindow
from test_application import raw_file


def test_batch_progress_cancel_and_details(qtbot, tmp_path, monkeypatch):
    for number in range(3):
        raw_file(tmp_path, [0], name=f'{number}.raw')
    app = Application()
    app.open_folder(tmp_path).result(timeout=10)
    window = MainWindow(app)
    qtbot.addWidget(window)
    window.show()
    window._finished(app.state)
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *args: str(tmp_path / 'output'))
    # Public progress callback gates the real worker while Qt receives progress
    # and the user clicks Cancel; no timing sleep or internal collaborator mock.
    reached, release = Event(), Event()
    def observer(state):
        window._completion.batch_progress.emit(state)
        if state.completed == 1 and state.status == 'exporting':
            reached.set()
            release.wait(timeout=10)
    future = app.export_batch_png(tmp_path / 'output', progress=observer)
    try:
        qtbot.waitUntil(reached.is_set)
        qtbot.waitUntil(lambda: window.batch_progress.value() == 1)
        assert window.cancel_export_button.isEnabled()
        qtbot.mouseClick(window.cancel_export_button, Qt.LeftButton)
    finally:
        release.set()
    result = future.result(timeout=15)
    qtbot.waitUntil(lambda: '未完成 2' in window.batch_status_label.text())
    assert result.summary['success'] == 1
    assert not window.cancel_export_button.isEnabled()
    qtbot.mouseClick(window.export_details_button, Qt.LeftButton)
    assert window.export_details.isVisible()
    assert '0.raw' in window.export_details.toPlainText()
    assert '未完成' in window.export_details.toPlainText()
    # The menu also connects batch start to the same real Application entry.
    window.export_button.menu().actions()[2].trigger()
    qtbot.waitUntil(lambda: app.batch_export_status.status == 'completed')
    qtbot.waitUntil(lambda: '成功 3' in window.batch_status_label.text())
    window.close()
