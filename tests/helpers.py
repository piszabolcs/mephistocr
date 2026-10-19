"""Small fixture generator shared by the tests and tools/make_screenshots.py:
image-only ("scanned") PDFs, born-digital PDFs and TIFFs rendered from text
lines, plus deliberately misbehaving workers for the supervisor tests."""
from __future__ import annotations

import io
import os
import time

import pymupdf
from PIL import Image, ImageDraw, ImageFont

from mephistocr.engine import long_path

EN = ["Maintenance schedule for the conveyor belt",
      "Check the lubricant level every week.",
      "The quick brown fox jumps over the lazy dog."]
HU = ["Árvíztűrő tükörfúrógép",
      "Őszi szélvihar söpört végig a hűvös völgyön."]
A4 = (595, 842)  # points


def _font(px: int):
    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return ImageFont.load_default(px)


def page_image(lines, dpi: int = 200, mode: str = "L") -> Image.Image:
    """An A4 'scan' of the given text lines at the given resolution."""
    w, h = round(A4[0] / 72 * dpi), round(A4[1] / 72 * dpi)
    im = Image.new("L", (w, h), 255)
    draw = ImageDraw.Draw(im)
    font = _font(int(dpi * 0.16))
    y = dpi
    for line in lines:
        draw.text((int(dpi * 0.9), y), line, fill=0, font=font)
        y += int(dpi * 0.4)
    return im.convert(mode)


def _mkparent(path: str) -> None:
    os.makedirs(long_path(os.path.dirname(path)), exist_ok=True)


def scanned_pdf(path: str, pages=(EN,), dpi: int = 200) -> str:
    """Image-only PDF without a text layer, like a scanner produces."""
    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page(width=A4[0], height=A4[1])
        buf = io.BytesIO()
        page_image(lines, dpi).save(buf, "PNG")
        page.insert_image(page.rect, stream=buf.getvalue())
    _mkparent(path)
    doc.save(long_path(path))
    doc.close()
    return path


def text_pdf(path: str, lines=("This PDF already has a real text layer.",) * 3) -> str:
    """Born-digital PDF with a real, extractable text layer."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    y = 72
    for line in lines:
        page.insert_text((72, y), line, fontsize=12)
        y += 18
    _mkparent(path)
    doc.save(long_path(path))
    doc.close()
    return path


def tiff(path: str, pages=(EN,), dpi: int = 200, mode: str = "1") -> str:
    """(Multi-page) TIFF scan that records its DPI, like a fax/scanner TIFF."""
    frames = [page_image(lines, dpi, mode) for lines in pages]
    _mkparent(path)
    extra = {"compression": "group4"} if mode == "1" else {}
    frames[0].save(long_path(path), save_all=True, append_images=frames[1:],
                   dpi=(dpi, dpi), **extra)
    return path


def deep_dir(base: str, min_len: int) -> str:
    """A real folder under `base` whose path is at least `min_len` characters.
    Created through the \\\\?\\ prefix: plain os.makedirs fails past MAX_PATH."""
    path = base
    while len(path) < min_len:
        path = os.path.join(path, "segment_" + "x" * 20)
    os.makedirs(long_path(path), exist_ok=True)
    return path


# --- misbehaving workers for the supervisor tests --------------------------
# (top-level functions so multiprocessing can import them in the child)

def lossy_worker(wid, task_q, result_q, current, *args):
    """Takes work off the queue and never reports it."""
    while task_q.get() is not None:
        pass


def sleepy_worker(wid, task_q, result_q, current, *args):
    """Claims a file and then hangs on it, like a page stuck inside MuPDF."""
    item = task_q.get()
    current[wid] = (item[0], time.time())
    time.sleep(600)


def crashy_worker(wid, task_q, result_q, current, target, backup, langs, dpi):
    """The real per-file work, except it dies hard on files named crash*."""
    from mephistocr import engine
    engine.setup_tesseract()
    while (item := task_q.get()) is not None:
        current[wid] = (item[0], time.time())
        if os.path.basename(item[0]).startswith("crash"):
            os._exit(3)
        result = engine.ocr_one(item[0], item[1], target, backup, langs, dpi)
        result_q.put(("DONE", wid, item[0], *result))
        current[wid] = None
