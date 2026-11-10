"""Light and dark themes for the desktop app.

The theme is "system" (follow Windows/macOS/Linux), "light" or "dark". Light keeps
the platform's own style. Dark uses Qt's Fusion style with the palette below,
because the classic Windows style cannot draw dark controls.

Widgets that cache colours refresh them when they receive a palette change
event, so the theme can be switched while the app runs.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

THEMES = {"system": "System", "light": "Light", "dark": "Dark"}

PALETTE_EVENTS = (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange)

_native_style: str | None = None


def resolve(theme: str) -> str:
    """'light' or 'dark' for a theme setting."""
    if theme in ("light", "dark"):
        return theme
    scheme = QGuiApplication.styleHints().colorScheme()
    return "dark" if scheme == Qt.ColorScheme.Dark else "light"


def dark_palette() -> QPalette:
    window, base, alternate = QColor("#202124"), QColor("#17181a"), QColor("#26282b")
    text, dim = QColor("#e3e3e3"), QColor("#8a8d91")
    button, highlight = QColor("#2b2d31"), QColor("#3b82f6")
    p = QPalette()
    for role, color in [
        (QPalette.ColorRole.Window, window), (QPalette.ColorRole.WindowText, text),
        (QPalette.ColorRole.Base, base), (QPalette.ColorRole.AlternateBase, alternate),
        (QPalette.ColorRole.Text, text), (QPalette.ColorRole.Button, button),
        (QPalette.ColorRole.ButtonText, text), (QPalette.ColorRole.BrightText, QColor("#ff6b6b")),
        (QPalette.ColorRole.Highlight, highlight), (QPalette.ColorRole.HighlightedText, QColor("white")),
        (QPalette.ColorRole.ToolTipBase, button), (QPalette.ColorRole.ToolTipText, text),
        (QPalette.ColorRole.PlaceholderText, dim), (QPalette.ColorRole.Link, QColor("#7aa7ff")),
        (QPalette.ColorRole.Light, QColor("#3a3d42")), (QPalette.ColorRole.Midlight, QColor("#33363a")),
        (QPalette.ColorRole.Mid, QColor("#4a4d52")), (QPalette.ColorRole.Dark, QColor("#121314")),
        (QPalette.ColorRole.Shadow, QColor("#000000")),
    ]:
        p.setColor(role, color)
    disabled = QPalette.ColorGroup.Disabled
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText,
                 QPalette.ColorRole.ButtonText):
        p.setColor(disabled, role, dim)
    p.setColor(disabled, QPalette.ColorRole.Base, window)
    # Keep the selection visible in windows that are not active.
    p.setColor(QPalette.ColorGroup.Inactive, QPalette.ColorRole.Highlight, QColor("#2f5fa8"))
    return p


def apply_theme(theme: str, app: QApplication | None = None) -> str:
    """Apply a theme setting to the app; returns the resulting 'light' or 'dark'."""
    global _native_style
    app = app or QApplication.instance()
    if _native_style is None:
        _native_style = app.style().name()
    mode = resolve(theme)
    if mode == "dark":
        app.setStyle(QStyleFactory.create("Fusion"))
        app.setPalette(dark_palette())
    else:
        app.setStyle(QStyleFactory.create(_native_style))
        app.setPalette(app.style().standardPalette())
    return mode
