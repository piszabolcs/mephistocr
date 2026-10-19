"""
MephistOCR engine
------------------
OCR engine for scanned PDFs and TIFFs: adds an invisible, searchable text
layer to pages that don't have one yet, so document search / RAG indexing can
actually read their content. The original pages are kept as they are (same
images, resolution, links, bookmarks, metadata); only the text layer is added.
Pages that already contain real extractable text are left untouched, so a
mixed PDF only gets OCR on its scanned pages.

Design (battle-tested against real-world runs on thousands of files):
  - Multi-PROCESS parallelism (Tesseract/MuPDF release the GIL anyway, and a
    separate OS process gives true fault isolation).
  - Fault isolation: if a worker process crashes on a bad/corrupt PDF, only
    that one file is marked as failed and skipped - the batch keeps going and
    a fresh worker is spawned to replace it. A shared-memory "current file"
    map lets the supervisor attribute a crash to the exact file that caused
    it (a plain result-queue can't do this reliably, since a hard crash can
    happen before the "I'm working on X" message is flushed). The same map
    carries a per-page heartbeat, so a page stuck for PAGE_TIMEOUT_S gets its
    worker killed and the file reported as ERROR_TIMEOUT.
  - Resumable: PDFs already OCR'd (named "<name>_ocr.pdf") are skipped, so a
    stopped/restarted run doesn't redo completed work.
  - Reversible: originals that get OCR'd are moved (not deleted) into a
    backup folder that mirrors the source's subfolder structure. A move never
    overwrites anything: name clashes get a _1, _2, ... suffix.
  - Hang-proof: every queued file has its own output name (no two sources can
    race for the same <name>_ocr.pdf), and a watchdog ends the run if work
    goes missing instead of waiting forever.

This module is imported both by the GUI process (validation, language list)
and run standalone as a worker-subprocess entry point (`--worker <cfg.json>`),
so it has zero Qt / GUI dependencies.
"""
from __future__ import annotations

import errno
import json
import multiprocessing as mp
import os
import queue
import shutil
import sys
import time
from dataclasses import dataclass
from typing import Callable

import pymupdf

# ---------------------------------------------------------------------------
# Tesseract language data. MuPDF has Tesseract compiled in, so no
# tesseract.exe is needed - only the *.traineddata files.
# ---------------------------------------------------------------------------
PKG_DIR = os.path.dirname(os.path.abspath(__file__))
BUNDLED_TESSDATA = os.path.join(PKG_DIR, "tessdata")

# Fallback only: a system Tesseract install's tessdata folder.
TESSERACT_DIRS = [
    r"C:\Program Files\Tesseract-OCR",
    r"C:\Program Files (x86)\Tesseract-OCR",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Tesseract-OCR"),
]

DEFAULT_LANGS = "hun+deu+eng+swe"
LANG_NAMES = {"eng": "English", "deu": "German", "hun": "Hungarian", "swe": "Swedish",
              "fra": "French", "pol": "Polish"}


def _has_traineddata(folder: str) -> bool:
    try:
        return any(f.endswith(".traineddata") for f in os.listdir(folder))
    except OSError:
        return False


def tessdata_dir() -> str | None:
    """The bundled language packs first, then $TESSDATA_PREFIX, then a system
    Tesseract install's tessdata folder."""
    cands = [BUNDLED_TESSDATA, os.environ.get("TESSDATA_PREFIX", "")]
    cands += [os.path.join(d, "tessdata") for d in TESSERACT_DIRS]
    exe = shutil.which("tesseract")
    if exe:
        cands.append(os.path.join(os.path.dirname(exe), "tessdata"))
    return next((c for c in cands if c and _has_traineddata(c)), None)


def setup_tesseract() -> tuple[bool, str]:
    """Point MuPDF's built-in Tesseract at the language data."""
    folder = tessdata_dir()
    if folder is None:
        return False, ("No Tesseract language data (*.traineddata) found - "
                       "put the files into mephistocr/tessdata.")
    os.environ["TESSDATA_PREFIX"] = folder
    return True, folder


def available_languages() -> list[str]:
    """Language codes with a .traineddata file (osd is a helper model, not a language)."""
    folder = tessdata_dir()
    if folder is None:
        return []
    return sorted(f[:-len(".traineddata")] for f in os.listdir(folder)
                  if f.endswith(".traineddata") and f != "osd.traineddata")


def check_langs(langs: str) -> str | None:
    """Validate a "hun+eng" style language list; returns an error message or None.
    (MuPDF would otherwise drop unknown languages silently.)"""
    codes = [c for c in langs.split("+") if c]
    if not codes:
        return "Select at least one OCR language."
    avail = available_languages()
    unknown = [c for c in codes if c not in avail]
    if unknown:
        return (f"Unknown OCR language(s): {', '.join(unknown)}. "
                f"Available: {', '.join(avail) or 'none'}.")
    return None


# ---------------------------------------------------------------------------
# Config / constants
# ---------------------------------------------------------------------------
MIN_CHARS_PER_PAGE = 20   # a page with fewer extractable chars counts as "scanned"
MAX_PAGES = 1200          # safety valve against one giant scanned binder; scanned
                          # parts catalogues and machine manuals routinely run
                          # 400-1000 pages, and MAX_MEGAPIXELS already bounds the
                          # per-page cost, so the old limit of 300 only lost data
MAX_MEGAPIXELS = 40       # per-page render cap; large-format (A0+) engineering
                          # drawings would otherwise ask for multi-GB pixmaps and
                          # exhaust the commit limit -> VIDEO_TDR_ERROR bugcheck
PAGE_TIMEOUT_S = 300      # one page busy longer than this -> worker killed, file
                          # reported ERROR_TIMEOUT, run continues
WATCHDOG_S = 15           # queue drained + every worker idle this long while files
                          # are still unaccounted -> they are reported ERROR_LOST

PDF_EXTS = (".pdf",)
TIFF_EXTS = (".tif", ".tiff")
OCR_SUFFIX = "_ocr.pdf"


def app_data_dir() -> str:
    """Per-user folder for the settings file and for the logs of runs that have
    no backup folder. MEPHISTOCR_HOME overrides it (tests, portable use)."""
    return os.environ.get("MEPHISTOCR_HOME") or os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "MephistOCR")


@dataclass
class Job:
    """Folder mode: walk `target`, OCR in place, originals go to `backup`.
    File mode (`files` set): OCR just these; outputs go next to each file or
    into `out_dir`; originals stay put unless a `backup` is given."""
    target: str = ""
    backup: str = ""
    langs: str = DEFAULT_LANGS
    dpi: int = 200
    recursive: bool = True
    workers: int = 4
    files: list[str] | None = None
    out_dir: str = ""
    force: bool = False  # redo files whose <name>_ocr.pdf already exists

    @classmethod
    def from_dict(cls, raw: dict) -> "Job":
        return cls(target=raw.get("target", ""), backup=raw.get("backup", ""),
                   langs=raw.get("langs", DEFAULT_LANGS), dpi=int(raw.get("dpi", 200)),
                   recursive=bool(raw.get("recursive", True)),
                   workers=max(1, int(raw.get("workers", 4))), files=raw.get("files"),
                   out_dir=raw.get("out_dir", ""), force=bool(raw.get("force", False)))

    def base_root(self) -> str:
        """The folder the backup mirrors: the target, or the files' common parent."""
        if self.files is None:
            return self.target
        try:
            return os.path.commonpath([os.path.dirname(os.path.abspath(f)) for f in self.files])
        except ValueError:  # files on different drives
            return ""

    def log_dir(self) -> str:
        return self.backup or os.path.join(app_data_dir(), "logs")

    def problem(self) -> str | None:
        """Why this job cannot start, or None."""
        if self.files is None:
            err = check_folders(self.target, self.backup)
        else:
            err = check_files(self.files, self.backup, self.base_root())
        return err or check_langs(self.langs)


@dataclass
class Counts:
    has_text: int = 0
    ocr: int = 0
    skip: int = 0
    error: int = 0


def page_dpi(page: "pymupdf.Page", dpi: int) -> int:
    """Scale dpi down so one page never renders above MAX_MEGAPIXELS.
    A0 sheets at 300 dpi are ~870 MPx (2.5 GB); their lettering is plotted 1:1
    and stays legible far below that, so clamping costs no OCR accuracy."""
    r = page.rect
    mpx = (r.width / 72 * dpi) * (r.height / 72 * dpi) / 1e6
    if mpx <= MAX_MEGAPIXELS:
        return dpi
    return max(50, int(dpi * (MAX_MEGAPIXELS / mpx) ** 0.5))


def pages_needing_ocr(doc: "pymupdf.Document") -> tuple[list[int], bool]:
    """(indexes of scanned pages, whether any page has real text).

    A page needs OCR when it shows an image but carries (almost) no
    extractable text. Deciding per page - instead of from the first few pages -
    means a mixed PDF gets OCR on its scanned pages while its born-digital
    pages stay untouched."""
    todo, any_text = [], False
    for page in doc:
        if len(page.get_text().strip()) >= MIN_CHARS_PER_PAGE:
            any_text = True
        elif page.get_image_info():
            todo.append(page.number)
    return todo, any_text


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def long_path(path: str) -> str:
    """Prefix an absolute Windows path with the \\\\?\\ extended-length marker
    so file APIs (pymupdf.open, os.*, shutil.*) can reach paths beyond the
    legacy 260-char MAX_PATH limit. Deeply nested archive folders routinely
    exceed it, which otherwise surfaces as a spurious "no such file" even
    though the file is there. No-op on non-Windows or already-prefixed paths."""
    if os.name != "nt" or path.startswith("\\\\?\\"):
        return path
    path = os.path.abspath(path)
    if path.startswith("\\\\"):  # UNC: \\server\share\... -> \\?\UNC\server\share\...
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def short_path(path: str) -> str:
    """Undo long_path(): for display, relpath and comparisons."""
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def _collect_by_ext(root: str, recursive: bool, exts: tuple[str, ...],
                    errors: list | None = None) -> list[str]:
    """Files under root ending in one of exts. The walk goes through the
    \\\\?\\ prefix, so folders deeper than MAX_PATH are not silently skipped,
    and unreadable folders land in `errors` as (path, message)."""
    def failed(e: OSError):
        if errors is not None:
            errors.append((short_path(e.filename or root), e.strerror or str(e)))

    found = []
    lroot = long_path(root)
    if recursive:
        for dp, _, fns in os.walk(lroot, onerror=failed):
            found += [short_path(os.path.join(dp, fn)) for fn in fns if fn.lower().endswith(exts)]
    else:
        try:
            names = os.listdir(lroot)
        except OSError as e:
            failed(e)
            names = []
        for fn in names:
            full = os.path.join(lroot, fn)
            if fn.lower().endswith(exts) and os.path.isfile(full):
                found.append(short_path(full))
    return sorted(found)


def collect_tiffs(root: str, recursive: bool) -> list[str]:
    return _collect_by_ext(root, recursive, TIFF_EXTS)


def collect_inputs(root: str, recursive: bool, errors: list | None = None) -> list[str]:
    """PDFs and TIFFs under root, minus our own <name>_ocr.pdf outputs."""
    found = _collect_by_ext(root, recursive, PDF_EXTS + TIFF_EXTS, errors)
    return [p for p in found if not p.lower().endswith(OCR_SUFFIX)]


def _is_tiff(path: str) -> bool:
    return path.lower().endswith(TIFF_EXTS)


def _norm(path: str) -> str:
    """Comparison key for paths: absolute, case-folded on Windows."""
    return os.path.normcase(os.path.abspath(path))


def output_path(src: str, out_dir: str = "") -> str:
    """The searchable twin's path: <stem>_ocr.pdf next to the source (or in out_dir)."""
    stem = os.path.splitext(src)[0]
    if out_dir:
        stem = os.path.join(out_dir, os.path.basename(stem))
    return stem + OCR_SUFFIX


def plan_items(sources: list[str], out_dir: str = "") -> tuple[list[tuple[str, str]], list[str]]:
    """Deduplicate the sources and give every one a distinct output path.

    Returns (items, clashes): items are (source, output) pairs, clashes are
    sources that could not get a free output name and must be skipped.
    PDFs are planned first so a PDF always keeps its natural <stem>_ocr.pdf.
    A TIFF whose natural name is taken - by a same-stem PDF next to it, or by
    another TIFF in this run - gets <stem>_<ext>_ocr.pdf instead. Without this,
    x.pdf + x.tif used to write the same file and the run never finished."""
    seen: set[str] = set()
    claimed: set[str] = set()
    items: list[tuple[str, str]] = []
    clashes: list[str] = []
    for src in sorted(sources, key=lambda p: (_is_tiff(p), p.lower())):
        key = _norm(src)
        if key in seen:
            continue
        seen.add(key)
        out = output_path(src, out_dir)
        if _is_tiff(src):
            stem, ext = os.path.splitext(src)
            if _norm(out) in claimed or os.path.exists(long_path(stem + ".pdf")):
                out = output_path(f"{stem}_{ext.lower().lstrip('.')}", out_dir)
        if _norm(out) in claimed:
            clashes.append(src)
            continue
        claimed.add(_norm(out))
        items.append((src, out))
    return items, clashes


def folders_overlap(a: str, b: str) -> bool:
    """True if a and b are the same folder or one contains the other
    (case-insensitive on Windows, symlinks/junctions resolved)."""
    ra = os.path.normcase(os.path.realpath(short_path(a)))
    rb = os.path.normcase(os.path.realpath(short_path(b)))
    try:
        common = os.path.commonpath([ra, rb])
    except ValueError:  # different drives
        return False
    return common in (ra, rb)


def check_folders(target: str, backup: str) -> str | None:
    """Validate a bulk run's folders; returns an error message or None."""
    if not target or not os.path.isdir(long_path(target)):
        return "Choose a valid target folder."
    if not backup:
        return "Choose a backup folder."
    if folders_overlap(target, backup):
        return ("The backup folder must not be the target folder, be inside it, "
                "or contain it.")
    return None


def _same_folder(a: str, b: str) -> bool:
    return (os.path.normcase(os.path.realpath(short_path(a)))
            == os.path.normcase(os.path.realpath(short_path(b))))


def check_files(files: list[str], backup: str = "", base_root: str = "") -> str | None:
    """Validate a file-mode run; returns an error message or None."""
    if not files:
        return "Add at least one PDF or TIFF file."
    for f in files:
        if not f.lower().endswith(PDF_EXTS + TIFF_EXTS):
            return f"Not a PDF or TIFF file: {f}"
        if not os.path.isfile(long_path(f)):
            return f"File not found: {f}"
    # A backup equal to a source folder would "move" each original onto itself.
    if backup and any(_same_folder(backup, d) for d in
                      {os.path.dirname(os.path.abspath(f)) for f in files} | {base_root} if d):
        return "The backup folder must be different from the folder of the files."
    return None


# ---------------------------------------------------------------------------
# Backup move
# ---------------------------------------------------------------------------
def _backup_rel(src: str, base_root: str) -> str:
    """src's path relative to base_root, for mirroring it under the backup."""
    try:
        rel = os.path.relpath(src, base_root)
    except ValueError:
        rel = ""
    if not rel or rel.startswith(".."):
        drive, tail = os.path.splitdrive(os.path.abspath(src))
        rel = os.path.join(drive.strip("\\/").replace(":", "") or "root", tail.lstrip("\\/"))
    return rel


def _remove_quietly(path: str) -> None:
    try:
        if os.path.exists(long_path(path)):
            os.remove(long_path(path))
    except OSError:
        pass


def _copy_across_volumes(src: str, dest: str) -> None:
    """Copy to a .part file, verify, rename, then delete the source. Any failure
    removes what was copied, so the backup never keeps a stray duplicate."""
    part = dest + ".part"
    try:
        shutil.copy2(long_path(src), long_path(part))
        if os.path.getsize(long_path(part)) != os.path.getsize(long_path(src)):
            raise OSError(f"size mismatch after copying {src}")
        os.rename(long_path(part), long_path(dest))
        os.remove(long_path(src))
    except BaseException:
        _remove_quietly(part)
        _remove_quietly(dest)
        raise


def move_to_backup(src: str, base_root: str, backup_root: str) -> str:
    """Move src into backup_root, mirroring its position under base_root, and
    return the destination. Never overwrites: a clash becomes name_1, name_2...
    On one volume this is a single rename (a locked source fails cleanly and
    leaves nothing behind); across volumes see _copy_across_volumes."""
    dest = os.path.join(backup_root, _backup_rel(src, base_root))
    os.makedirs(long_path(os.path.dirname(dest)), exist_ok=True)
    stem, ext = os.path.splitext(dest)
    for n in range(10_000):
        cand = dest if n == 0 else f"{stem}_{n}{ext}"
        if os.path.exists(long_path(cand)):
            continue
        try:
            os.rename(long_path(src), long_path(cand))  # fails, never replaces, on Windows
            return cand
        except FileExistsError:
            continue
        except OSError as e:
            if getattr(e, "winerror", None) != 17 and e.errno != errno.EXDEV:
                raise  # 17 = ERROR_NOT_SAME_DEVICE
            _copy_across_volumes(src, cand)
            return cand
    raise OSError(f"no free backup name for {dest}")


# ---------------------------------------------------------------------------
# One file
# ---------------------------------------------------------------------------
def _open_source(path: str) -> "pymupdf.Document":
    """Open a PDF, or convert a (multi-page) TIFF to an in-memory PDF. MuPDF
    honours the TIFF's DPI, so an A4 scan becomes an A4 page; nothing is
    written next to the TIFF, so an existing same-name PDF is never touched."""
    if _is_tiff(path):
        img = pymupdf.open(long_path(path))
        try:
            return pymupdf.open("pdf", img.convert_to_pdf())
        finally:
            img.close()
    return pymupdf.open(long_path(path))


def _ocr_page(page: "pymupdf.Page", langs: str, dpi: int) -> int:
    """Lay MuPDF's OCR text over the page as an invisible layer and return the
    number of characters recognised. The page's own content (images, vectors,
    links, annotations) is untouched, so nothing is lost or downsampled."""
    # RGB on purpose: MuPDF's OCR writer recognises nothing in a gray pixmap.
    pix = page.get_pixmap(dpi=page_dpi(page, dpi))
    ocr = pymupdf.open("pdf", pix.pdfocr_tobytes(language=langs))
    try:
        layer = ocr[0]
        chars = len(layer.get_text().strip())
        if chars:
            for img in layer.get_images(full=True):
                layer.delete_image(img[0])  # keep the invisible text, not MuPDF's re-render
            page.show_pdf_page(page.rect * page.derotation_matrix, ocr, 0,
                               overlay=True, rotate=page.rotation)
        return chars
    finally:
        ocr.close()


def ocr_one(src: str, out: str, base_root: str, backup_root: str, langs: str, dpi: int,
            heartbeat: Callable[[], None] | None = None, force: bool = False):
    """Process one PDF or TIFF into `out`. Returns (category, status, extra);
    category is one of: has_text / ocr / skip / error. `heartbeat` is called
    before every OCR'd page (the supervisor's stuck-page timeout); `force`
    replaces an existing `out` instead of skipping the file."""
    if src.lower().endswith(OCR_SUFFIX):
        return "skip", "SKIP_ALREADY_OCR", ""
    if os.path.exists(long_path(out)) and not force:
        return "skip", "SKIP_OCR_TWIN_EXISTS", os.path.basename(out)
    try:
        if os.path.getsize(long_path(src)) == 0:
            return "skip", "SKIP_EMPTY", "0 bytes"
        doc = _open_source(src)
    except Exception as e:
        return "error", "ERROR_OPEN", str(e)

    tmp = out + ".tmp"
    try:
        # Never strip a protection: encrypted files (even owner-password-only
        # ones that open without a password) are left alone.
        if doc.is_encrypted or doc.metadata.get("encryption"):
            return "skip", "SKIP_ENCRYPTED", ""
        npages = doc.page_count
        if npages == 0:
            return "error", "ERROR_NO_PAGES", "not a readable PDF"
        if npages > MAX_PAGES:
            return "skip", "SKIP_TOO_LONG", f"{npages} pages"
        todo, any_text = pages_needing_ocr(doc)
        if not todo:
            if any_text:
                return "has_text", "OK_HAS_TEXT", ""
            return "skip", "SKIP_NOTHING_TO_OCR", ""
        t0 = time.time()
        found = 0
        for i in todo:
            if heartbeat:
                heartbeat()
            found += _ocr_page(doc[i], langs, dpi)
        doc.save(long_path(tmp), garbage=4, deflate=True)
    except Exception as e:
        _remove_quietly(tmp)
        return "error", "ERROR_OCR", str(e)
    finally:
        doc.close()
    os.replace(long_path(tmp), long_path(out))

    if backup_root:
        try:
            move_to_backup(src, base_root, backup_root)
        except Exception as e:
            # The searchable twin exists but the original couldn't be moved
            # (e.g. locked by another process). Undo the twin: the twin-exists
            # check above would otherwise treat this file as handled forever,
            # while the original never makes it into the backup folder.
            _remove_quietly(out)
            return "error", "ERROR_BACKUP_MOVE", str(e)

    # Even "no text found" (e.g. a drawing) keeps its output, so the next run
    # does not OCR the same file again.
    extra = f"{len(todo)} of {npages} pages, {time.time() - t0:.0f}s"
    return "ocr", ("OCR_DONE" if found else "OCR_NO_TEXT"), extra


SKIP_REASONS = {
    "SKIP_ALREADY_OCR": "already an OCR output",
    "SKIP_OCR_TWIN_EXISTS": "{extra} already exists",
    "SKIP_ENCRYPTED": "password-protected",
    "SKIP_EMPTY": "empty file",
    "SKIP_TOO_LONG": "{extra}, the limit is " + str(MAX_PAGES),
    "SKIP_NAME_CLASH": "no free <name>_ocr.pdf name",
    "SKIP_NOTHING_TO_OCR": "no text and no images",
}


def describe(cat: str, status: str, extra: str, name: str) -> tuple[str, str]:
    """(log line, level) for one finished file; level drives the GUI colour."""
    if cat == "ocr":
        if status == "OCR_NO_TEXT":
            return f"OCR'd, but no text was recognised ({extra}): {name}", "warn"
        return f"OCR'd ({extra}): {name}", "ok"
    if cat == "has_text":
        return f"Already searchable: {name}", "muted"
    if cat == "skip":
        return f"Skipped ({SKIP_REASONS.get(status, status).format(extra=extra)}): {name}", "warn"
    return f"Failed ({status}{': ' + extra if extra else ''}): {name}", "error"


# ---------------------------------------------------------------------------
# Worker-process entry (spawned by the supervisor loop)
# ---------------------------------------------------------------------------
def _mp_worker(wid, task_q, result_q, current, target, backup, langs, dpi):
    setup_tesseract()
    while True:
        try:
            item = task_q.get()
        except Exception:
            break
        if item is None:
            break
        src, out, force = item

        def beat(src=src):
            current[wid] = (src, time.time())

        beat()
        try:
            cat, status, extra = ocr_one(src, out, target, backup, langs, dpi,
                                         heartbeat=beat, force=force)
        except Exception as e:
            cat, status, extra = "error", "ERROR_OCR", str(e)
        result_q.put(("DONE", wid, src, cat, status, extra))
        current[wid] = None


# ---------------------------------------------------------------------------
# Supervisor: runs the crash-resilient worker pool and reports progress via
# a callback (so both a CLI and the Qt GUI can consume the same engine).
# ---------------------------------------------------------------------------
def _one_line(value) -> str:
    """Tabs/newlines would break the TSV log and the stdout protocol."""
    return " ".join(str(value).split())


def run_job(job: Job, log_path: str, on_event: Callable[[str, dict], None], *,
            worker: Callable = _mp_worker):
    """on_event(kind, data) is called for: 'start', 'progress', 'log', 'file'
    (one per finished source), 'done'. `worker` is the worker-process entry;
    tests substitute a misbehaving one."""
    def log_line(path, status, extra=""):
        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{status}\t{path}\t{_one_line(extra)}\n")
        except OSError as e:
            print(f"cannot write {log_path}: {e}", file=sys.stderr)

    counts = Counts()
    walk_errors: list[tuple[str, str]] = []
    if job.files is None:
        sources = collect_inputs(job.target, job.recursive, walk_errors)
    else:
        sources = [os.path.abspath(f) for f in job.files]
    for path, err in walk_errors:
        counts.error += 1
        log_line(path, "ERROR_WALK", err)
        on_event("log", {"text": f"Cannot read folder ({err}): {path}", "level": "error"})

    # TIFFs are converted in memory by the worker (no intermediate file), so
    # PDFs and TIFFs share one queue; plan_items() dedupes it and hands every
    # source its own output name.
    items, clashes = plan_items(sources, job.out_dir)
    outputs = dict(items)
    for _, out in items:  # clean up stale partial output from a previous crash
        _remove_quietly(out + ".tmp")

    total = len(items) + len(clashes)
    on_event("start", {"total": total, "workers": job.workers})
    finished: set[str] = set()
    done = 0

    def account(path, cat, status, extra):
        nonlocal done
        if path in finished:
            return
        finished.add(path)
        done += 1
        if cat == "has_text": counts.has_text += 1
        elif cat == "ocr": counts.ocr += 1
        elif cat == "skip": counts.skip += 1
        elif cat == "error": counts.error += 1
        log_line(path, status, extra)
        name = os.path.basename(path)
        text, level = describe(cat, status, _one_line(extra), name)
        on_event("log", {"text": text, "level": level})
        on_event("file", {"path": path, "out": outputs.get(path, "") if cat == "ocr" else "", "cat": cat,
                          "status": status, "extra": _one_line(extra)})
        on_event("progress", {"done": done, "total": total, "counts": counts, "name": name})

    for src in clashes:
        account(src, "skip", "SKIP_NAME_CLASH", "")
    if not items:
        on_event("done", {"counts": counts})
        return

    mgr = mp.Manager()
    current = mgr.dict()
    task_q: mp.Queue = mp.Queue()
    result_q: mp.Queue = mp.Queue()
    for src, out in items:
        task_q.put((src, out, job.force))

    procs: dict[int, mp.Process] = {}
    next_wid = 0
    base_root = job.base_root()

    def spawn():
        nonlocal next_wid
        wid = next_wid; next_wid += 1
        current[wid] = None
        p = mp.Process(target=worker,
                       args=(wid, task_q, result_q, current, base_root, job.backup, job.langs, job.dpi),
                       daemon=True)
        p.start()
        procs[wid] = p

    for _ in range(min(job.workers, len(items))):
        spawn()

    last_progress = time.monotonic()
    while done < total:
        before = done
        try:
            while True:
                msg = result_q.get(timeout=0.5)
                _, wid, path, cat, status, extra = msg
                account(path, cat, status, extra)
        except queue.Empty:
            pass

        for wid, p in list(procs.items()):
            busy = current.get(wid)  # (path, time of the last heartbeat) or None
            if p.is_alive():
                if not busy or time.time() - busy[1] <= PAGE_TIMEOUT_S:
                    continue
                p.terminate()
                p.join(5)
                reason = ("ERROR_TIMEOUT", f"a page took longer than {PAGE_TIMEOUT_S:.0f}s")
            else:
                reason = ("ERROR_CRASHED", f"exit {p.exitcode}")
            if busy and busy[0] not in finished:
                account(busy[0], "error", *reason)
            procs.pop(wid, None)
            try: current.pop(wid, None)
            except Exception: pass
            if done < total and len(procs) < min(job.workers, total - done):
                spawn()

        # Watchdog: an item can still go missing (e.g. a worker dies between
        # taking it off the queue and announcing it). With the queue drained
        # and nobody working, nothing will ever report it - so end the run
        # instead of waiting forever.
        now = time.monotonic()
        if done != before:
            last_progress = now
        elif (now - last_progress > WATCHDOG_S and task_q.empty()
              and not any(current.get(w) for w in procs)):
            for src, _ in items:
                if src not in finished:
                    account(src, "error", "ERROR_LOST", "never reported by a worker")

    for p in procs.values():
        try: p.terminate()
        except Exception: pass
    try: mgr.shutdown()
    except Exception: pass
    on_event("done", {"counts": counts})


# ---------------------------------------------------------------------------
# Worker-subprocess mode: prints a simple tab-separated event protocol to
# stdout so the GUI (a separate process) can drive a progress bar without
# ever running OCR work on its own event loop thread.
# ---------------------------------------------------------------------------
def _emit(*parts):
    sys.stdout.write("\t".join(_one_line(p) for p in parts) + "\n")
    sys.stdout.flush()


def prepare_dirs(job: Job) -> str:
    """Create the backup / output / log folders; returns the TSV log path."""
    for folder in (job.backup, job.out_dir, job.log_dir()):
        if folder:
            os.makedirs(long_path(folder), exist_ok=True)
    return os.path.join(job.log_dir(), "_ocr_log.tsv")


def worker_main(cfg_path: str) -> int:
    """Returns the process exit code: 0 = ran to completion, 2 = refused to start."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    finally:
        try: os.remove(cfg_path)  # only paths inside, but don't leave it in %TEMP%
        except OSError: pass
    ok, info = setup_tesseract()
    if not ok:
        _emit("FATAL", info)
        return 2

    job = Job.from_dict(raw)
    problem = job.problem()  # the GUI checks too; never trust it alone
    if problem:
        _emit("FATAL", problem)
        return 2
    try:
        log_path = prepare_dirs(job)
    except OSError as e:
        _emit("FATAL", f"Cannot create folder: {e}")
        return 2

    def on_event(kind, data):
        if kind == "start":
            _emit("START", data["total"], data["workers"])
        elif kind == "progress":
            c = data["counts"]
            _emit("PROG", data["done"], data["total"], c.has_text, c.ocr, c.skip, c.error, data["name"])
        elif kind == "log":
            _emit("LOG", data["level"], data["text"])
        elif kind == "file":
            _emit("FILE", data["cat"], data["status"], data["path"], data["out"], data["extra"])
        elif kind == "done":
            c = data["counts"]
            _emit("DONE", c.has_text, c.ocr, c.skip, c.error)

    run_job(job, log_path, on_event)
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--worker":
        sys.exit(worker_main(sys.argv[2]))
    else:
        print("This module is a library / worker entry point. Run main.py instead.")
