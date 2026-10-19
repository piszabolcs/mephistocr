"""
MephistOCR main window.

A normal, natively framed window whose caption is tinted to the dark theme
through DWM (win_frame.py). Resizing from every edge, maximize, double-click,
Aero Snap / Snap Layouts, Win+arrow keys, the system menu and per-monitor DPI
therefore come from Windows itself - the earlier frameless window re-built
dragging from Qt mouse events and lost most of that. Size and position are
remembered in an INI file (never the registry).

Two ways to work: the Folder tab OCRs a whole tree in place (originals move
to a backup folder); the Files tab OCRs a few dropped/selected files.

The actual OCR work never runs on this process/thread: it's launched as a
separate `python -m mephistocr.engine --worker <cfg>` subprocess via
QProcess, so a crash or a long CPU-bound page never touches the UI's event
loop. The engine's stderr is kept in <log folder>/_ocr_stderr.log.
"""
from __future__ import annotations

import dataclasses
import html
import json
import os
import subprocess
import sys
import tempfile
import uuid

from PySide6.QtCore import Qt, QEvent, QProcess, QSettings, QSize, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPalette, QTextCursor
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QProgressBar, QPushButton, QRadioButton, QScrollArea, QSizePolicy, QSpinBox,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from . import engine, theme, win_frame
from .help_popup import DPI_HELP_HTML, DPI_HELP_TITLE, HelpButton
from .scroll_fade import attach_scroll_fade, solid
from .signature import SignatureButton

APP_TITLE = "MephistOCR"
ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
WINDOW_ICON_PATH = os.path.join(ASSETS_DIR, "app.ico")

DEFAULT_SIZE = (900, 820)
MIN_SIZE = (640, 420)  # fits 1366x768 at 125 % (about 1093x576 usable)
LOG_MAX_BLOCKS, LOG_TRIM_BLOCKS = 800, 200
SOURCE_FILTER = "PDF and TIFF files (*.pdf *.tif *.tiff)"
ROW_STATUS = {"ocr": ("OCR'd", theme.OK_GREEN), "has_text": ("Already searchable", theme.TEXT_MUTED),
              "skip": ("Skipped", theme.WARN_AMBER), "error": ("Failed", theme.ERR_RED)}
# what the eye sees behind the log and the file list: base, card, then the well
WELL_COLOR = solid(theme.BG_BASE, theme.BG_PANEL, theme.BG_WELL)


def fit_size(avail_w: int, avail_h: int) -> tuple[int, int]:
    """Default window size: DEFAULT_SIZE, but never more than 90 % of the screen."""
    return (max(MIN_SIZE[0], min(DEFAULT_SIZE[0], int(avail_w * 0.9))),
            max(MIN_SIZE[1], min(DEFAULT_SIZE[1], int(avail_h * 0.9))))


def _card(*widgets) -> QFrame:
    f = QFrame()
    f.setProperty("card", "true")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(8)
    for w in widgets:
        lay.addWidget(w)
    return f


def _section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("role", "section")
    return lbl


def _path_row(edit: QLineEdit, on_browse) -> QWidget:
    btn = QPushButton("Browse…")
    btn.setObjectName("FolderBtn")
    btn.clicked.connect(on_browse)
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(edit, 1)
    lay.addWidget(btn)
    return row


class ElidedLabel(QLabel):
    """One-line label that elides long text instead of widening the window."""

    def __init__(self, text: str = ""):
        super().__init__(text)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().color(QPalette.WindowText))
        shown = self.fontMetrics().elidedText(self.text(), Qt.ElideMiddle, self.width())
        painter.drawText(self.contentsRect(), int(Qt.AlignLeft | Qt.AlignVCenter), shown)


class DropList(QListWidget):
    """The Files tab list; shows a drop hint while empty (the window handles drops)."""

    def sizeHint(self):
        return QSize(super().sizeHint().width(), 110)  # Qt's default 192 px starves the log

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.count() == 0:
            painter = QPainter(self.viewport())
            painter.setPen(QColor(theme.TEXT_DIM))
            painter.drawText(self.viewport().rect(), Qt.AlignCenter,
                             "Drop PDF or TIFF files here\nor use Add files…")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        if os.path.exists(WINDOW_ICON_PATH):
            self.setWindowIcon(QIcon(WINDOW_ICON_PATH))
        self.setAcceptDrops(True)
        self.setMinimumSize(*MIN_SIZE)

        self.proc: QProcess | None = None
        self._run_active = False
        self._stopping = False
        self._fatal = ""
        self._cfg_path: str | None = None
        self._stderr_file = None
        self._stderr_path = ""
        self._stdout_buf = b""      # partial-line carry-over between reads
        self._frame_themed = False
        self._rows: dict[str, QListWidgetItem] = {}
        self._settings = QSettings(os.path.join(engine.app_data_dir(), "settings.ini"),
                                   QSettings.IniFormat)

        self._build_ui()
        self.setStyleSheet(theme.build_qss())
        self._restore_geometry()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        root = QWidget()
        root.setObjectName("RootShell")
        self.setCentralWidget(root)
        root_lay = QVBoxLayout(root)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # Everything but the footer scrolls, so a short screen never cuts the
        # window off; on a tall one the log takes the extra space.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.content = QWidget()
        self.content.setObjectName("ContentArea")
        scroll.setWidget(self.content)
        self.page_fade = attach_scroll_fade(scroll, theme.BG_BASE)
        root_lay.addWidget(scroll, 1)
        c = QVBoxLayout(self.content)
        c.setContentsMargins(18, 14, 18, 8)
        c.setSpacing(12)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_folder_tab(), "Folder")
        self.tabs.addTab(self._build_files_tab(), "Files")
        self.tabs.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)  # spare height → log
        c.addWidget(_card(self.tabs))
        c.addWidget(_card(self._build_options()))

        self.btn_start = QPushButton("▶  Start")
        self.btn_start.setObjectName("PrimaryBtn")
        self.btn_start.setToolTip("Start OCR (Enter)")
        self.btn_start.clicked.connect(self._start)
        self.btn_stop = QPushButton("■  Stop")
        self.btn_stop.setObjectName("StopBtn")
        self.btn_stop.clicked.connect(self._stop)
        self.btn_stop.setEnabled(False)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        run_row = QWidget()
        rl = QHBoxLayout(run_row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(10)
        rl.addWidget(self.btn_start)
        rl.addWidget(self.btn_stop)
        rl.addWidget(self.progress, 1)
        self.lbl_status = ElidedLabel("Ready.")
        self.lbl_status.setProperty("role", "status")
        self.lbl_counts = QLabel("")
        self.lbl_counts.setProperty("role", "counts")
        c.addWidget(_card(run_row, self.lbl_status, self.lbl_counts))

        self.log_view = QTextEdit()
        self.log_view.setObjectName("LogView")
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(80)
        self.log_fade = attach_scroll_fade(self.log_view, WELL_COLOR)
        log_card = _card(_section_label("LOG"), self.log_view)
        log_card.layout().setStretch(1, 1)
        c.addWidget(log_card, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(14, 2, 14, 4)
        footer.addWidget(SignatureButton(), 0, Qt.AlignLeft)
        footer.addStretch(1)
        root_lay.addLayout(footer)

        for edit in (self.ed_target, self.ed_backup, self.ed_out):
            edit.returnPressed.connect(self._start)
        self._log("Pick a folder (or drop files) and press Start.", "muted")

    def _build_folder_tab(self) -> QWidget:
        self.ed_target = QLineEdit()
        self.ed_target.setPlaceholderText(r"e.g. D:\Scans\Archive")
        self.ed_backup = QLineEdit()
        self.ed_backup.setPlaceholderText("where the originals are moved to")
        self.chk_recursive = QCheckBox("Include subfolders")
        self.chk_recursive.setChecked(True)
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(0, 10, 0, 0)
        v.addWidget(_section_label("TARGET FOLDER  (scans are made searchable in place)"))
        v.addWidget(_path_row(self.ed_target, self._pick_target))
        v.addWidget(_section_label("BACKUP FOLDER  (originals are moved here, never deleted)"))
        v.addWidget(_path_row(self.ed_backup, self._pick_backup))
        v.addWidget(self.chk_recursive)
        v.addStretch(1)  # the Files tab is taller; keep these packed at the top
        return tab

    def _build_files_tab(self) -> QWidget:
        self.files_list = DropList()
        self.files_list.setObjectName("FilesList")
        self.files_list.setMinimumHeight(80)
        self.files_fade = attach_scroll_fade(self.files_list, WELL_COLOR)
        self.files_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.files_list.customContextMenuRequested.connect(self._file_menu)
        self.files_list.itemDoubleClicked.connect(lambda item: self._open(self._row_target(item)))
        self.files_list.setToolTip("Double-click opens the result; right-click for more")
        btn_add = QPushButton("Add files…")
        btn_add.setObjectName("FolderBtn")
        btn_add.clicked.connect(self._pick_files)
        btn_clear = QPushButton("Clear")
        btn_clear.setObjectName("FolderBtn")
        btn_clear.clicked.connect(self._clear_files)
        buttons = QHBoxLayout()
        buttons.addWidget(btn_add)
        buttons.addWidget(btn_clear)
        buttons.addStretch(1)

        self.rb_beside = QRadioButton("Next to each file as <name>_ocr.pdf (original untouched)")
        self.rb_folder = QRadioButton("Into a folder")
        self.rb_replace = QRadioButton("Next to each file, and move the original to a backup folder")
        self.rb_beside.setChecked(True)
        self.out_group = QButtonGroup(self)
        for rb in (self.rb_beside, self.rb_folder, self.rb_replace):
            self.out_group.addButton(rb)
            rb.toggled.connect(self._sync_output_row)
        self.ed_out = QLineEdit()
        self.out_row = _path_row(self.ed_out, self._pick_out)

        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(0, 10, 0, 0)
        v.addWidget(self.files_list)
        v.addLayout(buttons)
        v.addWidget(_section_label("OUTPUT"))
        for w in (self.rb_beside, self.rb_folder, self.rb_replace, self.out_row):
            v.addWidget(w)
        self._sync_output_row()
        return tab

    def _build_options(self) -> QWidget:
        self.lang_boxes: dict[str, QCheckBox] = {}
        default = engine.DEFAULT_LANGS.split("+")
        langs = engine.available_languages()
        langs.sort(key=lambda code: (code not in default,
                                     default.index(code) if code in default else 0, code))
        lang_row = QHBoxLayout()
        lang_row.setSpacing(14)
        for code in langs:
            cb = QCheckBox(engine.LANG_NAMES.get(code, code))
            cb.setChecked(code in default)
            cb.setToolTip(f"Tesseract language '{code}'. Each extra language slows OCR down.")
            self.lang_boxes[code] = cb
            lang_row.addWidget(cb)
        lang_row.addStretch(1)

        self.cb_dpi = QComboBox()
        self.cb_dpi.addItems(["150", "200", "300"])
        self.cb_dpi.setCurrentText("200")
        self.cb_dpi.setToolTip("Resolution used to read the pages. The output keeps the original images.")
        cpus = os.cpu_count() or 4
        self.sp_workers = QSpinBox()
        self.sp_workers.setRange(1, max(1, cpus))
        self.sp_workers.setValue(max(2, min(cpus - 4, 12)))
        self.sp_workers.setToolTip("Parallel OCR processes (one file each).")

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        grid.addWidget(_section_label("LANGUAGES"), 0, 0, 1, 2)
        grid.addLayout(lang_row, 1, 0, 1, 2)
        dpi_head = QHBoxLayout()
        dpi_head.setSpacing(6)
        dpi_head.addWidget(_section_label("DPI"))
        self.btn_dpi_help = HelpButton(DPI_HELP_TITLE, DPI_HELP_HTML)
        dpi_head.addWidget(self.btn_dpi_help)
        dpi_head.addStretch(1)
        grid.addLayout(dpi_head, 2, 0)
        grid.addWidget(_section_label("PARALLEL WORKERS"), 2, 1)
        grid.addWidget(self.cb_dpi, 3, 0)
        grid.addWidget(self.sp_workers, 3, 1)
        wrap = QWidget()
        wrap.setLayout(grid)
        return wrap

    # ------------------------------------------------------------------
    # window state
    # ------------------------------------------------------------------
    def _restore_geometry(self):
        saved = self._settings.value("window/geometry")
        # restoreGeometry moves a window that would be off-screen (e.g. a
        # monitor was unplugged) back onto an available screen.
        if saved is not None and self.restoreGeometry(saved):
            return
        avail = self.screen().availableGeometry()
        self.resize(*fit_size(avail.width(), avail.height()))

    def showEvent(self, event):
        super().showEvent(event)
        if not self._frame_themed:
            self._frame_themed = True
            if not win_frame.apply_dark_frame(self, theme.BG_BASE, theme.TEXT_PRIMARY, theme.EMBER_3):
                self._log("Dark title bar unavailable here — using the system window colours.", "muted")

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange:
            # Subtle dimming while inactive; replaces the old whole-window
            # opacity, which made the window a layered (WS_EX_LAYERED) one.
            self.content.setProperty("dimmed", "false" if self.isActiveWindow() else "true")
            self.content.style().unpolish(self.content)
            self.content.style().polish(self.content)

    def closeEvent(self, event):
        if self.proc and self.proc.state() != QProcess.NotRunning:
            self._kill_tree()
            self.proc.waitForFinished(3000)
        self._settings.setValue("window/geometry", self.saveGeometry())
        self._settings.sync()
        super().closeEvent(event)

    def _set_title(self, status: str = ""):
        self.setWindowTitle(f"{status} — {APP_TITLE}" if status else APP_TITLE)

    # ------------------------------------------------------------------
    # folder / file pickers, drag & drop
    # ------------------------------------------------------------------
    def _pick_target(self):
        d = QFileDialog.getExistingDirectory(self, "Choose the folder to OCR")
        if d:
            self._set_target(d)

    def _set_target(self, folder: str):
        folder = os.path.normpath(folder)
        self.ed_target.setText(folder)
        parent = os.path.dirname(folder)
        # suggest a sibling backup folder; a drive root has no sibling
        if not self.ed_backup.text().strip() and parent != folder:
            self.ed_backup.setText(os.path.join(parent, "OCR_originals_backup"))

    def _pick_backup(self):
        d = QFileDialog.getExistingDirectory(self, "Choose the backup folder")
        if d:
            self.ed_backup.setText(os.path.normpath(d))

    def _pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a folder")
        if d:
            self.ed_out.setText(os.path.normpath(d))

    def _pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Choose PDF or TIFF files", "", SOURCE_FILTER)
        self.add_files(files)

    def add_files(self, paths: list[str]):
        """Queue PDFs/TIFFs in the Files tab (duplicates and other files ignored)."""
        for p in paths:
            p = os.path.normpath(p)
            key = os.path.normcase(p)
            if key in self._rows or not p.lower().endswith(engine.PDF_EXTS + engine.TIFF_EXTS):
                continue
            item = QListWidgetItem(os.path.basename(p))
            item.setData(Qt.UserRole, p)
            item.setToolTip(p)
            self.files_list.addItem(item)
            self._rows[key] = item
        self.tabs.setCurrentIndex(1)

    def _clear_files(self):
        self.files_list.clear()
        self._rows.clear()

    def _sync_output_row(self):
        mode = self.out_group.checkedButton()
        self.out_row.setEnabled(mode is not self.rb_beside)
        self.ed_out.setPlaceholderText("backup folder for the originals" if mode is self.rb_replace
                                       else "output folder")

    @staticmethod
    def _dropped_paths(mime) -> list[str]:
        return [u.toLocalFile() for u in mime.urls() if u.isLocalFile()] if mime.hasUrls() else []

    def dragEnterEvent(self, event):
        if self._dropped_paths(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = self._dropped_paths(event.mimeData())
        files = [p for p in paths if os.path.isfile(p)]
        folders = [p for p in paths if os.path.isdir(p)]
        if files:
            self.add_files(files)
        elif folders:
            self._set_target(folders[0])
            self.tabs.setCurrentIndex(0)
        event.acceptProposedAction()

    # ------------------------------------------------------------------
    # file rows
    # ------------------------------------------------------------------
    @staticmethod
    def _row_target(item: QListWidgetItem) -> str:
        out = item.data(Qt.UserRole + 1)
        return out if out and os.path.exists(out) else item.data(Qt.UserRole)

    def _set_row(self, item: QListWidgetItem, text: str, color: str, tip: str = ""):
        item.setText(f"{os.path.basename(item.data(Qt.UserRole))}   —   {text}")
        item.setForeground(QColor(color))
        if tip:
            item.setToolTip(f"{item.data(Qt.UserRole)}\n{tip}")

    def _file_menu(self, pos):
        item = self.files_list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        menu.addAction("Open", lambda: self._open(self._row_target(item)))
        menu.addAction("Show in folder", lambda: self._show_in_folder(self._row_target(item)))
        menu.addAction("Remove from list", lambda: self._remove_row(item))
        menu.exec(self.files_list.viewport().mapToGlobal(pos))

    def _remove_row(self, item: QListWidgetItem):
        self._rows.pop(os.path.normcase(item.data(Qt.UserRole)), None)
        self.files_list.takeItem(self.files_list.row(item))

    @staticmethod
    def _open(path: str):
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    @staticmethod
    def _show_in_folder(path: str):
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    # ------------------------------------------------------------------
    # log helper
    # ------------------------------------------------------------------
    def _log(self, text: str, level: str = "info"):
        colors = {
            "ok": theme.OK_GREEN, "error": theme.ERR_RED, "warn": theme.WARN_AMBER,
            "muted": theme.TEXT_DIM, "info": theme.TEXT_MUTED,
        }
        color = colors.get(level, theme.TEXT_MUTED)
        # filenames can contain <, > or & — escape so they can't break the markup
        self.log_view.append(f'<span style="color:{color}">{html.escape(text)}</span>')
        self.log_view.moveCursor(QTextCursor.End)
        # keep the log from growing unbounded over a multi-hour run
        if self.log_view.document().blockCount() > LOG_MAX_BLOCKS:
            cursor = self.log_view.textCursor()
            cursor.movePosition(QTextCursor.Start)
            cursor.movePosition(QTextCursor.Down, QTextCursor.KeepAnchor, LOG_TRIM_BLOCKS)
            cursor.removeSelectedText()

    # ------------------------------------------------------------------
    # run / stop
    # ------------------------------------------------------------------
    def _langs(self) -> str:
        return "+".join(code for code, cb in self.lang_boxes.items() if cb.isChecked())

    def _job_from_form(self) -> engine.Job:
        common = dict(langs=self._langs(), dpi=int(self.cb_dpi.currentText()),
                      workers=self.sp_workers.value())
        if self.tabs.currentIndex() == 0:
            return engine.Job(target=self.ed_target.text().strip(), backup=self.ed_backup.text().strip(),
                              recursive=self.chk_recursive.isChecked(), **common)
        folder = self.ed_out.text().strip()
        files = [self.files_list.item(i).data(Qt.UserRole) for i in range(self.files_list.count())]
        return engine.Job(files=files, out_dir=folder if self.rb_folder.isChecked() else "",
                          backup=folder if self.rb_replace.isChecked() else "", **common)

    def _form_problem(self, job: engine.Job) -> str | None:
        if job.files is not None and not self.rb_beside.isChecked() and not self.ed_out.text().strip():
            return "Choose the " + ("backup" if self.rb_replace.isChecked() else "output") + " folder."
        return job.problem()

    def _start(self):
        if self._run_active:
            return
        job = self._job_from_form()
        problem = self._form_problem(job)
        if problem:
            QMessageBox.critical(self, "Cannot start", problem)
            return
        try:
            engine.prepare_dirs(job)
            self._stderr_path = os.path.join(job.log_dir(), "_ocr_stderr.log")
            self._stderr_file = open(self._stderr_path, "ab")
        except OSError as e:
            QMessageBox.critical(self, "Cannot start", f"Cannot create the folder:\n{e}")
            return

        self._reset_run_state(job)
        self._cfg_path = os.path.join(tempfile.gettempdir(), f"mephistocr_cfg_{uuid.uuid4().hex}.json")
        with open(self._cfg_path, "w", encoding="utf-8") as f:
            json.dump(dataclasses.asdict(job), f)

        engine_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.proc = QProcess(self)
        self.proc.setProgram(sys.executable)
        if getattr(sys, "frozen", False):
            # Frozen: sys.executable is MephistOCR.exe and there's no `python -m`.
            # main.py routes this back into the engine (see its --worker branch).
            self.proc.setArguments(["--worker", self._cfg_path])
        else:
            self.proc.setArguments(["-m", "mephistocr.engine", "--worker", self._cfg_path])
        self.proc.setWorkingDirectory(engine_dir)
        self.proc.setProcessChannelMode(QProcess.SeparateChannels)
        self.proc.readyReadStandardOutput.connect(self._read_stdout)
        self.proc.readyReadStandardError.connect(self._drain_stderr)
        self.proc.finished.connect(self._on_finished)
        self.proc.start()

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._run_active = True

    def _reset_run_state(self, job: engine.Job):
        self._stopping = False
        self._fatal = ""
        self._stdout_buf = b""
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.lbl_counts.setText("")
        self.lbl_status.setText("Collecting files…")
        self._set_title("Collecting files…")
        for i in range(self.files_list.count()):
            item = self.files_list.item(i)
            item.setData(Qt.UserRole + 1, None)
            if job.files is not None:
                self._set_row(item, "Queued", theme.TEXT_MUTED)
        self._log("──── START ────", "muted")

    def _kill_tree(self):
        """Kill the engine process AND its worker children.

        QProcess.kill() only terminates the direct child. The engine spawns a
        multiprocessing Manager plus N OCR workers; a hard kill of the parent
        skips multiprocessing's atexit cleanup, so those children would be
        orphaned and keep burning CPU. taskkill /T takes down the whole tree."""
        if not (self.proc and self.proc.state() != QProcess.NotRunning):
            return
        pid = int(self.proc.processId())
        if sys.platform == "win32" and pid:
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=10)
                return
            except (OSError, subprocess.SubprocessError):
                pass
        self.proc.kill()

    def _stop(self):
        if self.proc and self.proc.state() != QProcess.NotRunning:
            self._stopping = True
            self._kill_tree()
            self.lbl_status.setText("Stopping…")
            self.btn_stop.setEnabled(False)
            self._log("Stopped by user.", "warn")

    def _read_stdout(self):
        """Read stdout in chunks, carrying over any trailing partial line.

        QProcess hands us arbitrary byte chunks, so a protocol line (or even a
        multi-byte UTF-8 char) can be split across two reads. Splitting each
        chunk independently would silently corrupt progress messages."""
        if not self.proc:
            return
        self._stdout_buf += bytes(self.proc.readAllStandardOutput())
        *complete, self._stdout_buf = self._stdout_buf.split(b"\n")
        for raw in complete:
            line = raw.decode("utf-8", errors="replace").rstrip("\r")
            if line:
                self._handle_line(line)

    def _drain_stderr(self):
        """Keep the engine's stderr (tracebacks, MuPDF/Tesseract warnings) in
        _ocr_stderr.log instead of throwing it away."""
        if not self.proc:
            return
        data = bytes(self.proc.readAllStandardError())
        if self._stderr_file and data:
            try:
                self._stderr_file.write(data)
                self._stderr_file.flush()
            except OSError:
                pass

    def _counts_text(self, ht, ocr, sk, er) -> str:
        return f"OCR'd: {ocr}   |   Already searchable: {ht}   |   Skipped: {sk}   |   Failed: {er}"

    def _handle_line(self, line: str):
        parts = line.split("\t")
        kind = parts[0]
        try:
            if kind == "START":
                total, workers = int(parts[1]), parts[2]
                self.progress.setRange(0, max(1, total))
                self.progress.setValue(0)
                self.lbl_status.setText(f"Found {total} file(s) — {workers} parallel worker(s)")
                self._log(f"Found {total} PDF/TIFF file(s), {workers} worker(s).", "muted")
            elif kind == "PROG":
                done, total = int(parts[1]), int(parts[2])
                name = parts[7] if len(parts) > 7 else ""
                self.progress.setValue(done)
                self.lbl_status.setText(f"{done}/{total} — {name}")
                self.lbl_status.setToolTip(name)
                self.lbl_counts.setText(self._counts_text(*parts[3:7]))
                self._set_title(f"{int(done * 100 / total) if total else 0}% · {done}/{total}")
            elif kind == "LOG":
                self._log(parts[2] if len(parts) > 2 else "", parts[1])
            elif kind == "FILE":
                cat, status, src, out, extra = parts[1:6]
                item = self._rows.get(os.path.normcase(os.path.normpath(src)))
                if item is not None:
                    item.setData(Qt.UserRole + 1, out if cat == "ocr" else None)
                    text, color = ROW_STATUS.get(cat, (status, theme.TEXT_MUTED))
                    self._set_row(item, text, color, f"{status} {extra}".strip())
            elif kind == "DONE":
                ht, ocr, sk, er = parts[1:5]
                summary = f"Done — OCR'd {ocr}, failed {er}"
                self.lbl_status.setText(summary + ".")
                self.lbl_counts.setText(self._counts_text(ht, ocr, sk, er))
                self._log("──── DONE ────", "muted")
                self._set_title(summary)
            elif kind == "FATAL":
                self._fatal = parts[1] if len(parts) > 1 else ""
                self._log("Cannot run: " + self._fatal, "error")
            else:
                raise ValueError("unknown message")
        except (IndexError, ValueError) as e:
            self._log(f"Unreadable engine output ({e}): {line[:200]}", "warn")

    def _on_finished(self, exit_code: int = 0, exit_status=QProcess.NormalExit):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._run_active = False
        if self._stderr_file:
            self._stderr_file.close()
            self._stderr_file = None
        if self._cfg_path and os.path.exists(self._cfg_path):
            try: os.remove(self._cfg_path)
            except OSError: pass
        if self._stopping:
            status = "Stopped."
        elif exit_status != QProcess.NormalExit or exit_code != 0:
            status = f"Failed (exit {exit_code})" + (f": {self._fatal}" if self._fatal else ".")
            self._log(f"The engine stopped with exit code {exit_code}. Details: {self._stderr_path}",
                      "error")
        else:
            return  # DONE already set the final status
        self.lbl_status.setText(status)
        self._set_title(status.rstrip("."))
