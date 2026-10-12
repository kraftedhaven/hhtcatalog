# Renew Docker Sandbox Lifetime - Windows PowerShell
# Usage: .\renew-sandbox.ps1 -SandboxId "sbx_001m4kw6qwmfqbg4dx96xzxmwnz" -Hours 24

param(
    [Parameter(Mandatory=$true)]
    [string]$SandboxId,
    
    [Parameter(Mandatory=$false)]
    [int]$Hours = 24,
    
    [Parameter(Mandatory=$false)]
    [string]$Token = $env:DOCKER_SANDBOXES_TOKEN
)

if (-not $Token) {
    Write-Host "ERROR: DOCKER_SANDBOXES_TOKEN environment variable not set" -ForegroundColor Red
    Write-Host ""
    Write-Host "Get your token from: https://app.docker.com/settings/security"
    Write-Host ""
    Write-Host "Set it with:" -ForegroundColor Yellow
    Write-Host '  $env:DOCKER_SANDBOXES_TOKEN = "your_token_here"' -ForegroundColor Cyan
    exit 1
}

$Seconds = $Hours * 3600
$ApiUrl = "https://connect.docker.com/sandboxes/v1/sandboxes/$SandboxId/renew-timeout"

Write-Host "Renewing sandbox lifetime..." -ForegroundColor Cyan
Write-Host "Sandbox ID: $SandboxId"
Write-Host "New lifetime: $Hours hour(s) ($Seconds seconds)"
Write-Host ""

$body = @{
    timeout = @{
        seconds = $Seconds
    }
} | ConvertTo-Json

try {
    $response = Invoke-RestMethod `
        -Uri $ApiUrl `
        -Method POST `
        -Headers @{
            "Authorization" = "Bearer $Token"
            "Content-Type" = "application/json"
        } `
        -Body $body `
        -ErrorAction Stop

    Write-Host "SUCCESS: Sandbox lifetime renewed!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Sandbox Details:"
    Write-Host "  ID: $($response.id)" -ForegroundColor Cyan
    Write-Host "  Status: $($response.status)" -ForegroundColor Cyan
    Write-Host "  Expires at: $($response.expiresAt)" -ForegroundColor Cyan
    Write-Host ""
    
    if ($response.status -eq "stopped") {
        Write-Host "Sandbox is currently STOPPED. Restart it with:" -ForegroundColor Yellow
        Write-Host "  sbx run --name $SandboxId" -ForegroundColor Cyan
    } elseif ($response.status -eq "running") {
        Write-Host "Sandbox is RUNNING. You can reconnect immediately." -ForegroundColor Green
    }
    
} catch {
    $errorResponse = $_.Exception.Response
    $statusCode = [int]$errorResponse.StatusCode
    
    Write-Host "ERROR: Failed to renew sandbox" -ForegroundColor Red
    Write-Host "Status Code: $statusCode" -ForegroundColor Red
    
    try {
        $errorBody = $_.Exception.Response.Content | ConvertFrom-Json
        Write-Host "Error Message: $($errorBody.message)" -ForegroundColor Red
        Write-Host "Error Code: $($errorBody.code)" -ForegroundColor Red
    } catch {
        Write-Host "Response: $($_.Exception.Message)" -ForegroundColor Red
    }
    
    Write-Host ""
    Write-Host "Troubleshooting:" -ForegroundColor Yellow
    Write-Host "  - Verify DOCKER_SANDBOXES_TOKEN is correct"
    Write-Host "  - Check sandbox ID is accurate"
    Write-Host "  - If 'notFound', sandbox may have been permanently deleted"
    Write-Host "  - If 'unimplemented', feature not available on this account"
    
    exit 1
}
