@echo off
REM ===================================================================
REM  Clipper - jalankan di Windows (double-click file ini)
REM  Otomatis: cek Python -> buat .venv -> install dependensi -> start
REM ===================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Clipper

echo.
echo ==========================================================
echo   Clipper - persiapan
echo ==========================================================
echo.

REM --- cari Python ---
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo   [!] Python belum terpasang.
  echo.
  echo   Langkah:
  echo     1. Buka https://www.python.org/downloads/
  echo     2. Unduh Python 3.11 atau lebih baru
  echo     3. Saat install, centang "Add python.exe to PATH"
  echo     4. Tutup jendela ini, lalu double-click run.bat lagi
  echo.
  pause
  exit /b 1
)
echo   [OK] Python ditemukan: %PY%

REM --- virtualenv (rusak? biasanya karena folder dipindah C: ke D:) ---
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys" >nul 2>nul
  if errorlevel 1 (
    echo   [!]  .venv rusak ^(folder Clipper tampaknya pernah dipindah^)
    echo   [..] Menghapus dan membuat ulang .venv
    rmdir /s /q ".venv"
  )
)
if not exist ".venv\Scripts\python.exe" (
  echo   [..] Membuat virtualenv .venv ^(sekali saja^)
  %PY% -m venv .venv
  if errorlevel 1 (
    echo   [!] Gagal membuat virtualenv.
    pause
    exit /b 1
  )
)
call ".venv\Scripts\activate.bat"

REM --- dependensi ---
REM yt-dlp + EJS/Deno wajib cukup baru karena extractor YouTube sering berubah.
python -c "import datetime, sys, fastapi, uvicorn, imageio_ffmpeg; from importlib.metadata import version; y=tuple(int(p) for p in version('yt-dlp').split('.')[:3]); e=tuple(int(p) for p in version('yt-dlp-ejs').split('.')[:3]); d=tuple(int(p) for p in version('deno').split('.')[:3]); age=(datetime.date.today()-datetime.date(*y)).days; assert y >= (2026,10,21) and (sys.version_info < (3,11) or age <= 45) and e >= (0,8,0) and d >= (2,9,8)" >nul 2>nul
if errorlevel 1 (
  echo   [..] Memasang / memperbarui dependensi ^(termasuk extractor YouTube^)
  python -m pip install --upgrade pip >nul 2>nul
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo   [!] Gagal memasang dependensi. Cek koneksi internet lalu coba lagi.
    pause
    exit /b 1
  )
  python -m pip install --upgrade "yt-dlp[default,deno]>=2026.10.21"
  if errorlevel 1 (
    echo   [!] Gagal memperbarui yt-dlp. Cek koneksi internet lalu coba lagi.
    pause
    exit /b 1
  )
) else (
  echo   [OK] Dependensi dan runtime YouTube sudah siap
)

REM --- kalau venv tetap bermasalah, arahkan ke repair.bat ---
python -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
  echo.
  echo   [!] Lingkungan Python Clipper masih bermasalah.
  echo       Jalankan: repair.bat   ^(memperbaiki venv + service + autostart^)
  echo.
  pause
  exit /b 1
)

echo.
echo   Menjalankan Clipper...
echo.

python run.py %*
set "CODE=%ERRORLEVEL%"
echo.
if not "%CODE%"=="0" (
  echo   Clipper berhenti dengan kode %CODE%.
  echo   Kalau ini tidak terduga, jalankan: python run.py --doctor
)
pause
exit /b %CODE%
