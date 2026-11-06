import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from tf_decoder import Application, Viewport
from test_application import raw_file
import pytest


@pytest.fixture(autouse=True)
def readable_fonts(qapp):
    # The offscreen Qt plugin needs an explicit font; desktop Windows uses system fonts.
    from PySide6.QtGui import QFontDatabase
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")


def test_current_and_full_png_explain_ranges_without_changing_view(tmp_path, qapp):
    source = raw_file(tmp_path, [2097152])
    with Application() as app:
        opened = app.open_file(source).result(timeout=10)
        view = Viewport((0.02, 0.04), (1000, 12000))
        app.set_viewport(view)
        current = app.export_png(tmp_path, mode="current").result(timeout=10)
        full = app.export_png(tmp_path, mode="full").result(timeout=10)
        assert current.status == full.status == "succeeded"
        assert current.path.name == "采集.png"
        assert full.path.name == "采集_1.png"
        assert current.viewport == view
        assert full.viewport == opened.viewport
        assert app.state.viewport == view
        assert app.state.result is opened.result
        with Image.open(current.path) as image:
            image.load()
            assert image.size == (1400, 900)
            assert image.info["Source"] == str(source.resolve())
            assert tuple(map(float, image.info["Time range (s)"].split(","))) == (0.02, 0.04)
            assert image.info["Frequency range (Hz)"] == "1000, 12000"
            assert image.info["PSD unit"] == "dB re 1 V²/Hz"
            assert image.info["Color levels (dB)"] == f"{opened.result.levels[0]:.17g}, {opened.result.levels[1]:.17g}"
        with Image.open(full.path) as image:
            image.load()
            assert image.info["Frequency range (Hz)"] == "0, 48000"
            assert tuple(map(float, image.info["Time range (s)"].split(","))) == (0, 8192/96000)


def test_export_never_overwrites_existing_image_or_readonly_source(tmp_path, qapp):
    source = raw_file(tmp_path, [0])
    original = source.read_bytes()
    existing = tmp_path / "采集.png"
    existing.write_bytes(b"keep existing image")
    source.chmod(0o444)
    try:
        with Application() as app:
            app.open_file(source).result(timeout=10)
            exported = app.export_png(tmp_path).result(timeout=10)
            assert exported.status == "succeeded"
            assert exported.path.name == "采集_1.png"
            assert app.export_status == exported
        assert existing.read_bytes() == b"keep existing image"
        assert source.read_bytes() == original
    finally:
        source.chmod(0o666)


def test_not_ready_and_unwritable_destination_report_failure_and_recover(tmp_path, qapp):
    with Application() as app:
        assert app.export_png(tmp_path).result().status == "failed"
        assert "尚未就绪" in app.export_status.message
        app.open_file(raw_file(tmp_path, [0])).result(timeout=10)
        selected = app.set_viewport(Viewport((0.01, 0.03), (1000, 2000)))
        # A regular file cannot be used as an output directory on any platform.
        destination = tmp_path / "不是目录"
        destination.write_bytes(b"existing")
        failed = app.export_png(destination).result(timeout=10)
        assert failed.status == "failed" and failed.path is None
        assert str(destination) in failed.message
        assert app.state is selected
        assert destination.read_bytes() == b"existing"
        assert app.export_png(tmp_path, mode="full").result(timeout=10).status == "succeeded"
        assert app.state is selected


def test_raster_range_and_original_parula_scale_follow_applied_snapshot(tmp_path, qapp):
    active = raw_file(tmp_path, [2097152], name="active.raw")
    silent = raw_file(tmp_path, [0], name="silent.raw")
    source = tmp_path / "先有信号后静默.raw"
    source.write_bytes(active.read_bytes()+silent.read_bytes())
    with Application() as app:
        app.open_file(source).result(timeout=10)
        app.set_viewport(Viewport((.1, .15), (0, 100)))
        current = app.export_png(tmp_path).result(timeout=10)
        full = app.export_png(tmp_path, mode="full").result(timeout=10)
        with Image.open(current.path) as image:
            # The late silent interval retains the global scale's parula minimum.
            assert image.getpixel((400, 769))[:3] == (62, 38, 168)
            # Header and axis regions contain readable dark rasterized text.
            import numpy as np
            assert (np.asarray(image.crop((30, 18, 1000, 100)))[:, :, :3].max(axis=2) < 150).sum() > 150
            assert (np.asarray(image.crop((120, 825, 1170, 860)))[:, :, :3].max(axis=2) < 150).sum() > 30
        with Image.open(full.path) as image:
            # Full time includes the early DC signal at the original parula maximum.
            assert image.getpixel((400, 769))[:3] == (249, 251, 21)


def test_export_snapshot_survives_browsing_another_file(tmp_path, qapp):
    first = raw_file(tmp_path, [2097152], name="first.raw")
    second = raw_file(tmp_path, [0], name="second.raw")
    with Application() as app:
        state = app.open_file(first).result(timeout=10)
        selected = Viewport((.01, .03), (0, 12000))
        app.set_viewport(selected)
        future = app.export_png(tmp_path)
        new_state = app.open_file(second).result(timeout=10)
        exported = future.result(timeout=10)
        assert exported.status == "succeeded"
        assert exported.path.name == "first.png"
        assert exported.viewport == selected
        assert app.state is new_state
        assert app.state.path == second.resolve()
        with Image.open(exported.path) as image:
            assert image.info["Source"] == str(first.resolve())
            assert image.info["Color levels (dB)"] == f"{state.result.levels[0]:.17g}, {state.result.levels[1]:.17g}"
