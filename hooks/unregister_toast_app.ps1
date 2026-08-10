# Undo register_toast_app.ps1: remove the goof.AgentOps AUMID (HKCU) and the
# toast click target. Toasts fall back to the stock PowerShell identity.
# Generated icons under ~/.claude/hooks/toast_icons are left in place (harmless).
$ErrorActionPreference = 'Stop'

$key = 'HKCU:\SOFTWARE\Classes\AppUserModelId\goof.AgentOps'
if (Test-Path $key) { Remove-Item -Path $key -Recurse -Force; Write-Host ('removed ' + $key) }
else { Write-Host ('not registered: ' + $key) }

$click = Join-Path $env:USERPROFILE '.claude\hooks\toast_click.txt'
if (Test-Path $click) { Remove-Item -Path $click -Force; Write-Host ('removed ' + $click) }
Write-Host 'done.'
