param([int]$ParentPid, [string]$Source, [string]$Target, [string]$ExpectedSha256)
$ErrorActionPreference = 'Stop'
$log = Join-Path (Split-Path $Source) 'update.log'
$backup = "$Target.previous"
$backedUp = $false
try {
    $process = Get-Process -Id $ParentPid -ErrorAction SilentlyContinue
    if ($process) { Wait-Process -Id $ParentPid -Timeout 120 }
    if ((Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash.ToLower() -ne $ExpectedSha256.ToLower()) {
        throw 'SHA256 mismatch; installation cancelled'
    }
    if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force }
    Move-Item -LiteralPath $Target -Destination $backup
    $backedUp = $true
    Move-Item -LiteralPath $Source -Destination $Target
    Start-Process -FilePath $Target -WorkingDirectory (Split-Path $Target)
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
