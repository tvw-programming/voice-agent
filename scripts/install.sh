#!/usr/bin/env bash
set -euo pipefail
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
[ -f .env ] || { cp .env.example .env; echo "Created .env - fill in your keys."; }
echo "Kokoro Hindi voices need espeak-ng: sudo apt install espeak-ng  (macOS: brew install espeak-ng)"
