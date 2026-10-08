$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path $PSScriptRoot -Parent
$pythonExecutable = Join-Path $projectDirectory '.venv\Scripts\python.exe'
$reviewDirectory = Join-Path $env:LOCALAPPDATA 'ElektroViennaKnowledge\review'
$launchSettings = Join-Path $reviewDirectory 'launch.json'
if (-not (Test-Path -LiteralPath $launchSettings)) { throw 'Keine lokale Review-Ansicht eingerichtet.' }
$runtimeInfo = Join-Path $reviewDirectory 'service.json'
if (Test-Path -LiteralPath $runtimeInfo) {
    try {
        $existingService = Get-Content -LiteralPath $runtimeInfo -Raw | ConvertFrom-Json
        if ($existingService.url -match '^http://127\.0\.0\.1:[0-9]+/s/[A-Za-z0-9_-]+/$') {
            $probe = Invoke-WebRequest -Uri ($existingService.url + 'draft') -UseBasicParsing -TimeoutSec 2
            if ($probe.StatusCode -eq 200) { Start-Process $existingService.url; exit 0 }
        }
    } catch { }
}
Start-Process -FilePath $pythonExecutable -ArgumentList @('-m','elektro_vienna','review-serve','--open-browser') -WorkingDirectory $projectDirectory -WindowStyle Hidden -RedirectStandardOutput $runtimeInfo -RedirectStandardError (Join-Path $reviewDirectory 'service-error.log')
