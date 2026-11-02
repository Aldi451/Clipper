@echo off
REM ===================================================================
REM  Clipper - PERBAIKI setelah folder dipindah (mis. C:\ ke D:\)
REM
REM  Kenapa perlu: .venv menyimpan path absolut, jadi setelah folder
REM  dipindah Python di dalamnya tidak bisa dipakai dan Windows service
REM  masih menunjuk lokasi lama. Skrip ini membereskan semuanya.
REM
REM  Double-click file ini. Data (video, klip, cookies, token YouTube)
REM  TIDAK dihapus.
REM ===================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Clipper - Perbaikan

echo.
echo ==========================================================
echo   Clipper - Perbaikan setelah folder dipindah
echo ==========================================================
echo   Folder sekarang: %~dp0
echo.

REM ---------- 1. cek file penting ----------
set "MISSING=0"
for %%F in (run.py requirements.txt web\index.html app\server.py) do (
  if not exist "%%F" (
    echo   [!] Hilang: %%F
    set "MISSING=1"
  )
)
if not exist "app\" (
  echo   [!] Hilang: folder app\
  set "MISSING=1"
)
if "%MISSING%"=="1" (
  echo.
  echo   Folder ini belum lengkap. Kemungkinan:
  echo     - proses salin/cut belum selesai
  echo     - antivirus memblokir sebagian file
  echo     - yang dibuka bukan folder Clipper
  echo.
  echo   Perbaiki: salin ulang folder Clipper, atau:
  echo     git clone https://github.com/Aldi451/Clipper
  echo.
  pause
  exit /b 1
)
echo   [OK]  File project lengkap

REM ---------- 2. cari Python sistem (untuk membuat venv baru) ----------
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
  echo   [!] Python tidak ditemukan di PATH.
  echo       Install Python 3.11/3.12 dari https://www.python.org/downloads/
  echo       ^(centang "Add python.exe to PATH"^), lalu jalankan file ini lagi.
  pause
  exit /b 1
)
echo   [OK]  Python: %PY%

REM ---------- 3. hentikan Clipper yang mungkin masih jalan ----------
echo.
echo   [..] Menghentikan Clipper/service yang masih jalan ^(kalau ada^)
sc stop Clipper >nul 2>nul
taskkill /f /im python.exe /fi "WINDOWTITLE eq Clipper*" >nul 2>nul
timeout /t 1 >nul

REM ---------- 4. venv rusak? hapus & buat ulang ----------
set "VENV_OK=0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import fastapi, uvicorn" >nul 2>nul
  if errorlevel 1 (
    echo   [!]  .venv rusak ^(biasanya karena folder dipindah^)
    echo   [..] Menghapus .venv lama
    rmdir /s /q ".venv"
  ) else (
    set "VENV_OK=1"
    echo   [OK]  .venv sehat
  )
)

if "%VENV_OK%"=="0" (
  echo   [..] Membuat .venv baru
  %PY% -m venv .venv
  if errorlevel 1 (
    echo   [!] Gagal membuat .venv
    pause
    exit /b 1
  )
  echo   [..] Memasang dependensi ^(butuh internet, 2-5 menit^)
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>nul
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo   [!] Gagal memasang dependensi.
    echo       Cek koneksi internet, lalu jalankan repair.bat lagi.
    pause
    exit /b 1
  )
)

REM ---------- 5. perbaiki service & autostart yang menunjuk path lama ----------
echo.
echo   [..] Memeriksa Windows service dan autostart
".venv\Scripts\python.exe" run.py --repair --no-pause
if errorlevel 1 (
  echo.
  echo   [i]  Sebagian perbaikan butuh Administrator ^(service Windows^).
  echo        Klik kanan repair.bat - Run as administrator, lalu jalankan lagi.
)

REM ---------- 6. verifikasi akhir ----------
echo.
echo ==========================================================
echo   Pemeriksaan akhir
echo ==========================================================
".venv\Scripts\python.exe" run.py --doctor --no-pause

echo.
echo ==========================================================
echo   Selesai.
echo   Jalankan: run.bat   ^(atau ClipperService.exe - Start^)
echo   Buka    : http://localhost:8420
echo ==========================================================
echo.
if not "%~1"=="--quiet" pause
exit /b 0
