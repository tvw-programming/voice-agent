#!/usr/bin/env bash
set -euo pipefail
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
[ -f .env ] || { cp .env.example .env; echo "Created .env - fill in your keys."; }
if grep -q '^STAFF_PIN_SECRET=$' .env; then
  secret=$(python -c "import secrets; print(secrets.token_hex(32))")
  sed -i.bak "s/^STAFF_PIN_SECRET=$/STAFF_PIN_SECRET=$secret/" .env && rm -f .env.bak
  echo "Generated STAFF_PIN_SECRET in .env"
fi
if command -v npm >/dev/null 2>&1; then
  (cd student_api && npm install --no-fund --no-audit)
else
  echo "Node.js not found: install it (macOS: brew install node) to run the student API in student_api/"
fi
echo "Kokoro Hindi voices need espeak-ng: sudo apt install espeak-ng  (macOS: brew install espeak-ng)"
