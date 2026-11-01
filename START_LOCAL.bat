@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" goto setup
if exist ".venv\installed_8_4_5.ok" goto run
:setup
call INSTALL_DEPS.bat
if errorlevel 1 (
  echo ERROR: Dependency installation failed.
  pause
  exit /b 1
)
:run
".venv\Scripts\python.exe" main.py %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo Exit code: %EXIT_CODE%  (0=run saved, 1=processing error, 2=strict quality review required)
echo 100 percent means work completed, not that every value is verified.
pause
exit /b %EXIT_CODE%
