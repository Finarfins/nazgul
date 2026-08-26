$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$log = Join-Path $PSScriptRoot 'baslatma.log'
"[$(Get-Date -Format s)] Başlatılıyor" | Out-File $log -Encoding utf8
function Log($m) { $m | Tee-Object -FilePath $log -Append }
try {
  $py = $null
  if (Get-Command py -ErrorAction SilentlyContinue) { $py = @('py','-3') }
  elseif (Get-Command python -ErrorAction SilentlyContinue) { $py = @('python') }
  if (-not $py) { throw 'Python bulunamadı. Python 3.11 veya 3.12 kurun.' }
  $venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
  if (-not (Test-Path $venvPython)) {
    Log 'Sanal ortam oluşturuluyor...'
    if ($py.Count -eq 2) { & $py[0] $py[1] -m venv .venv *>> $log }
    else { & $py[0] -m venv .venv *>> $log }
  }
  Log 'Python paketleri kontrol ediliyor...'
  & $venvPython -m pip install --disable-pip-version-check -r 'backend\requirements.txt' *>> $log

  # Her gün ilk açılışta veritabanının güvenli kopyasını al.
  $dbFile = Join-Path $PSScriptRoot 'backend\veriler.db'
  if (Test-Path $dbFile) {
    $backupDir = Join-Path $PSScriptRoot 'yedekler'
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    $backupFile = Join-Path $backupDir ("veriler_" + (Get-Date -Format 'yyyyMMdd') + '.db')
    if (-not (Test-Path $backupFile)) {
      Copy-Item $dbFile $backupFile -Force
      Log ("Günlük veritabanı yedeği oluşturuldu: " + $backupFile)
    }
  }
  # Eski test paketinden kalan 5050 sunucusu yeni frontend dosyalarını sunmasın.
  $inUse = Get-NetTCPConnection -LocalPort 5050 -State Listen -ErrorAction SilentlyContinue
  if ($inUse) {
    Log '5050 portundaki önceki Yerel Hesap sunucusu kapatılıyor...'
    $inUse | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
      Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
  }
  Log 'Sunucu başlatılıyor...'
  $proc = Start-Process -FilePath $venvPython -ArgumentList 'run.py' -WorkingDirectory (Join-Path $PSScriptRoot 'backend') -RedirectStandardOutput (Join-Path $PSScriptRoot 'sunucu.log') -RedirectStandardError (Join-Path $PSScriptRoot 'sunucu_hata.log') -PassThru
  $ready = $false
  for ($i=0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    if ($proc.HasExited) { throw 'Sunucu başlatılırken kapandı. sunucu_hata.log dosyasına bakın.' }
    try {
      $r = Invoke-WebRequest 'http://127.0.0.1:5050/api/ready' -UseBasicParsing -TimeoutSec 2
      if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch {}
  }
  if (-not $ready) { throw 'Sunucu 20 saniye içinde hazır olmadı.' }
  Start-Process 'http://127.0.0.1:5050/?build=v2.9-hardening'
  Write-Host 'Yerel Hesap Pro açıldı: http://127.0.0.1:5050' -ForegroundColor Green
  Write-Host 'Giriş: admin / admin123'
  Read-Host 'Bu pencereyi kapatmak için Enter'
} catch {
  Log ('HATA: ' + $_.Exception.Message)
  Write-Host $_.Exception.Message -ForegroundColor Red
  Read-Host 'Kapatmak için Enter'
  exit 1
}
