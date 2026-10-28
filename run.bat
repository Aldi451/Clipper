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

REM --- virtualenv ---
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
python -c "import fastapi, uvicorn, imageio_ffmpeg" >nul 2>nul
if errorlevel 1 (
  echo   [..] Memasang dependensi ^(butuh internet, sekali saja^)
  python -m pip install --upgrade pip >nul 2>nul
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo   [!] Gagal memasang dependensi. Cek koneksi internet lalu coba lagi.
    pause
    exit /b 1
  )
) else (
  echo   [OK] Dependensi sudah terpasang
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
