# session_notify.ps1 - Claude Code hook: session state JSON + Windows toast
# Events wired in settings.json:
#   UserPromptSubmit -> state only ("working")
#   Stop             -> state ("waiting_input") + toast
#   Notification     -> state ("waiting_permission" / "waiting_input") + toast
# State files (live board data source, read by live_board.py):
#   %USERPROFILE%\.claude\hooks\session_state\<session_id>.json
# ASCII-only script (Japanese comes from data). Windows PowerShell 5.1 (WinRT toast).
# Invoked as:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "...\session_notify.ps1"

$ErrorActionPreference = 'SilentlyContinue'

# ---- read hook input JSON from stdin (Claude Code sends UTF-8) ----
try { [Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }
$raw = [Console]::In.ReadToEnd()
if (-not $raw) { exit 0 }
try { $hook = $raw | ConvertFrom-Json } catch { exit 0 }

$eventName = if ($hook.hook_event_name) { [string]$hook.hook_event_name } else { 'Stop' }
$sessionId = [string]$hook.session_id
$transcript = [string]$hook.transcript_path
$cwd = [string]$hook.cwd
$msg = [string]$hook.message     # Notification event only
$prompt = [string]$hook.prompt   # UserPromptSubmit event only

# ---- resolve session title: first user message in transcript ----
$title = ''
if ($transcript -and (Test-Path $transcript)) {
    try {
        $reader = New-Object System.IO.StreamReader($transcript, [System.Text.Encoding]::UTF8)
        $count = 0
        while (-not $reader.EndOfStream -and $count -lt 80 -and -not $title) {
            $line = $reader.ReadLine(); $count++
            if ($line -notmatch '"type"\s*:\s*"user"') { continue }
            try {
                $obj = $line | ConvertFrom-Json
                $content = $obj.message.content
                $text = ''
                if ($content -is [string]) { $text = $content }
                elseif ($content) {
                    foreach ($c in $content) { if ($c.type -eq 'text') { $text = [string]$c.text; break } }
                }
                $text = ($text -replace '\s+', ' ').Trim()
                # skip system-ish / command wrapper messages
                if ($text -and $text -notmatch '^<' -and $text.Length -gt 1) {
                    if ($text.Length -gt 60) { $text = $text.Substring(0, 60) + '...' }
                    $title = $text
                }
            } catch { }
        }
        $reader.Close()
    } catch { }
}
if (-not $title -and $prompt) {
    $p = ($prompt -replace '\s+', ' ').Trim()
    if ($p.Length -gt 60) { $p = $p.Substring(0, 60) + '...' }
    $title = $p
}
if (-not $title) {
    $title = if ($cwd) { Split-Path $cwd -Leaf } else { 'Claude Code' }
}

# ---- write session state JSON (live board data source) ----
if ($sessionId) {
    $state = 'waiting_input'
    if ($eventName -eq 'UserPromptSubmit') { $state = 'working' }
    elseif ($eventName -eq 'Notification') {
        $state = if ($msg -match 'permission') { 'waiting_permission' } else { 'waiting_input' }
    }
    $stateDir = Join-Path $env:USERPROFILE '.claude\hooks\session_state'
    try {
        New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
        $lastPrompt = ''
        if ($prompt) {
            $lastPrompt = ($prompt -replace '\s+', ' ').Trim()
            if ($lastPrompt.Length -gt 120) { $lastPrompt = $lastPrompt.Substring(0, 120) + '...' }
        }
        $obj = [ordered]@{
            session_id      = $sessionId
            state           = $state
            event           = $eventName
            ts              = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
            cwd             = $cwd
            transcript_path = $transcript
            title           = $title
            message         = $msg
            last_prompt     = $lastPrompt
        }
        $json = $obj | ConvertTo-Json -Compress
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText((Join-Path $stateDir "$sessionId.json"), $json, $utf8NoBom)
        # prune state files older than 7 days (closed sessions)
        Get-ChildItem $stateDir -Filter *.json |
            Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-7) } |
            Remove-Item -Force -Confirm:$false
    } catch { }
}

# ---- toast only for Stop / Notification (UserPromptSubmit is state-only) ----
if ($eventName -eq 'UserPromptSubmit') { exit 0 }

if ($eventName -eq 'Notification') {
    $head = 'Claude Code: needs attention'
    $body = if ($msg) { "$title`n$msg" } else { $title }
} else {
    $head = 'Claude Code: waiting for input'
    $body = $title
}

# ---- show toast (WinRT, PS 5.1) ----
try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
    $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $texts = $xml.GetElementsByTagName('text')
    $texts.Item(0).AppendChild($xml.CreateTextNode($head)) | Out-Null
    $texts.Item(1).AppendChild($xml.CreateTextNode($body)) | Out-Null
    $toast = New-Object Windows.UI.Notifications.ToastNotification($xml)
    $appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
} catch { }

exit 0
