import os
import sys

# Tests must never put a window on the screen.
os.environ["QT_QPA_PLATFORM"] = "offscreen"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _tessdata():
    """Workers call setup_tesseract() themselves; tests that call ocr_one()
    directly need the same environment in the test process."""
    from mephistocr import engine
    assert engine.setup_tesseract()[0]


@pytest.fixture(scope="session", autouse=True)
def _isolated_home(tmp_path_factory):
    """Settings and file-mode logs go to a temp folder, never %LOCALAPPDATA%."""
    os.environ["MEPHISTOCR_HOME"] = str(tmp_path_factory.mktemp("home"))
