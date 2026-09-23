# Windows: create a virtual env and install dependencies.
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "Created .env - fill in your keys." }
Write-Host "Kokoro Hindi voices need espeak-ng: https://github.com/espeak-ng/espeak-ng/releases (install the .msi)"
