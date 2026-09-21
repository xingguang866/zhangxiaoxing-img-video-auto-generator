@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

python -c "import PySide6, requests, openpyxl, PIL, imageio_ffmpeg" >nul 2>&1
if errorlevel 1 (
    echo Installing required packages...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Dependency installation failed.
        pause
        exit /b 1
    )
)

start "" pythonw app.py
if errorlevel 1 (
    echo Application exited with an error.
    pause
)

endlocal
