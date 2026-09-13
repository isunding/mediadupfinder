@echo off
setlocal
title Media Duplicate Finder - Web UI
set "PATH=%PATH%;C:\Program Files\MediaInfo"

echo ============================================================
echo      Media Duplicate Finder  -  Web UI (browser mode)
echo ============================================================
echo.

cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found, please install Python 3.8+
    echo         Download: https://www.python.org/downloads/
    pause
    exit /b 1
)

python -c "import flask, flask_cors" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing flask / flask-cors ...
    pip install flask flask-cors
    if errorlevel 1 (
        echo [ERROR] Install failed. Run manually: pip install flask flask-cors
        pause
        exit /b 1
    )
)

where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo [WARN] ffmpeg not found - result page thumbnails will show placeholders.
    echo        Optional install: scoop install ffmpeg
    echo.
)

set "PORT=5000"
echo Starting web service : http://localhost:%PORT%
echo Browser will open in a few seconds. Press Ctrl+C to stop.
echo.

start "" /min cmd /c "ping -n 3 127.0.0.1 >nul & start http://localhost:%PORT%"

python "%~dp0web.py" %*

echo.
echo Web service stopped.
pause
