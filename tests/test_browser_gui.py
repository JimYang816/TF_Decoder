import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QLineEdit
from tf_decoder.gui import MainWindow
from test_application import raw_file


def test_folder_list_navigation_keyboard_and_view_memory(qtbot, tmp_path, monkeypatch):
    first = raw_file(tmp_path, [0], '1.raw')
    second = raw_file(tmp_path, [1], '2.RAW')
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *args: str(tmp_path))
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.folder_button.click()
    qtbot.waitUntil(lambda: window.current_result is not None)
    assert window.file_list.count() == 2
    assert not window.previous_button.isEnabled()
    window.plot.setRange(xRange=(0.01, 0.03), yRange=(2, 8), padding=0)
    remembered = window.application.state.viewport
    window.next_button.click()
    qtbot.waitUntil(lambda: window.current_result is not None and window.current_result.path == second)
    assert not window.next_button.isEnabled()
    window.file_list.setCurrentRow(0)
    qtbot.waitUntil(lambda: window.current_result is not None and window.current_result.path == first)
    np.testing.assert_allclose(window.plot.viewRange(), [remembered.time_range, [2, 8]])
    editor = QLineEdit(window)
    editor.show()
    editor.setText('123')
    editor.setFocus()
    qtbot.keyClick(editor, Qt.Key_Right)
    assert window.application.browser.current_index == 0
    window.next_button.setFocus()
    qtbot.keyClick(window.next_button, Qt.Key_Right)
    qtbot.waitUntil(lambda: window.current_result is not None and window.current_result.path == second)
    window.list_toggle.click()
    assert not window.file_dock.isVisible()
    window.close()
