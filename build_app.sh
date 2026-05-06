#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if ! python -c 'import PyInstaller' >/dev/null 2>&1; then
  echo "PyInstaller is not installed. Installing from requirements.txt..."
  python -m pip install -r requirements.txt
fi

python -m PyInstaller --clean --noconfirm koja_app.spec

echo
printf 'Built app at: %s\n' "$(pwd)/dist/KojaCore"
echo "Run it with: ./dist/KojaCore/KojaCore"
