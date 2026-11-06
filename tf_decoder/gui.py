"""Qt presentation adapter. All numeric work goes through Application."""
import sys
from pathlib import Path
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QObject, Signal, Qt, QRectF, QEvent, QTimer
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QMainWindow, QProgressBar, QPushButton, QVBoxLayout, QWidget,
    QDockWidget, QListWidget, QListWidgetItem, QLineEdit, QAbstractSpinBox,
    QTextEdit, QPlainTextEdit, QComboBox, QAbstractSlider)
from .application import Application, Viewport


class _Completion(QObject):
    ready = Signal(object)


class MainWindow(QMainWindow):
    def __init__(self, application=None):
        super().__init__()
        self.application = application or Application()
        self.current_result = None
        self._completion = _Completion(self)
        self._completion.ready.connect(self._finished)
        self.setWindowTitle("TF Decoder · RAW 时频分析")
        self.resize(1200, 780)
        self.setMinimumSize(720, 480)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(16)
        top = QHBoxLayout()
        title = QLabel("RAW 时频分析")
        title.setObjectName("heading")
        top.addWidget(title)
        top.addStretch()
        self.open_button = QPushButton("打开 RAW 文件…")
        self.open_button.setObjectName("primary")
        self.open_button.setAccessibleName("打开 RAW 采集文件")
        self.open_button.clicked.connect(self.choose_file)
        top.addWidget(self.open_button)
        self.folder_button = QPushButton('打开文件夹…')
        self.folder_button.clicked.connect(self.choose_folder)
        top.addWidget(self.folder_button)
        self.list_toggle = QPushButton('文件列表')
        self.list_toggle.setCheckable(True)
        self.list_toggle.setChecked(True)
        top.addWidget(self.list_toggle)
        self.file_dock = QDockWidget('采集文件', self)
        self.file_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.file_list = QListWidget()
        self.file_list.setAccessibleName('当前批次采集文件列表')
        self.file_list.setMinimumWidth(200)
        self.file_dock.setWidget(self.file_list)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.file_dock)
        self.resizeDocks([self.file_dock], [240], Qt.Horizontal)
        self.list_toggle.toggled.connect(self.file_dock.setVisible)
        self.file_dock.visibilityChanged.connect(self.list_toggle.setChecked)
        self.file_list.currentRowChanged.connect(self._select_row)
        layout.addLayout(top)
        self.file_title = QLabel("尚未打开采集文件")
        self.file_title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.file_title.setWordWrap(True)
        layout.addWidget(self.file_title)
        info = QHBoxLayout()
        self.configuration_label = QLabel("未滤波 · 96 kHz · 窗长 1024 · 重叠 512 · FFT 1024")
        info.addWidget(self.configuration_label)
        info.addStretch()
        layout.addLayout(info)
        tools = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        self.pan_button = QPushButton("平移 (&P)")
        self.rectangle_button = QPushButton("框选放大 (&R)")
        for button, tooltip in (
            (self.pan_button, "平移模式：按住左键拖动；滚轮围绕指针缩放（Alt+P）"),
            (self.rectangle_button, "框选放大模式：左键拖出矩形放大；滚轮仍围绕指针缩放（Alt+R）"),
        ):
            button.setCheckable(True)
            button.setEnabled(False)
            button.setToolTip(tooltip)
            self.mode_group.addButton(button)
            tools.addWidget(button)
        self.pan_button.setChecked(True)
        self.pan_button.toggled.connect(lambda checked: self._set_drag_mode(False) if checked else None)
        self.rectangle_button.toggled.connect(lambda checked: self._set_drag_mode(True) if checked else None)
        tools.addStretch()
        self.reset_button = QPushButton("恢复全图")
        self.reset_button.setToolTip("恢复当前文件全时段和适用频率范围（Alt+F）；保留当前工具模式")
        self.reset_button.setShortcut(QKeySequence("Alt+F"))
        self.reset_button.setEnabled(False)
        self.reset_button.clicked.connect(self.show_full_range)
        tools.addWidget(self.reset_button)
        layout.addLayout(tools)
        pg.setConfigOptions(background="w", foreground="#172033", antialias=False)
        self.graphics = pg.GraphicsLayoutWidget()
        self.plot = self.graphics.addPlot()
        self.plot.setLabel("bottom", "时间", units="s")
        self.plot.setLabel("left", "频率", units="kHz")
        self.plot.getAxis("bottom").enableAutoSIPrefix(False)
        self.plot.getAxis("left").enableAutoSIPrefix(False)
        self.plot.hideButtons()
        self.plot.getViewBox().setMouseMode(pg.ViewBox.PanMode)
        self.plot.getViewBox().setMouseEnabled(False, False)
        self.plot.getViewBox().sigRangeChanged.connect(self._viewport_changed)
        self._set_drag_mode(False)
        self.plot.setMenuEnabled(False)
        self.plot.showGrid(x=True, y=True, alpha=0.12)
        self.image = pg.ImageItem(axisOrder="row-major")
        self.plot.addItem(self.image)
        colors = np.loadtxt(Path(__file__).with_name("parula256.csv"), delimiter=",")
        self.color_map = pg.ColorMap(np.linspace(0, 1, 256), np.rint(colors*255).astype(np.uint8))
        self.color_bar = pg.ColorBarItem(values=(-156.5356, -155.5356), colorMap=self.color_map,
                                         label="PSD (dB re 1 V²/Hz)", interactive=False)
        self.color_bar.setImageItem(self.image, insert_in=self.plot)
        self.graphics.setAccessibleName("时频图，横轴时间秒，纵轴频率 kHz，颜色为功率谱密度 dB")
        layout.addWidget(self.graphics, 1)
        navigation = QHBoxLayout()
        self.previous_button = QPushButton('上一张')
        self.next_button = QPushButton('下一张')
        self.position_label = QLabel('0 / 0')
        self.previous_button.clicked.connect(lambda: self._browse(self.application.previous_file))
        self.next_button.clicked.connect(lambda: self._browse(self.application.next_file))
        navigation.addStretch()
        navigation.addWidget(self.previous_button)
        navigation.addWidget(self.position_label)
        navigation.addWidget(self.next_button)
        navigation.addStretch()
        layout.addLayout(navigation)
        self.summary_label = QLabel('尚未导入文件')
        layout.addWidget(self.summary_label)
        self.status_label = QLabel("请选择 RAW 采集文件。默认显示未滤波信号的全时段时频图。")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status_label)
        self.progress = QProgressBar()
        self.progress.setAccessibleName("时频图计算进度")
        self.progress.setMaximumHeight(6)
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.setStyleSheet("""
            QWidget { background:#F5F7FA; color:#172033; font-family:'Microsoft YaHei UI'; font-size:14px; }
            QLabel#heading { font-size:22px; font-weight:600; }
            QPushButton { background:white; border:1px solid #526174; border-radius:6px; padding:8px 16px; min-height:22px; }
            QPushButton:hover { background:#EFF6FF; }
            QPushButton:pressed { background:#D6DEE8; }
            QPushButton:checked { background:#EFF6FF; color:#1E40AF; border-color:#1E40AF; }
            QPushButton:focus { border:2px solid #1E40AF; padding:7px 15px; }
            QPushButton:disabled { color:#526174; border-color:#D6DEE8; }
            QPushButton#primary { background:#2563EB; color:white; border-color:#2563EB; }
            QPushButton#primary:hover { background:#1E40AF; }
            QPushButton#primary:focus { border:2px solid #172033; }
            QProgressBar::chunk { background:#2563EB; }
            QListWidget { background:white; border:1px solid #D6DEE8; }
            QListWidget::item { padding:8px; border-bottom:1px solid #D6DEE8; }
            QListWidget::item:selected { background:#EFF6FF; color:#1E40AF; }
            QListWidget:focus { border:2px solid #1E40AF; }
        """)
        self.open_shortcut = QShortcut(QKeySequence.Open, self)
        self.open_shortcut.activated.connect(self.choose_file)
        self._browser_snapshot = None
        self._browser_timer = QTimer(self)
        self._browser_timer.timeout.connect(self._refresh_browser)
        self._browser_timer.start(100)
        QApplication.instance().installEventFilter(self)
        self._refresh_browser()

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, '打开 RAW 采集文件夹')
        if path:
            self.open_folder(path)

    def open_folder(self, path):
        self._browse(lambda: self.application.open_folder(path))

    def _select_row(self, row):
        if row >= 0 and row != self.application.browser.current_index:
            self._browse(lambda: self.application.select_file(row))

    def _browse(self, request):
        future = request()
        self._show_pending()
        self._refresh_browser()
        future.add_done_callback(lambda task: self._completion.ready.emit(task.result()))

    def _refresh_browser(self):
        browser = self.application.browser
        if browser == self._browser_snapshot:
            return
        self._browser_snapshot = browser
        self.file_list.blockSignals(True)
        self.file_list.clear()
        names = {'pending': '尚未分析', 'computing': '计算中', 'success': '成功', 'warning': '警告', 'failed': '失败'}
        for index, entry in enumerate(browser.entries):
            item = QListWidgetItem(f'{index+1}. {entry.path.name}\n{entry.relative_path.parent} · {names[entry.status]}')
            item.setToolTip(f'{entry.relative_path}\n{entry.message}')
            self.file_list.addItem(item)
        if browser.current_index is not None:
            self.file_list.setCurrentRow(browser.current_index)
        self.file_list.blockSignals(False)
        index = browser.current_index
        self.position_label.setText(f'{index+1 if index is not None else 0} / {len(browser.entries)}')
        self.previous_button.setEnabled(index is not None and index > 0)
        self.next_button.setEnabled(index is not None and index < len(browser.entries)-1)
        counts = browser.summary
        self.summary_label.setText(f'成功 {counts["success"]} · 警告 {counts["warning"]} · 失败 {counts["failed"]} · 待分析 {counts["pending"]} · 计算中 {counts["computing"]}' + (f' · {browser.message}' if browser.message else ''))

    def eventFilter(self, watched, event):
        if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Left, Qt.Key_Right) and not event.modifiers():
            focus = QApplication.focusWidget()
            if focus is not None and focus.window() is self and not isinstance(focus,
                    (QLineEdit, QAbstractSpinBox, QTextEdit, QPlainTextEdit, QComboBox, QAbstractSlider)):
                self._browse(self.application.previous_file if event.key() == Qt.Key_Left else self.application.next_file)
                return True
        return super().eventFilter(watched, event)

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开 RAW 采集文件", "", "RAW 采集文件 (*.raw *.RAW);;所有文件 (*)")
        if path:
            self.open_file(path)

    def open_file(self, path):
        self._browse(lambda: self.application.open_file(path))

    def _show_pending(self):
        self.current_result = None
        self.image.clear()
        self.reset_button.setEnabled(False)
        self.pan_button.setEnabled(False)
        self.rectangle_button.setEnabled(False)
        self.plot.getViewBox().setMouseEnabled(False, False)
        state = self.application.state
        self.file_title.setText(state.path.name if state.path else '尚未打开采集文件')
        self.file_title.setToolTip(str(state.path or ''))
        self.status_label.setText(state.message)
        self.progress.setRange(0, 0)
        self.progress.show()

    def _finished(self, state):
        if state is not self.application.state:
            return
        self.progress.hide()
        self._refresh_browser()
        self.file_title.setText(state.path.name if state.path else '尚未打开采集文件')
        self.file_title.setToolTip(str(state.path or ''))
        self.status_label.setText(state.message)
        if state.result is None:
            self.graphics.setAccessibleDescription(state.message)
            return
        self.current_result = result = state.result
        self.image.setImage(result.db, autoLevels=False, levels=result.levels)
        dt = (result.parameters.window_length-result.parameters.overlap)/result.parameters.sample_rate
        df = result.parameters.sample_rate/result.parameters.fft_length/1000
        self.image.setRect(QRectF(result.times[0]-dt/2, -df/2, len(result.times)*dt, len(result.frequencies)*df))
        self.color_bar.setLevels(result.levels)
        self.reset_button.setEnabled(True)
        self.pan_button.setEnabled(True)
        self.rectangle_button.setEnabled(True)
        self.plot.getViewBox().setMouseEnabled(True, True)
        view = state.viewport
        self.plot.setRange(xRange=view.time_range, yRange=tuple(v/1000 for v in view.frequency_range), padding=0)
        self.graphics.setAccessibleDescription(f"{result.path.name}，{result.sample_count} 个采样点，未滤波。{state.message}")

    def show_full_range(self):
        if self.current_result is not None:
            view = self.application.restore_full_view().viewport
            self.plot.setRange(xRange=view.time_range,
                               yRange=tuple(v/1000 for v in view.frequency_range), padding=0)

    def _set_drag_mode(self, rectangle):
        box = self.plot.getViewBox()
        box.rbScaleBox.hide()
        box.setMouseMode(pg.ViewBox.RectMode if rectangle else pg.ViewBox.PanMode)
        self.graphics.setCursor(Qt.CrossCursor if rectangle else Qt.OpenHandCursor)

    def _viewport_changed(self, box, ranges):
        if self.current_result is not None and self.application.state.status == "ready":
            self.application.set_viewport(Viewport(tuple(ranges[0]), tuple(v*1000 for v in ranges[1])))

    def closeEvent(self, event):
        self.application.close()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = MainWindow()
    window.show()
    return app.exec()
