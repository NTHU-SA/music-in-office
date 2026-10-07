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
    & node --test tests\extension\*.test.cjs
    if ($LASTEXITCODE -ne 0) { throw "Extension tests failed." }
    & $Python -m PyInstaller --noconfirm --clean --log-level WARN OfficeMusicBot.spec
    if ($LASTEXITCODE -ne 0) { throw "Executable build failed." }
    $EngineContents = & $Python -m PyInstaller.utils.cliutils.archive_viewer `
        --list --recursive .\dist\OfficeMusicEngine.exe
    if ($LASTEXITCODE -ne 0) { throw "Engine archive inspection failed." }
    if ($EngineContents | Select-String -Pattern "playwright|node\.exe" -Quiet) {
        throw "The engine must not bundle Playwright or its Node driver."
    }
    $Smoke = Start-Process -FilePath (Join-Path $Root "dist\OfficeMusicEngine.exe") `
        -ArgumentList "--version" -PassThru -Wait
    if ($Smoke.ExitCode -ne 0) { throw "Executable startup check failed." }
    $Output = Join-Path $Root "dist\OfficeMusicDesktop"
    if (Test-Path -LiteralPath $Output) {
        Remove-Item -LiteralPath $Output -Recurse -Force
    }
    & dotnet publish .\OfficeMusicDesktop\OfficeMusicDesktop.csproj -c Release -r win-x64 `
        -p:Platform=x64 -p:Portable=true -p:PublishReadyToRun=false `
        -p:DebugType=None -p:DebugSymbols=false -o $Output --verbosity quiet
    if ($LASTEXITCODE -ne 0) { throw "Native WinUI publish failed." }
    foreach ($Unused in @("onnxruntime.dll", "DirectML.dll")) {
        if (Test-Path (Join-Path $Output $Unused)) {
            throw "Framework-dependent distribution unexpectedly includes $Unused."
        }
    }
    foreach ($Required in @("OfficeMusicDesktop.exe", "OfficeMusicDesktop.pri", "App.xbf",
                            "MainPage.xbf", "MainWindow.xbf", "Assets\AppIcon.ico",
                            "Backend\OfficeMusicEngine.exe")) {
        if (-not (Test-Path (Join-Path $Output $Required))) {
            throw "Portable publish is incomplete: $Required"
        }
    }
    $Payload = Join-Path $Root "dist\OfficeMusicDesktop.payload.zip"
    Compress-Archive -Path (Join-Path $Output "*") -DestinationPath $Payload -Force
    & dotnet publish .\OfficeMusicLauncher\OfficeMusicLauncher.csproj -c Release `
        -o .\dist\launcher --verbosity quiet
    if ($LASTEXITCODE -ne 0) { throw "Single executable launcher build failed." }
    $Executable = Join-Path $Root "dist\OfficeMusicBot.exe"
    Copy-Item .\dist\launcher\OfficeMusicBot.exe $Executable -Force
    $Verify = Start-Process -FilePath $Executable -ArgumentList "--verify-payload" -PassThru -Wait
    if ($Verify.ExitCode -ne 0) { throw "Single executable extraction check failed." }
    $Extension = Join-Path $Root "dist\OfficeMusicLink-extension.zip"
    Compress-Archive -Path .\extension\* -DestinationPath $Extension -Force
    foreach ($Artifact in @($Executable, $Extension)) {
        $Size = (Get-Item $Artifact).Length
        Write-Host ("Distribution: {0} ({1:N2} MiB)" -f $Artifact, ($Size / 1MB))
    }
}
finally {
    Pop-Location
}
