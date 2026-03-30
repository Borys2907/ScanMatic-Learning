#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
LOCAL_IP=$(python - <<'PY'
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
ip = "127.0.0.1"
try:
    s.connect(("8.8.8.8", 80))
    ip = s.getsockname()[0]
except Exception:
    pass
finally:
    s.close()
print(ip)
PY
)
echo "Iniciando servidor ScanMatic AutoTech Learning..."
echo "Instructor local: http://127.0.0.1:8000/instructor"
echo "Estudiante en esta PC: http://127.0.0.1:8000/student"
echo "Estudiante en la red: http://${LOCAL_IP}:8000/student"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
