# Register the claude-wt:// URL scheme for the current user (HKCU only, no admin).
# The live board's "open" button emits claude-wt://resume/<uuid> links; this wires
# them to open_session.ps1. Undo anytime with unregister_protocol.ps1.
$ErrorActionPreference = 'Stop'

$handler = Join-Path $PSScriptRoot 'open_session.ps1'
if (-not (Test-Path $handler)) { throw "handler not found: $handler" }

$root = 'HKCU:\Software\Classes\claude-wt'
New-Item -Path "$root\shell\open\command" -Force | Out-Null
Set-ItemProperty -Path $root -Name '(default)' -Value 'URL:claude-wt session resume'
Set-ItemProperty -Path $root -Name 'URL Protocol' -Value ''
$cmd = 'powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" "%1"' -f $handler
Set-ItemProperty -Path "$root\shell\open\command" -Name '(default)' -Value $cmd

Write-Host "registered: claude-wt:// -> $handler"
Write-Host 'first click in a browser will ask for confirmation once (check "always allow").'
