@echo off
setlocal
cd /d "%~dp0"
if not exist backend\demo_veriler.db (
  echo Demo veritabani bulunamadi. Once DEMO_SURUMU_KUR.bat dosyasini calistirin.
  pause
  exit /b 1
)
set DEMO_MODE=true
set DATABASE_URL=sqlite:///%CD:\=/%/backend/demo_veriler.db
call baslat.bat
