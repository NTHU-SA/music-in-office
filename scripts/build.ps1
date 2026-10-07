$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Missing .venv. Run: uv venv .venv; uv pip install --python .venv\Scripts\python.exe -e '.[dev]'"
}
Push-Location $Root
try {
    & $Python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Tests failed." }
    & $Python -m ruff check src tests
    if ($LASTEXITCODE -ne 0) { throw "Lint failed." }
    & $Python -m PyInstaller --noconfirm --clean OfficeMusicBot.spec
    if ($LASTEXITCODE -ne 0) { throw "Executable build failed." }
    $Smoke = Start-Process -FilePath (Join-Path $Root "dist\OfficeMusicEngine.exe") `
        -ArgumentList "--version" -PassThru -Wait
    if ($Smoke.ExitCode -ne 0) { throw "Executable startup check failed." }
    $Output = Join-Path $Root "dist\OfficeMusicDesktop"
    if (Test-Path -LiteralPath $Output) {
        Remove-Item -LiteralPath $Output -Recurse -Force
    }
    & dotnet publish .\OfficeMusicDesktop\OfficeMusicDesktop.csproj -c Release -r win-x64 `
        -p:Platform=x64 -p:Portable=true -o $Output --verbosity quiet
    if ($LASTEXITCODE -ne 0) { throw "Native WinUI publish failed." }
    foreach ($Required in @("OfficeMusicDesktop.exe", "OfficeMusicDesktop.pri", "App.xbf",
                            "MainPage.xbf", "MainWindow.xbf", "Assets\AppIcon.ico",
                            "Backend\OfficeMusicEngine.exe")) {
        if (-not (Test-Path (Join-Path $Output $Required))) {
            throw "Portable publish is incomplete: $Required"
        }
    }
    $Archive = Join-Path $Root "dist\OfficeMusicBot-win-x64.zip"
    Compress-Archive -Path $Output -DestinationPath $Archive -Force
    Write-Host "Launch: $(Join-Path $Output 'OfficeMusicDesktop.exe')"
    Write-Host "Portable distribution: $Archive"
}
finally {
    Pop-Location
}
