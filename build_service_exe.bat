@echo off
REM ===================================================================
REM  Build ClipperService.exe  (jalankan sekali di Windows)
REM  Hasil: dist\ClipperService.exe  ->  salin ke folder Clipper
REM ===================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Build ClipperService.exe

echo.
echo ==========================================================
echo   Build ClipperService.exe
echo ==========================================================
echo.

REM ---------- cari Python + venv ----------
if not exist ".venv\Scripts\python.exe" (
  echo   [..] .venv belum ada, menyiapkan dulu ^(sekali saja^)
  set "PY="
  where py >nul 2>nul && set "PY=py -3"
  if not defined PY ( where python >nul 2>nul && set "PY=python" )
  if not defined PY (
    echo   [!] Python belum terpasang. Unduh di https://www.python.org/downloads/
    echo       Centang "Add python.exe to PATH" saat install.
    pause
    exit /b 1
  )
  %PY% -m venv .venv
  if errorlevel 1 ( echo   [!] Gagal membuat venv. & pause & exit /b 1 )
)

set "VPY=.venv\Scripts\python.exe"

REM ---------- pastikan dependensi inti + pywin32 + pyinstaller ----------
"%VPY%" -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
  echo   [..] Memasang dependensi Clipper ^(butuh internet^)
  "%VPY%" -m pip install -r requirements.txt
  if errorlevel 1 ( echo   [!] Gagal memasang dependensi. & pause & exit /b 1 )
)

"%VPY%" -c "import win32service" >nul 2>nul
if errorlevel 1 (
  echo   [..] Memasang pywin32 ^(dibutuhkan service Clipper^)
  "%VPY%" -m pip install pywin32
)

"%VPY%" -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
  echo   [..] Memasang PyInstaller ^(untuk membuat .exe^)
  "%VPY%" -m pip install --upgrade pyinstaller
  if errorlevel 1 ( echo   [!] Gagal memasang PyInstaller. & pause & exit /b 1 )
)

REM ---------- build ----------
echo.
echo   [..] Membuat .exe  ^(1-2 menit^)
"%VPY%" -m PyInstaller ^
  --noconfirm --clean ^
  --onefile ^
  --windowed ^
  --name ClipperService ^
  --distpath dist ^
  --workpath build\pyinstaller ^
  --specpath build ^
  service_manager.py
if errorlevel 1 (
  echo.
  echo   [!] Build gagal. Salin pesan di atas kalau butuh bantuan.
  pause
  exit /b 1
)

REM ---------- salin ke folder project ----------
if exist "dist\ClipperService.exe" (
  copy /y "dist\ClipperService.exe" "ClipperService.exe" >nul
  echo.
  echo   [OK] Selesai: %~dp0ClipperService.exe
  echo.
  echo   Pakai:
  echo     1. Double-click ClipperService.exe
  echo     2. Pilih tipe startup: Automatic / Manual / Disabled
  echo     3. Klik "Pasang / Update service", lalu Start bila perlu
  echo.
  echo   Catatan: .exe ini hanya MANAJER. Service-nya tetap memakai
  echo   .venv\Scripts\python.exe + run.py, jadi git pull langsung berlaku.
) else (
  echo   [!] dist\ClipperService.exe tidak ditemukan.
)

echo.
pause
