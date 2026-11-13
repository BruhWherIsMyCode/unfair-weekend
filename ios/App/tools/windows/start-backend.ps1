<#
.SYNOPSIS
    Starts the check-in backend (call service) on this Windows PC, so the parent iPhone app can reach it over Wi-Fi.

.DESCRIPTION
    It runs the backend from main (call service, voice and avatar clips), whatever branch is checked out here:
    main is fetched into a folder next to this repo (<repo>-call-service) and run from there, up to date on
    every start. Your own checkout and branch are not touched. -Branch runs another branch instead.

    Steps: fetch the branch, install uv if missing, install dependencies, install the measuring packages
    (mediapipe, opencv) and the face/hand model files into the backend's environment on every start (fast when
    cached) and run a self-check, set up Claude, open the firewall port (when run as administrator), print what to
    type into the phone, then run the server until you press Ctrl+C. The server is started with the environment's
    own Python (not "uv run"), so nothing syncs the measuring packages away. If measuring is not ready, a yellow
    message says why and the call falls back to the scripted one (no measuring), unless -Runner analysis was given.

.PARAMETER Branch
    Branch to run the backend from. Default: main.
.PARAMETER Port
    TCP port. Default: 8000.
.PARAMETER DemoKey
    Demo key the phone must send. Default: $env:DEMO_KEY, else one generated once and kept in
    %LOCALAPPDATA%\checkin-backend\demo_key.txt (never in the repo).
.PARAMETER Runner
    CALL_RUNNER for the backend: auto, analysis, fake or scripted. Default: auto.
.PARAMETER Claude
    Who runs the call: subscription (default; your logged-in `claude` command, installs claude-agent-sdk, no API key
    used), api (needs ANTHROPIC_API_KEY already set in the environment; never stored, asked or printed) or off (the
    fixed-order call, no Claude). If Claude cannot be used, a yellow warning says why and the call runs without it.
.PARAMETER Mirrored
    The phone's front camera frames are mirrored (UW_MIRRORED=on).
.PARAMETER BaselineNeeded
    How many normal calls make her usual ready (UW_BASELINE_NEEDED; default 5). Use 1 or 2 to try things out.
.PARAMETER Label
    normal: this run's calls may enter her usual; acted: they never do (DEMO_SESSION_LABEL). Default: live.
.PARAMETER ResetKeys
    Ask for the ElevenLabs and Simli keys again. They are asked for on the first run (Enter skips), stored
    encrypted for this Windows user (DPAPI) in %LOCALAPPDATA%\checkin-backend\provider_keys.txt, never in the
    repo, and reused after that. A key set in the environment always wins.
.PARAMETER AllowPublicNetwork
    Also open the port when Windows calls the Wi-Fi "Public" (typical for event Wi-Fi). Anyone on that
    network can then reach the backend; the demo key is the only lock.

.EXAMPLE
    .\start-backend.ps1
.EXAMPLE
    .\start-backend.ps1 -AllowPublicNetwork
#>
[CmdletBinding()]
param(
    [string]$Branch = "main",
    [int]$Port = 8000,
    [string]$DemoKey = $env:DEMO_KEY,
    [ValidateSet("auto", "analysis", "fake", "scripted")]
    [string]$Runner = "auto",
    [ValidateSet("off", "subscription", "api")]
    [string]$Claude = "subscription",
    [switch]$Mirrored,
    [ValidateRange(0, 1000)]
    [int]$BaselineNeeded = 0,
    [ValidateSet("", "normal", "acted")]
    [string]$Label = "",
    [switch]$AllowPublicNetwork,
    [switch]$ResetKeys
)

if ($PSBoundParameters.ContainsKey("BaselineNeeded") -and $BaselineNeeded -lt 1) {
    Write-Host "-BaselineNeeded must be a whole number of 1 or more." -ForegroundColor Red
    exit 2
}

function Step([string]$text) {
    Write-Host ""
    Write-Host "== $text" -ForegroundColor Cyan
}

# DPAPI: only this Windows user on this PC can decrypt. An empty key stays empty (skipped).
function Protect-Key([string]$plain) {
    if (-not $plain) { return "" }
    return ConvertTo-SecureString $plain -AsPlainText -Force | ConvertFrom-SecureString
}

function Unprotect-Key([string]$cipher) {
    if (-not $cipher) { return "" }
    try {
        $secure = ConvertTo-SecureString $cipher -ErrorAction Stop
        return (New-Object System.Net.NetworkCredential("", $secure)).Password
    } catch {
        Write-Host "A stored key could not be read (another user or PC?). Run with -ResetKeys." -ForegroundColor Yellow
        return ""
    }
}

function Fail([string]$text) {
    Write-Host ""
    Write-Host $text -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------- 1. the call-service code
Step "Getting $Branch"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Fail "Git is not installed. Install Git for Windows (https://git-scm.com/download/win) and run this again."
}
$repo = git -C $PSScriptRoot rev-parse --show-toplevel
if ($LASTEXITCODE -ne 0 -or -not $repo) {
    Fail "Run this script from inside the unfair-weekend git checkout."
}
$repo = (Resolve-Path $repo.Trim()).Path
$work = Join-Path (Split-Path $repo -Parent) ((Split-Path $repo -Leaf) + "-call-service")

git -C $repo fetch origin $Branch
if ($LASTEXITCODE -ne 0) { Fail "Could not fetch $Branch from GitHub. Check the network and your GitHub access." }

if (Test-Path (Join-Path $work ".git")) {
    git -C $work checkout --quiet --detach "origin/$Branch"
    if ($LASTEXITCODE -ne 0) { Fail "Could not update $work. Delete that folder and run this again." }
} else {
    git -C $repo worktree prune
    git -C $repo worktree add --detach $work "origin/$Branch"
    if ($LASTEXITCODE -ne 0) { Fail "Could not create $work." }
}
$backend = Join-Path $work "backend"
if (-not (Test-Path (Join-Path $backend "checkin_backend\hub.py"))) {
    Fail "$Branch has no call service (backend\checkin_backend\hub.py is missing)."
}
Write-Host "Backend code: $backend"

# ---------------------------------------------------------------- 2. uv and dependencies
Step "Checking uv"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    $answer = Read-Host "uv (the Python package manager the backend uses) is not installed. Install it now from astral.sh? [Y/n]"
    if ($answer -match "^[Nn]") {
        Fail "Install uv (https://docs.astral.sh/uv/getting-started/installation/) and run this again."
    }
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Fail "uv was installed but is not on PATH yet. Open a new PowerShell window and run this again."
    }
}

Step "Installing backend dependencies (the first run also downloads Python 3.12)"
Push-Location $backend
try {
    uv sync --locked --extra dev
    if ($LASTEXITCODE -ne 0) { Fail "uv sync failed. See the messages above." }
    $venvPy = Join-Path $backend ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPy)) { Fail "The backend environment has no Python at $venvPy." }
    # so `analysis` (next to backend\) can be imported
    if ($env:PYTHONPATH) { $env:PYTHONPATH = "$work;$($env:PYTHONPATH)" } else { $env:PYTHONPATH = $work }

    # ------------------------------------------------------------ 2b. measuring: packages, models, self-check
    Step "Measuring (face and hand): packages, model files, self-check"
    $measuringProblem = ""
    $requirements = Join-Path $work "analysis\requirements.txt"
    if (-not (Test-Path $requirements)) {
        $measuringProblem = "$Branch has no analysis\requirements.txt (the measuring code is not in that branch)."
    } else {
        uv pip install --python $venvPy -r $requirements
        if ($LASTEXITCODE -ne 0) { $measuringProblem = "Installing the measuring packages (mediapipe, opencv) failed." }
    }
    if (-not $measuringProblem) {
        Push-Location $work
        try {
            & $venvPy -m analysis.get_models
            if ($LASTEXITCODE -ne 0) {
                $measuringProblem = "Downloading the face/hand model files failed."
            } else {
                & $venvPy -m analysis.selfcheck
                if ($LASTEXITCODE -ne 0) { $measuringProblem = "The measuring self-check found a problem (see above)." }
            }
        } finally {
            Pop-Location
        }
    }
    if ($measuringProblem) {
        Write-Host $measuringProblem -ForegroundColor Yellow
        if ($Runner -eq "auto") {
            $Runner = "scripted"
            Write-Host "Continuing with the scripted call: it plays the lines but measures nothing." -ForegroundColor Yellow
        } elseif ($Runner -eq "analysis") {
            Write-Host "You asked for -Runner analysis: the backend will refuse to start and print the problems." -ForegroundColor Yellow
        }
    }

    # ------------------------------------------------------------ 2c. Claude
    $claudeMode = $Claude
    Step "Claude ($claudeMode)"
    if ($claudeMode -eq "subscription") {
        if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
            Write-Host "The 'claude' command was not found, so the subscription cannot be used. Calls run without Claude." -ForegroundColor Yellow
            Write-Host "(Install Claude Code and log in with it, or use -Claude api with ANTHROPIC_API_KEY set.)" -ForegroundColor Yellow
            $claudeMode = "off"
        } else {
            uv pip install --python $venvPy claude-agent-sdk
            if ($LASTEXITCODE -ne 0) {
                Write-Host "Installing claude-agent-sdk failed. Calls run without Claude." -ForegroundColor Yellow
                $claudeMode = "off"
            }
        }
    } elseif ($claudeMode -eq "api") {
        if (-not $env:ANTHROPIC_API_KEY) {
            Write-Host "ANTHROPIC_API_KEY is not set in the environment, so the API cannot be used. Calls run without Claude." -ForegroundColor Yellow
            $claudeMode = "off"
        } else {
            uv pip install --python $venvPy anthropic
            if ($LASTEXITCODE -ne 0) {
                Write-Host "Installing the anthropic package failed. Calls run without Claude." -ForegroundColor Yellow
                $claudeMode = "off"
            }
        }
    } else {
        Write-Host "Claude is off: the call follows the fixed order."
    }

    # ------------------------------------------------------------ 3. demo key
    $keyFile = Join-Path $env:LOCALAPPDATA "checkin-backend\demo_key.txt"
    if (-not $DemoKey) {
        if (Test-Path $keyFile) {
            $DemoKey = (Get-Content $keyFile -Raw).Trim()
        } else {
            $bytes = New-Object byte[] 12
            [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
            $DemoKey = -join ($bytes | ForEach-Object { $_.ToString("x2") })
            New-Item -ItemType Directory -Force -Path (Split-Path $keyFile -Parent) | Out-Null
            Set-Content -Path $keyFile -Value $DemoKey -NoNewline
        }
    }

    # ------------------------------------------------------------ provider keys
    Step "Provider keys"
    $providerFile = Join-Path (Split-Path $keyFile -Parent) "provider_keys.txt"
    $stored = @{}
    if (-not $ResetKeys -and (Test-Path $providerFile)) {
        foreach ($line in Get-Content $providerFile) {
            $name, $cipher = $line -split "=", 2
            if ($name -and $null -ne $cipher) { $stored[$name] = $cipher }
        }
    }
    $asked = $false
    $providerKeys = [ordered]@{
        "ELEVENLABS_API_KEY" = "ElevenLabs API key (voice and transcription; Enter to skip)"
        "SIMLI_API_KEY" = "Simli API key (avatar clips; Enter to skip)"
    }
    $values = @{}
    foreach ($name in $providerKeys.Keys) {
        $fromEnvironment = [Environment]::GetEnvironmentVariable($name)
        if ($fromEnvironment) {
            $values[$name] = $fromEnvironment
        } elseif ($stored.ContainsKey($name)) {
            $values[$name] = Unprotect-Key $stored[$name]
        } elseif ($ResetKeys -or -not (Test-Path $providerFile)) {
            $secure = Read-Host -AsSecureString $providerKeys[$name]
            $values[$name] = (New-Object System.Net.NetworkCredential("", $secure)).Password.Trim()
            $asked = $true
        } else {
            $values[$name] = ""
        }
    }
    if ($asked) {
        $lines = foreach ($name in $providerKeys.Keys) { "$name=$(Protect-Key $values[$name])" }
        Set-Content -Path $providerFile -Value $lines
        Write-Host "Saved, encrypted for this Windows user, to $providerFile. Change them later with -ResetKeys."
    }
    foreach ($name in $providerKeys.Keys) {
        if ($values[$name]) { Write-Host "  ${name}: set" } else { Write-Host "  ${name}: not set" }
    }

    # ------------------------------------------------------------ voice and avatar clips
    Step "Voice and avatar clips"
    if ($values["ELEVENLABS_API_KEY"]) {
        Write-Host "Prepared lines are reused; only new lines are generated (the first run takes about 2 minutes)."
        $env:ELEVENLABS_API_KEY = $values["ELEVENLABS_API_KEY"]
        if ($values["SIMLI_API_KEY"]) {
            $env:SIMLI_API_KEY = $values["SIMLI_API_KEY"]
            & $venvPy -m checkin_backend.prepare --video
        } else {
            & $venvPy -m checkin_backend.prepare
            Write-Host "No Simli key: no avatar clips, lines play as voice."
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Preparation stopped (see above). The backend still starts; lines without a clip play as voice." -ForegroundColor Yellow
        }
    } else {
        Write-Host "No ElevenLabs key: nothing to prepare (lines are captions only)."
    }

    # ------------------------------------------------------------ 4. firewall
    Step "Windows firewall"
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $isAdmin = ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $profiles = @("Private", "Domain")
    if ($AllowPublicNetwork) { $profiles += "Public" }
    $ruleName = "Check-in backend TCP $Port"
    if ($isAdmin) {
        Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort $Port `
            -Action Allow -Profile $profiles | Out-Null
        Write-Host "Inbound TCP $Port allowed on: $($profiles -join ', ')."
    } else {
        Write-Host "Not running as administrator, so no firewall rule was added." -ForegroundColor Yellow
        Write-Host "When Windows asks whether Python may communicate on networks, tick Private networks and allow." -ForegroundColor Yellow
        Write-Host "If the phone still cannot connect, run this once as administrator." -ForegroundColor Yellow
    }
    $public = Get-NetConnectionProfile -ErrorAction SilentlyContinue | Where-Object { $_.NetworkCategory -eq "Public" }
    if ($public -and -not $AllowPublicNetwork) {
        Write-Host "Windows calls this network Public ($($public.Name -join ', ')). Inbound connections are blocked there." -ForegroundColor Yellow
        Write-Host "Either set it to Private (Settings > Network & internet > Wi-Fi > this network)," -ForegroundColor Yellow
        Write-Host "or run as administrator with -AllowPublicNetwork." -ForegroundColor Yellow
    }

    # ------------------------------------------------------------ 5. what to type into the phone
    $ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
        Select-Object -ExpandProperty IPAddress
    Step "Phone settings (long-press the idle screen for 2 s)"
    foreach ($ip in $ips) {
        Write-Host "  Backend address: http://${ip}:$Port"
    }
    Write-Host "  Demo key:        $DemoKey"
    Write-Host "  Person:          p_demo"
    Write-Host ""
    Write-Host "The phone and this PC must be on the same Wi-Fi. If the event Wi-Fi keeps devices apart,"
    Write-Host "join this PC to the iPhone's Personal Hotspot and use the 172.20.10.x address above."
    Write-Host ""
    Write-Host "Start a call from another PowerShell window:  .\start-call.ps1"
    if (-not $values["ELEVENLABS_API_KEY"]) {
        Write-Host "No ELEVENLABS_API_KEY set: the avatar's lines are silent (captions only) and answers are not transcribed." -ForegroundColor Yellow
    }

    # ------------------------------------------------------------ 6. run
    Step "Settings of this run"
    if ($claudeMode -eq "off") { Write-Host "  Claude:    off (fixed-order call)" } else { Write-Host "  Claude:    $claudeMode" }
    if ($measuringProblem) { Write-Host "  Measuring: NOT ready ($Runner call)" } else { Write-Host "  Measuring: ready" }
    Write-Host "  Runner:    $Runner"
    if ($Mirrored) { Write-Host "  Camera:    mirrored" } else { Write-Host "  Camera:    not mirrored" }
    if ($BaselineNeeded -ge 1) { Write-Host "  Baseline:  needs $BaselineNeeded normal calls" } else { Write-Host "  Baseline:  needs 5 normal calls (the default)" }
    if ($Label) { Write-Host "  Label:     $Label" } else { Write-Host "  Label:     live" }

    Step "Running the backend on port $Port (Ctrl+C to stop)"
    $env:DEMO_KEY = $DemoKey
    $env:CALL_RUNNER = $Runner
    if ($claudeMode -eq "off") {
        $env:CALL_DRIVER = "fixed"
        Remove-Item -Path "env:UW_CLAUDE_BACKEND" -ErrorAction SilentlyContinue
    } else {
        $env:CALL_DRIVER = "claude"
        $env:UW_CLAUDE_BACKEND = $claudeMode
    }
    # The API key is only passed on for -Claude api, from the environment it was already in (never stored or printed).
    if ($claudeMode -ne "api") { Remove-Item -Path "env:ANTHROPIC_API_KEY" -ErrorAction SilentlyContinue }
    if ($Mirrored) { $env:UW_MIRRORED = "on" } else { Remove-Item -Path "env:UW_MIRRORED" -ErrorAction SilentlyContinue }
    if ($BaselineNeeded -ge 1) { $env:UW_BASELINE_NEEDED = "$BaselineNeeded" } else { Remove-Item -Path "env:UW_BASELINE_NEEDED" -ErrorAction SilentlyContinue }
    if ($Label) { $env:DEMO_SESSION_LABEL = $Label } else { Remove-Item -Path "env:DEMO_SESSION_LABEL" -ErrorAction SilentlyContinue }
    foreach ($name in $values.Keys) {
        if ($values[$name]) { Set-Item -Path "env:$name" -Value $values[$name] }
        else { Remove-Item -Path "env:$name" -ErrorAction SilentlyContinue }
    }
    & $venvPy -u -m uvicorn checkin_backend.app:create_app --factory --host 0.0.0.0 --port $Port
} finally {
    Pop-Location
}
