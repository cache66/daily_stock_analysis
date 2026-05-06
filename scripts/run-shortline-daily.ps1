param(
  [string]$TradeDate = (Get-Date -Format 'yyyy-MM-dd'),
  [int]$TopN = 5,
  [string]$RepoPythonExecutable = '',
  [string]$WonderTraderPythonExecutable = '',
  [string]$FinGeniusPythonExecutable = 'D:\bb\FinGenius\.venv311\Scripts\python.exe',
  [string]$ExplainCachePath = '',
  [string]$TrackingHistoryPath = '',
  [string]$LogLevel = 'INFO',
  [string]$RunId = '',
  [string]$OutputDir = '',
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'shortline-wrapper-common.ps1')

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$hubScript = Join-Path $projectRoot 'scripts\run_shortline_hub.py'
$trendLeaderScript = Join-Path $projectRoot 'scripts\select_trend_leader_candidates.py'
$earningsScript = Join-Path $projectRoot 'scripts\select_earnings_surprise_candidates.py'
$commodityScript = Join-Path $projectRoot 'scripts\collect_commodity_beneficiary_snapshots.py'
$wtScriptPath = 'D:\bb\WonderTrader\bridge\wt_export_candidates.py'
$fgScriptPath = 'D:\bb\FinGenius\bridge\fg_explain_candidate.py'
$wtWorkdir = 'D:\bb\WonderTrader'
$fgWorkdir = 'D:\bb\FinGenius'
$resolvedRepoPythonExecutable = Resolve-RepoPythonExecutable -RequestedExecutable $RepoPythonExecutable
$resolvedWonderTraderPythonExecutable = $WonderTraderPythonExecutable

if ([string]::IsNullOrWhiteSpace($resolvedWonderTraderPythonExecutable)) {
  $resolvedWonderTraderPythonExecutable = $resolvedRepoPythonExecutable
}

$dateToken = $TradeDate.Replace('-', '')
if ([string]::IsNullOrWhiteSpace($RunId)) {
  $RunId = "shortline_daily_$dateToken"
}
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
  $OutputDir = Join-Path $projectRoot "data\manual_runs\$RunId"
}
if ([string]::IsNullOrWhiteSpace($ExplainCachePath)) {
  $ExplainCachePath = Join-Path $projectRoot "data\runtime\shortline_hub\explain_cache\shortline_explain_cache.json"
}
if ([string]::IsNullOrWhiteSpace($TrackingHistoryPath)) {
  $TrackingHistoryPath = Join-Path $projectRoot "data\runtime\shortline_hub\tracking\shortline_tracking_history.json"
}

$wtRuntimeDir = Join-Path $projectRoot "data\runtime\shortline_hub\wt_$RunId"
$fgRuntimeDir = Join-Path $projectRoot "data\runtime\shortline_hub\fg_$RunId"

foreach ($requiredPath in @($hubScript, $trendLeaderScript, $earningsScript, $commodityScript, $wtScriptPath, $fgScriptPath, $FinGeniusPythonExecutable, $resolvedRepoPythonExecutable, $resolvedWonderTraderPythonExecutable)) {
  if (!(Test-Path $requiredPath)) {
    throw "Required path not found: $requiredPath"
  }
}

Write-Host "Running shortline daily prep + replay..."
Write-Host "trade_date=$TradeDate top_n=$TopN run_id=$RunId"
$trendArgs = @(
  '--snapshot-date', $TradeDate,
  '--signal-type', 'trend_leader_unified'
)
$earningsArgs = @(
  '--snapshot-date', $TradeDate,
  '--strategy-profile', 'balanced'
)
$commodityArgs = @(
  '--snapshot-date', $TradeDate,
  '--commodities', 'optical_fiber,memory,hard_disk'
)
$hubArgs = @(
  '--mode', 'process',
  '--trade-date', $TradeDate,
  '--top-n', $TopN,
  '--run-id', $RunId,
  '--output-dir', $OutputDir,
  '--wt-python-executable', $resolvedWonderTraderPythonExecutable,
  '--wt-script-path', $wtScriptPath,
  '--wt-workdir', $wtWorkdir,
  '--wt-runtime-dir', $wtRuntimeDir,
  '--wt-source-mode', 'prefer_real_engine',
  '--fg-python-executable', $FinGeniusPythonExecutable,
  '--fg-script-path', $fgScriptPath,
  '--fg-workdir', $fgWorkdir,
  '--fg-runtime-dir', $fgRuntimeDir,
  '--enable-explain-cache',
  '--explain-cache-path', $ExplainCachePath,
  '--explain-cache-mode', 'light',
  '--tracking-history-path', $TrackingHistoryPath,
  '--log-level', $LogLevel
)

if ($DryRun) {
  Write-Host "Dry run only. Command not executed."
  Write-Host "repo_python=$resolvedRepoPythonExecutable"
  Write-Host "wt_python=$resolvedWonderTraderPythonExecutable"
  Write-Host "fg_python=$FinGeniusPythonExecutable"
  Write-Host "tracking_history_path=$TrackingHistoryPath"
  Write-Host "$resolvedRepoPythonExecutable $trendLeaderScript $($trendArgs -join ' ')"
  Write-Host "$resolvedRepoPythonExecutable $earningsScript $($earningsArgs -join ' ')"
  Write-Host "$resolvedRepoPythonExecutable $commodityScript $($commodityArgs -join ' ')"
  Write-Host "$resolvedRepoPythonExecutable $hubScript $($hubArgs -join ' ')"
  return
}

& $resolvedRepoPythonExecutable $trendLeaderScript @trendArgs
& $resolvedRepoPythonExecutable $earningsScript @earningsArgs
& $resolvedRepoPythonExecutable $commodityScript @commodityArgs
& $resolvedRepoPythonExecutable $hubScript @hubArgs

Write-Host "Shortline daily replay finished."
Write-Host "output_dir=$OutputDir"
