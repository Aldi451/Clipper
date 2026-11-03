@echo off
REM ===================================================================
REM  Clipper - PERBARUI ke versi terbaru (double-click file ini)
REM
REM  Kenapa perlu: kalau Bang menerima perbaikan dari saya, kode di folder
REM  ini harus diperbarui dulu. Kalau hanya menutup-lalu-membuka Clipper,
REM  versinya tetap yang lama.
REM
REM  Skrip ini:
REM    1. Menutup Clipper yang sedang jalan (supaya file tidak terkunci)
REM    2. Mengambil kode terbaru (git kalau ada, kalau tidak unduh zip)
REM    3. Memasang dependensi baru (kalau ada)
REM    4. Menampilkan versi lama -> versi baru
REM
REM  Data Bang TIDAK dihapus: folder data\ (video, klip, cookies, token,
REM  profil TikTok) tidak pernah ditimpa. File .env juga tidak.
REM ===================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Clipper - Perbarui
set "ROOT=%~dp0"
set "NEWVER="

echo.
echo ==========================================================
echo   Clipper - perbarui ke versi terbaru
echo ==========================================================
echo   Folder: %ROOT%
echo.

REM ---------- versi sebelum update ----------
set "OLDVER="
if exist ".venv\Scripts\python.exe" (
  for /f "usebackq delims=" %%V in (`".venv\Scripts\python.exe" -c "import re,pathlib;m=re.search(r'VERSION\s*=\s*.([0-9.]+).',pathlib.Path('app/config.py').read_text(encoding='utf-8'));print(m.group(1) if m else '?')" 2^>nul`) do set "OLDVER=%%V"
)
if not defined OLDVER (
  for /f "tokens=3" %%V in ('findstr /b /c:"VERSION = " app\config.py 2^>nul') do set "OLDVER=%%V"
  set "OLDVER=!OLDVER:"=!"
)
echo   Versi terpasang sekarang: !OLDVER!
echo.

REM ---------- 1. hentikan Clipper ----------
echo [1/4] Menghentikan Clipper yang sedang jalan...
taskkill /f /im "ClipperService.exe" >nul 2>nul
REM matikan proses run.py milik folder ini saja
for /f "usebackq delims=" %%L in (`powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*run.py*' -and $_.CommandLine -like '*%ROOT%*' } | Select-Object -ExpandProperty ProcessId" 2^>nul`) do (
  echo        menghentikan PID %%L
  taskkill /f /pid %%L >nul 2>nul
)
echo        selesai.

REM ---------- 2. ambil kode terbaru ----------
echo.
echo [2/4] Mengambil kode terbaru...
set "GOT=0"
where git >nul 2>nul
if exist ".git" (
  if not errorlevel 1 (
    echo        git terdeteksi - menjalankan git pull...
    git pull --ff-only
    if not errorlevel 1 set "GOT=1"
    if not errorlevel 1 echo        [OK] kode diperbarui lewat git.
  )
)

if "!GOT!"=="0" (
  echo        Mengunduh paket kode dari GitHub ^(tanpa git^)...
  set "TMPZIP=%TEMP%\clipper_update.zip"
  set "TMPDIR=%TEMP%\clipper_update"
  if exist "!TMPZIP!" del /f /q "!TMPZIP!" >nul 2>nul
  if exist "!TMPDIR!" rmdir /s /q "!TMPDIR!" >nul 2>nul
  powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest -UseBasicParsing 'https://codeload.github.com/Aldi451/Clipper/zip/refs/heads/main' -OutFile '!TMPZIP!' -TimeoutSec 120; exit 0 } catch { Write-Host $_.Exception.Message; exit 1 }"
  if errorlevel 1 (
    echo.
    echo   [!] Gagal mengunduh. Periksa koneksi internet, lalu coba lagi.
    goto :failed
  )
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Path '!TMPZIP!' -DestinationPath '!TMPDIR!' -Force"
  set "SRC=!TMPDIR!\Clipper-main"
  if not exist "!SRC!\run.py" (
    echo   [!] Isi unduhan tidak seperti yang diharapkan.
    goto :failed
  )
  echo        Menyalin kode baru ^(data\ tidak disentuh^)...
  for %%D in (app web tools assets) do (
    if exist "!SRC!\%%D" robocopy "!SRC!\%%D" "%%D" /e /nfl /ndl /njh /njs /nc /ns >nul
  )
  for %%F in (run.py requirements.txt README.md run.bat repair.bat update.bat service.bat build_service_exe.bat diagnosa_tiktok.bat perbaiki_tiktok.bat backup_youtube.bat) do (
    if exist "!SRC!\%%F" copy /y "!SRC!\%%F" "%%F" >nul
  )
  echo        [OK] kode diperbarui dari unduhan.
  rmdir /s /q "!TMPDIR!" >nul 2>nul
  del /f /q "!TMPZIP!" >nul 2>nul
)

REM ---------- 3. dependensi ----------
echo.
echo [3/4] Memasang dependensi (kalau ada yang baru)...
if not exist ".venv\Scripts\python.exe" (
  echo        .venv belum ada - jalankan run.bat dulu, dependensi akan dipasang di sana.
) else (
  ".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
  if errorlevel 1 echo        [!] Sebagian dependensi gagal dipasang - jalankan run.bat untuk mencoba lagi.
  if not errorlevel 1 echo        [OK] dependensi siap.
)

REM ---------- 4. versi baru ----------
echo.
echo [4/4] Versi sekarang:
if exist ".venv\Scripts\python.exe" (
  for /f "usebackq delims=" %%V in (`".venv\Scripts\python.exe" -c "import re,pathlib;m=re.search(r'VERSION\s*=\s*.([0-9.]+).',pathlib.Path('app/config.py').read_text(encoding='utf-8'));print(m.group(1) if m else '?')" 2^>nul`) do set "NEWVER=%%V"
)
if not defined NEWVER (
  for /f "tokens=3" %%V in ('findstr /b /c:"VERSION = " app\config.py 2^>nul') do set "NEWVER=%%V"
  set "NEWVER=!NEWVER:"=!"
)
echo        sebelum: !OLDVER!
echo        sesudah: !NEWVER!
echo.
if "!OLDVER!"=="!NEWVER!" (
  echo   Versi tidak berubah. Kemungkinan folder ini sudah versi terbaru,
  echo   atau unduhan tidak berhasil. Coba lagi dengan koneksi internet aktif.
) else (
  echo   [OK] Clipper diperbarui ke versi !NEWVER!
)
echo.
echo ==========================================================
echo   Selesai. Double-click run.bat untuk menjalankan Clipper.
echo   Setelah terbuka, versi terlihat di kiri bawah layar (v...).
echo ==========================================================
echo.
pause
exit /b 0

:failed
echo.
echo ==========================================================
echo   Update gagal. Clipper TIDAK diubah (masih versi !OLDVER!).
echo   Jalankan run.bat seperti biasa - tidak ada yang rusak.
echo ==========================================================
echo.
pause
exit /b 1
