#requires -Version 5.1
<#
  Conflux 协作台一键启动与桌面快捷方式生成脚本
  用法：
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\launch-conflux.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\launch-conflux.ps1 -Demo
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\launch-conflux.ps1 -CreateShortcutOnly
#>
param(
    [switch]$Demo,
    [switch]$NoShortcut,
    [switch]$NoBrowser,
    [switch]$CreateShortcutOnly,
    [int]$Port = 0
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$HubScript = Join-Path $ProjectRoot 'scripts\hub.py'
$IconPath = Join-Path $ProjectRoot 'data\hub\conflux.ico'
$ShortcutName = if ($Demo) { 'Conflux 协作台 (演示模式).lnk' } else { 'Conflux 协作台.lnk' }

function Ensure-DesktopShortcut {
    try {
        $desktopDir = [Environment]::GetFolderPath('Desktop')
        if (-not $desktopDir -or -not (Test-Path -LiteralPath $desktopDir)) {
            return $null
        }
        $lnkPath = Join-Path $desktopDir $ShortcutName
        $targetCmd = Join-Path $ProjectRoot '启动Conflux协作台.cmd'
        if (-not (Test-Path -LiteralPath $targetCmd)) {
            $targetCmd = Join-Path $ProjectRoot 'start-conflux.cmd'
        }
        $wsh = New-Object -ComObject WScript.Shell
        $sc = $wsh.CreateShortcut($lnkPath)
        if (Test-Path -LiteralPath $targetCmd) {
            $sc.TargetPath = $targetCmd
            $sc.Arguments = if ($Demo) { '--demo' } else { '' }
        } else {
            $sc.TargetPath = 'powershell.exe'
            $sc.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" + $(if ($Demo) { ' -Demo' } else { '' })
        }
        $sc.WorkingDirectory = $ProjectRoot
        $sc.Description = 'Conflux 多智能体协作台 - 一键启动'
        if (Test-Path -LiteralPath $IconPath) {
            $sc.IconLocation = "$IconPath,0"
        }
        $sc.Save()
        return $lnkPath
    } catch {
        Write-Warning "生成桌面快捷方式失败: $($_.Exception.Message)"
        return $null
    }
}

if (-not $NoShortcut) {
    $lnk = Ensure-DesktopShortcut
    if ($lnk) {
        Write-Host "[Conflux] 已同步桌面图标: $lnk" -ForegroundColor Green
    }
}

if ($CreateShortcutOnly) {
    exit 0
}

function Find-PythonExe {
    if ($env:CONFLUX_PYTHON -and (Test-Path -LiteralPath $env:CONFLUX_PYTHON)) {
        return $env:CONFLUX_PYTHON
    }
    $venvPy = Join-Path $ProjectRoot 'stt\venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPy) {
        return $venvPy
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) {
        return $cmd.Source
    }
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher -and $pyLauncher.Source) {
        return $pyLauncher.Source
    }
    throw "未找到 Python 解释器（需要 Python 3.11+）。请先安装 Python 并加入 PATH。"
}

$effectivePort = if ($Port -gt 0) {
    $Port
} elseif ($env:CONFLUX_PORT -and ($env:CONFLUX_PORT -as [int])) {
    [int]$env:CONFLUX_PORT
} else {
    8777
}

$hubUrl = "http://127.0.0.1:$effectivePort"
$statusUrl = "$hubUrl/api/status"

function Test-HubAlive {
    try {
        $resp = Invoke-WebRequest -Uri $statusUrl -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
        return ($resp.StatusCode -eq 200)
    } catch {
        return $false
    }
}

if (Test-HubAlive) {
    Write-Host "[Conflux] 协作台已在运行: $hubUrl" -ForegroundColor Cyan
} else {
    $pyExe = Find-PythonExe
    Write-Host "[Conflux] 正在启动协作台服务 ($hubUrl) ..." -ForegroundColor Yellow
    $argList = @("`"$HubScript`"")
    if ($Demo) {
        $argList += '--demo'
    }
    $dataHubDir = Join-Path $ProjectRoot 'data\hub'
    New-Item -ItemType Directory -Force $dataHubDir | Out-Null
    $stdoutLog = Join-Path $dataHubDir 'stdout.log'
    $stderrLog = Join-Path $dataHubDir 'stderr.log'

    $oldPort = $env:CONFLUX_PORT
    try {
        $env:CONFLUX_PORT = [string]$effectivePort
        Start-Process -FilePath $pyExe -ArgumentList ($argList -join ' ') `
            -WorkingDirectory $ProjectRoot -WindowStyle Hidden `
            -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog | Out-Null
    } finally {
        if ($null -eq $oldPort) {
            Remove-Item Env:\CONFLUX_PORT -ErrorAction SilentlyContinue
        } else {
            $env:CONFLUX_PORT = $oldPort
        }
    }

    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 400
        if (Test-HubAlive) {
            break
        }
    }
    if (Test-HubAlive) {
        Write-Host "[Conflux] 协作台启动成功: $hubUrl" -ForegroundColor Green
    } else {
        Write-Warning "[Conflux] 服务启动超时，请检查日志: $stderrLog"
    }
}

if (-not $NoBrowser) {
    Start-Process $hubUrl | Out-Null
}
