# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versioning: [SemVer](https://semver.org/).

## [1.0.0] - 2026-10-19

### Added
- PySide6 desktop app that adds an invisible OCR text layer to scanned PDFs and TIFFs without re-rendering
  the original pages (resolution, links, annotations, bookmarks and metadata are kept).
- Folder mode: a whole folder tree, `<name>_ocr.pdf` next to each original, originals moved (never deleted)
  into a backup folder that mirrors the tree.
- Files mode: dropped or chosen files, with the result next to each file, in a chosen folder, or replacing
  the original with a backup.
- Page-level detection: only image pages without real text get OCR, so mixed PDFs keep their text pages;
  multi-page TIFFs become PDFs with the page size from their DPI.
- Engine in separate worker processes with a per-page timeout and a watchdog; resumable runs, no overwrites,
  long-path support, colour-coded per-file log, `_ocr_log.tsv` run log.
- MuPDF's built-in Tesseract with bundled Hungarian, German, English and Swedish language data.
- Command line on the same engine (`python main.py FILE...`, `--folder`, `--json`, exit codes).
- Settings in `%LOCALAPPDATA%\MephistOCR\settings.ini`, About card, PyInstaller onedir build, offscreen
  pytest suite, third-party notices (PyMuPDF AGPL-3.0, PySide6 LGPL-3.0, language data Apache-2.0).
