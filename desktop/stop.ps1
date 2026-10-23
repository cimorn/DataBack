[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$webRoot = Join-Path $projectRoot 'web'
$appPath = Join-Path $webRoot 'app.py'
$databasePath = Join-Path $projectRoot 'data\ledger.sqlite3'
$metadataPath = Join-Path $projectRoot 'data\server.json'
$exitCode = 0

try {
    . (Join-Path $PSScriptRoot 'launcher-utils.ps1')
    if (-not (Test-Path -LiteralPath $metadataPath -PathType Leaf)) {
        Write-Host '没有本项目的后台启动记录。'
    } else {
        try { $metadata = Get-Content -LiteralPath $metadataPath -Raw -Encoding UTF8 | ConvertFrom-Json }
        catch { throw '无法读取 data/server.json，未停止任何进程。请检查启动记录。' }
        if ($metadata.appPath -ne $appPath -or -not $metadata.pid) {
            throw '启动记录不属于当前程序文件夹，未停止任何进程。移动文件夹前请先在原位置关闭后台。'
        }
        $processId = [int]$metadata.pid
        if ($processId -le 0) { throw '启动记录中的 PID 无效，未停止任何进程。' }
        $candidate = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
        if ($null -eq $candidate) {
            Remove-Item -LiteralPath $metadataPath -Force
            Write-Host '后台已经关闭，已清理旧启动记录。'
        } else {
            if (-not (Test-LedgerProcessOwnership -Candidate $candidate -AppPath $appPath -DatabasePath $databasePath -ListenPort ([int]$metadata.port) -ExpectedPythonExecutable $metadata.pythonExecutable)) {
                throw '启动记录的 PID 对应其他进程，未停止它。请重新启动本项目以更新记录。'
            }
            Stop-Process -Id $processId -Force -ErrorAction Stop
            Remove-Item -LiteralPath $metadataPath -Force
            Write-Host '文件备份台账后台已关闭。数据保存在 data/ledger.sqlite3。'
        }
    }
} catch {
    Write-Host ('关闭失败：' + $_.Exception.Message) -ForegroundColor Red
    $exitCode = 1
}
exit $exitCode
