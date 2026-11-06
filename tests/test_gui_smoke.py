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


def test_pointer_zoom_pan_rectangle_and_keyboard_restore(qtbot, tmp_path):
    from PySide6.QtCore import QPoint, QPointF, Qt, QEvent
    from PySide6.QtGui import QWheelEvent, QMouseEvent
    import numpy as np

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.open_file(raw_file(tmp_path, [2097152]))
    qtbot.waitUntil(lambda: window.current_result is not None)
    full = window.application.state
    assert window.pan_button.isChecked()
    assert not window.rectangle_button.isChecked()
    canvas = window.graphics.viewport()
    box = window.plot.getViewBox()

    def point(x_fraction, y_fraction):
        rect = box.sceneBoundingRect()
        return window.graphics.mapFromScene(QPointF(rect.left()+rect.width()*x_fraction,
                                                   rect.top()+rect.height()*y_fraction))

    pointer = point(0.3, 0.6)
    coordinate = box.mapSceneToView(window.graphics.mapToScene(pointer))
    event = QWheelEvent(QPointF(pointer), QPointF(canvas.mapToGlobal(pointer)), QPoint(),
                        QPoint(0, 120), Qt.NoButton, Qt.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(canvas, event)
    zoomed = window.application.state.viewport
    assert zoomed.time_range[1]-zoomed.time_range[0] < full.viewport.time_range[1]
    after = box.mapSceneToView(window.graphics.mapToScene(pointer))
    np.testing.assert_allclose([after.x(), after.y()], [coordinate.x(), coordinate.y()], rtol=1e-8)

    def drag(start, finish):
        qtbot.mousePress(canvas, Qt.LeftButton, pos=start)
        for fraction in (0.25, 0.5, 0.75, 1):
            pos = start + (finish-start)*fraction
            QApplication.sendEvent(canvas, QMouseEvent(QEvent.MouseMove, QPointF(pos),
                QPointF(canvas.mapToGlobal(pos)), Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
        qtbot.mouseRelease(canvas, Qt.LeftButton, pos=finish)

    drag(point(0.45, 0.45), point(0.6, 0.6))
    panned = window.application.state.viewport
    assert panned != zoomed
    np.testing.assert_allclose(np.diff(panned.time_range), np.diff(zoomed.time_range))
    window.rectangle_button.setFocus()
    qtbot.keyClick(window.rectangle_button, Qt.Key_Space)
    assert window.rectangle_button.isChecked() and not window.pan_button.isChecked()
    drag(point(0.25, 0.25), point(0.75, 0.75))
    selected = window.application.state
    assert np.diff(selected.viewport.time_range)[0] < np.diff(panned.time_range)[0]*0.6
    assert selected.result is full.result
    assert tuple(window.image.levels) == full.result.levels
    for name in ("bottom", "left"):
        assert not window.plot.getAxis(name).autoSIPrefix
    window.reset_button.setFocus()
    qtbot.keyClick(window.reset_button, Qt.Key_Space)
    assert window.application.state.viewport == full.viewport
    assert window.rectangle_button.isChecked()
    visible = box.viewRange()
    np.testing.assert_allclose(visible, [full.viewport.time_range, [0, 48]])
    window.close()
