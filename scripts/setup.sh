#!/usr/bin/env bash
# Setup Clipper sekali jalan (Linux / macOS).
set -euo pipefail

cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "Python 3.10+ tidak ditemukan. Install dulu: https://www.python.org/downloads/"
  exit 1
fi

"$PY" - <<'EOF'
import sys
if sys.version_info < (3, 10):
    sys.exit("Butuh Python 3.10 atau lebih baru (sekarang %s.%s)" % sys.version_info[:2])
EOF

echo "==> Membuat virtualenv .venv"
"$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Upgrade pip"
pip install --quiet --upgrade pip

echo "==> Install dependensi (bisa beberapa menit)"
pip install --quiet -r requirements.txt

echo "==> Cek ffmpeg"
python - <<'EOF'
import imageio_ffmpeg
print("ffmpeg:", imageio_ffmpeg.get_ffmpeg_exe())
EOF

mkdir -p data models
echo
echo "Selesai! Jalankan:"
echo "  source .venv/bin/activate && python run.py"
