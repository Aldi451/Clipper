@echo off
REM ===================================================================
REM  Clipper - BACKUP clip ke YouTube (video private)
REM
REM  Yang di-backup HANYA file video hasil render (data\clips\*.mp4).
REM  Database, settings, cookies, dan token TIDAK ikut - YouTube hanya
REM  menerima video, bukan arsip/zip.
REM
REM  Video dikirim sebagai PRIVATE: tidak muncul di channel/pencarian,
REM  hanya bisa dibuka lewat link oleh akun sendiri.
REM
REM  BATASAN: kuota YouTube API 10.000 unit/hari, 1 upload = 1.600 unit
REM  -> maksimal 6 video/hari. Script mengirim 5 per jalan, sisanya
REM  dilanjutkan otomatis besok (tidak ada yang dikirim dobel).
REM
REM  Mode default = RENCANA (tidak mengunggah). Untuk benar-benar
REM  mengirim, jalankan dengan argumen --upload:
REM      backup_youtube.bat --upload
REM ===================================================================
setlocal
cd /d "%~dp0"
title Clipper - Backup ke YouTube

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo   [!] Folder .venv belum ada. Jalankan run.bat sekali dulu.
  echo.
  pause
  exit /b 1
)

if not exist "tools\backup_youtube.py" (
  echo.
  echo   [!] tools\backup_youtube.py tidak ditemukan.
  echo       Folder ini kemungkinan masih versi lama - jalankan update.bat.
  echo.
  pause
  exit /b 1
)

REM Tanpa argumen = dry-run; dengan --upload = benar-benar mengirim
".venv\Scripts\python.exe" "tools\backup_youtube.py" %*
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
  echo   Selesai. Lihat daftar video yang sudah di-backup dengan:
  echo       backup_youtube.bat --show
) else if "%RC%"=="2" (
  echo   [!] Tidak bisa melanjutkan - lihat pesan di atas
  echo       ^(biasanya: YouTube belum terhubung, atau kuota hari ini habis^).
) else (
  echo   [!] Sebagian berkas gagal. Jalankan lagi besok untuk melanjutkan.
)
echo.
pause
exit /b %RC%
