[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8767,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$webRoot = Join-Path $projectRoot 'web'
$appPath = Join-Path $webRoot 'app.py'
$dataDirectory = Join-Path $projectRoot 'data'
$metadataPath = Join-Path $dataDirectory 'server.json'
$databasePath = Join-Path $dataDirectory 'ledger.sqlite3'
$stdoutPath = Join-Path $dataDirectory 'server.stdout.log'
$stderrPath = Join-Path $dataDirectory 'server.stderr.log'
$portWasSpecified = $PSBoundParameters.ContainsKey('Port')

function Get-OwnedProcess {
    param([int]$ProcessId, [int]$ListenPort = 0, [string]$PythonExecutable)
    if ($ProcessId -le 0) { return $null }
    $candidate = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if (-not (Test-LedgerProcessOwnership -Candidate $candidate -AppPath $appPath -DatabasePath $databasePath -ListenPort $ListenPort -ExpectedPythonExecutable $PythonExecutable)) { return $null }
    return $candidate
}

function Get-ServerMetadata {
    if (-not (Test-Path -LiteralPath $metadataPath -PathType Leaf)) { return $null }
    try { return Get-Content -LiteralPath $metadataPath -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { Write-Warning '启动记录无法读取，将重新检查端口。'; return $null }
}

function Test-LedgerHealth {
    param([int]$ListenPort)
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$ListenPort/api/health" -Method Get -TimeoutSec 1 -UseBasicParsing
        return ($health.app -eq 'filebackup-ledger' -and $health.status -eq 'ok')
    } catch { return $false }
}

function Get-PortState {
    param([int]$ListenPort)
    $ownerIds = @()
    try {
        $listeners = @(Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction Stop)
        $ownerIds = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
        if ($ownerIds.Count -gt 0) { return [pscustomobject]@{ Busy = $true; OwnerIds = $ownerIds } }
    } catch {
        # On Windows without TCP ownership data, never infer ownership from health alone.
    }
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $attempt = $client.BeginConnect('127.0.0.1', $ListenPort, $null, $null)
        if ($attempt.AsyncWaitHandle.WaitOne(300)) {
            try { $client.EndConnect($attempt) } catch { }
        }
        return [pscustomobject]@{ Busy = $client.Connected; OwnerIds = $ownerIds }
    } finally { $client.Dispose() }
}

function Save-ServerMetadata {
    param([int]$ProcessId, [int]$ListenPort, [string]$PythonExecutable)
    $record = [ordered]@{
        schemaVersion = 1
        pid = $ProcessId
        port = $ListenPort
        appPath = $appPath
        pythonExecutable = $PythonExecutable
        startedAt = [DateTime]::UtcNow.ToString('o')
        url = "http://127.0.0.1:$ListenPort/"
    }
    $temporaryPath = Join-Path $dataDirectory ('server.' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        $record | ConvertTo-Json | Set-Content -LiteralPath $temporaryPath -Encoding UTF8
        Move-Item -LiteralPath $temporaryPath -Destination $metadataPath -Force
    } finally {
        if (Test-Path -LiteralPath $temporaryPath -PathType Leaf) { Remove-Item -LiteralPath $temporaryPath -Force }
    }
}

function ConvertTo-WindowsArgument {
    param([string]$Value)
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Find-Python {
    $seen = @{}
    foreach ($name in @('python.exe', 'python3.exe', 'python', 'python3')) {
        $commands = @(Get-Command -Name $name -CommandType Application -ErrorAction SilentlyContinue)
        foreach ($command in $commands) {
            if ($seen.ContainsKey($command.Source)) { continue }
            $seen[$command.Source] = $true
            $found = Test-PythonCandidate -Executable $command.Source
            if ($found) { return $found }
        }
    }
    $py = Get-Command -Name 'py.exe' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($py) {
        $found = Test-PythonCandidate -Executable $py.Source -PyLauncher
        if ($found) { return $found }
    }
    foreach ($candidate in @(
        (Join-Path $projectRoot '.venv\Scripts\python.exe'),
        (Join-Path $env:USERPROFILE 'miniconda3\python.exe'),
        (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')
    )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $found = Test-PythonCandidate -Executable $candidate
            if ($found) { return $found }
        }
    }
    throw '找不到 Python 3.10 或更高版本。请先安装 Python，或配置可用的本地 Python 运行环境，再双击“启动.cmd”。启动脚本不会联网安装软件。'
}

function Open-Ledger {
    param([int]$ListenPort)
    $url = "http://127.0.0.1:$ListenPort/"
    Write-Host "文件备份台账已就绪：$url"
    if (-not $NoBrowser) {
        try { Start-Process -FilePath $url }
        catch { Write-Warning "浏览器未自动打开，请复制上面的地址访问。" }
    }
}

function Invoke-LedgerStartup {
    if (-not (Test-Path -LiteralPath $appPath -PathType Leaf)) { throw '缺少 app.py，请保持程序文件夹完整。' }
    if (-not (Test-Path -LiteralPath (Join-Path $webRoot 'dist\index.html') -PathType Leaf)) {
        throw '缺少 dist/index.html。请使用完整发行文件夹；开发者可运行 pnpm build 生成界面。'
    }
    $requestedPort = $Port
    $metadata = Get-ServerMetadata
    if ($metadata -and $metadata.appPath -eq $appPath -and $metadata.pid -and $metadata.port) {
        $owned = Get-OwnedProcess -ProcessId ([int]$metadata.pid) -ListenPort ([int]$metadata.port) -PythonExecutable $metadata.pythonExecutable
        if ($owned) {
            $recordedPort = [int]$metadata.port
            if ($portWasSpecified -and $requestedPort -ne $recordedPort) {
                throw '本项目已在其他端口运行。请先双击“关闭.cmd”，再用 -Port 指定新端口。'
            }
            $state = Get-PortState -ListenPort $recordedPort
            if ($state.OwnerIds -contains [int]$metadata.pid -and (Test-LedgerHealth -ListenPort $recordedPort)) {
                Open-Ledger -ListenPort $recordedPort
                return
            }
            throw '本项目后台仍在运行，但未通过就绪检查。请查看 data/server.stderr.log，或先双击“关闭.cmd”再启动。'
        }
    }
    $state = Get-PortState -ListenPort $requestedPort
    if ($state.Busy) {
        foreach ($ownerId in $state.OwnerIds) {
            $owner = Get-OwnedProcess -ProcessId ([int]$ownerId) -ListenPort $requestedPort
            if ($owner -and (Test-LedgerHealth -ListenPort $requestedPort)) {
                New-Item -ItemType Directory -Path $dataDirectory -Force | Out-Null
                Save-ServerMetadata -ProcessId ([int]$ownerId) -ListenPort $requestedPort -PythonExecutable $owner.ExecutablePath
                Open-Ledger -ListenPort $requestedPort
                return
            }
        }
        throw "端口 $requestedPort 已被占用，无法确认该服务属于本项目。脚本没有停止它。请运行 powershell -NoProfile -ExecutionPolicy Bypass -File start.ps1 -Port 8768，或先自行处理端口占用。"
    }
    $python = Find-Python
    New-Item -ItemType Directory -Path $dataDirectory -Force | Out-Null
    $arguments = '-u ' + (ConvertTo-WindowsArgument $appPath) + ' --port ' + $requestedPort + ' --db ' + (ConvertTo-WindowsArgument $databasePath)
    $started = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
    try {
        Save-ServerMetadata -ProcessId $started.Id -ListenPort $requestedPort -PythonExecutable $python
        $timer = [System.Diagnostics.Stopwatch]::StartNew()
        while ($timer.Elapsed.TotalSeconds -lt 10) {
            $started.Refresh()
            if ($started.HasExited) { throw '后台进程提前退出。请查看 data/server.stderr.log 中的错误信息。' }
            if (Test-LedgerHealth -ListenPort $requestedPort) {
                $readyState = Get-PortState -ListenPort $requestedPort
                if ($readyState.OwnerIds -contains $started.Id -and (Get-OwnedProcess -ProcessId $started.Id -ListenPort $requestedPort -PythonExecutable $python)) {
                    Open-Ledger -ListenPort $requestedPort
                    return
                }
            }
            Start-Sleep -Milliseconds 250
        }
        throw '后台未在 10 秒内就绪。请查看 data/server.stderr.log，检查运行环境后重试。'
    } catch {
        $startupFailure = $_
        if (Get-OwnedProcess -ProcessId $started.Id -ListenPort $requestedPort -PythonExecutable $python) {
            Stop-Process -Id $started.Id -Force -ErrorAction SilentlyContinue
        }
        $latest = Get-ServerMetadata
        if ($latest -and $latest.appPath -eq $appPath -and [int]$latest.pid -eq $started.Id) {
            Remove-Item -LiteralPath $metadataPath -Force -ErrorAction SilentlyContinue
        }
        throw $startupFailure
    }
}

$mutex = $null
$lockTaken = $false
$exitCode = 0
try {
    . (Join-Path $PSScriptRoot 'launcher-utils.ps1')
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { $digest = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($projectRoot.ToLowerInvariant())) }
    finally { $sha.Dispose() }
    $lockName = 'Local\FileBackupLedger-' + ([System.BitConverter]::ToString($digest).Replace('-', '').Substring(0, 24))
    $mutex = New-Object System.Threading.Mutex($false, $lockName)
    try { $lockTaken = $mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $lockTaken = $true }
    if (-not $lockTaken) { throw '本项目的启动检查正在进行，请稍后重试。' }
    Invoke-LedgerStartup
} catch {
    Write-Host ('启动失败：' + $_.Exception.Message) -ForegroundColor Red
    $exitCode = 1
} finally {
    if ($lockTaken) { $mutex.ReleaseMutex() }
    if ($mutex) { $mutex.Dispose() }
}
exit $exitCode
