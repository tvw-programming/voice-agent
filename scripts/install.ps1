# Windows: create a virtual env and install dependencies.
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "Created .env - fill in your keys." }
if (Select-String -Path .env -Pattern '^STAFF_PIN_SECRET=$' -Quiet) {
    $secret = python -c "import secrets; print(secrets.token_hex(32))"
    (Get-Content .env) -replace '^STAFF_PIN_SECRET=$', "STAFF_PIN_SECRET=$secret" | Set-Content .env
    Write-Host "Generated STAFF_PIN_SECRET in .env"
}
if (Get-Command npm -ErrorAction SilentlyContinue) {
    Push-Location student_api; npm install --no-fund --no-audit; Pop-Location
} else {
    Write-Host "Node.js not found: install it from https://nodejs.org to run the student API in student_api\"
}
Write-Host "Kokoro Hindi voices need espeak-ng: https://github.com/espeak-ng/espeak-ng/releases (install the .msi)"
