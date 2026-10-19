"""Small "?" buttons that open a rounded help card next to a setting.

The card is a frameless Qt.Popup (Esc and click-outside close it). Windows
gives popup windows a square system drop shadow, which looks wrong around a
rounded card, so the window asks for no system shadow, stays transparent and
the card draws its own rounded shadow inside the window margins.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from . import theme

SHADOW_MARGIN = 18
CARD_WIDTH = 380

DPI_HELP_TITLE = "DPI – how sharp the pages are read"
DPI_HELP_HTML = """
<p>Before OCR every page is rendered to an image at this resolution, and
Tesseract reads the text from that image. The saved PDF keeps the original
page images; only the hidden text layer depends on this setting.</p>
<table cellspacing="0" cellpadding="3">
<tr><td><b>150</b></td><td>Fastest, least memory. Fine for clean scans with
normal-sized text (11&nbsp;pt and up). Small print may be misread.</td></tr>
<tr><td><b>200</b></td><td><i>Default.</i> Good balance for everyday office
documents, letters and forms.</td></tr>
<tr><td><b>300</b></td><td>Best accuracy for small print (8–10&nbsp;pt),
tables, footnotes and faded or low-contrast scans. About twice as slow as
200 and uses more memory.</td></tr>
</table>
<p><b>Going higher</b> (400–500) rarely helps: Tesseract is tuned for text
around 300&nbsp;dpi, and extra pixels mostly cost time. It only pays off for
tiny print below 8&nbsp;pt.</p>
<p><b>Rule of thumb:</b> start with 200; if words come out garbled, try 300.
A higher value cannot add detail the scan does not have – a 150&nbsp;dpi
scan read at 300 is not sharper. Very large pages (e.g. A0 drawings) are
scaled down automatically to keep memory in check.</p>
"""


class HelpPopup(QWidget):
    """Rounded help card shown under (or above) the button that opened it."""

    def __init__(self, title: str, body_html: str, parent: QWidget | None = None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setObjectName("HelpPopup")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN)

        card = QWidget()
        card.setObjectName("HelpCard")
        card.setFixedWidth(CARD_WIDTH)
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 170))
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 14, 18, 16)
        lay.setSpacing(8)
        heading = QLabel(title)
        heading.setObjectName("HelpTitle")
        heading.setWordWrap(True)
        body = QLabel(body_html)
        body.setObjectName("HelpBody")
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)
        lay.addWidget(heading)
        lay.addWidget(body)

        self.setStyleSheet(f"""
            #HelpPopup {{ background: transparent; border: none; }}
            #HelpCard {{
                background: rgba(20, 13, 15, 0.98);
                border: 1px solid {theme.BORDER_GLASS};
                border-radius: 12px;
            }}
            #HelpTitle {{
                color: {theme.TEXT_PRIMARY}; font-family: {theme.FONT_UI};
                font-size: 14px; font-weight: 700;
            }}
            #HelpBody {{
                color: {theme.TEXT_MUTED}; font-family: {theme.FONT_UI}; font-size: 12px;
            }}
        """)

    def show_near(self, anchor: QWidget) -> None:
        """Place the card below the anchor (above it when there is no room),
        kept inside the screen's available area."""
        self.adjustSize()
        below = anchor.mapToGlobal(QPoint(0, anchor.height()))
        pos = QPoint(below.x() - SHADOW_MARGIN, below.y() - SHADOW_MARGIN + 6)
        screen = QGuiApplication.screenAt(below) or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            if pos.y() + self.height() > area.bottom():
                top = anchor.mapToGlobal(QPoint(0, 0)).y()
                pos.setY(top - self.height() + SHADOW_MARGIN - 6)
            pos.setX(max(area.left(), min(pos.x(), area.right() - self.width())))
            pos.setY(max(area.top(), pos.y()))
        self.move(pos)
        self.show()


class HelpButton(QPushButton):
    """A small round "?" that opens a HelpPopup."""

    def __init__(self, title: str, body_html: str, parent: QWidget | None = None):
        super().__init__("?", parent)
        self.setObjectName("HelpButton")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(18, 18)
        self.setToolTip("What does this setting do?")
        self.setAccessibleName(f"Help: {title}")
        self._title = title
        self._body = body_html
        self.popup: HelpPopup | None = None
        self.setStyleSheet(f"""
            #HelpButton {{
                background: transparent; color: {theme.TEXT_MUTED};
                border: 1px solid {theme.TEXT_DIM}; border-radius: 9px;
                font-size: 11px; font-weight: 700; padding: 0;
            }}
            #HelpButton:hover, #HelpButton:focus {{
                color: {theme.TEXT_PRIMARY}; border-color: {theme.EMBER_1};
            }}
        """)
        self.clicked.connect(self.open_help)

    def open_help(self) -> None:
        self.popup = HelpPopup(self._title, self._body, self.window())
        self.popup.show_near(self)
