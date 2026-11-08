"""Default rectangle zoom, mouse-centered wheel zoom and middle-button pan."""
from __future__ import annotations

import math

from matplotlib.backend_bases import MouseButton
from matplotlib.backend_tools import Cursors


class SpectrumNavigation:
    """Keep custom gestures in the standard toolbar's view history.

    Only the main spectrum axes participates; the colorbar and loading/error
    screens are deliberately excluded. Standard toolbar pan/rectangle modes
    retain their own mouse handling.
    """

    def __init__(self, canvas, toolbar):
        self.canvas = canvas
        self.toolbar = toolbar
        self.axes = None
        self.full_view = None
        self._drag = None
        self._selection = None
        canvas.mpl_connect("scroll_event", self._on_scroll)
        canvas.mpl_connect("button_press_event", self._on_press)
        canvas.mpl_connect("motion_notify_event", self._on_motion)
        canvas.mpl_connect("button_release_event", self._on_release)
        canvas.mpl_connect("figure_leave_event", self._on_leave)

    def bind_axes(self, axes):
        self._cancel_selection()
        self._drag = None
        self.axes = axes
        self.full_view = ((tuple(axes.get_xlim()), tuple(axes.get_ylim()))
                          if axes is not None else None)
        self.canvas.set_cursor(Cursors.POINTER)
        if axes is not None:
            # The UI resets the toolbar whenever a different plot is shown.
            self.toolbar.push_current()

    @staticmethod
    def _bounded(low, high, bounds):
        first, last = bounds
        width = high - low
        if width >= last - first:
            return first, last
        low = min(max(low, first), last - width)
        return low, low + width

    @classmethod
    def _scaled(cls, limits, bounds, anchor, factor):
        low, high = limits
        width = high - low
        full_width = bounds[1] - bounds[0]
        new_width = min(full_width, max(full_width * 1e-10, width * factor))
        fraction = min(1.0, max(0.0, (anchor - low) / width))
        first = anchor - fraction * new_width
        return cls._bounded(first, first + new_width, bounds)

    def _set_view(self, xlim, ylim, *, record=True):
        if self.axes is None:
            return False
        previous = (tuple(self.axes.get_xlim()), tuple(self.axes.get_ylim()))
        if previous == (tuple(xlim), tuple(ylim)):
            return False
        self.axes.set_xlim(xlim)
        self.axes.set_ylim(ylim)
        if record:
            self.toolbar.push_current()
        self.canvas.draw_idle()
        return True

    def zoom(self, factor, anchor=None, *, time_axis=True, frequency_axis=True):
        if self.axes is None or not math.isfinite(factor) or factor <= 0:
            return
        xlim, ylim = tuple(self.axes.get_xlim()), tuple(self.axes.get_ylim())
        if anchor is None:
            anchor = ((xlim[0] + xlim[1]) / 2, (ylim[0] + ylim[1]) / 2)
        if time_axis:
            xlim = self._scaled(xlim, self.full_view[0], anchor[0], factor)
        if frequency_axis:
            ylim = self._scaled(ylim, self.full_view[1], anchor[1], factor)
        self._set_view(xlim, ylim)

    def zoom_in(self):
        self.zoom(1 / 1.25)

    def zoom_out(self):
        self.zoom(1.25)

    def reset_view(self):
        self._cancel_selection()
        self._drag = None
        if self.full_view is not None:
            self._set_view(*self.full_view)
        self.canvas.set_cursor(Cursors.POINTER)

    def _on_scroll(self, event):
        if (self.axes is None or event.inaxes is not self.axes or self._drag is not None
                or self._selection is not None
                or getattr(self.toolbar, "_pan_info", None) is not None
                or getattr(self.toolbar, "_zoom_info", None) is not None):
            return
        if event.xdata is None or event.ydata is None or not event.step:
            return
        modifiers = set(getattr(event, "modifiers", ()) or ())
        modifiers.update((event.key or "").lower().split("+"))
        # Use the native wheel's modifiers too: on Qt, synthetic events and
        # some touchpads need not update QApplication.keyboardModifiers().
        native = getattr(event, "guiEvent", None)
        if native is not None and hasattr(native, "modifiers"):
            from PySide6.QtCore import Qt
            held = native.modifiers()
            if held & Qt.KeyboardModifier.ShiftModifier:
                modifiers.add("shift")
            if held & Qt.KeyboardModifier.ControlModifier:
                modifiers.add("ctrl")
        shift = "shift" in modifiers
        control = bool({"ctrl", "control"} & modifiers)
        step = max(-10.0, min(10.0, event.step))
        self.zoom(1.25 ** -step, (event.xdata, event.ydata),
                  time_axis=not control or shift, frequency_axis=not shift or control)

    def _on_press(self, event):
        if self.axes is None or event.inaxes is not self.axes or self.toolbar.mode:
            return
        if event.dblclick and event.button == MouseButton.LEFT:
            self.reset_view()
            return
        if event.button not in (MouseButton.LEFT, MouseButton.MIDDLE):
            return
        if event.x is None or event.y is None:
            return
        if event.button == MouseButton.LEFT:
            self._selection = (event.x, event.y)
            self.canvas.set_cursor(Cursors.SELECT_REGION)
        else:
            self._drag = (event.x, event.y, tuple(self.axes.get_xlim()),
                          tuple(self.axes.get_ylim()), self.axes.bbox.width,
                          self.axes.bbox.height, event.button)
            self.canvas.set_cursor(Cursors.MOVE)

    def _selection_box(self, event):
        if self._selection is None or self.axes is None or event.x is None or event.y is None:
            return None
        start_x, start_y = self._selection
        box = self.axes.bbox
        end_x = min(max(event.x, box.x0), box.x1)
        end_y = min(max(event.y, box.y0), box.y1)
        return (min(start_x, end_x), min(start_y, end_y),
                max(start_x, end_x), max(start_y, end_y))

    def _cancel_selection(self):
        if self._selection is not None:
            self.toolbar.remove_rubberband()
            self._selection = None

    def _finish_selection(self, event):
        box = self._selection_box(event)
        self._cancel_selection()
        self.canvas.set_cursor(Cursors.POINTER)
        if box is None:
            return
        x0, y0, x1, y1 = box
        minimum = 5 * self.canvas.device_pixel_ratio
        # Ordinary clicks and tiny hand movements must not accidentally zoom.
        if x1 - x0 < minimum or y1 - y0 < minimum:
            return
        first, last = self.axes.transData.inverted().transform(((x0, y0), (x1, y1)))
        self._set_view(self._bounded(first[0], last[0], self.full_view[0]),
                       self._bounded(first[1], last[1], self.full_view[1]))

    def _on_motion(self, event):
        if self._selection is not None:
            box = self._selection_box(event)
            if box is not None:
                self.toolbar.draw_rubberband(event, *box)
            return
        if self._drag is None or self.axes is None or event.x is None or event.y is None:
            return
        x, y, xlim, ylim, width, height, _ = self._drag
        if width <= 0 or height <= 0:
            return
        dx = (event.x - x) / width * (xlim[1] - xlim[0])
        dy = (event.y - y) / height * (ylim[1] - ylim[0])
        self._set_view(self._bounded(xlim[0] - dx, xlim[1] - dx, self.full_view[0]),
                       self._bounded(ylim[0] - dy, ylim[1] - dy, self.full_view[1]),
                       record=False)

    def _finish_drag(self):
        drag, self._drag = self._drag, None
        if drag is not None and self.axes is not None:
            previous = (drag[2], drag[3])
            current = (tuple(self.axes.get_xlim()), tuple(self.axes.get_ylim()))
            if current != previous:
                self.toolbar.push_current()
        self.canvas.set_cursor(Cursors.POINTER)

    def _on_release(self, event):
        if self._selection is not None and event.button == MouseButton.LEFT:
            self._finish_selection(event)
        elif self._drag is not None and event.button == self._drag[-1]:
            self._finish_drag()

    def _on_leave(self, _event):
        if self._selection is not None:
            self._cancel_selection()
            self.canvas.set_cursor(Cursors.POINTER)
        if self._drag is not None:
            self._finish_drag()
