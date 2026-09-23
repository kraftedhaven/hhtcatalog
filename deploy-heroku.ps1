<#!
.SYNOPSIS
Deploys HHT Catalog to Heroku from Windows PowerShell.

.EXAMPLE
.\deploy-heroku.ps1
#>

[CmdletBinding()]
param(
    [string]$AppName = "hht-catalog",
    [ValidateRange(1, 30)]
    [int]$HealthRetries = 12,
    [ValidateRange(1, 60)]
    [int]$HealthRetrySeconds = 10,
    [switch]$ShowLogsOnFailure
)

$ErrorActionPreference = "Stop"
$HerokuCli = Join-Path ${env:ProgramFiles} "Heroku\bin\heroku.cmd"
$appInfo = $null

function Test-RequiredCommand {
    param([string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found on PATH."
    }
}

if (-not (Test-Path -LiteralPath $HerokuCli -PathType Leaf)) {
    throw "Heroku CLI was not found at $HerokuCli. Install it with: choco install heroku-cli -y"
}

function Invoke-Heroku {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)

    & $HerokuCli @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Heroku command failed: heroku $($Arguments -join ' ')"
    }
}

function Show-DeploymentDiagnostics {
    Write-Host ""
    Write-Host "Deployment diagnostics (read-only):"
    & $HerokuCli ps --app $AppName
    & $HerokuCli releases --app $AppName --num 1
    if ($ShowLogsOnFailure) {
        Write-Host ""
        Write-Host "Recent application logs:"
        & $HerokuCli logs --app $AppName --num 100
    }
}

function Test-HealthEndpoint {
    param([string]$Url)

    $lastFailure = "Health endpoint did not return a successful response."
    for ($attempt = 1; $attempt -le $HealthRetries; $attempt += 1) {
        try {
            $health = Invoke-RestMethod -Uri $Url -TimeoutSec 30
            if ($health.status -eq "ok") {
                Write-Host "API is healthy."
                return
            }
            $lastFailure = "Health endpoint returned status '$($health.status)'."
        }
        catch {
            $lastFailure = $_.Exception.Message
        }

        if ($attempt -lt $HealthRetries) {
            Write-Host "Health check attempt $attempt of $HealthRetries did not pass. Retrying in $HealthRetrySeconds seconds..."
            Start-Sleep -Seconds $HealthRetrySeconds
        }
    }

    throw "Health check failed after $HealthRetries attempts: $lastFailure"
}

try {
    Test-RequiredCommand git
    if (-not (git rev-parse --is-inside-work-tree 2>$null)) {
        throw "Run this script from inside the HHT Catalog Git repository."
    }
    git show-ref --verify --quiet refs/heads/main
    if ($LASTEXITCODE -ne 0) {
        throw "The local main branch is required for deployment."
    }

    Write-Host "Deploying HHT Catalog to Heroku..."
    Write-Host "App: $AppName"
    Write-Host "Checking Heroku login..."
    Invoke-Heroku auth:whoami --app $AppName | Out-Null

    Write-Host "Retrieving Heroku app metadata..."
    $appInfo = Invoke-Heroku apps:info --app $AppName | Out-String
    $urlMatch = [regex]::Match($appInfo, "(?m)^Web URL:\s*(https?://\S+)")
    if (-not $urlMatch.Success) {
        throw "Heroku did not return a canonical Web URL for $AppName."
    }

    $remoteUrl = git remote get-url heroku 2>$null
    if (-not $remoteUrl) {
        Write-Host "Adding Heroku Git remote..."
        Invoke-Heroku git:remote --app $AppName | Out-Null
    }

    $dirtyFiles = @(git status --porcelain)
    if ($dirtyFiles.Count -gt 0) {
        Write-Warning "Uncommitted local changes will not be included in the push."
    }

    Write-Host "Pushing main branch to Heroku..."
    git push heroku main
    if ($LASTEXITCODE -ne 0) {
        throw "Git push to Heroku failed. Inspect the build output above."
    }

    Write-Host "Scaling web and worker dynos..."
    Invoke-Heroku ps:scale web=1 worker=1 --app $AppName | Out-Null

    $healthUrl = "$($urlMatch.Groups[1].Value.TrimEnd('/'))/health"
    Write-Host "Checking health endpoint: $healthUrl"
    Test-HealthEndpoint $healthUrl

    Write-Host "Deployment status:"
    Invoke-Heroku ps --app $AppName
    Write-Host "Deployment complete."
}
catch {
    Write-Error "Deployment failed: $($_.Exception.Message)"
    Show-DeploymentDiagnostics
    exit 1
}