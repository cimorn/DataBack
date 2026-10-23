# Shared ownership checks for startup, readiness cleanup and shutdown.
# Parse Windows argv rather than searching for the script in arbitrary arguments.
if (-not ('FileBackup.Launcher.NativeCommandLine' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
namespace FileBackup.Launcher {
    public static class NativeCommandLine {
        [DllImport("shell32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern IntPtr CommandLineToArgvW(string commandLine, out int count);
        [DllImport("kernel32.dll")]
        private static extern IntPtr LocalFree(IntPtr memory);
        public static string[] Split(string commandLine) {
            if (String.IsNullOrWhiteSpace(commandLine)) { return new string[0]; }
            int count;
            IntPtr memory = CommandLineToArgvW(commandLine, out count);
            if (memory == IntPtr.Zero) { throw new Win32Exception(); }
            try {
                string[] result = new string[count];
                for (int index = 0; index < count; index++) {
                    result[index] = Marshal.PtrToStringUni(Marshal.ReadIntPtr(memory, index * IntPtr.Size));
                }
                return result;
            } finally { LocalFree(memory); }
        }
    }
}
'@
}

function ConvertFrom-WindowsCommandLine {
    param([string]$CommandLine)
    return [FileBackup.Launcher.NativeCommandLine]::Split($CommandLine)
}

function Get-CanonicalAbsolutePath {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $null }
    if ($Path -notmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+[\\/])') { return $null }
    try { return [System.IO.Path]::GetFullPath($Path.Replace('/', '\')) }
    catch { return $null }
}

function Test-PythonCandidate {
    param([string]$Executable, [switch]$PyLauncher)
    if ([string]::IsNullOrWhiteSpace($Executable)) { return $null }
    if ($Executable -match '\\WindowsApps\\') { return $null }
    $probe = New-Object System.Diagnostics.Process
    try {
        $probe.StartInfo.FileName = $Executable
        $prefix = if ($PyLauncher) { '-3 ' } else { '' }
        $probe.StartInfo.Arguments = $prefix + '-c "import sys; sys.exit(1) if sys.version_info < (3, 10) else print(sys.executable)"'
        $probe.StartInfo.UseShellExecute = $false
        $probe.StartInfo.CreateNoWindow = $true
        $probe.StartInfo.RedirectStandardOutput = $true
        $probe.StartInfo.RedirectStandardError = $true
        $probe.StartInfo.StandardOutputEncoding = [System.Text.Encoding]::UTF8
        $probe.StartInfo.StandardErrorEncoding = [System.Text.Encoding]::UTF8
        $probe.StartInfo.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
        if (-not $probe.Start()) { return $null }
        if (-not $probe.WaitForExit(4000)) { $probe.Kill(); return $null }
        if ($probe.ExitCode -ne 0) { return $null }
        $resolvedExecutable = $probe.StandardOutput.ReadToEnd().Trim()
        if (Test-Path -LiteralPath $resolvedExecutable -PathType Leaf) { return $resolvedExecutable }
    } catch { return $null }
    finally { $probe.Dispose() }
    return $null
}

$script:ledgerVerifiedPythonExecutables = @{}

function Test-LedgerProcessOwnership {
    param(
        [object]$Candidate,
        [string]$AppPath,
        [string]$DatabasePath,
        [int]$ListenPort = 0,
        [string]$ExpectedPythonExecutable
    )
    if ($null -eq $Candidate) { return $false }
    $actualExecutable = Get-CanonicalAbsolutePath -Path $Candidate.ExecutablePath
    $expectedApp = Get-CanonicalAbsolutePath -Path $AppPath
    $expectedDatabase = Get-CanonicalAbsolutePath -Path $DatabasePath
    if (-not $actualExecutable -or -not $expectedApp -or -not $expectedDatabase) { return $false }
    if ([System.IO.Path]::GetFileName($actualExecutable) -notmatch '^python(?:\d+(?:\.\d+)*)?\.exe$') { return $false }
    try { $arguments = @(ConvertFrom-WindowsCommandLine -CommandLine $Candidate.CommandLine) }
    catch { return $false }
    # Exactly the invocation produced by start.ps1; app.py must be the script entry.
    if ($arguments.Count -ne 7 -or $arguments[1] -cne '-u' -or $arguments[3] -cne '--port' -or $arguments[5] -cne '--db') { return $false }
    $invokedExecutable = Get-CanonicalAbsolutePath -Path $arguments[0]
    $invokedApp = Get-CanonicalAbsolutePath -Path $arguments[2]
    $invokedDatabase = Get-CanonicalAbsolutePath -Path $arguments[6]
    if ($invokedExecutable -ne $actualExecutable -or $invokedApp -ne $expectedApp -or $invokedDatabase -ne $expectedDatabase) { return $false }
    $invokedPort = 0
    if (-not [int]::TryParse($arguments[4], [ref]$invokedPort) -or $invokedPort -lt 1 -or $invokedPort -gt 65535) { return $false }
    if ($ListenPort -ne 0 -and $invokedPort -ne $ListenPort) { return $false }
    if (-not [string]::IsNullOrWhiteSpace($ExpectedPythonExecutable)) {
        $expectedExecutable = Get-CanonicalAbsolutePath -Path $ExpectedPythonExecutable
        if (-not $expectedExecutable -or $expectedExecutable -ne $actualExecutable) { return $false }
    }
    # ExecutablePath must identify a working Python interpreter, not an editor or
    # merely an executable whose name looks like Python. Cache only this run's probe.
    $cacheKey = $actualExecutable.ToLowerInvariant()
    if (-not $script:ledgerVerifiedPythonExecutables.ContainsKey($cacheKey)) {
        $confirmedExecutable = Get-CanonicalAbsolutePath -Path (Test-PythonCandidate -Executable $actualExecutable)
        if ($confirmedExecutable -ne $actualExecutable) { return $false }
        $script:ledgerVerifiedPythonExecutables[$cacheKey] = $true
    }
    return $true
}
