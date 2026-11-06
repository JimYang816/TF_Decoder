"""Labeled PNG rasterization from immutable analysis snapshots; no live widgets."""
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
import numpy as np
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter

if TYPE_CHECKING:
    from .application import Viewport


@dataclass(frozen=True)
class ExportResult:
    status: str
    path: Path | None = None
    viewport: "Viewport | None" = None
    message: str = ""


def _bounds(values):
    return ", ".join(f"{v:.17g}" for v in values)


def write_png(result, view, directory, mode):
    """Reusable single-image output primitive for current and future sequential batch jobs."""
    output = None
    try:
        if not isinstance(QGuiApplication.instance(), QGuiApplication):
            raise ValueError("PNG 文字渲染需要运行中的 Qt 图形应用")
        image = _render(result, view, mode)
        # Exclusive creation protects existing images even across concurrent processes.
        suffix = 0
        while True:
            candidate = directory / (result.path.stem + (f"_{suffix}" if suffix else "") + ".png")
            try:
                stream = candidate.open("xb")
                output = candidate
                break
            except FileExistsError:
                suffix += 1
        from PySide6.QtCore import QBuffer, QIODevice
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        if not image.save(buffer, "PNG"):
            raise OSError("PNG 编码失败")
        with stream:
            stream.write(bytes(buffer.data()))
            stream.flush()
        return ExportResult("succeeded", output.resolve(), view, f"PNG 已导出：{output.resolve()}")
    except Exception as error:
        if output is not None:
            try:
                stream.close()
                output.unlink()
            except OSError:
                pass
        return ExportResult("failed", viewport=view, message=f"{directory}: PNG 导出失败：{error}")


def _render(result, view, mode):
    image = QImage(1400, 900, QImage.Format_RGB32)
    image.fill(QColor("white"))
    image.setText("Source", str(result.path))
    image.setText("Time range (s)", _bounds(view.time_range))
    image.setText("Frequency range (Hz)", _bounds(view.frequency_range))
    image.setText("PSD unit", "dB re 1 V²/Hz")
    image.setText("Color levels (dB)", _bounds(result.levels))
    image.setText("Export mode", mode)
    image.setText("Analysis parameters", str(result.parameters))
    x, y, w, h = 120, 150, 1050, 620
    t0, t1 = view.time_range
    f0, f1 = view.frequency_range
    dt = (result.parameters.window_length-result.parameters.overlap)/result.parameters.sample_rate
    df = result.parameters.sample_rate/result.parameters.fft_length
    # Same cell edges as the interactive ImageItem. Sample to output resolution,
    # preserving original dB and file-wide levels (never viewport normalization).
    ti = np.floor((t0+(np.arange(w)+.5)*(t1-t0)/w-result.times[0]+dt/2)/dt).astype(int)
    fi = np.floor((f1-(np.arange(h)+.5)*(f1-f0)/h+df/2)/df).astype(int)
    valid = ((fi[:, None] >= 0) & (fi[:, None] < len(result.frequencies)) &
             (ti[None, :] >= 0) & (ti[None, :] < len(result.times)))
    db = result.db[np.clip(fi, 0, len(result.frequencies)-1)[:, None],
                   np.clip(ti, 0, len(result.times)-1)[None, :]]
    palette = np.rint(np.loadtxt(Path(__file__).with_name("parula256.csv"), delimiter=",")*255).astype(np.uint8)
    # ImageItem bins the normalized range into 256 LUT entries (upper end clipped).
    indexes = np.clip((db-result.levels[0])/(result.levels[1]-result.levels[0])*256, 0, 255).astype(np.uint8)
    rgb = np.ascontiguousarray(palette[indexes])
    rgb[~valid] = 255
    raster = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.TextAntialiasing)
    painter.drawImage(x, y, raster)
    painter.setPen(QColor("#172033"))
    painter.setFont(QFont("Microsoft YaHei UI", 14))
    painter.drawText(QRect(30, 18, 1340, 40), Qt.AlignLeft | Qt.AlignVCenter,
                     ("当前视图" if mode == "current" else "全时段") + " · " + result.path.name)
    painter.setFont(QFont("Microsoft YaHei UI", 9))
    metrics = painter.fontMetrics()
    painter.drawText(30, 85, metrics.elidedText(str(result.path), Qt.ElideMiddle, 1330))
    painter.drawRect(x, y, w, h)
    painter.setFont(QFont("Microsoft YaHei UI", 11))
    for i in range(6):
        xx = x+i*w/5
        yy = y+h-i*h/5
        painter.drawLine(int(xx), y+h, int(xx), y+h+7)
        painter.drawText(QRect(int(xx)-60, y+h+12, 120, 30), Qt.AlignHCenter, f"{t0+i*(t1-t0)/5:.5g}")
        painter.drawLine(x-7, int(yy), x, int(yy))
        painter.drawText(QRect(10, int(yy)-15, 97, 30), Qt.AlignRight | Qt.AlignVCenter, f"{(f0+i*(f1-f0)/5)/1000:.5g}")
    painter.drawText(QRect(x, 825, w, 35), Qt.AlignHCenter, "时间 (s)")
    painter.save()
    painter.translate(30, y+h/2)
    painter.rotate(-90)
    painter.drawText(QRect(-180, -20, 360, 35), Qt.AlignCenter, "频率 (kHz)")
    painter.restore()
    bar = np.ascontiguousarray(palette[np.linspace(255, 0, h).astype(int)][:, None, :])
    painter.drawImage(QRect(1210, y, 28, h), QImage(bar.data, 1, h, 3, QImage.Format_RGB888))
    painter.drawRect(1210, y, 28, h)
    for i in range(6):
        yy = int(y+h-i*h/5)
        painter.drawText(QRect(1248, yy-15, 140, 30), Qt.AlignLeft | Qt.AlignVCenter,
                         f"{result.levels[0]+i*(result.levels[1]-result.levels[0])/5:.4g}")
    painter.setFont(QFont("Microsoft YaHei UI", 9))
    painter.drawText(QRect(1185, 103, 210, 42), Qt.AlignCenter, "PSD (dB re 1 V²/Hz)")
    painter.end()
    return image
