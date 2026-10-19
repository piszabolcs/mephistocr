import os

import pytest
from PySide6.QtCore import QMimeData, QPointF, QProcess, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QLabel, QMessageBox

import helpers
from mephistocr import engine, theme
from mephistocr.main_window import MainWindow, fit_size


@pytest.fixture
def dialogs(monkeypatch):
    """Error dialogs are recorded instead of blocking the test."""
    shown = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: shown.append(a[2]))
    return shown


@pytest.fixture
def win(qtbot):
    w = MainWindow()
    qtbot.addWidget(w)
    yield w
    if w.proc and w.proc.state() != QProcess.NotRunning:
        w._kill_tree()
        w.proc.waitForFinished(5000)


def _run_to_end(qtbot, win, timeout=90_000):
    win._start()
    assert win._run_active
    qtbot.waitUntil(lambda: not win._run_active, timeout=timeout)


def test_start_refuses_backup_equal_to_target(win, tmp_path, dialogs):
    # C-7: backup == target (here: differently cased) used to start a run that
    # renamed originals in place and re-OCR'd them on every run.
    win.ed_target.setText(str(tmp_path))
    win.ed_backup.setText(str(tmp_path).upper())

    win._start()

    assert dialogs and "backup folder" in dialogs[0] and win.proc is None


def test_window_is_natively_framed_and_fits_small_screens(win):
    # T-6/T-7: no frameless/translucent/layered window; 1366x768 @125 % fits.
    win.show()
    assert not win.windowFlags() & Qt.FramelessWindowHint
    assert not win.testAttribute(Qt.WA_TranslucentBackground) and win.windowOpacity() == 1.0
    assert win.minimumSizeHint().height() <= 520 and win.minimumHeight() <= 520
    w, h = fit_size(1093, 576)
    assert w <= 1093 and h <= 576
    # offscreen there is no DWM: the fallback must be said, not assumed
    assert "Dark title bar unavailable" in win.log_view.toPlainText()


def test_size_and_position_are_remembered(qtbot, win):
    win.setGeometry(60, 50, 700, 500)
    win.close()

    again = MainWindow()
    qtbot.addWidget(again)
    assert again.geometry() == win.geometry()


def test_finished_states_are_shown(win):
    # C-8: after Stop the status stayed "Stopping…" and after FATAL "Collecting files…".
    win._handle_line("FATAL\tUnknown OCR language(s): xyz")
    win._on_finished(2, QProcess.NormalExit)
    assert win.lbl_status.text().startswith("Failed (exit 2): Unknown OCR language")

    win._stopping = True
    win._on_finished(1, QProcess.CrashExit)
    assert win.lbl_status.text() == "Stopped." and win.windowTitle().startswith("Stopped")

    win._handle_line("garbage\tline")
    assert "Unreadable engine output" in win.log_view.toPlainText()


def test_folder_run_logs_skips_in_amber_and_resets_progress(qtbot, win, tmp_path):
    target, backup = tmp_path / "target", tmp_path / "backup"
    helpers.text_pdf(str(target / "text.pdf"))
    enc = engine.pymupdf.open()
    enc.new_page().insert_text((72, 72), "secret " * 10)
    enc.save(str(target / "locked.pdf"), encryption=engine.pymupdf.PDF_ENCRYPT_AES_256,
             user_pw="u", owner_pw="o")
    win.ed_target.setText(str(target))
    win.ed_backup.setText(str(backup))
    win.progress.setRange(0, 10)
    win.progress.setValue(7)  # left over from an earlier run

    _run_to_end(qtbot, win)

    assert win.progress.value() == 2 and win.lbl_status.text().startswith("Done")
    html = win.log_view.toHtml()
    assert "Skipped (password-protected): locked.pdf" in win.log_view.toPlainText()
    assert theme.WARN_AMBER in html
    assert (backup / "_ocr_stderr.log").exists()


def test_stop_ends_in_stopped_state(qtbot, win, tmp_path):
    target = tmp_path / "target"
    for i in range(6):
        helpers.scanned_pdf(str(target / f"scan_{i}.pdf"), pages=(helpers.EN,) * 3)
    win.ed_target.setText(str(target))
    win.ed_backup.setText(str(tmp_path / "backup"))
    win.sp_workers.setValue(1)
    win._start()
    qtbot.waitUntil(lambda: win.progress.maximum() == 6, timeout=60_000)

    win._stop()
    qtbot.waitUntil(lambda: not win._run_active, timeout=30_000)

    assert win.lbl_status.text() == "Stopped." and win.btn_start.isEnabled()


def test_dropped_file_is_ocrd_next_to_the_untouched_original(qtbot, win, tmp_path):
    # T-21 acceptance: drop a PDF -> <name>_ocr.pdf appears beside it.
    src = helpers.scanned_pdf(str(tmp_path / "drop me.pdf"))
    before = open(src, "rb").read()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(src), QUrl.fromLocalFile(str(tmp_path / "notes.txt"))])
    win.dropEvent(QDropEvent(QPointF(5, 5), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier))
    assert win.tabs.currentIndex() == 1 and win.files_list.count() == 1
    for code, box in win.lang_boxes.items():
        box.setChecked(code == "eng")

    _run_to_end(qtbot, win)

    assert "OCR'd" in win.files_list.item(0).text()
    assert os.path.exists(engine.output_path(src)) and open(src, "rb").read() == before


def test_languages_come_from_tessdata_and_one_is_required(win, dialogs):
    # C-11: free text allowed unknown languages; now only installed ones exist.
    assert set(win.lang_boxes) == set(engine.available_languages())
    for box in win.lang_boxes.values():
        box.setChecked(False)
    win.add_files([helpers.text_pdf(os.path.join(os.environ["MEPHISTOCR_HOME"], "x.pdf"))])

    win._start()

    assert dialogs == ["Select at least one OCR language."]


def test_enter_starts_and_dim_text_meets_contrast(win, dialogs):
    # T-19: Enter in a field = Start; muted text >= 4.5:1 (WCAG AA).
    win.ed_target.returnPressed.emit()
    assert dialogs == ["Choose a valid target folder."]

    def luminance(hex_rgb):
        def channel(c):
            c = int(hex_rgb[c:c + 2], 16) / 255
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5)

    ratio = (luminance(theme.TEXT_DIM) + 0.05) / (luminance(theme.BG_BASE) + 0.05)
    assert ratio >= 4.5


def test_signature_shows_version_and_about_card_stays_on_screen(win):
    # T-15 / C-20 / C-22: one version source, escaped <name>, card on screen.
    from PySide6.QtWidgets import QLabel
    from mephistocr import __version__
    from mephistocr.signature import SignatureButton
    win.show()
    sig = win.findChild(SignatureButton)
    assert sig.text() == f"psz // v{__version__}"

    sig.click()

    card = sig._dialog
    text = " ".join(label.text() for label in card.findChildren(QLabel))
    assert f"v{__version__}" in text and "&lt;name&gt;_ocr.pdf" in text
    assert "github.com/piszabolcs/MephistOCR" in text
    assert win.screen().availableGeometry().contains(card.geometry())
    card.close()


def test_dpi_help_opens_a_rounded_card_without_system_shadow(win, qtbot):
    win.btn_dpi_help.click()
    popup = win.btn_dpi_help.popup
    assert popup is not None and popup.isVisible()
    assert popup.windowFlags() & Qt.NoDropShadowWindowHint
    assert popup.testAttribute(Qt.WA_TranslucentBackground)
    text = popup.findChild(QLabel, "HelpBody").text()
    for value in ("150", "200", "300"):
        assert value in text
    qtbot.keyClick(popup, Qt.Key_Escape)
    qtbot.waitUntil(lambda: not popup.isVisible(), timeout=1000)


def test_log_fades_only_towards_hidden_lines(win):
    win.show()
    for line in range(200):
        win._log(f"line {line}")
    bar = win.log_view.verticalScrollBar()
    assert bar.maximum() > 0  # the log must really scroll, or the checks pass vacuously
    fades = win.log_fade

    bar.setValue(bar.minimum())
    assert (fades.top.isVisible(), fades.bottom.isVisible()) == (False, True)
    bar.setValue(bar.maximum() // 2)
    assert (fades.top.isVisible(), fades.bottom.isVisible()) == (True, True)
    bar.setValue(bar.maximum())
    assert (fades.top.isVisible(), fades.bottom.isVisible()) == (True, False)
