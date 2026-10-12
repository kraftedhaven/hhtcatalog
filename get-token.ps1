# Get Fresh Docker Personal Access Token - Windows PowerShell

Write-Host ""
Write-Host "╔════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║  Docker Sandboxes API Token Setup                          ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

Write-Host "Your current token status:" -ForegroundColor Yellow
if ($env:DOCKER_SANDBOXES_TOKEN) {
    $tokenPreview = $env:DOCKER_SANDBOXES_TOKEN.Substring(0, [Math]::Min(20, $env:DOCKER_SANDBOXES_TOKEN.Length)) + "..."
    Write-Host "  Token is SET: $tokenPreview" -ForegroundColor Yellow
    Write-Host "  (Showing first 20 characters only)" -ForegroundColor Gray
} else {
    Write-Host "  Token is NOT SET" -ForegroundColor Red
}

Write-Host ""
Write-Host "Steps to get a valid token:" -ForegroundColor Green
Write-Host ""

Write-Host "1. Open Docker Dashboard" -ForegroundColor Cyan
Write-Host "   Run this command:" -ForegroundColor Gray
Write-Host "   " -NoNewline
Write-Host "explorer.exe" -ForegroundColor Green
Write-Host ""
Write-Host "   OR open Docker Desktop app from Start menu" -ForegroundColor Gray
Write-Host ""

Write-Host "2. Go to Settings → Security" -ForegroundColor Cyan
Write-Host "   Open in browser: " -ForegroundColor Gray -NoNewline
Write-Host "https://app.docker.com/settings/security" -ForegroundColor Green
Write-Host ""

Write-Host "3. Create a Personal Access Token (PAT)" -ForegroundColor Cyan
Write-Host "   - Click 'New Access Token' or 'Generate Token'" -ForegroundColor Gray
Write-Host "   - Description: 'HHT Sandbox Worker'" -ForegroundColor Gray
Write-Host "   - Scopes: Check 'Sandboxes' or 'sandboxesLifecycle'" -ForegroundColor Gray
Write-Host "   - Click 'Generate' and COPY the full token immediately" -ForegroundColor Gray
Write-Host "   (You cannot see it again after closing)" -ForegroundColor Red
Write-Host ""

Write-Host "4. Set the token in this PowerShell window:" -ForegroundColor Cyan
Write-Host ""
Write-Host "   `$env:DOCKER_SANDBOXES_TOKEN = " -NoNewline
Write-Host '"PASTE_YOUR_FULL_TOKEN_HERE"' -ForegroundColor Yellow
Write-Host ""

Write-Host "5. Verify it's set:" -ForegroundColor Cyan
Write-Host "   " -NoNewline
Write-Host "`$env:DOCKER_SANDBOXES_TOKEN | Select-Object -First 20" -ForegroundColor Green
Write-Host ""

Write-Host "6. Test the token (before renewal):" -ForegroundColor Cyan
Write-Host ""
Write-Host "   " -NoNewline
Write-Host "Invoke-RestMethod -Uri 'https://connect.docker.com/sandboxes/v1/sandboxes' " `
    -NoNewline -ForegroundColor Green
Write-Host ""
Write-Host "   " -NoNewline
Write-Host "-Headers @{'Authorization'='Bearer ' + `$env:DOCKER_SANDBOXES_TOKEN}" -ForegroundColor Green
Write-Host ""

Write-Host "7. Once token works, renew sandbox:" -ForegroundColor Cyan
Write-Host "   " -NoNewline
Write-Host ".\renew-sandbox.ps1 -SandboxId 'sbx_001m4kw6qwmfqbg4dx96xzxmwnz' -Hours 24" `
    -ForegroundColor Green
Write-Host ""

Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""
Write-Host "⚠️  IMPORTANT:" -ForegroundColor Red
Write-Host "   - Token is CASE-SENSITIVE" -ForegroundColor Yellow
Write-Host "   - Keep it SECRET (never commit to Git)" -ForegroundColor Yellow
Write-Host "   - Only expires if you revoke it manually" -ForegroundColor Yellow
Write-Host ""
