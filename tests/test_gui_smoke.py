"""Small GUI wiring smoke; numerical coverage remains at Application seam."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog
from tf_decoder.gui import MainWindow
from test_application import raw_file


def test_open_dialog_button_reaches_plot_without_blocking_qt(tmp_path, monkeypatch):
    qt = QApplication.instance() or QApplication([])
    path = raw_file(tmp_path, [2097152])
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), "RAW (*.raw)"))
    window = MainWindow()
    window.show()
    window.open_button.click()
    assert window.application.state.status in ("computing", "ready")
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: loop.quit() if window.current_result is not None else None)
    timer.start()
    QTimer.singleShot(10000, loop.quit)
    loop.exec()
    assert window.current_result is not None
    assert window.current_result.path == path.resolve()
    assert window.plot.getAxis("bottom").labelText == "时间"
    assert window.plot.getAxis("left").labelText == "频率"
    assert "采集.raw" in window.file_title.text()
    assert "计算完成" in window.status_label.text()
    window.close()
