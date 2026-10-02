# Build Largo Auto Config into a single-file EXE
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -m pip install -q -r requirements.txt pyinstaller

$assets = "assets;assets"
$defaults = "defaults;defaults"

python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --windowed `
  --name "LargoAutoConfig" `
  --icon "assets\app.ico" `
  --paths "app" `
  --add-data $assets `
  --add-data $defaults `
  --hidden-import "pystray._win32" `
  --hidden-import "PIL._tkinter_finder" `
  --collect-all "windnd" `
  "app\main.py"

Write-Host ""
Write-Host "OK: dist\LargoAutoConfig.exe"
