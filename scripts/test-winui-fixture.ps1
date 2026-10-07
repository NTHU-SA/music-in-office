param(
    [Parameter(Mandatory)][int]$AppProcessId,
    [string]$ScreenshotDirectory
)
$ErrorActionPreference = "Stop"

function Ui {
    $Output = & winapp ui @args -a $AppProcessId --json
    if ($LASTEXITCODE -ne 0) {
        throw "Native UI command failed: $($args -join ' '): $Output"
    }
    $Output | ConvertFrom-Json
}
function Assert-Value([string]$Selector, [string]$Expected) {
    [void](Ui wait-for $Selector --value $Expected -t 15000)
    $Actual = (Ui get-value $Selector).text
    if ($Actual -ne $Expected) { throw "$Selector expected '$Expected', got '$Actual'." }
}
function Assert-Enabled([string]$Selector, [string]$Expected) {
    [void](Ui wait-for $Selector --property IsEnabled --value $Expected -t 15000)
}
function Screenshot([string]$Name) {
    if ($ScreenshotDirectory) {
        [void](Ui screenshot -o (Join-Path $ScreenshotDirectory "$Name.png"))
    }
}
function Start-Scenario([string]$Guild) {
    [void](Ui set-value ServerId $Guild)
    [void](Ui invoke StartButton)
}
function Stop-Scenario {
    [void](Ui invoke StopButton)
    Assert-Value ConnectionStatus "已停止"
    Assert-Enabled StartButton true
}

# Only run against the copied app with tests\desktop\EngineFixture.cs as its backend.
Assert-Value ServerId "123"
Assert-Value ChannelId "456"
Assert-Enabled StartButton true
if ($ScreenshotDirectory) {
    New-Item -ItemType Directory -Path $ScreenshotDirectory -Force | Out-Null
}
Screenshot "winui-modern-initial"

Start-Scenario "123"
Assert-Value ConnectionStatus "Discord 已連線"
Assert-Value SongTitle "GUI fixture song"
Assert-Value SongArtist "Test artist"
Assert-Value SongSource "點歌：Office Alice"
Assert-Value PlaybackStatus "正在播放"
Assert-Value QueueCount "待播 3 首"
Assert-Value AutoplayStatus "自動推薦已開啟"
Assert-Enabled StopButton true
Screenshot "winui-connected-fixture"
[void](Ui invoke SettingsExpander --action expand)
Assert-Enabled ServerId false
[void](Ui invoke SettingsExpander --action collapse)
Stop-Scenario

Start-Scenario "321"
Assert-Value ConnectionStatus "Discord 已連線"
Assert-Value PlaybackStatus "已暫停"
Assert-Value SongSource "YouTube Music 自動推薦"
Assert-Value QueueCount "待播 0 首"
Assert-Value AutoplayStatus "自動推薦已關閉"
Stop-Scenario
Assert-Value AutoplayStatus "自動推薦已開啟"

Start-Scenario "456"
Assert-Value ConnectionStatus "連線已中斷"
Assert-Enabled StartButton true
Assert-Value StartButton "儲存並重試"
Assert-Value ServerId "456"

Start-Scenario "789"
Assert-Value ConnectionStatus "Discord 已連線"
Assert-Value PlaybackStatus "播放需要處理"
Assert-Value SongTitle "GUI fixture song"
Assert-Enabled StopButton true
Screenshot "winui-playback-error-fixture"
Stop-Scenario

foreach ($FailureGuild in @("999", "888")) {
    Start-Scenario $FailureGuild
    Assert-Value ConnectionStatus "連線已中斷"
    Assert-Enabled StartButton true
    Assert-Value StartButton "儲存並重試"
    Assert-Value ServerId $FailureGuild

    Start-Scenario "123"
    Assert-Value ConnectionStatus "Discord 已連線"
    Assert-Value SongTitle "GUI fixture song"
    Stop-Scenario
}
Write-Host "Native connection, metadata, queue, playback errors, fast failures, stop and engine restart passed."
