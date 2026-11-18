# One-time setup so your phone can reach Dropzone running in WSL.
# Run by double-clicking Setup-Dropzone.bat (it asks for admin once).

$ErrorActionPreference = 'Continue'
Write-Host "`n== Dropzone one-time setup ==`n" -ForegroundColor Cyan

# 1. WSL mirrored networking (WSL shares the PC's real Wi-Fi address)
$cfg = Join-Path $env:USERPROFILE '.wslconfig'
$text = if (Test-Path $cfg) { Get-Content $cfg -Raw } else { '' }
if ($text -match 'networkingMode\s*=\s*mirrored') {
    Write-Host "[ok] .wslconfig already uses mirrored networking"
} else {
    if (Test-Path $cfg) { Copy-Item $cfg "$cfg.bak" -Force; Write-Host "[..] backed up existing .wslconfig to .wslconfig.bak" }
    if ($text -match '\[wsl2\]') {
        $text = $text -replace '\[wsl2\]', "[wsl2]`r`nnetworkingMode=mirrored"
    } else {
        $text = "[wsl2]`r`nnetworkingMode=mirrored`r`n" + $text
    }
    Set-Content -Path $cfg -Value $text -Encoding ascii
    Write-Host "[ok] enabled mirrored networking in $cfg"
}

# 2. Firewall: allow port 8765 in (Windows firewall + WSL Hyper-V firewall)
Remove-NetFirewallRule -DisplayName 'Dropzone' -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName 'Dropzone' -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow -Profile Any | Out-Null
Write-Host "[ok] Windows firewall: port 8765 allowed"
try {
    Remove-NetFirewallHyperVRule -Name 'Dropzone' -ErrorAction SilentlyContinue
    New-NetFirewallHyperVRule -Name 'Dropzone' -DisplayName 'Dropzone' -Direction Inbound `
        -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol TCP -LocalPorts 8765 -ErrorAction Stop | Out-Null
    Write-Host "[ok] WSL (Hyper-V) firewall: port 8765 allowed"
} catch {
    Write-Host "[--] Hyper-V firewall rule skipped (not needed on this Windows version)"
}

# 3. Restart WSL so the new network mode applies
Write-Host "`nRestarting WSL (close any open Kali windows)..."
wsl.exe --shutdown
Write-Host "`nAll done! Open Kali and type:  dropzone`n" -ForegroundColor Green
Read-Host "Press Enter to close"
