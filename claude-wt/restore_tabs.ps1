# claude-wt restore: reopen the last known set of live sessions as Windows Terminal tabs.
# "Last known set" = reports\tabs_snapshot.json, written by the live_board.py watcher on
# every tick (only when at least one session is alive), so it survives shutdown/reboot.
# ASCII-only on purpose (PS 5.1 reads no-BOM UTF-8 .ps1 as ANSI; see launch.ps1).
#
# Usage:
#   .\restore_tabs.ps1            # one tab per snapshot session (claude --resume <sid>)
#   .\restore_tabs.ps1 -Only GEM  # substring filter on project/title (comma-separated ok)
#   .\restore_tabs.ps1 -DryRun    # print the wt command line instead of launching
param(
    [switch]$DryRun,
    [string]$Only = ""
)
$ErrorActionPreference = 'Stop'

$snap = Join-Path (Split-Path $PSScriptRoot -Parent) 'reports\tabs_snapshot.json'
if (-not (Test-Path $snap)) { throw "snapshot not found: $snap (start live_board.bat first)" }
$data = Get-Content $snap -Raw -Encoding UTF8 | ConvertFrom-Json
$age = (Get-Date) - [datetime]$data.saved_at
Write-Host ("snapshot : {0}  ({1:0.#}h ago, {2} sessions)" -f $data.saved_at, $age.TotalHours, @($data.sessions).Count)

$sel = @($data.sessions)
if ($Only) {
    $needles = $Only.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    $sel = @($sel | Where-Object {
        $s = $_
        ($needles | Where-Object { ($s.project + ' ' + $s.title) -like ('*' + $_ + '*') }).Count -gt 0
    })
}
if ($sel.Count -eq 0) { throw "no snapshot session matched -Only '$Only'" }

# --- locate claude (same logic as launch.ps1 Find-Claude) ---
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
    # honor claude_cmd pinned in workspaces.json (same file the launcher uses)
    $cfgPath = Join-Path $PSScriptRoot 'workspaces.json'
    if (Test-Path $cfgPath) {
        try {
            $cfg = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($cfg.claude_cmd) { return $cfg.claude_cmd }
        } catch {}
    }
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
    throw 'no working claude executable found. Install the standalone CLI (irm https://claude.ai/install.ps1 | iex)'
}
$claude = Find-Claude

# optional dedicated WT profile (tab icon etc.); empty/absent = default profile
$pArg = ''
try {
    $cfgWt = Get-Content (Join-Path $PSScriptRoot 'workspaces.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($cfgWt.wt_profile) { $pArg = ' -p "' + $cfgWt.wt_profile + '"' }
} catch {}

# --- skip sessions that are alive right now (double --resume on one sid = two writers
#     on the same jsonl). `claude agents --json` is the authoritative live-session list. ---
$liveSids = @()
try {
    if ($claude -eq 'claude') { $json = claude agents --json 2>$null }
    else                      { $json = & $claude agents --json 2>$null }
    $liveSids = @(($json | ConvertFrom-Json) | ForEach-Object { $_.sessionId } | Where-Object { $_ })
} catch {
    Write-Warning 'could not query live sessions (claude agents --json failed); no skip guard'
}

$tabs = @()
foreach ($s in $sel) {
    if ($liveSids -contains $s.sid) {
        Write-Host ("skip (already running) : {0} {1}" -f $s.project, $s.s_no)
        continue
    }
    if (-not (Test-Path $s.cwd)) {
        Write-Warning ("skip {0} {1}: cwd not found ({2})" -f $s.project, $s.s_no, $s.cwd)
        continue
    }
    $title = ('{0} {1}' -f $s.project, $s.s_no).Trim()
    if ($claude -eq 'claude') { $inner = "claude --resume $($s.sid)" }
    else                      { $inner = "& '$claude' --resume $($s.sid)" }
    $tabs += ('new-tab{3} -d "{0}" --title "{1}" powershell -NoExit -NoLogo -ExecutionPolicy Bypass -Command "{2}"' `
        -f $s.cwd, $title, $inner, $pArg)
    Write-Host ("restore : {0}  ({1})" -f $title, $s.sid.Substring(0, 8))
}
if ($tabs.Count -eq 0) { Write-Host 'nothing to open (everything is already running, or filtered out)'; exit 0 }

$argStr = '-w claude ' + ($tabs -join ' ; ')
if ($DryRun) {
    Write-Host '--- dry run: command that would be executed ---'
    Write-Host ("wt {0}" -f $argStr)
    exit 0
}
Start-Process -FilePath 'wt' -ArgumentList $argStr
Write-Host ("restored {0} tab(s) into the ""claude"" window." -f $tabs.Count)
