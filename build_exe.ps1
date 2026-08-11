# Builds the launcher into a single self-contained .exe for teammates who have
# no Python installed.  Output: dist\EldenRingModSync.exe
#
# Requires:  python -m pip install pyinstaller

Set-Location $PSScriptRoot

# PyInstaller logs progress to stderr, which Windows PowerShell turns into a
# terminating error under "Stop".  Exit code is the real signal here.
$ErrorActionPreference = "Continue"

python -m PyInstaller `
    --noconfirm `
    --onefile `
    --windowed `
    --name EldenRingModSync `
    mod_sync_launcher.py

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$exe = Join-Path $PSScriptRoot "dist\EldenRingModSync.exe"
if (-not (Test-Path $exe)) {
    throw "Build reported success but $exe is missing"
}
"Built: $exe ({0:N1} MB)" -f ((Get-Item $exe).Length / 1MB)
