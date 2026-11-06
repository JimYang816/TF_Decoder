import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from tf_decoder.gui import MainWindow
from test_application import raw_file


def test_parameter_edit_apply_validation_and_collapsible_areas(qtbot, tmp_path):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.open_file(raw_file(tmp_path, [2097152]))
    qtbot.waitUntil(lambda: window.current_result is not None)
    original = window.current_result
    assert not window.advanced_panel.isVisible()
    window.advanced_toggle.click()
    assert window.advanced_panel.isVisible()
    window.sample_rate_edit.setText('48000')
    window.display_high_edit.setText('20')
    window.filter_checkbox.setChecked(True)
    window.passband_low_edit.setText('5')
    window.passband_high_edit.setText('13')
    assert window.current_result is original
    window.apply_button.click()
    qtbot.waitUntil(lambda: window.current_result is not None and window.current_result.parameters.sample_rate == 48000)
    assert '48 kHz' in window.configuration_label.text()
    assert '带通 5–13 kHz' in window.configuration_label.text()
    assert window.current_result.parameters.filter_enabled
    assert window.application.state.displayed_frequency_range == (0, 20000)
    applied = window.current_result
    window.overlap_edit.setText('1024')
    window.apply_button.click()
    assert '重叠' in window.parameter_error.text()
    assert window.current_result is applied
    window.parameters_toggle.click()
    assert not window.parameter_dock.isVisible()
    window.close()
