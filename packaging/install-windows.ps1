<#
  HomeBase installer (Windows, no admin required).

  - copies HomeBase.exe into %LOCALAPPDATA%\HomeBase
  - picks a FREE port and PINS it for the life of the install (the Brave homepage URL
    never changes afterwards — §10)
  - writes the owner-only config via "HomeBase.exe --install --port <port>"
  - registers auto-start on logon (Task Scheduler, runs hidden in the background)
  - drops a desktop shortcut that opens the page
  - prints the exact URL + the Brave homepage steps

  Run:  right-click -> Run with PowerShell   (or:  powershell -ExecutionPolicy Bypass -File install-windows.ps1)
#>
$ErrorActionPreference = "Stop"

$AppName   = "HomeBase"
$InstallDir = Join-Path $env:LOCALAPPDATA $AppName
$ExeSource = Join-Path $PSScriptRoot "HomeBase.exe"
$ExeDest   = Join-Path $InstallDir "HomeBase.exe"

if (-not (Test-Path $ExeSource)) {
    Write-Error "HomeBase.exe not found next to this script. Put them in the same folder."
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

# Lock the install dir to the current user (strip inheritance) — the interest-graph appliance.
icacls $InstallDir /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F" | Out-Null

Copy-Item -Force $ExeSource $ExeDest

# --- pick a free port (pinned for this install) -------------------------------------
function Test-PortFree([int]$p) {
    try {
        $l = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $p)
        $l.Start(); $l.Stop(); return $true
    } catch { return $false }
}
$port = 0
foreach ($p in 8777..8820) { if (Test-PortFree $p) { $port = $p; break } }
if ($port -eq 0) { Write-Error "No free port in 8777-8820. Close some apps and retry." }

# --- write config + pin the port, capture the fixed URL -----------------------------
$Url = (& $ExeDest --install --port $port | Select-Object -First 1).Trim()
Write-Host "Configured $AppName on $Url" -ForegroundColor Green

# --- auto-start on logon (hidden background) ----------------------------------------
$hiddenLauncher = "powershell.exe -WindowStyle Hidden -Command `"Start-Process -WindowStyle Hidden '$ExeDest'`""
schtasks /Create /TN "$AppName" /TR $hiddenLauncher /SC ONLOGON /RL LIMITED /F | Out-Null

# start it now too
Start-Process -WindowStyle Hidden $ExeDest

# --- desktop shortcut to open the page ----------------------------------------------
$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = Join-Path $desktop "Open HomeBase.url"
"[InternetShortcut]`r`nURL=$Url" | Set-Content -Encoding ASCII $lnk

Write-Host ""
Write-Host "==================== HomeBase is installed ====================" -ForegroundColor Cyan
Write-Host "Your private homepage:  $Url"
Write-Host ""
Write-Host "Set it as your Brave homepage:"
Write-Host "  1) Brave -> Settings (brave://settings/)"
Write-Host "  2) 'Get started' -> On startup -> Open a specific page or set of pages"
Write-Host "  3) Add a new page ->  $Url"
Write-Host "  4) 'Appearance' -> turn on 'Show home button' -> set it to  $Url"
Write-Host ""
Write-Host "HomeBase will start automatically each time you log in."
Write-Host "To remove it later, run uninstall-windows.ps1."
Write-Host "===============================================================" -ForegroundColor Cyan
