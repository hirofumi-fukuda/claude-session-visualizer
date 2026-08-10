# clip2file: save the clipboard image to .\clips\ and put the saved file path
# back on the clipboard as text.
# Flow: Win+Shift+S (screenshot) -> run clip2file.cmd -> Ctrl+V the path into the claude prompt.
# Why: native-Windows claude CLI has a known bug where raw clipboard images
# silently fail to paste (Alt+V); a file path always works.
# ASCII-only on purpose (PS 5.1 no-BOM encoding). Requires STA (the .cmd wrapper passes -STA).
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms | Out-Null
Add-Type -AssemblyName System.Drawing | Out-Null

$clipDir = Join-Path $PSScriptRoot 'clips'
New-Item -ItemType Directory -Force -Path $clipDir | Out-Null

# prune saved clips older than 14 days
Get-ChildItem $clipDir -Filter 'clip_*.png' -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-14) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

$img = [System.Windows.Forms.Clipboard]::GetImage()
if ($img) {
    $path = Join-Path $clipDir ('clip_{0:yyyyMMdd_HHmmss}.png' -f (Get-Date))
    $img.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    Set-Clipboard -Value $path
    Write-Host ("saved   : {0}" -f $path)
    Write-Host 'clipboard: path copied - paste it into the claude prompt (Ctrl+V)'
    exit 0
}

# fallback: an image FILE copied in Explorer -> just hand over its path
$files = [System.Windows.Forms.Clipboard]::GetFileDropList()
if ($files -and $files.Count -gt 0) {
    Set-Clipboard -Value $files[0]
    Write-Host ("clipboard: file path copied - {0}" -f $files[0])
    exit 0
}

Write-Host 'no image on the clipboard (take a screenshot first: Win+Shift+S)'
exit 1
