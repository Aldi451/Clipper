@echo off
REM ===================================================================
REM  Clipper - DIAGNOSA dukungan TikTok (hanya membaca, tidak mengubah)
REM
REM  Double-click file ini kalau tab TikTok Clipper menulis
REM  "belum dipasang" / "browser belum ditemukan".
REM
REM  Script ini TIDAK mengubah apa pun. Ia melaporkan:
REM    - versi Clipper di folder ini dan versi yang sedang jalan
REM    - paket playwright ada di mana (di .venv atau di luar)
REM    - browser yang terdeteksi (Edge/Chrome/Brave/Vivaldi/Chromium)
REM    - apakah Clipper siap upload TikTok
REM  Semua hasil juga disimpan di logs\tiktok-doctor-<waktu>.log
REM
REM  Untuk memperbaiki: jalankan perbaiki_tiktok.bat
REM ===================================================================
setlocal
cd /d "%~dp0"
title Clipper - Diagnosa TikTok

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

".venv\Scripts\python.exe" "tools\doctor.py" %*
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
  echo   Hasil: SIAP dipakai untuk upload TikTok.
) else (
  echo   Hasil: BELUM siap. Jalankan perbaiki_tiktok.bat untuk memperbaiki,
  echo          atau kirim isi log di atas ke saya.
)
echo.
pause
exit /b %RC%
