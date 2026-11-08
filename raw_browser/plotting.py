"""Figure creation shared by the interactive canvas and PNG export."""
from __future__ import annotations

import math
import threading
from pathlib import Path

import matplotlib
import numpy as np
from matplotlib.figure import Figure
from matplotlib.ticker import AutoLocator, FuncFormatter

from .core import HOP, SAMPLE_RATE, WINDOW_SIZE, Spectrum
from .colormaps import PARULA_CMAP
from .raster import ViewportSpectrumImage
from .theme import COLORS

PLOT_LOCK = threading.RLock()


def configure_fonts() -> None:
    matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False


def _time_precision(span: float) -> int:
    return max(0, 2 - math.floor(math.log10(span)))


class _TimeLocator(AutoLocator):
    """Add the recording end to the automatic ticks when it is visible."""

    def __init__(self, duration: float):
        super().__init__()
        self.duration = duration

    def __call__(self):
        first, last = sorted(self.axis.get_view_interval())
        span = last - first
        minute_digits = len(f"{math.ceil(max(abs(first), abs(last)) / 60):02d}")
        label_chars = minute_digits + 4 + max(3, _time_precision(span))
        # Matplotlib's tick space assumes labels three font sizes wide.
        # Allow four characters per slot for labels such as 02:54.763.
        labels_fit = 4 * max(1, self.axis.get_tick_space()) / label_chars
        self.set_params(nbins=max(1, min(9, int(labels_fit))))
        ticks = super().__call__()
        if first <= self.duration <= math.nextafter(last, math.inf):
            ticks = ticks[np.abs(ticks - self.duration) >= span / labels_fit]
            return np.sort(np.append(ticks, self.duration))
        return ticks


def set_time_format(ax, duration: float, *, minutes: bool) -> None:
    def format_tick(value, _position):
        first, last = ax.get_xlim()
        digits = _time_precision(abs(last - first))
        if value == duration:
            digits = max(3, digits)
        whole, _, fraction = f"{abs(value):.{digits}f}".partition(".")
        if minutes:
            minute, second = divmod(int(whole), 60)
            whole = f"{minute:02d}:{second:02d}"
        fraction = fraction.rstrip("0")
        label = f"{whole}.{fraction}" if fraction else whole
        return f"-{label}" if value < 0 else label

    ax.xaxis.set_major_formatter(FuncFormatter(format_tick))
    ax.set_xlabel("时间 (分:秒)" if minutes else "时间 (s)")


def clear_spectrum(figure: Figure) -> None:
    resize_id = getattr(figure, "_spectrum_resize_id", None)
    if resize_id is not None:
        figure.canvas.mpl_disconnect(resize_id)
        figure._spectrum_resize_id = None
    # Matplotlib artists can survive Figure.clear() until cyclic GC runs.
    for axes in figure.axes:
        for image in axes.images:
            if isinstance(image, ViewportSpectrumImage):
                image.release_source()
    figure.clear()


def draw_spectrum(figure: Figure, result: Spectrum, band: tuple[float, float], *,
                  minutes: bool = False, compact: bool = False):
    frequencies, values = result.band_data(band)
    low, high = result.color_limits()
    clear_spectrum(figure)
    ax = figure.add_subplot(111)
    half_time = HOP / SAMPLE_RATE / 2
    half_freq = SAMPLE_RATE / WINDOW_SIZE / 2
    extent = (result.times[0] - half_time, result.times[-1] + half_time,
              (frequencies[0] - half_freq) / 1000, (frequencies[-1] + half_freq) / 1000)
    ax.set_aspect("auto")
    image = ViewportSpectrumImage(ax, values, extent, cmap=PARULA_CMAP)
    image.set_clim(low, high)
    ax.add_image(image)
    duration = result.duration
    ax.set_xlim(0, duration)
    ax.set_ylim(band[0] / 1000, band[1] / 1000)
    ax.xaxis.set_major_locator(_TimeLocator(duration))
    set_time_format(ax, duration, minutes=minutes)
    ax.set_ylabel("频率 (kHz)")
    mode = (f"带通 {result.settings.low_hz / 1000:g}–{result.settings.high_hz / 1000:g} kHz"
            if result.settings.filter_enabled else "未滤波")
    ax.grid(alpha=0.16)
    if compact:
        # The desktop header already identifies the file and processing mode.
        # Reserve fixed typographic space for axes, not a percentage of a large canvas.
        color_axes = figure.add_axes((0.94, 0.1, 0.012, 0.85))
        colorbar = figure.colorbar(image, cax=color_axes, label="功率/频率(dB/Hz)")
        for axis in (ax, color_axes):
            axis.tick_params(labelsize=9, colors=COLORS["muted"], length=3)
            axis.xaxis.label.set_color(COLORS["muted"])
            axis.yaxis.label.set_color(COLORS["muted"])
            axis.xaxis.label.set_fontsize(10)
            axis.yaxis.label.set_fontsize(10)
            for spine in axis.spines.values():
                spine.set_color(COLORS["border"])
        colorbar.outline.set_linewidth(0.5)

        def fit_canvas(_event=None):
            width, height = figure.get_size_inches() * 72
            left, bottom = 46 / width, 36 / height
            right, top = 1 - 78 / width, 1 - 10 / height
            ax.set_position((left, bottom, right - left, top - bottom))
            color_axes.set_position((right + 12 / width, bottom, 9 / width, top - bottom))

        fit_canvas()
        figure._spectrum_resize_id = figure.canvas.mpl_connect("resize_event", fit_canvas)
    else:
        # Exported images are standalone, so retain their identifying title.
        ax.set_title(f"{result.path.name}\n{mode} · 功率谱密度", fontsize=11, parse_math=False)
        figure.colorbar(image, ax=ax, label="功率/频率(dB/Hz)", pad=0.02)
        figure.subplots_adjust(left=0.075, right=0.96, bottom=0.12, top=0.88)
    return ax


def save_unique_png(figure: Figure, directory: Path, stem: str, *, dpi: int = 130) -> Path:
    """Exclusive creation prevents accidental overwrites, including concurrent exports."""
    directory = Path(directory)
    suffix = 1
    while True:
        name = f"{stem}.png" if suffix == 1 else f"{stem}_{suffix}.png"
        path = directory / name
        try:
            handle = path.open("xb")
            break
        except FileExistsError:
            suffix += 1
    try:
        with handle, PLOT_LOCK:
            figure.savefig(handle, format="png", dpi=dpi)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path
