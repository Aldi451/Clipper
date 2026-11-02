@echo off
REM ===================================================================
REM  Clipper - Windows service (tanpa jendela Command Prompt)
REM  Double-click file ini, lalu pilih menu.
REM  Butuh izin Administrator untuk memasang / melepas service.
REM ===================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Clipper Service

REM ---------- minta izin Administrator (sekali saja) ----------
net session >nul 2>&1
if errorlevel 1 (
  echo.
  echo   Meminta izin Administrator...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "ACTION=%~1"

echo.
echo ==========================================================
echo   Clipper - mode service (tanpa jendela cmd)
echo ==========================================================

REM ---------- siapkan Python + dependensi kalau belum ada ----------
if not exist "%VENV_PY%" (
  echo.
  echo   [!] .venv belum ada. Jalankan run.bat sekali dulu, atau saya buatkan sekarang.
  set "PY="
  where py >nul 2>nul && set "PY=py -3"
  if not defined PY ( where python >nul 2>nul && set "PY=python" )
  if not defined PY (
    echo   [!] Python belum terpasang. Unduh di https://www.python.org/downloads/
    echo       Centang "Add python.exe to PATH" saat install.
    pause
    exit /b 1
  )
  echo   [..] Membuat virtualenv .venv
  %PY% -m venv .venv
  if errorlevel 1 ( echo   [!] Gagal membuat venv. & pause & exit /b 1 )
)

"%VENV_PY%" -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
  echo   [..] Memasang dependensi ^(butuh internet, sekali saja^)
  "%VENV_PY%" -m pip install --upgrade pip >nul 2>nul
  "%VENV_PY%" -m pip install -r requirements.txt
  if errorlevel 1 ( echo   [!] Gagal memasang dependensi. Cek koneksi internet. & pause & exit /b 1 )
)

"%VENV_PY%" -c "import win32service" >nul 2>nul
if errorlevel 1 (
  echo   [..] Memasang pywin32 ^(untuk Windows service^)
  "%VENV_PY%" -m pip install pywin32
)

REM ---------- mode tanpa menu: service.bat install / start / stop / ... ----------
if defined ACTION goto dispatch

:menu
echo.
echo   1.  Pasang service  ^(auto-start saat Windows nyala^)
echo   2.  Mulai service
echo   3.  Hentikan service
echo   4.  Status service
echo   5.  Hapus service
echo   6.  Buka Clipper di browser
echo   7.  Lihat log service
echo   8.  Buat shortcut Clipper di Desktop
echo   9.  Alternatif tanpa service: autostart Task Scheduler
echo   10. Buat ClipperService.exe ^(manajer service versi .exe^)
echo   11. Perbaiki setelah folder dipindah ^(jalankan repair.bat^)
echo   0.  Keluar
echo.
set /p "CHOICE=  Pilih menu (0-11): "
if "%CHOICE%"=="1" set "ACTION=install"
if "%CHOICE%"=="2" set "ACTION=start"
if "%CHOICE%"=="3" set "ACTION=stop"
if "%CHOICE%"=="4" set "ACTION=status"
if "%CHOICE%"=="5" set "ACTION=remove"
if "%CHOICE%"=="6" set "ACTION=open"
if "%CHOICE%"=="7" set "ACTION=log"
if "%CHOICE%"=="8" set "ACTION=shortcut"
if "%CHOICE%"=="9" set "ACTION=autostart"
if "%CHOICE%"=="10" set "ACTION=build"
if "%CHOICE%"=="11" set "ACTION=repair"
if "%CHOICE%"=="0" exit /b 0
if not defined ACTION goto menu

:dispatch
if /i "%ACTION%"=="install"    goto do_install
if /i "%ACTION%"=="start"      goto do_start
if /i "%ACTION%"=="stop"       goto do_stop
if /i "%ACTION%"=="status"     goto do_status
if /i "%ACTION%"=="remove"     goto do_remove
if /i "%ACTION%"=="open"       goto do_open
if /i "%ACTION%"=="log"        goto do_log
if /i "%ACTION%"=="shortcut"   goto do_shortcut
if /i "%ACTION%"=="autostart"  goto do_autostart
if /i "%ACTION%"=="build"      goto do_build
if /i "%ACTION%"=="repair"     goto do_repair
echo   [!] Menu tidak dikenal: %ACTION%
goto end

:do_install
"%VENV_PY%" run.py --install-service --no-pause
if errorlevel 1 goto end
echo.
echo   [..] Menjalankan service sekarang
"%VENV_PY%" run.py --start-service --no-pause
echo.
echo   Selesai. Clipper sekarang jalan di latar belakang.
echo   Buka browser: http://localhost:8420
echo   Kelola lewat: services.msc  ^(cari "Clipper"^)
goto end

:do_start
"%VENV_PY%" run.py --start-service --no-pause
goto end

:do_stop
"%VENV_PY%" run.py --stop-service --no-pause
goto end

:do_status
"%VENV_PY%" run.py --service-status --no-pause
echo.
echo   Service Windows (sc query):
sc query Clipper 2>nul | findstr /i "STATE WIN32_EXIT" || echo   (service belum terpasang)
goto end

:do_remove
"%VENV_PY%" run.py --remove-service --no-pause
goto end

:do_open
start "" "http://localhost:8420"
goto end

:do_log
"%VENV_PY%" run.py --service-log --no-pause
goto end

:do_shortcut
"%VENV_PY%" run.py --shortcut --no-pause
goto end

:do_autostart
"%VENV_PY%" run.py --autostart --no-pause
goto end

:do_build
REM build .exe butuh internet sekali (PyInstaller) dan beberapa menit
start "" cmd /c "%~dp0build_service_exe.bat"
goto end

:do_repair
"%VENV_PY%" run.py --repair --no-pause
if errorlevel 1 (
  echo.
  echo   [!] Perbaikan service butuh Administrator.
  echo       Tutup jendela ini, klik kanan repair.bat - Run as administrator.
)
goto end

:end
echo.
if not defined ACTION pause
if defined ACTION if /i not "%ACTION%"=="status" pause
exit /b 0
