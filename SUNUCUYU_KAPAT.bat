@echo off
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -Command "$c=Get-NetTCPConnection -LocalPort 5050 -State Listen -ErrorAction SilentlyContinue; if($c){$c|%%{Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue}; Write-Host 'Yerel Hesap sunucusu kapatildi.' -ForegroundColor Green}else{Write-Host '5050 portunda calisan sunucu yok.'}"
pause
