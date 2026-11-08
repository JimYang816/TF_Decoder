"""Render only the visible spectral cells, at the output's pixel resolution."""
from __future__ import annotations

import math

import numpy as np
from matplotlib.image import AxesImage


class ViewportSpectrumImage(AxesImage):
    """Pixel-sized nearest-neighbor sampling for the currently displayed file.

    The source is a view of the unchanged float32 spectrum cache. A coarse
    view uses original cells rather than changing their intensities through
    maximum/mean aggregation. Fine zooms return to all original cells.
    make_image is also used during savefig,
    so PNG output adapts to its actual DPI and viewport rather than a screenshot.
    """

    def __init__(self, axes, values, extent, **kwargs):
        super().__init__(axes, origin="lower", interpolation="nearest",
                         interpolation_stage="data", **kwargs)
        self._source = values
        self._source_extent = tuple(extent)
        self._dx = (extent[1] - extent[0]) / values.shape[1]
        self._dy = (extent[3] - extent[2]) / values.shape[0]
        self._view_key = None
        self.set_data(np.zeros((1, 1), dtype=np.float32))
        self.set_extent(extent)
        self.set_clip_path(axes.patch)

    def release_source(self):
        self._source = None

    def _prepare_view(self, renderer, magnification):
        if self._source is None:
            return False
        left, right, bottom, top = self._source_extent
        x0, x1 = sorted(self.axes.get_xlim())
        y0, y1 = sorted(self.axes.get_ylim())
        if x1 <= left or x0 >= right or y1 <= bottom or y0 >= top:
            return False
        rows, columns = self._source.shape
        start = max(0, math.floor((x0 - left) / self._dx) - 1)
        stop = min(columns, math.ceil((x1 - left) / self._dx) + 1)
        row_start = max(0, math.floor((y0 - bottom) / self._dy) - 1)
        row_stop = min(rows, math.ceil((y1 - bottom) / self._dy) + 1)
        pixels = max(1, self.axes.get_window_extent(renderer).width * magnification)
        ratio = max(1.0, (stop - start) / pixels)
        level = min(int(math.log2(ratio)), int(math.ceil(math.log2(columns))))
        stride = 2 ** level
        first = start // stride
        last = min((columns + stride - 1) // stride, (stop + stride - 1) // stride)
        key = (level, first, last, row_start, row_stop)
        if key != self._view_key:
            indices = np.minimum(np.arange(first, last) * stride + (stride - 1) // 2,
                                 columns - 1)
            self.set_data(self._source[row_start:row_stop, indices])
            self.set_extent((left + first * stride * self._dx,
                             left + last * stride * self._dx,
                             bottom + row_start * self._dy,
                             bottom + row_stop * self._dy))
            self._view_key = key
        return True

    def make_image(self, renderer, magnification=1.0, unsampled=False):
        if not self._prepare_view(renderer, magnification):
            return None, 0, 0, None
        return super().make_image(renderer, magnification, unsampled)

    def get_cursor_data(self, event):
        # Report the exact original cell under the cursor at every zoom level.
        if self._source is None or event.xdata is None or event.ydata is None:
            return None
        left, right, bottom, top = self._source_extent
        if not (left <= event.xdata < right and bottom <= event.ydata < top):
            return None
        column = int((event.xdata - left) / self._dx)
        row = int((event.ydata - bottom) / self._dy)
        return self._source[row, column]
