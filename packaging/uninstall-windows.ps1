<#
  HomeBase uninstaller. Stops the background process, removes the auto-start task, and
  deletes the install dir (config + cache + the interest-graph). Leaves no trace.
  Run:  powershell -ExecutionPolicy Bypass -File uninstall-windows.ps1
#>
$ErrorActionPreference = "SilentlyContinue"
$AppName = "HomeBase"
$InstallDir = Join-Path $env:LOCALAPPDATA $AppName

schtasks /Delete /TN "$AppName" /F | Out-Null
Get-Process HomeBase | Stop-Process -Force
Remove-Item -Recurse -Force $InstallDir
Remove-Item -Force (Join-Path ([Environment]::GetFolderPath("Desktop")) "Open HomeBase.url")
Write-Host "HomeBase removed." -ForegroundColor Green
