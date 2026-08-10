# Register the "goof.AgentOps" toast identity (AUMID) in HKCU, so session_notify
# toasts show our own name/icon instead of "Windows PowerShell".
# Also points the toast click target at this repo's live board HTML.
# Pair: unregister_toast_app.ps1 (fully reversible, no admin required).
# ASCII-only on purpose (PS 5.1 reads no-BOM UTF-8 .ps1 as ANSI; Japanese
# display name is smuggled in as base64(UTF-8) below).
#
# Prereq: session_notify.py deployed to ~/.claude/hooks (the hook execution
# realm; IconUri must point there, not at the repo).
$ErrorActionPreference = 'Stop'

$appId = 'goof.AgentOps'
# base64(UTF-8) of the display name: "e-jento un'you kiban" in Japanese
$name = [Text.Encoding]::UTF8.GetString(
    [Convert]::FromBase64String('44Ko44O844K444Kn44Oz44OI6YGL55So5Z+655uk'))

$hookDir = Join-Path $env:USERPROFILE '.claude\hooks'
$notify = Join-Path $hookDir 'session_notify.py'
if (-not (Test-Path $notify)) {
    Write-Error ("session_notify.py not deployed: " + $notify + "`nCopy hooks\session_notify.py there first, then re-run.")
}

# 1. make sure the icons exist in the execution realm (ICO referenced by IconUri)
python $notify --make-icons | Out-Null
$ico = Join-Path $hookDir 'toast_icons\agentops.ico'
if (-not (Test-Path $ico)) { Write-Error ("icon generation failed: " + $ico) }

# 2. AUMID registration (HKCU = per-user, no admin)
$key = 'HKCU:\SOFTWARE\Classes\AppUserModelId\' + $appId
New-Item -Path $key -Force | Out-Null
Set-ItemProperty -Path $key -Name 'DisplayName' -Value $name
Set-ItemProperty -Path $key -Name 'IconUri' -Value $ico

# 3. toast click target -> this repo's live board (optional; hook works without it)
$board = Join-Path (Split-Path $PSScriptRoot -Parent) 'reports\live_board.html'
$uri = 'file:///' + ($board -replace '\\', '/')
Set-Content -Path (Join-Path $hookDir 'toast_click.txt') -Value $uri -Encoding Ascii

Write-Host ('registered AUMID  : ' + $key)
Write-Host ('display name      : ' + $name)
Write-Host ('icon              : ' + $ico)
Write-Host ('toast click opens : ' + $uri)
Write-Host 'done. Test: python ~\.claude\hooks\session_notify.py --test-toast waiting_permission'
