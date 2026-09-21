@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set QT_QPA_PLATFORM=offscreen
python -m unittest discover -s tests -v
pause
endlocal
