$ErrorActionPreference = 'Stop'

python -m pip install --upgrade pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed --name EldenRingModSync mod_sync_launcher.py

Write-Host "Created dist\EldenRingModSync.exe"
