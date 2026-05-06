param(
  [string]$BaselineTradeDate = '2026-04-29',
  [string]$TradeDate = '2026-04-30',
  [int]$TopN = 5,
  [string]$RepoPythonExecutable = '',
  [string]$WonderTraderPythonExecutable = '',
  [string]$FinGeniusPythonExecutable = 'D:\bb\FinGenius\.venv311\Scripts\python.exe',
  [string]$ExplainCachePath = '',
  [string]$TrackingHistoryPath = '',
  [string]$OutputRoot = '',
  [string]$LogLevel = 'INFO',
  [switch]$SkipShortlinePersistSnapshot,
  [switch]$SkipFastReviewPersistSnapshots,
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'shortline-wrapper-common.ps1')

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$bundleScript = Join-Path $PSScriptRoot 'run-shortline-review-bundle.ps1'
$resolvedRepoPythonExecutable = Resolve-RepoPythonExecutable -RequestedExecutable $RepoPythonExecutable
$resolvedWonderTraderPythonExecutable = $WonderTraderPythonExecutable

if ([string]::IsNullOrWhiteSpace($resolvedWonderTraderPythonExecutable)) {
  $resolvedWonderTraderPythonExecutable = $resolvedRepoPythonExecutable
}

if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
  $OutputRoot = Join-Path $projectRoot "data\manual_runs\shortline_replay_validation_${BaselineTradeDate}_${TradeDate}"
}
if ([string]::IsNullOrWhiteSpace($ExplainCachePath)) {
  $ExplainCachePath = Join-Path $projectRoot "data\runtime\shortline_hub\explain_cache\validation_replay_${BaselineTradeDate}_${TradeDate}.json"
}
if ([string]::IsNullOrWhiteSpace($TrackingHistoryPath)) {
  $TrackingHistoryPath = Join-Path $projectRoot "data\runtime\shortline_hub\tracking\validation_replay_${BaselineTradeDate}_${TradeDate}.json"
}

foreach ($requiredPath in @($bundleScript, $FinGeniusPythonExecutable, $resolvedRepoPythonExecutable, $resolvedWonderTraderPythonExecutable)) {
  if (!(Test-Path $requiredPath)) {
    throw "Required path not found: $requiredPath"
  }
}

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

function Invoke-ReplayPhase {
  param(
    [string]$PhaseName,
    [string]$PhaseTradeDate,
    [string]$PhaseRunId,
    [string]$PhaseOutputDir
  )

  $bundleParams = @{
    TradeDate = $PhaseTradeDate
    TopN = $TopN
    RunId = $PhaseRunId
    OutputDir = $PhaseOutputDir
    RepoPythonExecutable = $resolvedRepoPythonExecutable
    WonderTraderPythonExecutable = $resolvedWonderTraderPythonExecutable
    FinGeniusPythonExecutable = $FinGeniusPythonExecutable
    ExplainCachePath = $ExplainCachePath
    TrackingHistoryPath = $TrackingHistoryPath
    LogLevel = $LogLevel
  }

  if ($SkipShortlinePersistSnapshot) {
    $bundleParams.SkipShortlinePersistSnapshot = $true
  }

  if ($SkipFastReviewPersistSnapshots) {
    $bundleParams.SkipFastReviewPersistSnapshots = $true
  }

  if ($DryRun) {
    $bundleParams.DryRun = $true
  }

  Write-Host ("[{0}] trade_date={1} output_dir={2}" -f $PhaseName, $PhaseTradeDate, $PhaseOutputDir)
  & $bundleScript @bundleParams
}

$baselineOutputDir = Join-Path $OutputRoot 'baseline'
$coldOutputDir = Join-Path $OutputRoot 'cold'
$warmOutputDir = Join-Path $OutputRoot 'warm'

Invoke-ReplayPhase -PhaseName 'baseline' -PhaseTradeDate $BaselineTradeDate -PhaseRunId "shortline_replay_validation_${BaselineTradeDate}_baseline" -PhaseOutputDir $baselineOutputDir
Invoke-ReplayPhase -PhaseName 'cold' -PhaseTradeDate $TradeDate -PhaseRunId "shortline_replay_validation_${TradeDate}_cold" -PhaseOutputDir $coldOutputDir
Invoke-ReplayPhase -PhaseName 'warm' -PhaseTradeDate $TradeDate -PhaseRunId "shortline_replay_validation_${TradeDate}_warm" -PhaseOutputDir $warmOutputDir
