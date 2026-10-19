"""
MephistOCR visual theme - a dark "infernal" palette: near-black base so the
ember-red gradient accent actually pops, instead of the generic flat
Bootstrap-danger red. Fits the Mephisto naming (deep red / hellfire).
"""

# Core palette
BG_BASE = "#120c0e"          # near-black, faint warm tint (not pure #000)
BG_RAISED = "#1d1618"        # menus, tooltips, dialogs
BG_PANEL = "rgba(255,255,255,0.035)"
BORDER_GLASS = "rgba(255,255,255,0.09)"
BG_WELL = "rgba(0,0,0,0.28)"  # the log and the file list, sunk into their cards

TEXT_PRIMARY = "#f3ecec"
TEXT_MUTED = "#b9a3a6"
TEXT_DIM = "#a08a8d"         # >= 4.5:1 on BG_BASE and on the cards (WCAG AA)

EMBER_1 = "#ff4d5e"   # bright ember highlight
EMBER_2 = "#c81236"   # core red
EMBER_3 = "#5c0512"   # deep blood shadow

OK_GREEN = "#57d68d"
WARN_AMBER = "#f0a94e"
ERR_RED = "#ff5b5b"

FONT_UI = "Segoe UI Variable Text, Segoe UI, sans-serif"
FONT_MONO = "Cascadia Code, Consolas, monospace"

EMBER_GRADIENT_H = f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {EMBER_1}, stop:1 {EMBER_2})"


def build_qss() -> str:
    return f"""
    QWidget {{
        background: transparent;
        color: {TEXT_PRIMARY};
        font-family: {FONT_UI};
        font-size: 13px;
    }}
    #RootShell {{ background: {BG_BASE}; }}
    #ContentArea[dimmed="true"] {{ background: rgba(0,0,0,0.18); }}
    QDialog, QMessageBox, QMenu, QToolTip {{
        background: {BG_RAISED};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER_GLASS};
    }}
    QMenu::item {{ padding: 5px 18px; }}
    QMenu::item:selected {{ background: {EMBER_2}; }}

    QFrame[card="true"] {{
        background: {BG_PANEL};
        border: 1px solid {BORDER_GLASS};
        border-radius: 10px;
    }}

    QLabel[role="section"] {{
        color: {TEXT_MUTED};
        font-weight: 600;
        font-size: 11px;
    }}
    QLabel[role="status"] {{ color: {TEXT_PRIMARY}; font-weight: 500; }}
    QLabel[role="counts"] {{ color: {TEXT_MUTED}; font-size: 12px; }}

    QTabWidget::pane {{ border: none; }}
    QTabBar::tab {{
        background: transparent;
        color: {TEXT_MUTED};
        padding: 6px 16px;
        border-bottom: 2px solid transparent;
        font-weight: 600;
    }}
    QTabBar::tab:selected {{ color: {TEXT_PRIMARY}; border-bottom: 2px solid {EMBER_1}; }}
    QTabBar::tab:focus {{ color: {EMBER_1}; }}

    QLineEdit, QComboBox, QSpinBox {{
        background: rgba(255,255,255,0.05);
        border: 1px solid {BORDER_GLASS};
        border-radius: 7px;
        padding: 6px 10px;
        color: {TEXT_PRIMARY};
        selection-background-color: {EMBER_2};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 1px solid {EMBER_1}; }}
    QLineEdit:disabled {{ color: {TEXT_DIM}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{ background: {BG_RAISED}; selection-background-color: {EMBER_2}; }}
    QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; }}

    QCheckBox, QRadioButton {{ spacing: 8px; color: {TEXT_PRIMARY}; }}
    QCheckBox:focus, QRadioButton:focus {{ color: {EMBER_1}; }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 16px; height: 16px;
        border: 1px solid {BORDER_GLASS};
        background: rgba(255,255,255,0.04);
    }}
    QCheckBox::indicator {{ border-radius: 4px; }}
    QRadioButton::indicator {{ border-radius: 8px; }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
        background: {EMBER_GRADIENT_H};
        border: 1px solid {EMBER_1};
    }}

    QPushButton#FolderBtn {{
        background: rgba(255,255,255,0.06);
        border: 1px solid {BORDER_GLASS};
        border-radius: 7px;
        padding: 6px 14px;
        color: {TEXT_PRIMARY};
        font-weight: 500;
    }}
    QPushButton#FolderBtn:hover {{ background: rgba(255,255,255,0.11); }}
    QPushButton#FolderBtn:focus {{ border: 1px solid {EMBER_1}; }}
    QPushButton#FolderBtn:disabled {{ color: {TEXT_DIM}; }}

    QPushButton#PrimaryBtn {{
        background: {EMBER_GRADIENT_H};
        border: 2px solid transparent;
        border-radius: 9px;
        padding: 8px 22px;
        color: white;
        font-weight: 600;
    }}
    QPushButton#PrimaryBtn:hover {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff6270, stop:1 #d81a3f);
    }}
    QPushButton#PrimaryBtn:focus {{ border: 2px solid {TEXT_PRIMARY}; }}
    QPushButton#PrimaryBtn:disabled {{ background: rgba(255,255,255,0.06); color: {TEXT_DIM}; }}

    QPushButton#StopBtn {{
        background: rgba(255,255,255,0.05);
        border: 1px solid rgba(255,91,91,0.4);
        border-radius: 9px;
        padding: 9px 20px;
        color: {ERR_RED};
        font-weight: 600;
    }}
    QPushButton#StopBtn:hover {{ background: rgba(255,91,91,0.12); }}
    QPushButton#StopBtn:focus {{ border: 1px solid {TEXT_PRIMARY}; }}
    QPushButton#StopBtn:disabled {{ color: {TEXT_DIM}; border: 1px solid {BORDER_GLASS}; }}

    QProgressBar {{
        background: rgba(255,255,255,0.05);
        border: none;
        border-radius: 6px;
        max-height: 10px;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{ background: {EMBER_GRADIENT_H}; border-radius: 6px; }}

    QTextEdit#LogView, QListWidget#FilesList {{
        background: {BG_WELL};
        border: 1px solid {BORDER_GLASS};
        border-radius: 8px;
        color: {TEXT_MUTED};
        font-size: 12px;
        padding: 6px;
    }}
    QTextEdit#LogView {{ font-family: {FONT_MONO}; }}
    QListWidget#FilesList:focus, QTextEdit#LogView:focus {{ border: 1px solid {EMBER_1}; }}
    QListWidget#FilesList::item:selected {{ background: rgba(255,77,94,0.18); color: {TEXT_PRIMARY}; }}

    QPushButton#SignatureBtn {{
        background: transparent;
        border: none;
        padding: 2px 6px;
        color: {TEXT_DIM};
        font-size: 11px;
        font-weight: 500;
    }}
    QPushButton#SignatureBtn[glowing="true"] {{ color: {EMBER_1}; }}

    QScrollArea {{ border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 10px 2px; }}
    QScrollBar::handle:vertical {{
        background: rgba(255,255,255,0.15); border-radius: 5px; min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,0.25); }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
    """
