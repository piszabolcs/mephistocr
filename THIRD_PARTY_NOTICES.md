# Third-party notices

MephistOCR's own source code is released under the MIT License (see `LICENSE`).
It depends on, and a built `MephistOCR.exe` bundles, the components below.
Their licences apply to those components, and some of them place obligations
on anyone who **distributes** the program.

| Component | Used for | Licence | Source |
|---|---|---|---|
| PyMuPDF / MuPDF (incl. its built-in Tesseract OCR engine) | PDF/TIFF reading, rendering, OCR, writing | **GNU AGPL-3.0** (or a commercial licence from Artifex) | https://github.com/pymupdf/PyMuPDF |
| PySide6 / Qt 6 (Essentials) | GUI | GNU LGPL-3.0 (Qt for Python is also available under commercial terms) | https://code.qt.io/cgit/pyside/pyside-setup.git/ |
| Tesseract trained language data (`mephistocr/tessdata/*.traineddata`: eng, deu, hun, swe) | OCR language models | Apache License 2.0 | https://github.com/tesseract-ocr/tessdata_fast |

## Important: PyMuPDF is AGPL-3.0

PyMuPDF is licensed under the GNU Affero General Public License v3.0 unless a
commercial licence is bought from Artifex. Running the program from source on
your own machine is unaffected, but **distributing** a build that contains
PyMuPDF (for example `dist\MephistOCR\`) means the combined work has to be
offered under AGPL-3.0 terms, including access to the complete corresponding
source code. Whether this project's releases should be AGPL-3.0 as a whole,
stay MIT for the source only, or move to a commercial PyMuPDF licence is an
**open decision for the owner** - see the README's licence section.

Full licence texts:
- AGPL-3.0: https://www.gnu.org/licenses/agpl-3.0.txt
- LGPL-3.0: https://www.gnu.org/licenses/lgpl-3.0.txt
- Apache-2.0: https://www.apache.org/licenses/LICENSE-2.0.txt

Development-only tools (not bundled): Pillow (MIT-CMU), pytest (MIT),
pytest-qt (MIT), pip-audit (Apache-2.0), PyInstaller (GPL-2.0 with a
bootloader exception that allows bundling applications under any licence).
