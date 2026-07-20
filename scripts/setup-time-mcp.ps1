[CmdletBinding()]
param(
    [string]$ImageTag = "market-lens/mcp-time:2026.7.10",
    [string]$ConfigPath = "mcp.servers.json"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$buildContext = Join-Path $projectRoot "infra/mcp/time"
$resolvedConfigPath = Join-Path $projectRoot $ConfigPath

docker version --format "Docker Engine {{.Server.Version}}" | Out-Host
docker build --pull=false --tag $ImageTag $buildContext | Out-Host

$imageId = docker image inspect --format "{{.Id}}" $ImageTag
if ($imageId -notmatch '^sha256:[0-9a-f]{64}$') {
    throw "Docker returned an invalid immutable image ID"
}

$config = [ordered]@{
    servers = @(
        [ordered]@{
            name = "time_reference"
            enabled = $true
            transport = "stdio"
            image = $imageId
            command = @("--local-timezone", "Asia/Shanghai")
            tools = [ordered]@{
                get_current_time = [ordered]@{
                    description = "Get the current time for a reviewed IANA timezone"
                    risk = "read"
                    capability = "utility"
                    timeout_seconds = 15
                    idempotent = $true
                    requires_network = $false
                }
                convert_time = [ordered]@{
                    description = "Convert a clock time between reviewed IANA timezones"
                    risk = "compute"
                    capability = "utility"
                    timeout_seconds = 15
                    idempotent = $true
                    requires_network = $false
                }
            }
            timeout_seconds = 15
            max_response_bytes = 65536
            memory_mb = 256
            cpu_count = 0.5
            pids_limit = 64
        }
    )
}

$config | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $resolvedConfigPath -Encoding utf8
Write-Host "Created ignored MCP configuration: $resolvedConfigPath"
Write-Host "Immutable image ID: $imageId"
Write-Host "Set MARKET_LENS_MCP_SERVERS_FILE=mcp.servers.json, then restart the API."
