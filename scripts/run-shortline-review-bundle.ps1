param(
  [string]$TradeDate = (Get-Date -Format 'yyyy-MM-dd'),
  [int]$TopN = 3,
  [string]$RepoPythonExecutable = '',
  [string]$WonderTraderPythonExecutable = '',
  [string]$FinGeniusPythonExecutable = 'D:\bb\FinGenius\.venv311\Scripts\python.exe',
  [string]$ExplainCachePath = '',
  [string]$TrackingHistoryPath = '',
  [string]$LogLevel = 'INFO',
  [string]$RunId = '',
  [string]$OutputDir = '',
  [switch]$SkipShortlinePersistSnapshot,
  [switch]$SkipFastReviewPersistSnapshots,
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'shortline-wrapper-common.ps1')

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$bundleScript = Join-Path $projectRoot 'scripts\run_shortline_review_bundle.py'
$wtScriptPath = 'D:\bb\WonderTrader\bridge\wt_export_candidates.py'
$fgScriptPath = 'D:\bb\FinGenius\bridge\fg_explain_candidate.py'
$wtWorkdir = 'D:\bb\WonderTrader'
$fgWorkdir = 'D:\bb\FinGenius'
$resolvedRepoPythonExecutable = Resolve-RepoPythonExecutable -RequestedExecutable $RepoPythonExecutable
$resolvedWonderTraderPythonExecutable = $WonderTraderPythonExecutable

if ([string]::IsNullOrWhiteSpace($resolvedWonderTraderPythonExecutable)) {
  $resolvedWonderTraderPythonExecutable = $resolvedRepoPythonExecutable
}

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
  $OutputDir = Join-Path $projectRoot 'data\manual_runs\shortline_review_bundle_latest'
}
if ([string]::IsNullOrWhiteSpace($ExplainCachePath)) {
  $ExplainCachePath = Join-Path $projectRoot 'data\runtime\shortline_hub\explain_cache\shortline_explain_cache.json'
}
if ([string]::IsNullOrWhiteSpace($TrackingHistoryPath)) {
  $TrackingHistoryPath = Join-Path $projectRoot 'data\runtime\shortline_hub\tracking\shortline_tracking_history.json'
}

foreach ($requiredPath in @($bundleScript, $wtScriptPath, $fgScriptPath, $FinGeniusPythonExecutable, $resolvedRepoPythonExecutable, $resolvedWonderTraderPythonExecutable)) {
  if (!(Test-Path $requiredPath)) {
    throw "Required path not found: $requiredPath"
  }
}

$bundleArgs = @(
  '--trade-date', $TradeDate,
  '--top-n', $TopN,
  '--repo-python-executable', $resolvedRepoPythonExecutable,
  '--wt-python-executable', $resolvedWonderTraderPythonExecutable,
  '--fg-python-executable', $FinGeniusPythonExecutable,
  '--wt-script-path', $wtScriptPath,
  '--fg-script-path', $fgScriptPath,
  '--wt-workdir', $wtWorkdir,
  '--fg-workdir', $fgWorkdir,
  '--wt-source-mode', 'prefer_real_engine',
  '--output-dir', $OutputDir,
  '--enable-explain-cache',
  '--explain-cache-path', $ExplainCachePath,
  '--explain-cache-mode', 'light',
  '--tracking-history-path', $TrackingHistoryPath,
  '--log-level', $LogLevel
)

if (![string]::IsNullOrWhiteSpace($RunId)) {
  $bundleArgs += @('--run-id', $RunId)
}

if ($SkipShortlinePersistSnapshot) {
  $bundleArgs += '--skip-persist-snapshot'
}

if ($SkipFastReviewPersistSnapshots) {
  $bundleArgs += '--skip-fast-review-persist-snapshots'
}

if ($DryRun) {
  $bundleArgs += '--dry-run'
}

& $resolvedRepoPythonExecutable $bundleScript @bundleArgs
