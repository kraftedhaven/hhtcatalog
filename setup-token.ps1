# Get Docker Sandboxes API Token - Windows PowerShell
# Opens Docker settings to get your Personal Access Token

Write-Host "Docker Sandboxes Renewal - Setup" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "Step 1: Get your Docker Personal Access Token" -ForegroundColor Yellow
Write-Host ""
Write-Host "Opening Docker settings in your browser..." -ForegroundColor Green
Start-Process "https://app.docker.com/settings/security"

Write-Host ""
Write-Host "Step 2: Create or copy a Personal Access Token" -ForegroundColor Yellow
Write-Host "  - Look for 'Personal Access Tokens' section"
Write-Host "  - Create one if you don't have it"
Write-Host "  - Copy the full token value"
Write-Host ""

Write-Host "Step 3: Set environment variable" -ForegroundColor Yellow
Write-Host ""
Write-Host "Paste this command and replace YOUR_TOKEN:" -ForegroundColor Cyan
Write-Host '  $env:DOCKER_SANDBOXES_TOKEN = "YOUR_TOKEN"' -ForegroundColor Green
Write-Host ""

Write-Host "Step 4: Verify token is set" -ForegroundColor Yellow
Write-Host "  `$env:DOCKER_SANDBOXES_TOKEN  # Should show your token (first 20 chars visible)" -ForegroundColor Cyan
Write-Host ""

Write-Host "Step 5: Run renewal script" -ForegroundColor Yellow
Write-Host "  .\renew-sandbox.ps1 -SandboxId 'sbx_001m4kw6qwmfqbg4dx96xzxmwnz' -Hours 24" -ForegroundColor Cyan
Write-Host ""

Read-Host "Press Enter when you've set the token..."
