"""
Author signature: a small `psz // v{version}` footer button that glows in the
app's accent colour on hover/focus and opens a compact "app info" card on
click (name + version, purpose, architecture layers, stack, repositories).

Self-contained: it borrows only the theme's colours (so the glow is never a
hardcoded colour), the app assets and `__version__` - the single source of
the version number.
"""
from __future__ import annotations

import html
import os

from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QPushButton, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGraphicsDropShadowEffect,
)

from . import __version__, theme

SIGNATURE_LABEL = f"psz // v{__version__}"

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
MARK_PATH = os.path.join(ASSETS_DIR, "mark.png")

# Accent for the glow — pulled from the theme, never hardcoded here, so the
# signature restyles itself for free if the palette changes.
ACCENT = theme.EMBER_1

GLOW_MAX = 22          # blur radius at full hover
GLOW_ANIM_MS = 170     # matches the app's other 150-200ms transitions

ABOUT = {
    "what": ("Makes scanned PDFs and TIFFs searchable by adding an invisible OCR text "
             "layer (saved as <name>_ocr.pdf) — a whole folder tree in place, or just a "
             "few files. The original pages stay exactly as they were; nothing is deleted."),
    "layers": [
        ("GUI", "PySide6 window; runs the engine as a subprocess (QProcess + a "
                "tab-separated progress protocol)"),
        ("Engine", "supervisor with crash-isolated worker processes, a watchdog and a "
                   "per-page timeout"),
        ("OCR", "MuPDF's built-in Tesseract with bundled language data"),
        ("CLI", "the same engine from a terminal: python main.py file.pdf"),
    ],
    "built": "Python · PySide6 (Qt 6) · PyMuPDF / MuPDF · Tesseract",
    "repos": [
        ("https://github.com/piszabolcs/MephistOCR", ""),
        ("pymupdf/PyMuPDF", "https://github.com/pymupdf/PyMuPDF"),
        ("tesseract-ocr/tesseract", "https://github.com/tesseract-ocr/tesseract"),
        ("tesseract-ocr/tessdata_fast", "https://github.com/tesseract-ocr/tessdata_fast"),
        ("pyside/pyside-setup", "https://code.qt.io/cgit/pyside/pyside-setup.git/"),
    ],
}


class SignatureButton(QPushButton):
    """Low-emphasis footer signature. Glows on hover/keyboard-focus, opens the
    app info card on click."""

    def __init__(self, parent=None):
        super().__init__(SIGNATURE_LABEL, parent)
        self.setObjectName("SignatureBtn")
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self.setToolTip("About MephistOCR")
        self.setFocusPolicy(Qt.TabFocus)

        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setOffset(0, 0)
        self._glow.setBlurRadius(0)
        self._glow.setColor(QColor(ACCENT))
        self.setGraphicsEffect(self._glow)

        self._anim = QPropertyAnimation(self._glow, b"blurRadius", self)
        self._anim.setDuration(GLOW_ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

        self._dialog: AboutDialog | None = None
        self.clicked.connect(self._open_about)

    # --- glow on hover + keyboard focus (both, for accessibility) ---------
    def _set_glow(self, on: bool):
        self._anim.stop()
        self._anim.setStartValue(self._glow.blurRadius())
        self._anim.setEndValue(GLOW_MAX if on else 0.0)
        self._anim.start()
        # slight text brighten so it also reads without the shadow (and for
        # users who can't perceive the soft glow)
        self.setProperty("glowing", "true" if on else "false")
        self.style().unpolish(self); self.style().polish(self)

    def enterEvent(self, event):
        self._set_glow(True); super().enterEvent(event)

    def leaveEvent(self, event):
        if not self.hasFocus():
            self._set_glow(False)
        super().leaveEvent(event)

    def focusInEvent(self, event):
        self._set_glow(True); super().focusInEvent(event)

    def focusOutEvent(self, event):
        if not self.underMouse():
            self._set_glow(False)
        super().focusOutEvent(event)

    # --- popup ------------------------------------------------------------
    def _open_about(self):
        dlg = AboutDialog(self.window())
        dlg.show_centered_over(self.window())
        self._dialog = dlg  # keep a reference so it isn't GC'd while open


class AboutDialog(QWidget):
    """Compact app info card. A Qt.Popup, so click-outside and Esc close it
    for free, plus an explicit close button."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setObjectName("AboutPopup")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self._build()
        self.setStyleSheet(_about_qss())

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)  # room for the rounded shadow

        card = QWidget(); card.setObjectName("AboutCard")
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(28); shadow.setOffset(0, 6); shadow.setColor(QColor(0, 0, 0, 170))
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(22, 18, 22, 20)
        lay.setSpacing(10)

        header = QHBoxLayout(); header.setSpacing(10)
        mark = QLabel()
        pix = QPixmap(MARK_PATH)
        if not pix.isNull():
            mark.setPixmap(pix.scaled(28, 28, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        header.addWidget(mark)
        title = QLabel("MephistOCR"); title.setObjectName("AboutTitle")
        header.addWidget(title)
        version = QLabel(f"v{__version__}"); version.setObjectName("AboutVersion")
        header.addWidget(version, 0, Qt.AlignBottom)
        header.addStretch(1)
        btn_close = QPushButton("✕"); btn_close.setObjectName("AboutClose")
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.setToolTip("Close  (Esc)")
        btn_close.clicked.connect(self.close)
        header.addWidget(btn_close, 0, Qt.AlignTop)
        lay.addLayout(header)
        lay.addWidget(_divider())

        lay.addWidget(_section("WHAT IT DOES"))
        lay.addWidget(_body(html.escape(ABOUT["what"])))

        lay.addWidget(_section("ARCHITECTURE"))
        rows = "".join(f"<div style='margin:2px 0'><b>{html.escape(name)}</b> — {html.escape(text)}</div>"
                       for name, text in ABOUT["layers"])
        lay.addWidget(_body(rows))

        lay.addWidget(_section("BUILT WITH"))
        lay.addWidget(_body(html.escape(ABOUT["built"])))

        lay.addWidget(_section("REPOSITORIES"))
        links = "<br>".join(
            f"<a style='color:{theme.EMBER_1}' href='{url}'>{html.escape(name)}</a>" if url
            else html.escape(name) for name, url in ABOUT["repos"])
        repos = _body(links)
        repos.setOpenExternalLinks(True)
        lay.addWidget(repos)

        self.setFixedWidth(440)

    def show_centered_over(self, ref: QWidget):
        """Centre over `ref`, kept fully on that screen (also on monitors with
        negative coordinates)."""
        self.adjustSize()
        if ref is not None:
            geo = ref.frameGeometry()
            avail = ref.screen().availableGeometry()
            x = geo.center().x() - self.width() // 2
            y = geo.center().y() - self.height() // 2
            x = max(avail.left(), min(x, avail.right() - self.width() + 1))
            y = max(avail.top(), min(y, avail.bottom() - self.height() + 1))
            self.move(x, y)
        self.show()


# ---------------------------------------------------------------------------
# small builders
# ---------------------------------------------------------------------------
def _section(text: str) -> QLabel:
    lbl = QLabel(text); lbl.setObjectName("AboutSection")
    return lbl


def _body(rich_text: str) -> QLabel:
    """Callers must html.escape() any plain text they pass in."""
    lbl = QLabel(rich_text); lbl.setObjectName("AboutBody")
    lbl.setWordWrap(True)
    lbl.setTextFormat(Qt.RichText)
    return lbl


def _divider() -> QWidget:
    line = QWidget(); line.setObjectName("AboutDivider"); line.setFixedHeight(1)
    return line


def _about_qss() -> str:
    return f"""
    #AboutPopup {{ background: transparent; border: none; }}
    #AboutCard {{
        background: rgba(20, 13, 15, 0.98);
        border: 1px solid {theme.BORDER_GLASS};
        border-radius: 12px;
    }}
    #AboutTitle {{
        color: {theme.TEXT_PRIMARY};
        font-family: {theme.FONT_UI};
        font-size: 16px;
        font-weight: 700;
    }}
    #AboutVersion {{
        color: {ACCENT};
        font-family: {theme.FONT_UI};
        font-size: 12px;
        font-weight: 600;
    }}
    #AboutClose {{
        background: transparent; border: none; border-radius: 6px;
        color: {theme.TEXT_MUTED}; font-size: 13px; min-width: 26px; min-height: 24px;
    }}
    #AboutClose:hover, #AboutClose:focus {{ background: {theme.ERR_RED}; color: white; }}
    #AboutDivider {{ background: {theme.BORDER_GLASS}; }}
    #AboutSection {{
        color: {theme.TEXT_MUTED};
        font-family: {theme.FONT_UI};
        font-size: 10px;
        font-weight: 700;
    }}
    #AboutBody {{
        color: {theme.TEXT_MUTED};
        font-family: {theme.FONT_UI};
        font-size: 12px;
    }}
    """
