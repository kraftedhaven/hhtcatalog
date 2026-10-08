[CmdletBinding()]
param(
    [string]$App = "hht-catalog-b34ed1b32417",
    [switch]$ShowOptional
)

$ErrorActionPreference = "Stop"

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host "=== $Title ===" -ForegroundColor Cyan
}

function Test-HerokuCommand {
    if (-not (Get-Command heroku -ErrorAction SilentlyContinue)) {
        throw "Heroku CLI was not found. Install it, then reopen VS Code and run: heroku login"
    }
}

function Invoke-HerokuJson {
    param([string[]]$Arguments)
    $raw = & heroku @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw (($raw | Out-String).Trim())
    }
    if ([string]::IsNullOrWhiteSpace(($raw | Out-String))) {
        return $null
    }
    return ($raw | Out-String | ConvertFrom-Json)
}

try {
    Test-HerokuCommand

    Write-Section "Heroku authentication"
    $account = & heroku auth:whoami 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Heroku is not authenticated. Run: heroku login"
    }
    Write-Host ("Authenticated account: " + (($account | Out-String).Trim())) -ForegroundColor Green

    Write-Section "Application"
    $apps = & heroku apps:info --app $App 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Cannot access Heroku app '$App'. Check the app name and account access."
    }
    Write-Host "App accessible: $App" -ForegroundColor Green

    Write-Section "Dyno and worker status"
    $dynos = Invoke-HerokuJson @("ps", "--app", $App, "--json")
    if (-not $dynos) {
        Write-Host "No dyno information was returned." -ForegroundColor Yellow
    } else {
        $dynoRows = @($dynos) | ForEach-Object {
            [PSCustomObject]@{
                Name   = $_.name
                State  = $_.state
                Type   = $_.type
                Command = $_.command
                Size   = $_.size
            }
        }
        $dynoRows | Format-Table -AutoSize

        $workers = @($dynos) | Where-Object { $_.name -like "worker*" -or $_.type -eq "worker" }
        if (-not $workers) {
            Write-Host "RESULT: No worker dyno is running." -ForegroundColor Red
            Write-Host "Start it with: heroku ps:scale worker=1 --app $App" -ForegroundColor Yellow
        } elseif (@($workers | Where-Object { $_.state -eq "up" }).Count -gt 0) {
            Write-Host "RESULT: At least one worker dyno is up." -ForegroundColor Green
        } else {
            Write-Host "RESULT: Worker dyno exists but is not up." -ForegroundColor Red
        }
    }

    Write-Section "Config Var presence (values hidden)"
    # --json is used only for local parsing. Values are never printed.
    $config = Invoke-HerokuJson @("config", "--app", $App, "--json")
    $required = @(
        "DATABASE_URL",
        "GROQ_API_KEY",
        "GROQ_MODEL",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "PHOTO_STORAGE_PROVIDER",
        "IBM_COS_ENDPOINT",
        "IBM_COS_BUCKET",
        "IBM_COS_ACCESS_KEY_ID",
        "IBM_COS_SECRET_ACCESS_KEY",
        "EBAY_CLIENT_ID",
        "EBAY_CLIENT_SECRET",
        "EBAY_REFRESH_TOKEN",
        "EBAY_REFRESH_TOKEN_ISSUED_AT",
        "EBAY_REDIRECT_URI",
        "EBAY_ACCOUNT_ID",
        "SELLER_BOOTSTRAP_AUTH_USER_ID",
        "EBAY_MUTATIONS_ENABLED",
        "EBAY_DRAFTS_ENABLED",
        "EBAY_DRAFT_MIN_ITEMS"
    )
    $optional = @(
        "OPENROUTER_API_KEY",
        "OPENROUTER_MODEL",
        "NVIDIA_NIM_API_KEY",
        "NVIDIA_NIM_BASE_URL",
        "NVIDIA_CATEGORY_MODEL",
        "WORKER_POLL_SECONDS",
        "WORKER_CONCURRENCY",
        "CORS_ORIGINS"
    )

    $rows = foreach ($name in $required) {
        $exists = $null -ne $config.PSObject.Properties[$name]
        $value = if ($exists) { [string]$config.$name } else { "" }
        [PSCustomObject]@{
            Variable = $name
            Status   = if (-not $exists) { "MISSING" } elseif ([string]::IsNullOrWhiteSpace($value)) { "BLANK" } else { "PRESENT" }
        }
    }
    $rows | Format-Table -AutoSize

    if ($ShowOptional) {
        Write-Host "Optional variables:" -ForegroundColor DarkCyan
        foreach ($name in $optional) {
            $exists = $null -ne $config.PSObject.Properties[$name]
            $value = if ($exists) { [string]$config.$name } else { "" }
            [PSCustomObject]@{
                Variable = $name
                Status   = if (-not $exists) { "MISSING" } elseif ([string]::IsNullOrWhiteSpace($value)) { "BLANK" } else { "PRESENT" }
            }
        } | Format-Table -AutoSize
    }

    Write-Section "Safety checks"
    $mutation = [string]$config.EBAY_MUTATIONS_ENABLED
    $drafts = [string]$config.EBAY_DRAFTS_ENABLED
    $minimum = [string]$config.EBAY_DRAFT_MIN_ITEMS

    if ($mutation -match "^(?i:true|1|yes|on)$") {
        Write-Host "FAIL: EBAY_MUTATIONS_ENABLED is enabled. Disable it before the pilot." -ForegroundColor Red
    } else {
        Write-Host "PASS: Live eBay mutations are disabled or unset." -ForegroundColor Green
    }

    if ($drafts -match "^(?i:true|1|yes|on)$") {
        Write-Host "PASS: Seller Hub drafts are enabled." -ForegroundColor Green
    } else {
        Write-Host "WARN: EBAY_DRAFTS_ENABLED is not enabled; the draft pilot cannot submit." -ForegroundColor Yellow
    }

    if ($minimum -eq "3") {
        Write-Host "PASS: Draft minimum is configured to 3." -ForegroundColor Green
    } elseif ([string]::IsNullOrWhiteSpace($minimum)) {
        Write-Host "WARN: EBAY_DRAFT_MIN_ITEMS is missing; deployed code default may be used." -ForegroundColor Yellow
    } else {
        Write-Host "WARN: EBAY_DRAFT_MIN_ITEMS is '$minimum', not 3." -ForegroundColor Yellow
    }

    Write-Section "Recommended next command"
    Write-Host "heroku logs --tail --dyno worker.1 --app $App"
    Write-Host "Do not paste log lines containing tokens, URLs with credentials, or Config Var values."

    $missing = @($rows | Where-Object { $_.Status -eq "MISSING" -or $_.Status -eq "BLANK" })
    if ($missing.Count -gt 0) {
        Write-Host "Overall result: REVIEW REQUIRED ($($missing.Count) required variables missing or blank)." -ForegroundColor Yellow
        exit 2
    }

    Write-Host "Overall result: required Config Var names are present; review worker state and safety checks above." -ForegroundColor Green
    exit 0
}
catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
