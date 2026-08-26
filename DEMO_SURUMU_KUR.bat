@echo off
setlocal
cd /d "%~dp0backend"
if exist demo_veriler.db del /q demo_veriler.db
python seed_demo_data.py --database demo_veriler.db
if errorlevel 1 (
  echo.
  echo Demo verileri olusturulamadi.
  pause
  exit /b 1
)
echo.
echo Demo veritabani hazirlandi: backend\demo_veriler.db
pause
