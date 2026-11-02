@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set LOCAL_CELL_OCR=0
if not exist ".venv\installed_8_4_5_1.ok" call INSTALL_DEPS.bat
if not exist ".venv\Scripts\python.exe" exit /b 1
".venv\Scripts\python.exe" -m unittest discover -s tests -v
set "EXIT_CODE=%ERRORLEVEL%"
pause
exit /b %EXIT_CODE%
