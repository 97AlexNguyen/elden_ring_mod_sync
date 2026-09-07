@echo off
setlocal

cd /d "%~dp0"

call conda activate elden_mod
if errorlevel 1 (
    echo Failed to activate the Conda environment: elden_mod
    echo Create it first with: conda create -n elden_mod python=3.10
    exit /b 1
)

python -m PyInstaller --noconfirm --onefile --windowed --name EldenRingModSync mod_sync_launcher.py
if errorlevel 1 (
    echo.
    echo Build failed. If PyInstaller is missing, run:
    echo   conda activate elden_mod
    echo   python -m pip install pyinstaller
    exit /b 1
)

echo.
echo Created: %CD%\dist\EldenRingModSync.exe
endlocal
