# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build recipe for MephistOCR.

    pyinstaller MephistOCR.spec        (or just run build.bat)

Produces a onedir build at  dist/MephistOCR/MephistOCR.exe

onedir — NOT onefile — on purpose: the app fans out OCR across many worker
processes via multiprocessing, and each spawned process re-launches this exe.
A onefile build would re-extract the whole bundle (Qt + MuPDF + tessdata) to a
temp dir for *every* worker on *every* run; onedir shares a single _internal
folder, so workers start instantly.
"""
import re

from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct,
    VSVersionInfo,
)

# Windows version resource, read from the single source of truth.
with open("mephistocr/__init__.py", encoding="utf-8") as f:
    VERSION = re.search(r'__version__ = "([^"]+)"', f.read()).group(1)
_nums = tuple(int(x) for x in VERSION.split(".")) + (0,)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_nums, prodvers=_nums),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("ProductName", "MephistOCR"),
            StringStruct("FileDescription", "MephistOCR - searchable PDFs from scans"),
            StringStruct("FileVersion", VERSION),
            StringStruct("ProductVersion", VERSION),
            StringStruct("LegalCopyright", "Copyright 2026 Pintér Szabolcs"),
        ])]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

# bundle the app icon/mark + the Tesseract language packs into the same
# relative layout the code already resolves via __file__ / sys._MEIPASS
datas = [
    ("mephistocr/assets", "mephistocr/assets"),
    ("mephistocr/tessdata", "mephistocr/tessdata"),
]

# our own package is imported dynamically (the --worker relaunch imports the
# engine lazily), so make sure every submodule is collected
hiddenimports = collect_submodules("mephistocr")

# The app only needs PySide6 + PyMuPDF + ctypes at runtime (MuPDF converts
# TIFFs itself, so Pillow is a dev/test dependency only). PyInstaller's
# analysis otherwise hoovers up whatever else is installed in the env (a
# scientific stack alone is ~230 MB). Excluding it keeps the bundle lean.
heavy_excludes = [
    "numpy", "scipy", "numba", "llvmlite", "pandas", "matplotlib", "PIL",
    "Cython", "cython", "tkinter", "IPython", "pytest",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=heavy_excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MephistOCR",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,             # windowed: no console flashes on launch
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="mephistocr/assets/app.ico",
    version=version_info,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MephistOCR",
)
