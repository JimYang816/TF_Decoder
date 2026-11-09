"""Headless integration checks for the desktop analysis workspace.

Run: python -m unittest discover -s tests -v
Optional screenshots: python tests/test_workspace.py --screenshots
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QDoubleSpinBox, QLineEdit, QPushButton
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from raw_browser.core import BLOCK_BYTES, BLOCK_DTYPE, BLOCK_SAMPLES, HEADER_BYTES, SAMPLE_RATE, read_raw
from raw_browser.plotting import clear_spectrum, draw_spectrum, save_unique_png
from raw_browser.ui import MainWindow


def make_raw(path, blocks=36):
    """Deterministic synthetic chirp, tone, and pulses in the supported RAW format."""
    t = np.arange(blocks * BLOCK_SAMPLES) / SAMPLE_RATE
    duration = len(t) / SAMPLE_RATE
    rng = np.random.default_rng(7)
    signal = (0.18 * np.sin(2 * np.pi * (4000 * t + 14000 / (2 * duration) * t**2))
              + 0.08 * np.sin(2 * np.pi * 24000 * t)
              + 0.12 * np.sin(2 * np.pi * 32000 * t) * (np.sin(2 * np.pi * 2 * t) > 0.8)
              + rng.normal(0, 0.00005, t.size))
    raw = np.zeros(blocks, dtype=BLOCK_DTYPE)
    raw["samples"] = (np.rint(signal * 2**23).astype(np.int32).astype(np.uint32)
                      & 0xFFFFFF).reshape(blocks, BLOCK_SAMPLES)
    raw.tofile(path)


class WorkspaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        cls.scratch = Path(__file__).resolve().parents[1] / ".test-tmp"
        cls.scratch.mkdir(exist_ok=True)
        cls.previous_temp = tempfile.tempdir
        tempfile.tempdir = str(cls.scratch)

    @classmethod
    def tearDownClass(cls):
        tempfile.tempdir = cls.previous_temp

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.window = MainWindow()
        # Test loads must never alter the user's remembered source directory.
        self.window.preferences = QSettings(str(self.directory / "settings.ini"), QSettings.Format.IniFormat)
        self.window.resize(1440, 900)
        self.window.show()
        self.settle()

    def tearDown(self):
        self.window.close()
        self.wait_for(lambda: not self.window.worker.isRunning())
        self.window.close()
        self.settle()
        self.temp.cleanup()

    def settle(self):
        for _ in range(8):
            self.app.processEvents()
            QTest.qWait(10)

    def wait_for(self, predicate, timeout=20):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(20)
        self.assertTrue(predicate(), "Background operation timed out")
        self.settle()

    def load_fixture(self):
        make_raw(self.directory / "声学采集_01.raw")
        make_raw(self.directory / "声学采集_02.raw")
        self.window.load_source(self.directory)
        self.wait_for(lambda: len(self.window.cache) == 2)

    def test_partial_final_raw_block_is_plotted(self):
        path = self.directory / "短尾块.raw"
        make_raw(path, blocks=2)
        tail_samples = 632
        expected = read_raw(path)[:BLOCK_SAMPLES + tail_samples]
        with path.open("r+b") as stream:
            # Keep three bytes of the next sample to exercise unaligned EOF.
            stream.truncate(BLOCK_BYTES + HEADER_BYTES + tail_samples * 4 + 3)

        np.testing.assert_array_equal(read_raw(path), expected)
        w = self.window
        w.load_source(path)
        self.wait_for(lambda: w.cache or w.errors)
        self.assertFalse(w.errors)
        self.assertEqual(w.cache[0].sample_count, expected.size)
        self.assertTrue(w.axes.images)

    def test_layout_focus_and_keyboard(self):
        self.load_fixture()
        w = self.window
        for width, height in ((1440, 900), (1240, 830), (1060, 620)):
            w.resize(width, height)
            self.settle()
            self.assertEqual(w.width(), width)
            self.assertGreater(w.axes.bbox.width / w.width(), 0.78)
            self.assertGreater(w.axes.bbox.height / w.height(), 0.42)
            controls = [control for kind in (QPushButton, QLineEdit, QDoubleSpinBox, QComboBox)
                        for control in w.findChildren(kind)]
            for control in controls:
                if control.isVisible():
                    rect = control.rect()
                    top_left = control.mapTo(w.centralWidget(), rect.topLeft())
                    bottom_right = control.mapTo(w.centralWidget(), rect.bottomRight())
                    self.assertTrue(w.centralWidget().rect().contains(top_left), control.accessibleName())
                    self.assertTrue(w.centralWidget().rect().contains(bottom_right), control.accessibleName())
            renderer = w.canvas.get_renderer()
            for axes in w.figure.axes:
                bounds = axes.get_tightbbox(renderer)
                self.assertGreaterEqual(bounds.x0, 0)
                self.assertGreaterEqual(bounds.y0, 0)
                self.assertLessEqual(bounds.x1, w.figure.bbox.width)
                self.assertLessEqual(bounds.y1, w.figure.bbox.height)
        before = w.canvas.height()
        w.focus_button.click()
        self.settle()
        self.assertGreater(w.canvas.height(), before)
        self.assertFalse(w.source_panel.isVisible())
        self.assertFalse(w.settings_panel.isVisible())
        QTest.keyClick(w.focus_button, Qt.Key.Key_Escape)
        self.settle()
        self.assertTrue(w.source_panel.isVisible())
        self.assertTrue(w.settings_panel.isVisible())
        w.display_low.setFocus()
        self.settle()
        QTest.keyClick(w.display_low, Qt.Key.Key_Right)
        self.assertEqual(w.index, 0)
        w.canvas.setFocus()
        self.settle()
        QTest.keyClick(w.canvas, Qt.Key.Key_Right)
        self.assertEqual(w.index, 1)

    def test_settings_zoom_and_export(self):
        self.load_fixture()
        w = self.window
        self.assertFalse(w.apply_button.isEnabled())
        generation = w.generation
        original = w.cache[0]
        w.display_low.setValue(10)
        w.display_high.setValue(30)
        self.assertTrue(w.apply_button.isEnabled())
        self.assertIn("待应用", w.settings_button.text())
        w.apply_button.click()
        self.assertEqual(w.generation, generation)
        self.assertIs(w.cache[0], original)
        self.assertEqual(tuple(w.axes.get_ylim()), (10, 30))
        self.assertFalse(w.apply_button.isEnabled())
        w.zoom_in_button.click()
        self.assertLess(np.diff(w.axes.get_xlim())[0], original.duration)
        w.time_format.setCurrentIndex(1)
        self.assertEqual(w.axes.get_xlabel(), "时间 (分:秒)")
        w.reset_view_button.click()
        self.assertEqual(tuple(w.axes.get_xlim()), (0, original.duration))
        figure = Figure(figsize=(12, 6.5))
        FigureCanvasAgg(figure)
        axes = draw_spectrum(figure, original, w.band, minutes=True)
        self.assertIn(original.path.name, axes.get_title())
        self.assertEqual(w.axes.get_title(), "")
        np.testing.assert_equal(w.axes.images[0].get_clim(), axes.images[0].get_clim())
        out = save_unique_png(figure, self.directory, "export")
        self.assertGreater(out.stat().st_size, 10000)
        clear_spectrum(figure)
        w.filter_check.setChecked(True)
        w.apply_button.click()
        self.assertGreater(w.generation, generation)
        self.wait_for(lambda: len(w.cache) == 2)
        self.assertTrue(w.cache[0].settings.filter_enabled)
        self.assertIn("带通", w.spectrum_info.text())

    def test_empty_error_and_long_names(self):
        w = self.window
        self.assertFalse(w.save_current_button.isEnabled())
        self.assertFalse(w.progress.isVisible())
        w.load_source(self.directory)
        self.assertIn("没有 RAW", w.file_label.full_text)
        long_name = "测试文件_" * 24 + ".raw"
        (self.directory / long_name).write_bytes(b"invalid")
        w.load_source(self.directory)
        self.wait_for(lambda: len(w.errors) == 1)
        self.assertEqual(w.file_label.full_text, long_name)
        self.assertIn(long_name, w.file_label.toolTip())
        self.assertFalse(w.save_current_button.isEnabled())
        self.assertIn("无法绘制", w.axes.texts[0].get_text())
        previous = w.band
        w.display_low.setValue(48)
        warnings = []
        w._warning = lambda title, text: warnings.append((title, text))
        w.apply_settings()
        self.assertTrue(warnings)
        self.assertEqual(w.band, previous)


def screenshots():
    test = WorkspaceTest()
    test.setUpClass()
    test.setUp()
    destination = Path(__file__).resolve().parents[1] / "artifacts" / "ui"
    destination.mkdir(parents=True, exist_ok=True)
    try:
        w = test.window
        w.grab().save(str(destination / "empty.png"))
        test.load_fixture()
        w.grab().save(str(destination / "workspace.png"))
        w.resize(1060, 620)
        test.settle()
        w.grab().save(str(destination / "compact.png"))
        w.resize(1440, 900)
        w.focus_button.click()
        test.settle()
        w.grab().save(str(destination / "focus.png"))
        print(destination)
    finally:
        test.tearDown()


if __name__ == "__main__":
    if "--screenshots" in sys.argv:
        screenshots()
    else:
        unittest.main()
