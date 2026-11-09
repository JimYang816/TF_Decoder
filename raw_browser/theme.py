"""Shared visual tokens for the desktop workspace and interactive chart."""

from PySide6.QtGui import QColor, QPalette
from pathlib import Path

COLORS = {
    "background": "#F1F5F9",
    "surface": "#FFFFFF",
    "text": "#172B43",
    "muted": "#526278",
    "border": "#CBD5E1",
    "control_border": "#8191A6",
    "primary": "#1E40AF",
    "hover": "#EFF6FF",
    "pressed": "#DBEAFE",
    "disabled": "#E9EEF5",
    "warning": "#92400E",
}


def workspace_palette():
    """Keep native Fusion indicators readable even when Windows uses dark mode."""
    palette = QPalette()
    roles = {
        "Window": "background", "WindowText": "text", "Base": "surface",
        "AlternateBase": "background", "Text": "text", "Button": "surface",
        "ButtonText": "text", "Highlight": "primary", "HighlightedText": "surface",
        "Light": "surface", "Midlight": "disabled", "Mid": "border",
        "Dark": "control_border", "Shadow": "muted",
    }
    for role, token in roles.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(COLORS[token]))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(COLORS["muted"]))
    return palette


def workspace_stylesheet():
    c = COLORS
    assets = Path(__file__).parent / "assets"
    check_icon = (assets / "check.svg").as_posix()
    up_icon = (assets / "chevron-up.svg").as_posix()
    down_icon = (assets / "chevron-down.svg").as_posix()
    return f"""
        QWidget#workspace {{ background: {c['background']}; color: {c['text']}; }}
        QWidget#panel {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px; }}
        QLabel {{ color: {c['text']}; background: transparent; }}
        QLabel#title {{ font-size: 19px; font-weight: 700; }}
        QLabel#section {{ font-size: 14px; font-weight: 600; }}
        QLabel#fileName {{ font-size: 16px; font-weight: 600; }}
        QLabel#secondary {{ color: {c['muted']}; font-size: 12px; }}
        QLabel#badge {{ color: {c['primary']}; background: {c['hover']}; padding: 5px 10px; border-radius: 4px; font-size: 12px; }}
        QLabel#pending {{ color: {c['warning']}; font-size: 12px; }}
        QPushButton, QLineEdit, QDoubleSpinBox, QComboBox {{
            background: {c['surface']}; color: {c['text']}; border: 1px solid {c['control_border']};
            border-radius: 5px; padding: 5px 9px; min-height: 22px;
        }}
        QPushButton:hover {{ background: {c['hover']}; border-color: {c['primary']}; }}
        QPushButton:pressed, QPushButton:checked {{ background: {c['pressed']}; color: {c['primary']}; border-color: {c['primary']}; }}
        QPushButton:focus, QLineEdit:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {c['primary']}; }}
        QPushButton#primary {{ background: {c['primary']}; color: white; border-color: {c['primary']}; }}
        QPushButton#primary:hover {{ background: #17348F; }}
        QPushButton#primary:focus {{ border-color: #93C5FD; }}
        QPushButton:disabled, QPushButton#primary:disabled,
        QLineEdit:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
            background: {c['disabled']}; color: {c['muted']}; border-color: {c['border']};
        }}
        QDoubleSpinBox {{ padding-right: 7px; }}
        QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
            subcontrol-origin: border; width: 22px; background: {c['background']};
            border: none; border-left: 1px solid {c['border']};
        }}
        QDoubleSpinBox::up-button {{ subcontrol-position: top right; border-top-right-radius: 5px; }}
        QDoubleSpinBox::down-button {{ subcontrol-position: bottom right; border-bottom-right-radius: 5px; }}
        QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{ background: {c['hover']}; }}
        QDoubleSpinBox::up-button:pressed, QDoubleSpinBox::down-button:pressed {{ background: {c['pressed']}; }}
        QDoubleSpinBox::up-button:disabled, QDoubleSpinBox::down-button:disabled,
        QDoubleSpinBox::up-button:off, QDoubleSpinBox::down-button:off {{ background: {c['disabled']}; }}
        QDoubleSpinBox::up-arrow {{ image: url("{up_icon}"); width: 10px; height: 6px; }}
        QDoubleSpinBox::down-arrow {{ image: url("{down_icon}"); width: 10px; height: 6px; }}
        QComboBox {{ padding-right: 28px; }}
        QComboBox::drop-down {{
            subcontrol-origin: border; subcontrol-position: top right; width: 24px;
            border: none; border-left: 1px solid {c['border']};
        }}
        QComboBox::down-arrow {{ image: url("{down_icon}"); width: 10px; height: 6px; }}
        QComboBox QAbstractItemView {{
            background: {c['surface']}; color: {c['text']};
            border: 1px solid {c['control_border']}; padding: 4px;
            selection-background-color: {c['primary']}; selection-color: {c['surface']};
            outline: none;
        }}
        QComboBox QAbstractItemView::item {{ min-height: 28px; padding: 2px 8px; }}
        QComboBox QAbstractItemView::item:hover {{ background: {c['hover']}; color: {c['text']}; }}
        QComboBox QAbstractItemView::item:selected {{ background: {c['primary']}; color: {c['surface']}; }}
        QCheckBox {{ color: {c['text']}; spacing: 6px; padding: 3px; border: 1px solid transparent; }}
        QCheckBox:focus {{ border: 1px solid {c['primary']}; border-radius: 4px; }}
        QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {c['control_border']}; border-radius: 3px; background: {c['surface']}; }}
        QCheckBox::indicator:checked {{ background: {c['primary']}; border-color: {c['primary']}; image: url("{check_icon}"); }}
        QProgressBar {{ background: {c['disabled']}; border: none; border-radius: 4px; text-align: center; color: {c['text']}; font-size: 12px; }}
        QProgressBar::chunk {{ background: #BFDBFE; border-radius: 4px; }}
        QFrame#divider {{ background: {c['border']}; border: none; }}
        QToolTip {{ background: {c['text']}; color: white; border: none; padding: 6px; }}
    """
