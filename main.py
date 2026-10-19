#!/usr/bin/env python
"""MephistOCR entry point.

Run from source:   python main.py                    (GUI)
                   python main.py scan.pdf [...]     (command line, see mephistocr/cli.py)
Frozen (.exe):      MephistOCR.exe        (see MephistOCR.spec / build.bat)

Two things here exist purely so the app survives being frozen into a single
distributable exe:

  * freeze_support() — Windows multiprocessing re-launches this exe for every
    worker/manager process; without this guard those relaunches would each spin
    up a whole new GUI.
  * the `--worker` dispatch in main() — from source the GUI starts the OCR
    engine with `python -m mephistocr.engine`, but a PyInstaller bundle has no
    `python -m`, so the frozen GUI relaunches *itself* as `MephistOCR.exe
    --worker cfg.json` and we route that straight into the engine here.
"""
import multiprocessing
import os
import sys


def _resource(*parts) -> str:
    """Path to a bundled resource, working both from source and when frozen
    (PyInstaller extracts data next to the exe under sys._MEIPASS)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


ICON_PATH = _resource("mephistocr", "assets", "app.ico")


def _set_windows_app_id():
    """Give this process its own AppUserModelID so Windows shows our .ico in
    the taskbar/alt-tab instead of grouping it under the generic python.exe
    icon (the default when a Python script hasn't claimed an identity)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MephistOCR.OCRTool")
    except Exception:
        pass


def _run_worker(cfg_path: str):
    """Frozen-exe OCR worker entry (see module docstring). Kept import-light so
    a worker process doesn't drag in Qt."""
    # A windowed (console=False) PyInstaller build can hand us sys.stdout=None;
    # the parent GUI reads our progress protocol over a pipe, so re-attach the
    # real OS handles (fd 1/2) or every _emit() would crash the worker.
    if sys.stdout is None:
        try: sys.stdout = open(1, "w", encoding="utf-8", closefd=False)
        except Exception: pass
    if sys.stderr is None:
        try: sys.stderr = open(2, "w", encoding="utf-8", closefd=False)
        except Exception: pass
    from mephistocr.engine import worker_main
    sys.exit(worker_main(cfg_path))


def _console_available() -> bool:
    """The windowed (console=False) build starts without stdout/stderr. When it
    was launched from a terminal, attach to that console so command-line output
    shows up; when there is none (e.g. files dropped onto the exe), say so."""
    if sys.stdout is not None:
        return True
    if sys.platform != "win32":
        return False
    import ctypes
    if not ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
        return False
    sys.stdout = open("CONOUT$", "w", encoding="utf-8")
    sys.stderr = open("CONOUT$", "w", encoding="utf-8")
    return True


def _run_gui(files: list[str] | None = None):
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication
    from mephistocr.main_window import MainWindow

    _set_windows_app_id()
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")  # neutral base so our QSS fully controls the look
    if os.path.exists(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))
    win = MainWindow()
    if files:
        win.add_files(files)
    win.show()
    sys.exit(app.exec())


def main():
    args = sys.argv[1:]
    if "--worker" in args:
        i = args.index("--worker")
        if i + 1 < len(args):
            _run_worker(args[i + 1])
            return
    if not args:
        _run_gui()
    elif _console_available():
        from mephistocr.cli import main as cli_main
        sys.exit(cli_main(args))
    else:  # files dropped onto the windowed exe: open them in the Files tab
        _run_gui([a for a in args if os.path.isfile(a)])


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
