# claude-wt protocol handler: resume a Claude Code session in a Windows Terminal tab.
# Invoked by the claude-wt:// URL scheme (register once with register_protocol.ps1).
# ASCII-only on purpose (PS 5.1 reads no-BOM UTF-8 .ps1 as ANSI; see launch.ps1).
#
# Security: the URL carries ONLY a session UUID (strictly validated below).
# No paths or commands ride in from the browser; cwd is reverse-looked-up from
# the local transcript (~/.claude/projects/*/<uuid>.jsonl).
param([string]$Url)

$ErrorActionPreference = 'Stop'

function Show-Err([string]$Msg) {
    Add-Type -AssemblyName PresentationFramework | Out-Null
    [System.Windows.MessageBox]::Show($Msg, 'claude-wt: open session', 'OK', 'Error') | Out-Null
    exit 1
}

# --- 1. validate: claude-wt://resume/<uuid> or claude-wt://new/<workspace-name> ---
# URLs carry only an identifier (uuid / workspace name); the actual path always comes
# from local files (transcript / workspaces.json) = no injection surface from the browser.
$reResume = '^claude-wt://resume/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/?$'
$reNew    = '^claude-wt://new/([^/?#]+?)/?$'
$mode = $null
if ($Url -match $reResume) { $mode = 'resume'; $uuid = $Matches[1].ToLower() }
elseif ($Url -match $reNew) {
    # browser links arrive percent-encoded (UTF-8 Japanese names); Start-Process may pass raw
    $mode = 'new'; $wsName = [System.Uri]::UnescapeDataString($Matches[1])
}
if (-not $mode) {
    Show-Err ("unsupported URL (expected claude-wt://resume/<session-uuid> or claude-wt://new/<workspace>):`n" + $Url)
}

$color = $null
if ($mode -eq 'resume') {
    # --- 2. reverse-lookup the transcript (slug-based paths are ambiguous around '--') ---
    $hits = @(Get-ChildItem "$env:USERPROFILE\.claude\projects\*\$uuid.jsonl" -ErrorAction SilentlyContinue)
    if ($hits.Count -eq 0) { Show-Err ("session transcript not found under ~/.claude/projects:`n" + $uuid) }
    $jsonl = $hits | Sort-Object LastWriteTime -Descending | Select-Object -First 1

    # --- 3. cwd from the transcript's own records ---
    $cwd = $null
    foreach ($line in (Get-Content $jsonl.FullName -TotalCount 50 -Encoding UTF8)) {
        try { $rec = $line | ConvertFrom-Json } catch { continue }
        if ($rec.PSObject.Properties['cwd'] -and $rec.cwd) { $cwd = $rec.cwd; break }
    }
    if (-not $cwd -or -not (Test-Path $cwd)) {
        Show-Err ("could not resolve a valid cwd from the transcript:`n" + $jsonl.FullName)
    }
    $title = 'resume ' + $uuid.Substring(0, 8)
} else {
    # --- 2'. new tab: the workspace name must match a workspaces.json entry (local whitelist).
    #     A fresh session needs an explicit workspace: the cwd decides which ~/.claude project
    #     dir (numbering / memory / board mapping) the session belongs to. ---
    $cfgPath = Join-Path $PSScriptRoot 'workspaces.json'
    if (-not (Test-Path $cfgPath)) { Show-Err ("workspaces.json not found: " + $cfgPath) }
    $cfg = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $ws = @($cfg.workspaces) | Where-Object { $_.name -eq $wsName } | Select-Object -First 1
    if (-not $ws) { Show-Err ("unknown workspace (not in workspaces.json): " + $wsName) }
    if (-not (Test-Path $ws.path)) { Show-Err ("workspace path not found: " + $ws.path) }
    $cwd = $ws.path
    $title = $ws.name
    $color = $ws.color
}

# --- 4. locate the claude executable (same logic as launch.ps1 Find-Claude) ---
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
    Show-Err 'no working claude executable found. Install the standalone CLI (irm https://claude.ai/install.ps1 | iex)'
}
$claude = Find-Claude

# --- 5. launch: join the shared "claude" wt window (same as launch.ps1) ---
$claudeArgs = if ($mode -eq 'resume') { " --resume $uuid" } else { '' }
if ($claude -eq 'claude') { $inner = "claude$claudeArgs" }
else                      { $inner = "& '$claude'$claudeArgs" }
$colorArg = if ($color) { ' --tabColor "' + $color + '"' } else { '' }
$argStr = ('-w claude new-tab -d "{0}" --title "{1}"{2} powershell -NoExit -NoLogo -ExecutionPolicy Bypass -Command "{3}"' `
    -f $cwd, $title, $colorArg, $inner)
Start-Process -FilePath 'wt' -ArgumentList $argStr
