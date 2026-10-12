# Fix Docker & sbx PATH - Windows PowerShell
# Finds Docker Desktop installation and adds to PATH

Write-Host ""
Write-Host "╔════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║  Docker & sbx PATH Setup                                  ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# Common Docker Desktop locations
$dockerLocations = @(
    "$env:ProgramFiles\Docker\Docker\resources\bin",
    "$env:LOCALAPPDATA\Docker\resources\bin",
    "$env:ProgramFiles\Docker\cli-plugins",
    "$env:LOCALAPPDATA\Docker\cli-plugins",
    "C:\Program Files\Docker\Docker\resources\bin",
    "$env:USERPROFILE\AppData\Local\Docker\resources\bin"
)

Write-Host "Searching for Docker installation..." -ForegroundColor Yellow
Write-Host ""

$found = $false
foreach ($location in $dockerLocations) {
    if (Test-Path $location) {
        Write-Host "✓ Found Docker at: $location" -ForegroundColor Green
        $found = $true
        $dockerBinPath = $location
        break
    }
}

if (-not $found) {
    Write-Host "✗ Docker not found in standard locations" -ForegroundColor Red
    Write-Host ""
    Write-Host "Checking for docker command directly..." -ForegroundColor Yellow
    
    # Try to run docker from wherever it is
    try {
        $dockerVersion = & docker --version 2>&1
        if ($dockerVersion) {
            Write-Host "✓ Docker is available: $dockerVersion" -ForegroundColor Green
            Write-Host ""
            Write-Host "Your Docker is already in PATH. Try sbx command:" -ForegroundColor Yellow
            Write-Host "  sbx --version" -ForegroundColor Cyan
            exit 0
        }
    } catch {
        Write-Host "✗ Docker command failed" -ForegroundColor Red
    }
    
    Write-Host ""
    Write-Host "SOLUTION: Docker Desktop may not be running" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "1. Start Docker Desktop from Windows Start menu" -ForegroundColor Cyan
    Write-Host "2. Wait 1-2 minutes for it to fully start" -ForegroundColor Cyan
    Write-Host "3. Run this script again" -ForegroundColor Cyan
    Write-Host ""
    exit 1
}

Write-Host ""
Write-Host "Adding Docker to PowerShell PATH..." -ForegroundColor Yellow
Write-Host ""

# Add to current session
$env:PATH = $dockerBinPath + ";" + $env:PATH

# Verify docker command works
Write-Host "Testing docker command..." -ForegroundColor Yellow
try {
    $dockerVersion = & docker --version
    Write-Host "✓ Docker available: $dockerVersion" -ForegroundColor Green
} catch {
    Write-Host "✗ docker command still not working" -ForegroundColor Red
    exit 1
}

Write-Host ""

# Test sbx command
Write-Host "Testing sbx command..." -ForegroundColor Yellow
try {
    $sbxVersion = & sbx --version
    Write-Host "✓ sbx available: $sbxVersion" -ForegroundColor Green
} catch {
    Write-Host "⚠ sbx not found yet (may need to enable in Docker Desktop)" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "NEXT STEP: Enable Sandboxes in Docker Desktop" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "1. Open Docker Desktop app" -ForegroundColor Gray
    Write-Host "2. Settings → Features in development" -ForegroundColor Gray
    Write-Host "3. Toggle ON 'Sandboxes (beta)'" -ForegroundColor Gray
    Write-Host "4. Restart Docker Desktop" -ForegroundColor Gray
    Write-Host "5. Run this script again" -ForegroundColor Gray
    Write-Host ""
    exit 1
}

Write-Host ""
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "SUCCESS! Docker and sbx are ready." -ForegroundColor Green
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""

Write-Host "Current PATH includes:" -ForegroundColor Cyan
Write-Host "  $dockerBinPath" -ForegroundColor Green
Write-Host ""

Write-Host "To make this permanent, add to your PowerShell profile:" -ForegroundColor Yellow
Write-Host ""
Write-Host "`$env:PATH = '$dockerBinPath' + ';' + `$env:PATH" -ForegroundColor Green
Write-Host ""

Write-Host "Or add permanently to Windows Environment Variables:" -ForegroundColor Yellow
Write-Host "  Control Panel → System → Advanced System Settings → Environment Variables" -ForegroundColor Cyan
Write-Host "  Add '$dockerBinPath' to PATH" -ForegroundColor Cyan
Write-Host ""

Write-Host "Ready to create sandbox!" -ForegroundColor Cyan
Write-Host "  .\run-sandbox-setup.ps1" -ForegroundColor Green
Write-Host ""
