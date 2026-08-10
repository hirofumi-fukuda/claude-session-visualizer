# claude-wt launcher: open Windows Terminal tabs running the claude CLI per workspace.
# ASCII-only on purpose (PS 5.1 reads no-BOM UTF-8 .ps1 as ANSI; Japanese lives in workspaces.json/README).
#
# Usage:
#   .\launch.ps1              # fresh session per tab (type your kickoff message)
#   .\launch.ps1 -Continue    # resume the latest session of each workspace dir (claude -c)
#   .\launch.ps1 -Only FBiz   # substring filter on workspace name (comma-separated ok)
#   .\launch.ps1 -DryRun      # print the wt command line instead of launching
param(
    [switch]$Continue,
    [switch]$DryRun,
    [string]$Only = "",
    [string]$ClaudeCmd = ""
)
$ErrorActionPreference = 'Stop'

$cfgPath = Join-Path $PSScriptRoot 'workspaces.json'
$cfg = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json

# A candidate can exist but be broken - e.g. a Volta/npm shim whose target exe vanished
# after a failed self-update (hit for real on 2026-08-10). Probe with --version first.
function Test-Claude([string]$Cmd) {
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try {
        if ($Cmd -eq 'claude') { $null = claude --version 2>$null }
        else                   { $null = & $Cmd --version 2>$null }
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
    finally { $ErrorActionPreference = $old }
}
function Find-Claude {
    param($Override, $FromConfig)
    if ($Override) { return $Override }
    if ($FromConfig) { return $FromConfig }
    $cands = @()
    if (Get-Command claude -ErrorAction SilentlyContinue) { $cands += 'claude' }
    $native = Join-Path $env:USERPROFILE '.local\bin\claude.exe'
    if (Test-Path $native) { $cands += $native }
    foreach ($root in @("$env:USERPROFILE\.cursor\extensions", "$env:USERPROFILE\.vscode\extensions")) {
        if (-not (Test-Path $root)) { continue }
        $hit = Get-ChildItem $root -Directory -Filter 'anthropic.claude-code-*' |
            Sort-Object LastWriteTime -Descending |
            ForEach-Object { Join-Path $_.FullName 'resources\native-binary\claude.exe' } |
            Where-Object { Test-Path $_ } |
            Select-Object -First 1
        if ($hit) { $cands += $hit }
    }
    foreach ($c in $cands) { if (Test-Claude $c) { return $c } }
    throw 'no working claude executable found. Install the standalone CLI (irm https://claude.ai/install.ps1 | iex) or set claude_cmd in workspaces.json'
}

$claude = Find-Claude -Override $ClaudeCmd -FromConfig $cfg.claude_cmd

$selected = @($cfg.workspaces)
if ($Only) {
    $needles = $Only.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    $selected = @($selected | Where-Object {
        $ws = $_
        ($needles | Where-Object { $ws.name -like ('*' + $_ + '*') }).Count -gt 0
    })
}
if ($selected.Count -eq 0) { throw "no workspace matched -Only '$Only'" }

$claudeArgs = ''
if ($Continue) { $claudeArgs = ' -c' }

$tabs = @()
foreach ($ws in $selected) {
    if (-not (Test-Path $ws.path)) {
        Write-Warning ("skip {0}: path not found ({1})" -f $ws.name, $ws.path)
        continue
    }
    $extra = ''
    if ($ws.PSObject.Properties['add_dirs'] -and $ws.add_dirs) {
        foreach ($d in $ws.add_dirs) { $extra += " --add-dir '$d'" }
    }
    if ($claude -eq 'claude') { $inner = "claude$claudeArgs$extra" }
    else                      { $inner = "& '$claude'$claudeArgs$extra" }
    $tabs += ('new-tab -d "{0}" --title "{1}" --tabColor "{2}" powershell -NoExit -NoLogo -ExecutionPolicy Bypass -Command "{3}"' `
        -f $ws.path, $ws.name, $ws.color, $inner)
}
if ($tabs.Count -eq 0) { throw 'nothing to launch (all workspace paths missing?)' }

$argStr = '-w claude ' + ($tabs -join ' ; ')

Write-Host ("claude : {0}" -f $claude)
Write-Host ("tabs   : {0}" -f (($selected | ForEach-Object { $_.name }) -join ', '))
if ($Continue) { Write-Host 'mode   : continue latest session per dir (-c)' }
else           { Write-Host 'mode   : fresh session (type your kickoff message per tab)' }

if ($DryRun) {
    Write-Host '--- dry run: command that would be executed ---'
    Write-Host ("wt {0}" -f $argStr)
    exit 0
}
Start-Process -FilePath 'wt' -ArgumentList $argStr
Write-Host 'launched. (re-running adds tabs to the same "claude" window)'
