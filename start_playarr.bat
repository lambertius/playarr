@echo off
:: Playarr — Music Video Manager
:: Production launcher for Windows
::
:: This script starts Playarr using the bundled or venv Python.
:: It is the entry point an installer shortcut would call.

title Playarr

:: Find Python — check for bundled runtime first, then venv, then system
if exist "%~dp0python\python.exe" (
    set "PYTHON=%~dp0python\python.exe"
) else if exist "%~dp0venv\Scripts\python.exe" (
    set "PYTHON=%~dp0venv\Scripts\python.exe"
) else if exist "%~dp0backend\venv\Scripts\python.exe" (
    set "PYTHON=%~dp0backend\venv\Scripts\python.exe"
) else (
    where python >nul 2>&1
    if %errorlevel% equ 0 (
        set "PYTHON=python"
    ) else (
        echo.
        echo  ERROR: Python not found.
        echo  Install Python 3.10+ from https://www.python.org/downloads/
        echo  or place a bundled Python runtime in the "python" folder.
        echo.
        pause
        exit /b 1
    )
)

echo.
echo  Starting Playarr...
echo.

:: Launch the production entry point
%PYTHON% "%~dp0run_playarr.py" %*

if %errorlevel% neq 0 (
    echo.
    echo  Playarr exited with an error. Check logs for details.
    echo.
    pause
)
