# Remove the claude-wt:// URL scheme registration (HKCU).
try {
    Remove-Item -Path 'HKCU:\Software\Classes\claude-wt' -Recurse -Force -ErrorAction Stop
    Write-Host 'unregistered: claude-wt://'
} catch [System.Management.Automation.ItemNotFoundException] {
    Write-Host 'claude-wt:// was not registered (nothing to do)'
}
