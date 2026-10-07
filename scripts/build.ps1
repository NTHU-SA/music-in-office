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
    & dotnet run --project .\tests\launcher\LauncherTests.csproj -c Release --verbosity quiet
    if ($LASTEXITCODE -ne 0) { throw "Launcher tests failed." }
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
    $Payload = Join-Path $Root "dist\OfficeMusicPayload.zip"
    if (Test-Path -LiteralPath $Payload) { Remove-Item -LiteralPath $Payload -Force }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory($Output, $Payload)
    $LauncherOutput = Join-Path $Root "dist\Launcher"
    if (Test-Path -LiteralPath $LauncherOutput) {
        Remove-Item -LiteralPath $LauncherOutput -Recurse -Force
    }
    & dotnet publish .\OfficeMusicLauncher\OfficeMusicLauncher.csproj -c Release `
        -o $LauncherOutput --verbosity quiet
    if ($LASTEXITCODE -ne 0) { throw "Single-file launcher publish failed." }
    $Files = @(Get-ChildItem -LiteralPath $LauncherOutput -File -Recurse)
    if ($Files.Count -ne 1 -or $Files[0].Name -ne "OfficeMusicBot.exe") {
        throw "The launcher publish must contain exactly one EXE."
    }
    $Executable = Join-Path $Root "dist\OfficeMusicBot.exe"
    Copy-Item -LiteralPath $Files[0].FullName -Destination $Executable -Force
    $Check = Start-Process -FilePath $Executable -ArgumentList "--check" `
        -PassThru -Wait -RedirectStandardOutput (Join-Path $Root "dist\launcher-check.log") `
        -RedirectStandardError (Join-Path $Root "dist\launcher-check-error.log")
    if ($Check.ExitCode -ne 0) {
        Get-Content -LiteralPath (Join-Path $Root "dist\launcher-check-error.log")
        throw "Single-file extraction and engine startup check failed."
    }
    Get-Content -LiteralPath (Join-Path $Root "dist\launcher-check.log")
    Write-Host "Single-file distribution: $Executable"
}
finally {
    Pop-Location
}
