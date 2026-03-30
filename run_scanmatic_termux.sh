#!/data/data/com.termux/files/usr/bin/bash
set -e

echo "== Actualizando Termux =="
pkg update -y
pkg upgrade -y

echo "== Instalando base =="
pkg install -y python git clang libffi openssl

echo "== Preparando proyecto =="
PROJECT_DIR="$HOME/ScanMatic"
REPO_URL="PEGA_AQUI_TU_REPO_GITHUB"

if [ ! -d "$PROJECT_DIR" ]; then
  git clone "$REPO_URL" "$PROJECT_DIR"
fi

cd "$PROJECT_DIR"

python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt

echo "== Iniciando servidor =="
echo "Abre: http://127.0.0.1:8000"
echo "En red local: http://IP_DE_TU_ANDROID:8000"
uvicorn app.main:app --host 0.0.0.0 --port 8000
