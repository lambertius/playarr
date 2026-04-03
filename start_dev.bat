@echo off
:: Playarr — Development Mode Launcher
:: Starts backend and frontend dev servers separately.
::
:: Backend: uvicorn on port 6969 (with auto-reload)
:: Frontend: Vite dev server on port 3000 (with HMR + API proxy)

title Playarr [DEV]

set PLAYARR_DEV=1

echo.
echo  Playarr Development Mode
echo  Backend:  http://localhost:6969/api
echo  Frontend: http://localhost:3000
echo.

:: Start backend in a new window
start "Playarr Backend" cmd /k "cd /d %~dp0backend && if exist venv\Scripts\activate.bat (call venv\Scripts\activate.bat) else (if exist ..\venv\Scripts\activate.bat call ..\venv\Scripts\activate.bat) && python _start_server.py"

:: Wait a moment for backend to start
timeout /t 2 /nobreak >nul

:: Start frontend in a new window
start "Playarr Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"

echo  Both servers starting in separate windows.
echo  Close this window when done.
