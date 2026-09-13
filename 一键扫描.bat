@echo off
set "PATH=%PATH%;C:\Program Files\MediaInfo"
title Media Duplicate Finder

echo ============================================================
echo              Media Duplicate Finder
echo ============================================================
echo.

cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found, please install Python 3.8+
    echo Download: https://www.python.org/downloads/
    pause
    exit /b 1
)

python -c "import pymediainfo" 2>nul
if errorlevel 1 (
    echo [INFO] Installing pymediainfo ...
    pip install pymediainfo
    if errorlevel 1 (
        echo [ERROR] pymediainfo install failed, run manually: pip install pymediainfo
        pause
        exit /b 1
    )
)

where mediainfo >nul 2>&1
if errorlevel 1 (
    echo [WARN] MediaInfo CLI not found
    echo         Download: https://mediaarea.net/en/MediaInfo/Download/Windows
    echo.
    choice /C YN /M "Continue anyway?"
    if errorlevel 2 exit /b 1
)

echo Select scan mode:
echo.
echo   [1] Single folder
echo   [2] Drive range (e.g. G-U)
echo   [3] Default G-U
echo.
set /p choice=Option [1-3]: 

if "%choice%"=="1" goto :single
if "%choice%"=="2" goto :range
if "%choice%"=="3" goto :auto
echo Invalid option
pause
exit /b 1

:single
set /p folder=Folder path: 
if not exist "%folder%" (
    echo [ERROR] Path not found: %folder%
    pause
    exit /b 1
)
echo.
echo Scanning %folder% ...
echo.
python "%~dp0mediadupfinder.py" "%folder%"
goto :end

:range
set /p drives=Drive range (e.g. G-U): 
echo.
echo Scanning %drives% ...
echo.
python "%~dp0mediadupfinder.py" --drives %drives%
goto :end

:auto
echo Scanning G-U ...
echo.
python "%~dp0mediadupfinder.py" --drives G-U
goto :end

:end
echo.
echo ============================================================
echo Done!
echo ============================================================
pause