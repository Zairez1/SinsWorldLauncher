param([int]$ParentPid, [int]$BootloaderPid = 0, [string]$Source, [string]$Target, [string]$ExpectedSha256, [string]$ReadySignal = "", [switch]$DeleteBackup)
$ErrorActionPreference = 'Stop'
$log = Join-Path (Split-Path $Source) 'update.log'
$backup = "$Target.previous"
$backedUp = $false
try {
    $process = Get-Process -Id $ParentPid -ErrorAction SilentlyContinue
    if ($process) { Wait-Process -Id $ParentPid -Timeout 120 }
    # The onefile bootloader owns the EXE and cleans the old extraction directory.
    if ($BootloaderPid -gt 0 -and $BootloaderPid -ne $ParentPid) {
        $bootloader = Get-Process -Id $BootloaderPid -ErrorAction SilentlyContinue
        if ($bootloader) { Wait-Process -Id $BootloaderPid -Timeout 120 }
    }
    $stream = [System.IO.File]::OpenRead($Source)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $actualHash = ([System.BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
    } finally {
        $stream.Dispose()
        $algorithm.Dispose()
    }
    if ($actualHash -ne $ExpectedSha256.ToLowerInvariant()) {
        throw 'SHA256 mismatch; installation cancelled'
    }
    if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force }
    Move-Item -LiteralPath $Target -Destination $backup
    $backedUp = $true
    Move-Item -LiteralPath $Source -Destination $Target
    # Also set this here for callers that did not sanitize their environment.
    $env:PYINSTALLER_RESET_ENVIRONMENT = '1'
    if ($ReadySignal) {
        if (Test-Path -LiteralPath $ReadySignal) { Remove-Item -LiteralPath $ReadySignal -Force }
        $env:SINSWORLD_UPDATE_READY_FILE = $ReadySignal
        $env:SINSWORLD_UPDATE_EXPECTED_HASH = $ExpectedSha256.ToLowerInvariant()
    }
    $newProcess = Start-Process -FilePath $Target -WorkingDirectory (Split-Path $Target) -PassThru
    if ($DeleteBackup) {
        if (!$ReadySignal) { throw 'Missing readiness signal; old EXE is retained' }
        $ready = $false
        for ($attempt = 0; $attempt -lt 240; $attempt++) {
            if (Test-Path -LiteralPath $ReadySignal) {
                try {
                    $ack = Get-Content -LiteralPath $ReadySignal -Raw | ConvertFrom-Json
                    if ($ack.sha256 -eq $ExpectedSha256.ToLowerInvariant()) { $ready = $true; break }
                } catch { }
            }
            Start-Sleep -Milliseconds 250
        }
        if (!$ready) {
            Stop-Process -Id $newProcess.Id -Force -ErrorAction SilentlyContinue
            throw 'New launcher did not become ready; restoring old EXE'
        }
        if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force }
        Remove-Item -LiteralPath $ReadySignal -Force -ErrorAction SilentlyContinue
    }
    'Update installed successfully' | Set-Content -LiteralPath $log
} catch {
    $_ | Out-String | Set-Content -LiteralPath $log
    if ($backedUp -and (Test-Path -LiteralPath $backup)) {
        if (Test-Path -LiteralPath $Target) { Remove-Item -LiteralPath $Target -Force }
        Move-Item -LiteralPath $backup -Destination $Target -Force
    }
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show("Не удалось установить обновление. Предыдущая версия сохранена. Журнал: $log", 'SinsWorld') | Out-Null
    exit 1
}
