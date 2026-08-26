@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Yerel Hesap Pro X - V2.9 Kalite Kapıları

where py >nul 2>nul
if errorlevel 1 (
  echo Python bulunamadı. Python 3.11 veya daha yeni bir sürüm kurun.
  pause
  exit /b 1
)

if not exist .venv\Scripts\python.exe py -m venv .venv
if errorlevel 1 goto :error

.venv\Scripts\python.exe -m pip install --disable-pip-version-check -r backend\requirements-dev.txt
if errorlevel 1 goto :error

set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
pushd backend
.venv\Scripts\python.exe -m compileall -q app alembic
if errorlevel 1 goto :backend_error
.venv\Scripts\python.exe -m pytest -q
if errorlevel 1 goto :backend_error
popd

echo.
echo Backend kalite kapıları geçti.

where npm >nul 2>nul
if errorlevel 1 (
  echo Node.js bulunamadığı için frontend testleri atlandı.
  goto :success
)

pushd frontend
if not exist node_modules call npm ci
if errorlevel 1 goto :frontend_error
call npm test -- --run
if errorlevel 1 goto :frontend_error
call npm run build
if errorlevel 1 goto :frontend_error
popd

:success
echo.
echo TÜM ERİŞİLEBİLİR V2.9 KALİTE KAPILARI GEÇTİ.
echo PostgreSQL testleri için gerçek PostgreSQL 16 bağlantısı gerekir.
pause
exit /b 0

:backend_error
popd
goto :error

:frontend_error
popd
goto :error

:error
echo.
echo TEST BAŞARISIZ. Yukarıdaki hata çıktısını paylaşın.
pause
exit /b 1
