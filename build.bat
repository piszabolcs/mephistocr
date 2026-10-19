@echo off
REM ------------------------------------------------------------------
REM  Build MephistOCR.exe (onedir, windowed) via PyInstaller.
REM  Output: dist\MephistOCR\MephistOCR.exe
REM ------------------------------------------------------------------
setlocal
cd /d "%~dp0"

echo Installing build dependencies...
python -m pip install -r requirements.txt || goto :err
python -m pip install pyinstaller==6.22.3 || goto :err

echo.
echo Cleaning previous build...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

echo.
echo Building...
python -m PyInstaller --noconfirm MephistOCR.spec || goto :err

echo.
echo ============================================================
echo  Done.  see dist\MephistOCR\MephistOCR.exe
echo ============================================================
goto :eof

:err
echo.
echo BUILD FAILED (exit code %errorlevel%).
exit /b 1
