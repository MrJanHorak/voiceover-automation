# Share the local studio for testing through a free Cloudflare Quick Tunnel (no account, no domain).
# The address is random, changes on every start, and works only while this window and your PC are running.
# Quick Tunnels are for testing only; use the deploy/ setup for real customers.
#
# One-time: winget install --id Cloudflare.cloudflared
# Then, from the project folder:  powershell -ExecutionPolicy Bypass -File scripts\share-studio.ps1
param([switch]$Cpu)

if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    Write-Host "cloudflared is not installed. Run: winget install --id Cloudflare.cloudflared, then open a new window." -ForegroundColor Yellow
    exit 1
}

$python = if ($Cpu) { ".\.tools\directed-env\Scripts\python.exe" } else { ".\.tools\cuda-benchmark-env\Scripts\python.exe" }
$env:VOICEOVER_DEVICE = if ($Cpu) { "cpu" } else { "cuda" }
$env:VOICEOVER_DIRECTED_DEVICE = if ($Cpu) { "cpu" } else { "cuda" }
# Accept the random *.trycloudflare.com address, send session cookies over HTTPS only,
# and take visitors' addresses from the tunnel so sign-in limits apply per person.
$env:VOICEOVER_ALLOWED_HOSTS = "127.0.0.1,localhost,*.trycloudflare.com"
$env:VOICEOVER_SECURE_COOKIES = "1"
$env:VOICEOVER_INBOX = "off"

Write-Host "Starting the studio..." -ForegroundColor Cyan
$studio = Start-Process -FilePath $python -PassThru -NoNewWindow -ArgumentList @(
    "-m", "uvicorn", "studio.app:app", "--host", "127.0.0.1", "--port", "8000",
    "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1", "--no-access-log")
Start-Sleep -Seconds 8

Write-Host "Opening the tunnel. Share the https://....trycloudflare.com address printed below." -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop sharing; the studio stops too." -ForegroundColor Cyan
try {
    cloudflared tunnel --url http://localhost:8000
} finally {
    if (-not $studio.HasExited) { Stop-Process -Id $studio.Id }
}
