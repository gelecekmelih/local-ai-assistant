@echo off
cd /d "%~dp0"

REM ── Ollama KV cache quantization (q8_0) — VRAM ~yariya iner ─────────────────
REM num_ctx 4096->8192 buyudu; KV cache VRAM'i artar, q8_0 bunu yariya indirir.
REM NOT: Ollama AYRI bir servis olarak calisiyorsa bu 'set' onu ETKILEMEZ.
REM O durumda kalici KULLANICI ortam degiskeni gerekir + Ollama yeniden baslat:
REM   setx OLLAMA_KV_CACHE_TYPE q8_0    (bir kez, sonra Ollama'yi kapat/ac)
set "OLLAMA_KV_CACHE_TYPE=q8_0"

set "PY=%~dp0.venv\Scripts\python.exe"
set "OUT=%~dp0data\klargus_stdout.log"
set "ERR=%~dp0data\klargus_stderr.log"

if not exist "%~dp0data" mkdir "%~dp0data"

echo [%date% %time%] Klargus baslatiliyor... > "%OUT%"
echo [%date% %time%] Klargus hata logu > "%ERR%"

if exist "%~dp0dist\Klargus\Klargus.exe" (
  start "" "%~dp0dist\Klargus\Klargus.exe"
  exit /b 0
)

if exist "%PY%" (
  "%PY%" -c "import sys; print(sys.version)" >nul 2>> "%ERR%"
  if "%errorlevel%"=="0" (
    "%PY%" main.py >> "%OUT%" 2>> "%ERR%"
    set "CODE=%errorlevel%"
    if not "%CODE%"=="0" (
      echo Klargus hata ile kapandi. Kod: %CODE%
      echo Hata logu:
      type "%ERR%"
      pause
    )
    exit /b %CODE%
  )
  echo Sanal ortam Python calismiyor, sistem Python deneniyor. >> "%ERR%"
)

where python >nul 2>nul
if %errorlevel%==0 (
  python main.py >> "%OUT%" 2>> "%ERR%"
  set "CODE=%errorlevel%"
  if not "%CODE%"=="0" (
    echo Klargus hata ile kapandi. Kod: %CODE%
    type "%ERR%"
    pause
  )
  exit /b %CODE%
)

echo Klargus sanal ortami bulunamadi.
echo Once su komutlari calistirin:
echo   python -m venv .venv
echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
pause
exit /b 1
