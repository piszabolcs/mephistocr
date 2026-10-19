"""
Native window-frame theming through DWM: a dark caption in the app's colours.

The window keeps the normal Windows frame, so edge/corner resizing, maximize,
double-click, Aero Snap / Snap Layouts, Win+arrow keys, the system menu and
per-monitor DPI all come from Windows itself. This module only tints it.
Windows 11 honours the caption/text/border colours; Windows 10 (20H1+) only
the dark mode. Every HRESULT is checked, so the caller learns whether the
tint really applied instead of assuming it did.
"""
from __future__ import annotations

import ctypes
import sys

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36


def colorref(hex_rgb: str) -> int:
    """'#rrggbb' -> Win32 COLORREF (0x00BBGGRR)."""
    r, g, b = (int(hex_rgb[i:i + 2], 16) for i in (1, 3, 5))
    return r | (g << 8) | (b << 16)


def _set_attr(hwnd: int, attr: int, value: int) -> bool:
    val = ctypes.c_int(value)
    try:
        hr = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), attr, ctypes.byref(val), ctypes.sizeof(val))
    except (AttributeError, OSError):
        return False
    return hr == 0  # S_OK


def apply_dark_frame(widget, caption: str, text: str, border: str) -> bool:
    """Tint the native caption of a top-level Qt window. Returns True only if
    Windows accepted at least the dark mode."""
    if sys.platform != "win32":
        return False
    try:
        hwnd = int(widget.winId())
    except Exception:
        return False
    dark = _set_attr(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 1)
    for attr, color in ((DWMWA_CAPTION_COLOR, caption), (DWMWA_TEXT_COLOR, text),
                        (DWMWA_BORDER_COLOR, border)):
        _set_attr(hwnd, attr, colorref(color))  # Windows 11 only; harmless elsewhere
    return dark
