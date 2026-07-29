# session_notify.ps1 - Claude Code Stop/Notification hook -> Windows toast
# Shows which session is waiting for input. ASCII-only script (Japanese comes from data).
# Requires Windows PowerShell 5.1 (WinRT toast). Invoked as:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "...\session_notify.ps1"

$ErrorActionPreference = 'SilentlyContinue'

# ---- read hook input JSON from stdin ----
$raw = [Console]::In.ReadToEnd()
if (-not $raw) { exit 0 }
try { $hook = $raw | ConvertFrom-Json } catch { exit 0 }

$eventName = if ($hook.hook_event_name) { [string]$hook.hook_event_name } else { 'Stop' }
$transcript = [string]$hook.transcript_path
$cwd = [string]$hook.cwd
$msg = [string]$hook.message   # Notification event only

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
if (-not $title) {
    $title = if ($cwd) { Split-Path $cwd -Leaf } else { 'Claude Code' }
}

# ---- compose toast text ----
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
