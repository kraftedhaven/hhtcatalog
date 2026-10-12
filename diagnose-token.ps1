# Diagnose Docker Sandboxes Token Issue - Windows PowerShell

Write-Host ""
Write-Host "╔════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║  Docker Token Diagnostics                                 ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# Check if token is set
if (-not $env:DOCKER_SANDBOXES_TOKEN) {
    Write-Host "❌ DOCKER_SANDBOXES_TOKEN is NOT SET" -ForegroundColor Red
    exit 1
}

$token = $env:DOCKER_SANDBOXES_TOKEN
$tokenLength = $token.Length

Write-Host "Token length: $tokenLength characters" -ForegroundColor Yellow
Write-Host "Token prefix: " -NoNewline -ForegroundColor Yellow
Write-Host ($token.Substring(0, [Math]::Min(10, $tokenLength))) -ForegroundColor Green

Write-Host ""
Write-Host "Checking token format..." -ForegroundColor Cyan
Write-Host ""

# Check for common format issues
if ($token.StartsWith("dckr_pat_")) {
    Write-Host "✓ Token starts with 'dckr_pat_' (correct prefix)" -ForegroundColor Green
} elseif ($token.StartsWith("dckr_")) {
    Write-Host "⚠ Token starts with 'dckr_' but not 'dckr_pat_'" -ForegroundColor Yellow
    Write-Host "  This might be a different token type" -ForegroundColor Gray
} else {
    Write-Host "❌ Token does NOT start with 'dckr_'" -ForegroundColor Red
    Write-Host "  Did you copy the full token? Check Docker settings again." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Testing token validity..." -ForegroundColor Cyan
Write-Host ""

try {
    $response = Invoke-RestMethod `
        -Uri "https://connect.docker.com/sandboxes/v1/sandboxes" `
        -Method GET `
        -Headers @{
            "Authorization" = "Bearer $token"
            "Content-Type" = "application/json"
        } `
        -ErrorAction Stop

    Write-Host "✓ TOKEN IS VALID!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Your sandboxes:" -ForegroundColor Cyan
    if ($response.sandboxes -and $response.sandboxes.Count -gt 0) {
        foreach ($sb in $response.sandboxes) {
            Write-Host "  - $($sb.id) ($($sb.status))" -ForegroundColor Green
        }
    } else {
        Write-Host "  (No sandboxes found)" -ForegroundColor Gray
    }
    
} catch {
    Write-Host "❌ TOKEN IS INVALID" -ForegroundColor Red
    Write-Host ""
    
    $errorMessage = $_.Exception.Message
    Write-Host "Error: $errorMessage" -ForegroundColor Red
    
    if ($errorMessage -match "TOKEN_INVALID") {
        Write-Host ""
        Write-Host "This token has been revoked or is no longer valid." -ForegroundColor Yellow
    } elseif ($errorMessage -match "Unauthorized") {
        Write-Host ""
        Write-Host "Authorization failed. Token might be malformed." -ForegroundColor Yellow
    }
    
    Write-Host ""
    Write-Host "SOLUTIONS:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "1. Double-check the token in Docker settings:" -ForegroundColor Cyan
    Write-Host "   https://app.docker.com/settings/security" -ForegroundColor Green
    Write-Host ""
    Write-Host "2. Make sure you:" -ForegroundColor Cyan
    Write-Host "   - Copied the ENTIRE token (no truncation)" -ForegroundColor Gray
    Write-Host "   - Did NOT include any quotes or extra spaces" -ForegroundColor Gray
    Write-Host "   - Checked scopes include 'Sandboxes' or 'sandboxesLifecycle'" -ForegroundColor Gray
    Write-Host ""
    Write-Host "3. Try revoking and creating a NEW token:" -ForegroundColor Cyan
    Write-Host "   - Go to https://app.docker.com/settings/security" -ForegroundColor Green
    Write-Host "   - Delete/Revoke the old token" -ForegroundColor Yellow
    Write-Host "   - Click 'Generate new token'" -ForegroundColor Yellow
    Write-Host "   - Name: 'HHT Sandbox Worker'" -ForegroundColor Gray
    Write-Host "   - Scopes: Check BOTH 'Read' and 'Write' for Sandboxes" -ForegroundColor Gray
    Write-Host "   - Copy immediately (can't be retrieved later)" -ForegroundColor Red
    Write-Host ""
    Write-Host "4. Set it again:" -ForegroundColor Cyan
    Write-Host '   $env:DOCKER_SANDBOXES_TOKEN = "YOUR_NEW_TOKEN_HERE"' -ForegroundColor Green
    Write-Host ""
    
    exit 1
}

Write-Host ""
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Cyan
