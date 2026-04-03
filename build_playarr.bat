@echo off
:: Playarr — Build Script
:: Builds the frontend for production and prepares for single-port serving.
::
:: Prerequisites:
::   - Node.js 18+ with npm
::   - Python 3.10+ with pip (for backend dependencies)

title Playarr Build

echo.
echo  ========================================
echo   Playarr Production Build
echo  ========================================
echo.

:: Check Node.js
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERROR: Node.js not found. Install Node.js 18+ from https://nodejs.org/
    pause
    exit /b 1
)

:: Check npm
where npm >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERROR: npm not found. Install Node.js 18+ from https://nodejs.org/
    pause
    exit /b 1
)

:: Install frontend dependencies
echo  [1/3] Installing frontend dependencies...
cd "%~dp0frontend"
call npm install
if %errorlevel% neq 0 (
    echo  ERROR: npm install failed.
    pause
    exit /b 1
)

:: Build frontend
echo  [2/3] Building frontend for production...
call npm run build
if %errorlevel% neq 0 (
    echo  ERROR: Frontend build failed.
    pause
    exit /b 1
)

:: Verify dist was created
if not exist "%~dp0frontend\dist\index.html" (
    echo  ERROR: Frontend build did not produce dist/index.html
    pause
    exit /b 1
)

:: Install backend dependencies
echo  [3/3] Checking backend dependencies...
cd "%~dp0backend"
if exist "%~dp0venv\Scripts\pip.exe" (
    "%~dp0venv\Scripts\pip.exe" install -r requirements.txt -q
) else if exist "%~dp0backend\venv\Scripts\pip.exe" (
    "%~dp0backend\venv\Scripts\pip.exe" install -r requirements.txt -q
) else (
    echo  NOTE: No venv found — skipping pip install. Ensure dependencies are installed.
)

echo.
echo  ========================================
echo   Build complete!
echo  ========================================
echo.
echo  Frontend built to: frontend\dist\
echo  Start Playarr with: start_playarr.bat
echo  Or: python run_playarr.py
echo.
pause
