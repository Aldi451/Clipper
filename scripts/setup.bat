@echo off
REM Setup Clipper sekali jalan (Windows)
setlocal
cd /d "%~dp0.."

where py >nul 2>nul
if errorlevel 1 (
  echo Python tidak ditemukan. Install dari https://www.python.org/downloads/ lalu jalankan lagi.
  exit /b 1
)

echo ==^> Membuat virtualenv .venv
py -m venv .venv
call .venv\Scripts\activate.bat

echo ==^> Upgrade pip
python -m pip install --quiet --upgrade pip

echo ==^> Install dependensi (bisa beberapa menit)
python -m pip install --quiet -r requirements.txt

if not exist data mkdir data
if not exist models mkdir models

echo.
echo Selesai! Jalankan:
echo   .venv\Scripts\activate ^&^& python run.py
endlocal
