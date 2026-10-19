"""Command-line mode: OCR a few files or a whole folder without the GUI.

    python main.py scan.pdf fax.tif                    -> <name>_ocr.pdf next to each
    python main.py scan.pdf --out D:\\OCR               -> outputs into D:\\OCR
    python main.py scan.pdf --replace --backup D:\\Orig  -> originals moved to D:\\Orig
    python main.py --folder D:\\Archive --backup D:\\Archive_originals

Progress goes to stderr; stdout gets one output path per OCR'd file (or a
JSON list of all results with --json). Exit codes: 0 = everything done,
1 = at least one file failed, 2 = usage error / the run could not start.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__, engine


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="MephistOCR",
        description="Make scanned PDFs and TIFFs searchable by adding an invisible OCR text layer.")
    p.add_argument("files", nargs="*", metavar="FILE", help="PDF or TIFF files to OCR")
    p.add_argument("--folder", help="OCR every PDF/TIFF under this folder (needs --backup)")
    p.add_argument("--backup", help="move the OCR'd originals here (with --folder or --replace)")
    p.add_argument("--replace", action="store_true",
                   help="FILE mode: move each original to --backup after OCR")
    p.add_argument("--out", help="FILE mode: write the outputs into this folder")
    p.add_argument("--lang", default=engine.DEFAULT_LANGS,
                   help="Tesseract languages joined by +, e.g. hun+eng (default: %(default)s)")
    p.add_argument("--dpi", type=int, default=200, choices=(150, 200, 300),
                   help="OCR render resolution (default: %(default)s)")
    p.add_argument("--workers", type=int, default=max(1, min((os.cpu_count() or 4) - 2, 8)),
                   help="parallel OCR processes (default: %(default)s)")
    p.add_argument("--no-subfolders", action="store_true", help="--folder: do not recurse")
    p.add_argument("--force", action="store_true",
                   help="redo files whose <name>_ocr.pdf already exists")
    p.add_argument("--json", action="store_true", help="print all results as JSON")
    p.add_argument("--version", action="version", version=f"MephistOCR {__version__}")
    return p


def main(argv: list[str]) -> int:
    parser = _parser()
    a = parser.parse_args(argv)
    if bool(a.files) == bool(a.folder):
        parser.error("give either FILE arguments or --folder")
    if a.folder and (a.out or a.replace or not a.backup):
        parser.error("--folder needs --backup and takes no --out/--replace")
    if a.files and a.replace != bool(a.backup):
        parser.error("--replace and --backup go together")
    if a.out and a.replace:
        parser.error("choose --out or --replace, not both")

    job = engine.Job(target=a.folder or "", backup=a.backup or "", langs=a.lang, dpi=a.dpi,
                     recursive=not a.no_subfolders, workers=max(1, a.workers),
                     files=[os.path.abspath(f) for f in a.files] or None,
                     out_dir=os.path.abspath(a.out) if a.out else "", force=a.force)
    ok, info = engine.setup_tesseract()
    problem = job.problem() if ok else info
    if problem:
        print(f"MephistOCR: error: {problem}", file=sys.stderr)
        return 2
    try:
        log_path = engine.prepare_dirs(job)
    except OSError as e:
        print(f"MephistOCR: error: cannot create folder: {e}", file=sys.stderr)
        return 2

    results: list[dict] = []
    summary = {}

    def on_event(kind, data):
        if kind == "log":
            print(data["text"], file=sys.stderr, flush=True)
        elif kind == "file":
            results.append(data)
        elif kind == "done":
            summary.update(vars(data["counts"]))

    engine.run_job(job, log_path, on_event)
    if a.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            if r["cat"] == "ocr":
                print(r["out"])
    print(f"Done: {summary.get('ocr', 0)} OCR'd, {summary.get('has_text', 0)} already searchable, "
          f"{summary.get('skip', 0)} skipped, {summary.get('error', 0)} failed. Log: {log_path}",
          file=sys.stderr)
    return 1 if summary.get("error") else 0
