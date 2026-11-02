@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if exist ".venv\Scripts\python.exe" goto install
py -3.11 -m venv .venv
if not exist ".venv\Scripts\python.exe" (
  echo ERROR: Python could not create .venv. Install Python with venv support.
  exit /b 1
)
:install
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
> ".venv\installed_4_8_5_4.ok" echo 4.8.5.4
exit /b 0
