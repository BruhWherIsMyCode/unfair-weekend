<#
.SYNOPSIS
    Rings the parent app: starts a check-in call on the backend that start-backend.ps1 is running.
    With -CallId, prints that call's state and log instead (handy to check frames and audio arrived).

.EXAMPLE
    .\start-call.ps1
.EXAMPLE
    .\start-call.ps1 -CallId call_2efb29ab15fd4f3abbea23a958c49292
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [string]$DemoKey = $env:DEMO_KEY,
    [string]$PersonId = "p_demo",
    [string]$CallId
)

if (-not $DemoKey) {
    $keyFile = Join-Path $env:LOCALAPPDATA "checkin-backend\demo_key.txt"
    if (-not (Test-Path $keyFile)) {
        Write-Host "No demo key: run start-backend.ps1 first, or pass -DemoKey." -ForegroundColor Red
        exit 1
    }
    $DemoKey = (Get-Content $keyFile -Raw).Trim()
}
$headers = @{ "X-Demo-Key" = $DemoKey }
$base = "http://127.0.0.1:$Port"

try {
    if ($CallId) {
        Invoke-RestMethod -Uri "$base/v1/calls/$CallId" -Headers $headers | ConvertTo-Json -Depth 10
        exit 0
    }
    $body = @{ person_id = $PersonId } | ConvertTo-Json
    $call = Invoke-RestMethod -Method Post -Uri "$base/v1/calls" -Headers $headers -ContentType "application/json" -Body $body
    Write-Host "Ringing. Call id: $($call.call_id)"
    Write-Host "Afterwards, see its log with:  .\start-call.ps1 -CallId $($call.call_id)"
} catch {
    Write-Host "The backend did not accept the request: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Is start-backend.ps1 running, and has the phone registered (open the app once)?" -ForegroundColor Red
    exit 1
}
