@echo off
REM ===================================================================
REM  Clipper - PERBAIKI dukungan TikTok (memasang paket & browser)
REM
REM  Yang diubah HANYA:
REM    1. paket playwright di dalam .venv Clipper
REM    2. unduhan browser Chromium (kalau Edge/Chrome tidak ada),
REM       disimpan di %LOCALAPPDATA%\ms-playwright
REM
REM  Yang TIDAK disentuh: folder data\ (video, klip, cookies, token
REM  YouTube, profil TikTok), .env, dan pengaturan apa pun.
REM
REM  Tidak butuh Administrator.
REM
REM  Setelah selesai, script juga menguji membuka browser (headless)
REM  supaya ketahuan langsung berhasil atau tidak.
REM ===================================================================
setlocal
cd /d "%~dp0"
title Clipper - Perbaiki TikTok

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo   [!] Folder .venv belum ada.
  echo.
  echo   Jalankan run.bat sekali dulu ^(dia yang membuat .venv dan
  echo   memasang dependensi^), lalu jalankan file ini lagi.
  echo.
  pause
  exit /b 1
)

if not exist "tools\doctor.py" (
  echo.
  echo   [!] tools\doctor.py tidak ditemukan.
  echo       Folder Clipper ini kemungkinan masih versi lama.
  echo       Double-click update.bat dulu, lalu jalankan file ini lagi.
  echo.
  pause
  exit /b 1
)

REM Pastikan Clipper tidak sedang berjalan supaya .venv tidak terkunci
echo.
echo   Memeriksa apakah Clipper sedang berjalan...
for /f "usebackq delims=" %%L in (`powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*run.py*' } | Select-Object -ExpandProperty ProcessId" 2^>nul`) do (
  echo   - menghentikan Clipper ^(PID %%L^) agar paket bisa dipasang
  taskkill /f /pid %%L >nul 2>nul
)

echo.
echo ==========================================================
echo   Memperbaiki dukungan TikTok Clipper
echo ==========================================================
REM --- greenlet rusak? ("DLL load failed while importing _greenlet") ---
REM Penyebab tersering di Windows. Perbaikan: pasang ulang greenlet +
REM playwright DI DALAM .venv (bukan di Python sistem).
echo.
echo   [1/3] Memeriksa greenlet ^(mesin Playwright^)...
".venv\Scripts\python.exe" -c "import playwright.sync_api" >nul 2>nul
if not errorlevel 1 (
  echo   [OK]  greenlet + playwright bisa dipakai
  goto :docs
)
echo   [!]   greenlet/playwright rusak - memasang ulang...
echo         ^(butuh internet, sekitar 1-3 menit^)
".venv\Scripts\python.exe" -m pip install --force-reinstall --no-cache-dir --disable-pip-version-check greenlet playwright
echo.
echo   [2/3] Memeriksa ulang...
".venv\Scripts\python.exe" -c "import playwright.sync_api; print('   [OK]  greenlet + playwright sehat')"
if errorlevel 1 (
  echo   [!]   Masih gagal.
  echo.
  echo   Kemungkinan Microsoft Visual C++ Redistributable belum terpasang.
  echo   Unduh "Microsoft Visual C++ Redistributable 2015-2022 x64" dari situs
  echo   Microsoft ^(gratis^), pasang, restart, lalu jalankan file ini lagi.
  echo.
  echo   Catatan: tombol "Buka Chrome manual" + "Cek login" tetap bisa dipakai
  echo   karena jalur itu TIDAK memakai Playwright.
)

:docs
echo.
echo   [3/3] Melengkapi browser ^& pengaturan lain...
".venv\Scripts\python.exe" "tools\doctor.py" --fix --verify
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
  echo   [OK] Selesai. Buka Clipper ^(run.bat^) lalu tab TikTok:
  echo        klik "Buka TikTok ^& login".
) else (
  echo   [!] Masih ada yang gagal. Baca pesan di atas - biasanya jelas
  echo       penyebabnya ^(koneksi internet, antivirus, atau browser^).
  echo       Log lengkap ada di folder logs\.
)
echo.
pause
exit /b %RC%
