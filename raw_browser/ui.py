"""Qt desktop UI and cancellable, generation-tagged background jobs."""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from collections import deque
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QSettings, QThread, Signal, Qt, QTimer
from PySide6.QtGui import QFont, QFontDatabase, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListView, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.backend_bases import DrawEvent

from .core import (
    AnalysisSettings, BATCH_SIZE, ProcessingCancelled, Spectrum,
    analyze_file, scan_raw_files, validate_display_band,
)
from .plotting import (
    PLOT_LOCK, clear_spectrum, configure_fonts, draw_spectrum, save_unique_png, set_time_format,
)
from .navigation import SpectrumNavigation
from .theme import COLORS, workspace_palette, workspace_stylesheet


APP_NAME = "TF Decoder"


@lru_cache(maxsize=1)
def chinese_font_family():
    # Explicit registration also supports Qt's offscreen platform, whose font
    # database can be empty on Windows even though installed fonts are readable.
    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyh.ttc"
    if font_path.is_file():
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            return families[0]
    return "Microsoft YaHei"


class BatchWorker(QThread):
    ready = Signal(int, int, object)
    failed = Signal(int, int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._condition = threading.Condition()
        self._jobs = deque()
        self._cancel = threading.Event()
        self._stopping = False

    def set_batch(self, generation, jobs, settings):
        with self._condition:
            self._cancel.set()
            self._cancel = threading.Event()
            self._jobs = deque((generation, index, path, settings, self._cancel)
                               for index, path in jobs)
            self._condition.notify()

    def prioritize(self, index):
        with self._condition:
            self._jobs = deque(sorted(self._jobs, key=lambda job: job[1] != index))

    def stop(self):
        with self._condition:
            self._stopping = True
            self._cancel.set()
            self._jobs.clear()
            self._condition.notify()

    def run(self):
        while True:
            with self._condition:
                while not self._jobs and not self._stopping:
                    self._condition.wait()
                if self._stopping:
                    return
                generation, index, path, settings, cancel = self._jobs.popleft()
            try:
                result = analyze_file(path, settings, cancel, disk_cache=True)
                if not cancel.is_set():
                    self.ready.emit(generation, index, result)
                del result
            except ProcessingCancelled:
                pass
            except Exception as error:
                if not cancel.is_set():
                    self.failed.emit(generation, index, f"{type(error).__name__}: {error}")


class ExportWorker(QThread):
    progress = Signal(int, int)
    report_ready = Signal(int, list)

    def __init__(self, items, directory, band, viewport=None, figsize=(12, 6.5), parent=None,
                 *, minutes=False):
        super().__init__(parent)
        self.items = deque(items)
        self.directory = Path(directory)
        self.band = band
        self.viewport = viewport
        self.figsize = figsize
        self.minutes = minutes
        self.cancel = threading.Event()

    def run(self):
        saved = 0
        errors = []
        total = len(self.items)
        done = 0
        while self.items and not self.cancel.is_set():
            path, result, error = self.items.popleft()
            try:
                if result is None:
                    raise ValueError(error or "文件没有可导出的时频图。")
                with PLOT_LOCK:
                    figure = Figure(figsize=self.figsize, dpi=100)
                    FigureCanvasAgg(figure)
                    try:
                        ax = draw_spectrum(figure, result, self.band, minutes=self.minutes)
                        if self.viewport:
                            ax.set_xlim(self.viewport[0])
                            ax.set_ylim(self.viewport[1])
                        save_unique_png(figure, self.directory, path.stem + "_stft")
                    finally:
                        clear_spectrum(figure)
                saved += 1
            except Exception as failure:
                errors.append(f"{path.name}: {failure}")
            finally:
                result = None
                done += 1
                self.progress.emit(done, total)
        self.items.clear()
        self.report_ready.emit(saved, errors)


class LockedCanvas(FigureCanvasQTAgg):
    FRAME_INTERVAL = 1 / 60

    def __init__(self, *args, **kwargs):
        self._last_draw_at = 0.0
        self._render_requested = False
        self._render_timer = None
        self._interactive_axes = None
        self._background = None
        self._background_key = None
        super().__init__(*args, **kwargs)
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._flush_render)

    def set_interactive_axes(self, axes):
        previous = self._interactive_axes
        if previous is not None:
            for artist in (*previous.images, previous.xaxis, previous.yaxis,
                           *previous.spines.values()):
                artist.set_animated(False)
        self._interactive_axes = axes
        self._background = self._background_key = None
        if axes is not None:
            for artist in (*axes.images, axes.xaxis, axes.yaxis, *axes.spines.values()):
                artist.set_animated(True)

    def _static_key(self, axes):
        image = axes.images[0]
        return (tuple(self.figure.bbox.bounds), self.figure.dpi,
                tuple(axes.get_position().bounds), id(image), image.get_clim(),
                id(image.get_cmap()), axes.get_title(), axes.get_xlabel(), axes.get_ylabel())

    def draw_idle(self):
        if self._render_timer is None:
            return super().draw_idle()
        self._render_requested = True
        if not self._render_timer.isActive():
            delay = max(0.0, self.FRAME_INTERVAL - (time.perf_counter() - self._last_draw_at))
            self._render_timer.start(int(delay * 1000 + 0.999))

    def _flush_render(self):
        if not self._render_requested:
            return
        # Export uses a separate figure but shares Matplotlib's global font
        # state. Do not block the Qt event loop waiting on an exporting thread.
        if not PLOT_LOCK.acquire(blocking=False):
            self._render_timer.start(16)
            return
        try:
            self.draw()
        finally:
            PLOT_LOCK.release()

    def draw(self):
        if self._render_timer is not None:
            self._render_timer.stop()
        self._render_requested = False
        with PLOT_LOCK:
            self._last_draw_at = time.perf_counter()
            axes = self._interactive_axes
            if axes is None or axes not in self.figure.axes or not axes.images:
                return super().draw()
            key = self._static_key(axes)
            if self._background is None or key != self._background_key:
                # Cache the title, colorbar, and window background once. Image
                # and axis artists are animated, so the base draw omits them.
                with self.callbacks.blocked(signal="draw_event"):
                    super().draw()
                self._background = self.copy_from_bbox(self.figure.bbox)
                self._background_key = self._static_key(axes)
            else:
                self.restore_region(self._background)
            axes.draw_artist(axes.images[0])
            axes.draw_artist(axes.xaxis)
            axes.draw_artist(axes.yaxis)
            for spine in axes.spines.values():
                axes.draw_artist(spine)
            self.figure.stale = False
            self.blit(self.figure.bbox)
            DrawEvent("draw_event", self, self.get_renderer())._process()


class ElidedLabel(QLabel):
    """Keep long filenames on one line, with the full value available to assistive tech."""

    def __init__(self, text=""):
        super().__init__()
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setText(text)

    def setText(self, text):
        self.full_text = text
        self.setAccessibleName(text)
        self.setToolTip(text)
        self._elide()

    def _elide(self):
        super().setText(self.fontMetrics().elidedText(
            self.full_text, Qt.TextElideMode.ElideMiddle, max(0, self.contentsRect().width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()


class MainWindow(QMainWindow):
    def __init__(self, source: Path | None = None):
        super().__init__()
        configure_fonts()
        self.setPalette(workspace_palette())
        self.setFont(QFont(chinese_font_family(), 10))
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1060, 620)
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1440, screen.width()), min(900, screen.height() - 40))
        self.files: list[Path] = []
        # Keep the original settings keys so the remembered directory survives the rename.
        self.preferences = QSettings("TF_Decoder", "RAW Browser")
        last_directory = self.preferences.value("lastDirectory", "", type=str)
        self.directory = Path(last_directory) if last_directory else None
        self.index = -1
        self.generation = 0
        self.cache: dict[int, Spectrum] = {}
        self.errors: dict[int, str] = {}
        self.settings = AnalysisSettings()
        self.band = (0.0, 48_000.0)
        self.export_worker: ExportWorker | None = None
        self._closing = False
        self._build_ui()
        self.worker = BatchWorker(self)
        self.worker.ready.connect(self._on_ready)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self._try_close)
        self.worker.start()
        self._show_message("打开 RAW 文件，开始时频分析\n\n选择单个文件，或打开文件夹连续浏览 · 支持 PNG 导出\nCtrl+O 打开文件 / Ctrl+Shift+O 打开文件夹")
        self._update_controls()
        if source is not None:
            self.source_edit.setText(str(source))
            QTimer.singleShot(0, self.rescan)

    def _build_ui(self):
        widget = QWidget()
        widget.setObjectName("workspace")
        widget.setStyleSheet(workspace_stylesheet())
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(12, 10, 12, 8)
        layout.setSpacing(8)
        self.setCentralWidget(widget)

        # One compact source row; all remaining width belongs to the chart.
        self.source_panel = QWidget()
        source = QHBoxLayout(self.source_panel)
        source.setContentsMargins(0, 0, 0, 0)
        source.setSpacing(8)
        title = QLabel(APP_NAME)
        title.setObjectName("title")
        source.addWidget(title)
        source.addSpacing(12)
        source_label = QLabel("数据源")
        source_label.setObjectName("secondary")
        source.addWidget(source_label)
        self.source_edit = QLineEdit()
        self.source_edit.setMinimumWidth(140)
        self.source_edit.setPlaceholderText("RAW 文件或文件夹路径 · 回车加载")
        self.source_edit.setAccessibleName("RAW 文件或文件夹路径")
        source_label.setBuddy(self.source_edit)
        self.source_edit.returnPressed.connect(self.rescan)
        source.addWidget(self.source_edit, 1)
        self.choose_file_button = QPushButton("打开 RAW…")
        self.choose_file_button.setObjectName("primary")
        self.choose_file_button.setToolTip("打开单个 RAW 文件（Ctrl+O）")
        self.choose_file_button.clicked.connect(self.choose_file)
        source.addWidget(self.choose_file_button)
        self.choose_folder_button = QPushButton("打开文件夹…")
        self.choose_folder_button.setToolTip("递归加载文件夹中的 RAW 文件（Ctrl+Shift+O）")
        self.choose_folder_button.clicked.connect(self.choose_batch_folder)
        source.addWidget(self.choose_folder_button)
        scan = QPushButton("重新扫描")
        scan.clicked.connect(self.rescan)
        source.addWidget(scan)
        layout.addWidget(self.source_panel)

        self.settings_panel = QWidget()
        self.settings_panel.setObjectName("panel")
        settings = QVBoxLayout(self.settings_panel)
        settings.setContentsMargins(12, 8, 12, 8)
        settings.setSpacing(4)
        fields = QHBoxLayout()
        fields.setSpacing(8)
        display_title = QLabel("显示频率")
        display_title.setObjectName("section")
        fields.addWidget(display_title)
        self.display_low = self._spin(0, 48, 0)
        self.display_high = self._spin(0, 48, 48)
        self.filter_low = self._spin(0, 48, 20)
        self.filter_high = self._spin(0, 48, 28)

        def add_field(caption, control, accessible_name):
            label = QLabel(caption)
            label.setObjectName("secondary")
            label.setBuddy(control)
            control.setAccessibleName(accessible_name)
            fields.addWidget(label)
            fields.addWidget(control)

        add_field("下限", self.display_low, "显示频率下限（kHz）")
        add_field("上限", self.display_high, "显示频率上限（kHz）")
        fields.addSpacing(8)
        self.filter_check = QCheckBox("带通滤波")
        self.filter_check.setToolTip("勾选并应用后重新计算当前批；关闭则显示原始信号")
        self.filter_check.toggled.connect(self.filter_low.setEnabled)
        self.filter_check.toggled.connect(self.filter_high.setEnabled)
        self.filter_low.setEnabled(False)
        self.filter_high.setEnabled(False)
        fields.addWidget(self.filter_check)
        add_field("下限", self.filter_low, "带通滤波下限（kHz）")
        add_field("上限", self.filter_high, "带通滤波上限（kHz）")
        fields.addStretch(1)
        self.apply_button = QPushButton("应用设置")
        self.apply_button.setToolTip("应用显示频率和滤波参数（Ctrl+Return）")
        self.apply_button.clicked.connect(self.apply_settings)
        fields.addWidget(self.apply_button)
        settings.addLayout(fields)
        detail = QHBoxLayout()
        note = QLabel("96 kHz · 单通道  /  Hanning 1024 · 重叠 512 · FFT 1024")
        note.setObjectName("secondary")
        note.setToolTip("采样计数按 4.098 V 标定换算；功率谱密度单位为 dB/Hz")
        detail.addWidget(note)
        detail.addStretch()
        self.pending_label = QLabel("参数已应用")
        self.pending_label.setObjectName("secondary")
        detail.addWidget(self.pending_label)
        settings.addLayout(detail)
        layout.addWidget(self.settings_panel)

        plot_panel = QWidget()
        plot_panel.setObjectName("panel")
        plot = QVBoxLayout(plot_panel)
        plot.setContentsMargins(8, 8, 8, 6)
        plot.setSpacing(4)
        layout.addWidget(plot_panel, 1)
        heading = QHBoxLayout()
        heading.setContentsMargins(4, 0, 4, 0)
        heading.setSpacing(10)
        plot_title = QLabel("时频图")
        plot_title.setObjectName("section")
        heading.addWidget(plot_title)
        self.file_label = ElidedLabel("尚未加载文件")
        self.file_label.setObjectName("secondary")
        heading.addWidget(self.file_label, 1)
        self.spectrum_info = QLabel("功率谱密度 · dB/Hz")
        self.spectrum_info.setObjectName("badge")
        heading.addWidget(self.spectrum_info)
        self.settings_button = QPushButton("分析参数")
        self.settings_button.setCheckable(True)
        self.settings_button.setChecked(True)
        self.settings_button.setToolTip("显示或收起参数栏")
        self.settings_button.toggled.connect(self._toggle_settings)
        heading.addWidget(self.settings_button)
        self.focus_button = QPushButton("专注视图")
        self.focus_button.setCheckable(True)
        self.focus_button.setToolTip("收起数据源和参数栏，扩大时频图（F11）；Esc 返回")
        self.focus_button.toggled.connect(self._toggle_focus)
        heading.addWidget(self.focus_button)
        plot.addLayout(heading)

        self.figure = Figure(figsize=(12, 6), dpi=100, facecolor=COLORS["surface"])
        self.canvas = LockedCanvas(self.figure)
        self.canvas.setMinimumSize(320, 240)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.canvas.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.canvas.setAccessibleName("时频图；可使用下方按钮放大、缩小或恢复全图")
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.toolbar.hide()
        self.navigation = SpectrumNavigation(self.canvas, self.toolbar)
        plot.addWidget(self.canvas, 1)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        plot.addWidget(divider)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.previous_button = QPushButton("上一文件")
        self.next_button = QPushButton("下一文件")
        self.previous_button.setToolTip("上一文件（←）；输入参数时方向键用于编辑")
        self.next_button.setToolTip("下一文件（→）；输入参数时方向键用于编辑")
        self.previous_button.clicked.connect(lambda: self.set_index(self.index - 1))
        self.next_button.clicked.connect(lambda: self.set_index(self.index + 1))
        self.position_label = QLabel("0 / 0")
        self.position_label.setObjectName("secondary")
        actions.addWidget(self.previous_button)
        actions.addWidget(self.position_label)
        actions.addWidget(self.next_button)
        actions.addSpacing(10)
        self.zoom_in_button = QPushButton("放大")
        self.zoom_out_button = QPushButton("缩小")
        self.reset_view_button = QPushButton("恢复全图")
        self.zoom_in_button.clicked.connect(self.navigation.zoom_in)
        self.zoom_out_button.clicked.connect(self.navigation.zoom_out)
        self.reset_view_button.clicked.connect(self.navigation.reset_view)
        for button in (self.zoom_in_button, self.zoom_out_button, self.reset_view_button):
            actions.addWidget(button)
        actions.addSpacing(8)
        time_label = QLabel("时间")
        time_label.setObjectName("secondary")
        actions.addWidget(time_label)
        self.time_format = QComboBox()
        # An explicit list view avoids the platform menu delegate and its dark
        # system palette leaking into this light workspace's popup.
        self.time_format.setView(QListView())
        self.time_format.view().setPalette(workspace_palette())
        self.time_format.setMinimumWidth(94)
        self.time_format.addItem("秒", False)
        self.time_format.addItem("分:秒", True)
        self.time_format.setAccessibleName("时间轴格式")
        self.time_format.setToolTip("切换时间轴标签：秒 / 分:秒；保留当前缩放范围")
        time_label.setBuddy(self.time_format)
        self.time_format.currentIndexChanged.connect(self._update_time_format)
        actions.addWidget(self.time_format)
        actions.addStretch()
        self.save_current_button = QPushButton("保存当前图…")
        self.save_current_button.setObjectName("primary")
        self.save_current_button.setToolTip("保存当前频段及缩放视图为 PNG")
        self.save_current_button.clicked.connect(self.save_current)
        self.save_batch_button = QPushButton("保存本批…")
        self.save_batch_button.setToolTip("本批准备完成后，导出最多 10 个文件的完整时频图")
        self.save_batch_button.clicked.connect(self.save_batch)
        actions.addWidget(self.save_current_button)
        actions.addWidget(self.save_batch_button)
        plot.addLayout(actions)
        gestures = QLabel("左键框选放大 · 中键平移 · 双击恢复 · 滚轮缩放  |  Shift + 滚轮：时间  /  Ctrl + 滚轮：频率")
        gestures.setObjectName("secondary")
        gestures.setWordWrap(True)
        plot.addWidget(gestures)

        self.progress = QProgressBar()
        self.progress.setFormat("本批准备 %v / %m")
        self.progress.setFixedSize(180, 20)
        self.progress.setAccessibleName("当前批次计算进度")
        self.status = QLabel()
        self.status.setObjectName("secondary")
        self.status.setWordWrap(True)
        status_row = QHBoxLayout()
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.progress)
        layout.addLayout(status_row)

        self.previous_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Left), self)
        self.next_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Right), self)
        self.previous_shortcut.activated.connect(lambda: self.set_index(self.index - 1))
        self.next_shortcut.activated.connect(lambda: self.set_index(self.index + 1))
        QApplication.instance().focusChanged.connect(self._update_navigation_shortcuts)
        self.shortcuts = []
        for sequence, callback in (
            ("Ctrl+O", self.choose_file), ("Ctrl+Shift+O", self.choose_batch_folder),
            ("Ctrl+Return", self.apply_settings), ("F11", self.focus_button.toggle),
            ("Escape", lambda: self.focus_button.setChecked(False)),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)
            self.shortcuts.append(shortcut)
        for control in (self.display_low, self.display_high, self.filter_low, self.filter_high):
            control.valueChanged.connect(self._update_pending_settings)
        self.filter_check.toggled.connect(self._update_pending_settings)
        self._update_pending_settings()

    @staticmethod
    def _spin(minimum, maximum, value):
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(3)
        spin.setSingleStep(1)
        spin.setSuffix(" kHz")
        spin.setValue(value)
        spin.setMinimumWidth(110)
        spin.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        spin.setKeyboardTracking(False)
        return spin

    def _toggle_settings(self, checked):
        self.settings_panel.setVisible(checked and not self.focus_button.isChecked())

    def _toggle_focus(self, focused):
        self.source_panel.setVisible(not focused)
        self.settings_panel.setVisible(not focused and self.settings_button.isChecked())
        self.settings_button.setEnabled(not focused)
        self.focus_button.setText("退出专注" if focused else "专注视图")
        self.focus_button.setFocus()

    def _update_navigation_shortcuts(self, _previous, current):
        # Preserve native arrow-key editing in every field, including spin-box children.
        editing = isinstance(current, (QLineEdit, QDoubleSpinBox, QComboBox))
        self.previous_shortcut.setEnabled(not editing)
        self.next_shortcut.setEnabled(not editing)

    def _update_pending_settings(self):
        band = (self.display_low.value() * 1000, self.display_high.value() * 1000)
        requested = AnalysisSettings(self.filter_check.isChecked(),
                                     self.filter_low.value() * 1000, self.filter_high.value() * 1000)
        pending = band != self.band or requested != self.settings
        self.pending_label.setText("参数已修改 · 应用后生效" if pending else "参数已应用")
        self.pending_label.setObjectName("pending" if pending else "secondary")
        self.pending_label.style().unpolish(self.pending_label)
        self.pending_label.style().polish(self.pending_label)
        self.apply_button.setEnabled(pending)
        self.settings_button.setText("分析参数 · 待应用" if pending else "分析参数")

    @property
    def batch_indices(self):
        start = self.index // BATCH_SIZE * BATCH_SIZE
        return range(max(0, start), min(start + BATCH_SIZE, len(self.files)))

    def choose_file(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "选择 RAW 文件", str(self.source_dialog_directory()), "RAW 文件 (*.raw *.RAW)"
        )
        if filename:
            self.load_source(filename)

    def choose_batch_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "选择文件夹以批量查看 RAW", str(self.source_dialog_directory())
        )
        if folder:
            self.load_source(folder)

    def source_dialog_directory(self):
        if self.directory is not None and self.directory.is_dir():
            return self.directory
        return Path.cwd()

    def load_source(self, source):
        self.source_edit.setText(str(source))
        self.rescan()

    def rescan(self):
        text = self.source_edit.text().strip()
        if not text:
            self._warning("请选择路径", "请选择或输入 RAW 文件或目录的路径。")
            return
        try:
            source = Path(text).expanduser().resolve()
            is_directory = source.is_dir()
            if is_directory:
                files = scan_raw_files(source, recursive=True)
            elif source.is_file() and source.suffix.casefold() == ".raw":
                files = [source]
            else:
                raise ValueError("请选择存在且可读取的 RAW 文件或目录。")
        except (OSError, ValueError) as error:
            self._warning("无法加载路径", str(error))
            return
        self.directory = source if is_directory else source.parent
        self.preferences.setValue("lastDirectory", str(self.directory))
        self.source_edit.setText(str(source))
        self.files = files
        self.index = -1
        if files:
            self.set_index(0)
        else:
            self._start_batch()
            self.file_label.setText("目录中没有 RAW 文件")
            self.spectrum_info.setText("功率谱密度 · dB/Hz")
            self._show_message("所选文件夹及其子文件夹中未找到 RAW 文件。")
            self._update_controls()

    def set_index(self, index):
        if not 0 <= index < len(self.files):
            return
        changed_batch = self.index < 0 or index // BATCH_SIZE != self.index // BATCH_SIZE
        self.index = index
        if changed_batch:
            self._start_batch()
        else:
            self.worker.prioritize(index)
        self._show_current()
        self._update_controls()

    def _start_batch(self):
        self.generation += 1
        self.cache.clear()
        self.errors.clear()
        order = list(self.batch_indices)
        if order:
            order.remove(self.index)
            order.insert(0, self.index)
        self.worker.set_batch(self.generation, [(i, self.files[i]) for i in order], self.settings)

    def _show_current(self):
        path = self.files[self.index]
        self.file_label.setText(path.name)
        self.file_label.setToolTip(str(path))
        mode = (f"带通 {self.settings.low_hz / 1000:g}–{self.settings.high_hz / 1000:g} kHz"
                if self.settings.filter_enabled else "原始信号")
        duration = f"{self.cache[self.index].duration:.3f} s · " if self.index in self.cache else ""
        self.spectrum_info.setText(f"{duration}{mode} · {self.band[0] / 1000:g}–{self.band[1] / 1000:g} kHz")
        if self.index in self.cache:
            with PLOT_LOCK:
                try:
                    self.axes = draw_spectrum(self.figure, self.cache[self.index], self.band,
                                              minutes=self.time_format.currentData(), compact=True)
                except OSError as error:
                    del self.cache[self.index]
                    self.errors[self.index] = f"无法读取临时频谱缓存：{error}"
                else:
                    self.canvas.set_interactive_axes(self.axes)
                    self.toolbar.update()
                    self.navigation.bind_axes(self.axes)
                    self.canvas.draw_idle()
                    return
        if self.index in self.errors:
            self._show_message("无法绘制此文件\n\n" + self.errors[self.index]
                               + "\n\n可使用左右箭头继续浏览。")
        else:
            self._show_message("正在计算时频图…\n窗口可继续操作，左右箭头可切换文件。")

    def _update_time_format(self):
        if self.index in self.cache:
            with PLOT_LOCK:
                set_time_format(self.axes, self.cache[self.index].duration,
                                minutes=self.time_format.currentData())
                self.canvas.draw_idle()

    def _show_message(self, text):
        with PLOT_LOCK:
            self.canvas.set_interactive_axes(None)
            clear_spectrum(self.figure)
            self.axes = self.figure.add_subplot(111)
            self.axes.set_axis_off()
            self.axes.text(0.5, 0.5, text, transform=self.axes.transAxes,
                           ha="center", va="center", wrap=True, fontsize=12,
                           color=COLORS["muted"], linespacing=1.8, parse_math=False)
            self.toolbar.update()
            self.navigation.bind_axes(None)
            self.canvas.draw_idle()

    def _on_ready(self, generation, index, result):
        if generation != self.generation or self._closing:
            return
        self.cache[index] = result
        if index == self.index:
            self._show_current()
        self._update_controls()

    def _on_failed(self, generation, index, error):
        if generation != self.generation or self._closing:
            return
        self.errors[index] = error
        if index == self.index:
            self._show_current()
        self._update_controls()

    def _update_controls(self):
        total = len(self.files)
        have = total > 0 and self.index >= 0
        self.previous_button.setEnabled(have and self.index > 0)
        self.next_button.setEnabled(have and self.index + 1 < total)
        batch_count = len(self.batch_indices)
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
        done = len(self.cache) + len(self.errors)
        self.progress.setRange(0, max(1, batch_count))
        self.progress.setValue(done)
        self.position_label.setText(
            f"{self.index + 1} / {total}"
            if have else "0 / 0")
        self.position_label.setToolTip(
            f"第 {self.index // BATCH_SIZE + 1} / {total_batches} 批 · 每批最多 {BATCH_SIZE} 个文件"
            if have else "尚未加载文件")
        self.progress.setVisible(have)
        self.time_format.setEnabled(self.index in self.cache)
        exporting = self.export_worker is not None and self.export_worker.isRunning()
        self.save_current_button.setEnabled(self.index in self.cache and not exporting)
        self.save_batch_button.setEnabled(have and done == batch_count and not exporting)
        for button in (self.zoom_in_button, self.zoom_out_button, self.reset_view_button):
            button.setEnabled(self.index in self.cache)
        if not exporting:
            if have:
                state = "本批准备完成" if done == batch_count else "正在后台计算"
                self.status.setText(f"第 {self.index // BATCH_SIZE + 1} / {total_batches} 批 · {state}：成功 {len(self.cache)}，失败 {len(self.errors)}。"
                                    + (" 失败原因可切换到对应文件查看。" if self.errors else ""))
            else:
                self.status.setText("请选择 RAW 文件或目录。")

    def apply_settings(self):
        for control in (self.display_low, self.display_high, self.filter_low, self.filter_high):
            control.interpretText()
        band = (self.display_low.value() * 1000, self.display_high.value() * 1000)
        settings = AnalysisSettings(self.filter_check.isChecked(),
                                    self.filter_low.value() * 1000, self.filter_high.value() * 1000)
        try:
            validate_display_band(band)
            settings.validate()
        except ValueError as error:
            self._warning("参数无效", str(error))
            return
        processing_changed = settings != self.settings and (
            settings.filter_enabled or self.settings.filter_enabled
        )
        self.band, self.settings = band, settings
        self._update_pending_settings()
        if self.index >= 0:
            if processing_changed:
                self._start_batch()
            self._show_current()
        self._update_controls()

    def save_current(self):
        if self.index not in self.cache:
            return
        directory = QFileDialog.getExistingDirectory(self, "选择当前图的保存目录",
                                                     str(self.directory or Path.cwd()))
        if directory:
            viewport = (tuple(self.axes.get_xlim()), tuple(self.axes.get_ylim()))
            self.start_export(Path(directory), current_only=True, viewport=viewport)

    def save_batch(self):
        directory = QFileDialog.getExistingDirectory(self, "选择本批 PNG 的保存目录",
                                                     str(self.directory or Path.cwd()))
        if directory:
            self.start_export(Path(directory))

    def start_export(self, directory, current_only=False, viewport=None):
        if self.export_worker is not None and self.export_worker.isRunning():
            return
        indices = (self.index,) if current_only else self.batch_indices
        if not indices or (not current_only and len(self.cache) + len(self.errors) != len(indices)):
            return
        items = [(self.files[i], self.cache.get(i), self.errors.get(i)) for i in indices]
        self.export_worker = ExportWorker(items, directory, self.band, viewport,
                                         tuple(self.figure.get_size_inches()) if current_only else (12, 6.5),
                                         self, minutes=self.time_format.currentData())
        self.export_worker.progress.connect(self._export_progress)
        self.export_worker.report_ready.connect(self._export_report)
        self.export_worker.finished.connect(self._export_finished)
        self.status.setText("正在保存 PNG…")
        self.export_worker.start()
        self._update_controls()

    def _export_progress(self, done, total):
        if not self._closing:
            self.status.setText(f"正在保存 PNG：{done} / {total}")

    def _export_report(self, saved, errors):
        if self._closing:
            return
        summary = f"保存完成：成功 {saved}，失败 {len(errors)}。"
        self.status.setText(summary)
        text = summary + "\n\n" + "\n".join(errors) if errors else summary
        QMessageBox.information(self, "图片保存结果", text)

    def _export_finished(self):
        worker = self.export_worker
        self.export_worker = None
        if worker:
            worker.deleteLater()
        self._update_controls()
        self._try_close()

    def _warning(self, title, text):
        QMessageBox.warning(self, title, text)

    def _try_close(self):
        if self._closing and not self.worker.isRunning() and (
                self.export_worker is None or not self.export_worker.isRunning()):
            self.close()

    def closeEvent(self, event):
        self._closing = True
        self.worker.stop()
        if self.export_worker is not None:
            self.export_worker.cancel.set()
        if self.worker.isRunning() or (self.export_worker is not None and self.export_worker.isRunning()):
            event.ignore()
            self.centralWidget().setEnabled(False)
            self.status.setText("正在结束后台任务，请稍候…")
        else:
            with PLOT_LOCK:
                clear_spectrum(self.figure)
            self.cache.clear()
            event.accept()


def main():
    parser = argparse.ArgumentParser(description=f"{APP_NAME} · 96 kHz RAW 时频分析")
    parser.add_argument("source", nargs="?", type=Path, help="可选：启动时加载的 RAW 文件或目录")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationDisplayName(APP_NAME)
    app.setStyle("Fusion")
    window = MainWindow(args.source)
    window.show()
    return app.exec()
