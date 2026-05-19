param(
  [string]$ProjectRoot = '',
  [string]$TestsDir = 'tests',
  [string]$Pattern = 'test_*.py',
  [int]$BatchSize = 25,
  [int]$HeartbeatSeconds = 15,
  [int]$StartBatch = 1,
  [int]$EndBatch = 0,
  [string]$LogDir = '',
  [string]$RepoPythonExecutable = '',
  [switch]$DryRun,
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$PytestArgs = @()
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'shortline-wrapper-common.ps1')

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
  $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
} else {
  $ProjectRoot = (Resolve-Path $ProjectRoot).Path
}

$runnerScript = Join-Path $ProjectRoot 'scripts\run_pytest_batches.py'
$resolvedRepoPythonExecutable = Resolve-RepoPythonExecutable -RequestedExecutable $RepoPythonExecutable

foreach ($requiredPath in @($runnerScript, $resolvedRepoPythonExecutable)) {
  if (!(Test-Path $requiredPath)) {
    throw "Required path not found: $requiredPath"
  }
}

$runnerArgs = @(
  $runnerScript,
  '--project-root', $ProjectRoot,
  '--tests-dir', $TestsDir,
  '--pattern', $Pattern,
  '--batch-size', $BatchSize,
  '--heartbeat-seconds', $HeartbeatSeconds,
  '--start-batch', $StartBatch
)

if ($EndBatch -gt 0) {
  $runnerArgs += @('--end-batch', $EndBatch)
}

if (![string]::IsNullOrWhiteSpace($LogDir)) {
  $runnerArgs += @('--log-dir', $LogDir)
}

if ($PytestArgs.Count -gt 0) {
  $runnerArgs += '--'
  $runnerArgs += $PytestArgs
}

if ($DryRun) {
  Write-Host 'Dry run only. Command not executed.'
  Write-Host "repo_python=$resolvedRepoPythonExecutable"
  Write-Host "$resolvedRepoPythonExecutable $($runnerArgs -join ' ')"
  return
}

& $resolvedRepoPythonExecutable @runnerArgs
