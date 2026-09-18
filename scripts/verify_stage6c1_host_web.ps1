param(
    [string]$ProjectName = "stage6c1qa",
    [int]$WebPort = 58080
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$composeFiles = @(
    "compose", "-p", $ProjectName,
    "-f", (Join-Path $projectRoot "docker-compose.yml"),
    "-f", (Join-Path $projectRoot "docker-compose.qa.yml")
)

function Invoke-Compose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & docker @composeFiles @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed: $($Arguments -join ' ')"
    }
}

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) {
        throw $Message
    }
}

function Get-HostHealth {
    $uri = "http://127.0.0.1:$WebPort/health"
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $uri -TimeoutSec 3
            $body = $response.Content | ConvertFrom-Json
            if ($response.StatusCode -eq 200 -and $body.application -eq "ok" -and $body.postgresql -eq "ok") {
                return $body
            }
        }
        catch {
            if ($attempt -eq 30) { throw }
        }
        Start-Sleep -Seconds 1
    }
    throw "Host health did not become ready at $uri"
}

$webId = (Invoke-Compose ps -q web | Select-Object -First 1).Trim()
$postgresId = (Invoke-Compose ps -q postgres | Select-Object -First 1).Trim()
Assert-True ($webId.Length -gt 0) "QA web container is not running"
Assert-True ($postgresId.Length -gt 0) "QA PostgreSQL container is not running"

$webInspect = (& docker inspect $webId | ConvertFrom-Json)[0]
$webBindings = $webInspect.NetworkSettings.Ports.'8080/tcp'
Assert-True ($null -ne $webBindings) "NetworkSettings.Ports has no 8080/tcp binding"
Assert-True ($webBindings.Count -eq 1) "Expected exactly one host binding for web port 8080/tcp"
Assert-True ($webBindings[0].HostIp -eq "127.0.0.1") "Web port is not bound to loopback"
Assert-True ($webBindings[0].HostPort -eq [string]$WebPort) "Web host port is not $WebPort"
$webHostBindings = $webInspect.HostConfig.PortBindings.'8080/tcp'
Assert-True ($null -ne $webHostBindings) "HostConfig.PortBindings has no web binding"
Assert-True ($webHostBindings[0].HostIp -eq "127.0.0.1") "HostConfig web binding is not loopback"
Assert-True ($webHostBindings[0].HostPort -eq [string]$WebPort) "HostConfig web port is not $WebPort"

$dockerPort = (& docker port $webId 8080/tcp) -join "`n"
Assert-True ($dockerPort -match "127\.0\.0\.1:$WebPort") "docker port does not report the expected loopback binding"

$postgresInspect = (& docker inspect $postgresId | ConvertFrom-Json)[0]
$postgresBindings = $postgresInspect.NetworkSettings.Ports.'5432/tcp'
if ($null -ne $postgresBindings) {
    foreach ($binding in @($postgresBindings)) {
        Assert-True ($binding.HostIp -eq "127.0.0.1") "PostgreSQL is not restricted to loopback"
    }
}

$beforeRestart = Get-HostHealth
Invoke-Compose restart web | Out-Null
$afterRestart = Get-HostHealth

[ordered]@{
    host_web_access = "PASS"
    host_port_binding_active = "PASS"
    host_health_http_200 = "PASS"
    web_container_restart_host_access = "PASS"
    postgres_not_public = "PASS"
    web_binding = "127.0.0.1:$WebPort->8080/tcp"
    before_restart = $beforeRestart
    after_restart = $afterRestart
} | ConvertTo-Json -Depth 5
