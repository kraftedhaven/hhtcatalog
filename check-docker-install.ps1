# Check Docker & Sandboxes Installation - Windows PowerShell

Write-Host ""
Write-Host "╔════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║  Docker & Sandboxes Installation Check                   ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# Check 1: Docker CLI
Write-Host "1. Checking Docker CLI..." -ForegroundColor Yellow
try {
    $dockerVersion = & docker --version 2>&1
    Write-Host "   ✓ Docker found: $dockerVersion" -ForegroundColor Green
} catch {
    Write-Host "   ✗ Docker not found" -ForegroundColor Red
    Write-Host "     Download: https://www.docker.com/products/docker-desktop" -ForegroundColor Yellow
    exit 1
}

# Check 2: sbx CLI
Write-Host ""
Write-Host "2. Checking sbx CLI (Docker Sandboxes)..." -ForegroundColor Yellow
try {
    $sbxVersion = & sbx --version 2>&1
    Write-Host "   ✓ sbx found: $sbxVersion" -ForegroundColor Green
} catch {
    Write-Host "   ✗ sbx NOT found" -ForegroundColor Red
    Write-Host ""
    Write-Host "   INSTALL OPTIONS:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "   Option A: Enable in Docker Desktop (easiest)" -ForegroundColor Cyan
    Write-Host "     1. Open Docker Desktop" -ForegroundColor Gray
    Write-Host "     2. Settings → Features in development" -ForegroundColor Gray
    Write-Host "     3. Enable 'Sandboxes (beta)'" -ForegroundColor Gray
    Write-Host "     4. Restart Docker Desktop" -ForegroundColor Gray
    Write-Host "     5. Run: docker-cli-plugin-metadata" -ForegroundColor Gray
    Write-Host ""
    Write-Host "   Option B: Manual install" -ForegroundColor Cyan
    Write-Host "     1. Visit: https://github.com/docker/desktop-linux/releases" -ForegroundColor Gray
    Write-Host "     2. Download 'docker-sbx' binary for Windows" -ForegroundColor Gray
    Write-Host "     3. Place in Docker CLI plugins folder:" -ForegroundColor Gray
    Write-Host "        %APPDATA%\Docker\cli-plugins\docker-sbx.exe" -ForegroundColor Green
    Write-Host "     4. Restart Docker" -ForegroundColor Gray
    exit 1
}

# Check 3: Docker daemon
Write-Host ""
Write-Host "3. Checking Docker daemon..." -ForegroundColor Yellow
try {
    $dockerInfo = & docker info 2>&1
    if ($dockerInfo -match "running") {
        Write-Host "   ✓ Docker daemon is running" -ForegroundColor Green
    } else {
        Write-Host "   ⚠ Docker daemon status unclear" -ForegroundColor Yellow
    }
} catch {
    Write-Host "   ✗ Cannot connect to Docker daemon" -ForegroundColor Red
    Write-Host "     Start Docker Desktop and try again" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "All systems ready! You can now create sandboxes." -ForegroundColor Green
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""
Write-Host "Next: Run the setup script again" -ForegroundColor Cyan
Write-Host "  .\run-sandbox-setup.ps1" -ForegroundColor Green
