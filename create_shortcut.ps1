# Create Desktop shortcut for Largo Auto Config (purple icon)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$exe = Join-Path $root "dist\LargoAutoConfig.exe"
$py = Join-Path $root "app\main.py"
$ico = Join-Path $root "assets\app.ico"
$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "Largo Auto Config.lnk"

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)

if (Test-Path $exe) {
    $lnk.TargetPath = $exe
    $lnk.WorkingDirectory = (Join-Path $root "dist")
} else {
    $python = (Get-Command python -ErrorAction Stop).Source
    $lnk.TargetPath = $python
    $lnk.Arguments = "`"$py`""
    $lnk.WorkingDirectory = $root
}

if (Test-Path $ico) {
    $lnk.IconLocation = "$ico,0"
}
$lnk.Description = "Largo Auto Config"
$lnk.Save()

Write-Host "Shortcut: $lnkPath"
