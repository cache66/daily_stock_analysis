function Resolve-RepoPythonExecutable {
  param(
    [string]$RequestedExecutable
  )

  if (![string]::IsNullOrWhiteSpace($RequestedExecutable)) {
    if (Test-Path $RequestedExecutable) {
      return (Resolve-Path $RequestedExecutable).Path
    }
    $requestedCommand = Get-Command $RequestedExecutable -ErrorAction SilentlyContinue
    if ($null -ne $requestedCommand -and ![string]::IsNullOrWhiteSpace($requestedCommand.Source)) {
      return $requestedCommand.Source
    }
    return $RequestedExecutable
  }

  try {
    $resolved = (& py -3.10 -c "import sys; print(sys.executable)" 2>$null)
    if ($LASTEXITCODE -eq 0 -and ![string]::IsNullOrWhiteSpace($resolved)) {
      return $resolved.Trim()
    }
  } catch {
  }

  $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
  if ($null -ne $pythonCommand -and ![string]::IsNullOrWhiteSpace($pythonCommand.Source)) {
    return $pythonCommand.Source
  }

  return 'python'
}
