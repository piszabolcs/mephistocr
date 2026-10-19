"""
Regenerate the README screenshots (docs/screenshot-*.png) from a generated demo
archive, with real OCR runs - without ever showing a window.

    python tools/make_screenshots.py          (needs requirements-dev.txt)

Qt renders offscreen (QT_QPA_PLATFORM=offscreen); the demo files are built in a
temp folder with tests/helpers.py and deleted afterwards. Before each grab the
folder fields are re-labelled with a neutral example path, so no local path or
user name ends up in an image. Offscreen there is no DWM, so the "dark title
bar unavailable" note is suppressed (the native caption is not in the image).
"""
import os
import shutil
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")  # offscreen QPA ships no fonts
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests")]
WORK = tempfile.mkdtemp(prefix="mephistocr_demo_")
os.environ["MEPHISTOCR_HOME"] = os.path.join(WORK, "home")

import helpers  # noqa: E402
import pymupdf  # noqa: E402
from PySide6.QtGui import QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mephistocr import win_frame  # noqa: E402

win_frame.apply_dark_frame = lambda *args, **kwargs: True
from mephistocr.main_window import MainWindow  # noqa: E402
from mephistocr.signature import SignatureButton  # noqa: E402

DOCS = os.path.join(ROOT, "docs")
SIZE = (900, 940)
LETTER = ["Dear Ms. Example,", "please find the signed maintenance contract attached.",
          "Kind regards, The Archive Team"]


def build_archive(root: str) -> None:
    j = os.path.join
    helpers.scanned_pdf(j(root, "Letters", "2019-03 letter.pdf"), pages=(LETTER, helpers.EN))
    helpers.scanned_pdf(j(root, "Letters", "Szerződés – ügyfél.pdf"), pages=(helpers.HU,))
    helpers.text_pdf(j(root, "Invoices", "invoice_0042.pdf"))
    mixed = helpers.text_pdf(j(root, "Invoices", "report_with_scanned_appendix.pdf"))
    doc = pymupdf.open(mixed)
    doc.insert_pdf(pymupdf.open(helpers.scanned_pdf(j(root, "_tmp.pdf"))))
    doc.saveIncr()
    doc.close()
    os.remove(j(root, "_tmp.pdf"))
    helpers.tiff(j(root, "Faxes", "fax_2021-11-02.tif"), pages=(helpers.EN, LETTER))
    os.makedirs(j(root, "Misc"), exist_ok=True)
    enc = pymupdf.open()
    enc.new_page().insert_text((72, 72), "confidential " * 8)
    enc.save(j(root, "Misc", "password_protected.pdf"),
             encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    with open(j(root, "Misc", "damaged.pdf"), "wb") as f:
        f.write(b"%PDF-1.7\n1 0 obj << /Type /Catalog >>\n garbage" * 20)


def run(app: QApplication, win: MainWindow, timeout: float = 180) -> None:
    win._start()
    end = time.time() + timeout
    while win._run_active and time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()


def window() -> MainWindow:
    win = MainWindow()
    win.resize(*SIZE)
    win.show()
    for code, box in win.lang_boxes.items():
        box.setChecked(code in ("eng", "hun"))
    return win


def main() -> None:
    app = QApplication([])
    app.setStyle("Fusion")
    archive = os.path.join(WORK, "Archive")
    build_archive(archive)

    win = window()
    win.ed_target.setText(archive)
    win.ed_backup.setText(os.path.join(WORK, "Archive_originals"))
    run(app, win)
    win.ed_target.setText(r"D:\Scans\Archive")
    win.ed_backup.setText(r"D:\Scans\OCR_originals_backup")
    win.grab().save(os.path.join(DOCS, "screenshot-folder.png"))

    inbox = os.path.join(WORK, "Inbox")
    files = [helpers.scanned_pdf(os.path.join(inbox, "scan_0001.pdf")),
             helpers.scanned_pdf(os.path.join(inbox, "scan_0002.pdf"), pages=(LETTER,)),
             helpers.tiff(os.path.join(inbox, "fax page.tif")),
             helpers.text_pdf(os.path.join(inbox, "already_digital.pdf"))]
    win2 = window()
    win2.add_files(files)
    run(app, win2)
    win2.grab().save(os.path.join(DOCS, "screenshot-files.png"))

    sig = win2.findChild(SignatureButton)
    sig.click()
    app.processEvents()
    shot = win2.grab()
    card = sig._dialog.grab()
    painter = QPainter(shot)
    painter.drawPixmap((shot.width() - card.width()) // 2, (shot.height() - card.height()) // 2, card)
    painter.end()
    shot.save(os.path.join(DOCS, "screenshot-about.png"))
    sig._dialog.close()

    for w in (win, win2):
        w.close()
    print("screenshots written to", DOCS)


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
