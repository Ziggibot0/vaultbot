@echo off
REM ===================================================================
REM  VaultBot one-click setup wizard (Windows)
REM  Double-click this file to set up VaultBot. No terminal skills needed.
REM ===================================================================
SETLOCAL ENABLEEXTENSIONS
CD /D "%~dp0"

echo.
echo ============================================================
echo   VaultBot Setup Wizard
echo ============================================================
echo.

REM Prefer a system Python (the venv doesn't exist yet on first run).
REM Try the `py` launcher first (bundled with official Python installers),
REM then fall back to `python` on PATH.
WHERE py >nul 2>nul
IF %ERRORLEVEL%==0 (
    py "%~dp0setup_wizard.py"
    GOTO :done
)
WHERE python >nul 2>nul
IF %ERRORLEVEL%==0 (
    python "%~dp0setup_wizard.py"
    GOTO :done
)

echo.
echo [ERROR] Python was not found on this computer.
echo.
echo VaultBot needs Python 3.11 or newer. Install it from:
echo   https://www.python.org/downloads/
echo.
echo IMPORTANT: on the installer's first screen, tick the box that says
echo "Add Python to PATH" before you click Install. Then run this file again.
echo.
PAUSE
EXIT /B 1

:done
echo.
PAUSE