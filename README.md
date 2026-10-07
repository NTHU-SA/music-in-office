# Office Music Bot

在 Discord 點歌，從辦公室 Windows 電腦的喇叭播放 YouTube Music。
這是使用 **WinUI 3／Windows App SDK** 的原生 Windows 程式，
採用 Mica 標題列、系統亮／暗色與原生控制項，**不會加入 Discord 語音頻道，也不下載歌曲**。

## 使用前準備

- Windows 10/11、Microsoft Edge，以及可連線至 Discord / YouTube Music 的網路。
- 電腦的預設音訊輸出設定為辦公室喇叭；exe 與專用 Edge 視窗都要保持開啟。
- YouTube Music 的登入、廣告、地區或訂閱限制仍然適用，必要時在專用 Edge 視窗手動處理。
- 一個 Discord bot、一個伺服器及一個固定的**一般文字頻道**。

在 [Discord Developer Portal](https://discord.com/developers/applications) 建立 application，
於 Bot 頁面取得 bot token。在 OAuth2 URL Generator 選擇 `bot` 與
`applications.commands`，將 bot 邀請至目標伺服器。Bot 在點歌頻道需要
**View Channel**、**Send Messages**；不需要 Administrator、Message Content Intent
或語音權限。成員需要能在該頻道使用 application commands。

Discord 設定 → 進階 → 開啟開發者模式，再對伺服器與頻道按右鍵複製 ID。
Token 只在辦公室電腦設定，不要貼到 Discord、GitHub 或對話中。

## 執行 exe

完成下方建置後，執行 `dist\OfficeMusicDesktop\OfficeMusicDesktop.exe`。
搬到辦公室電腦時，複製完整資料夾，或解壓 `dist\OfficeMusicBot-win-x64.zip`；
**不能只複製一個 exe**。可攜版包含 .NET、Windows App SDK 與播放引擎，
目標電腦不需要另外安裝 Python 或 .NET，但需要已安裝 Edge。

雙擊會開啟原生視窗，不會出現額外的主控台。填入 Bot Token（遮蔽顯示）、
Server ID、Channel ID，再按「儲存並啟動」。連線成功後會開啟專用 Edge。
請保持兩個分頁開啟：播放分頁和搜尋分頁。
登入及同意提示需要你在瀏覽器處理；不要直接在這些分頁選歌，以免和 bot 的佇列衝突。
所有點歌與控制指令都在設定的 Discord 頻道使用。

```powershell
.\dist\OfficeMusicDesktop\OfficeMusicDesktop.exe
```

視窗會顯示 Discord 是否已成功連線、目前歌名／歌手、點歌者或自動推薦、
播放／暫停狀態、待播首數與自動推薦開關。啟動中的狀態不會假裝已連線，
只有 Discord 實際就緒後才顯示「Discord 已連線」。

**設定會在輸入後自動儲存**，重新開啟視窗會帶入上次內容；也會讀取舊版已儲存的設定。
尚未填完的 ID 也能先保留，啟動前才檢查格式。啟動前會先儲存，因此 token 被拒絕、
ID 填錯或網路出問題，都不會讓已輸入的內容消失。
錯誤顯示在同一個視窗內，不會自動關閉；修改後按「儲存並重試」即可。
播放期間的錯誤也會顯示在視窗，可處理 Edge 提示後在 Discord 使用 `/resume`，
或按「停止」後重新啟動。設定在運行期間鎖定，先停止 bot 再修改。

亦可透過本機環境變數 `OFFICE_MUSIC_TOKEN`、`OFFICE_MUSIC_GUILD_ID`、
`OFFICE_MUSIC_CHANNEL_ID` 預填 GUI；在視窗修改後，以表單內容啟動。
舊版主控台模式仍以環境變數覆寫設定。不要在共用終端機或 shell 歷史紀錄貼入 token。

設定、log 與 Edge profile 放在 `%LOCALAPPDATA%\OfficeMusicBot`。
儲存的 token 由 Windows DPAPI 保護，僅原 Windows 使用者可解密；
這不代表同一使用者下的惡意程式無法存取它。瀏覽器登入狀態同樣是敏感資料，
不要分享 profile、設定或整個資料目錄。程式不會使用或修改你的個人 Edge profile。

按「停止」會中斷連線並關閉專用瀏覽器，但保留設定視窗；關閉視窗也會停止 bot。
相同 Windows 使用者不能同時執行兩個 bot 工作階段。

## Discord 指令

| 指令 | 行為 |
| --- | --- |
| `/play query:歌名或歌手` | 搜尋第一個符合的正式歌曲結果，回覆選到的歌名、歌手並排隊 |
| `/play query:單曲連結` | 支援 YouTube Music、YouTube watch 與 youtu.be 單曲網址 |
| `/queue page:1` | 檢視待播點歌，每頁最多 10 首 |
| `/nowplaying` | 查詢目前歌曲、是否暫停、自動推薦及錯誤狀態 |
| `/pause` | 暫停；新點歌不會解除暫停 |
| `/resume` | 繼續播放；手動處理瀏覽器提示後可用來重試 |
| `/skip` | 跳到下一首點歌，沒有點歌時依自動推薦開關決定推薦或停止 |
| `/autoplay enabled:True` | 開啟 YouTube Music 自動推薦，預設開啟 |
| `/autoplay enabled:False` | 目前歌曲播完後，若沒有點歌就停止 |

所有可使用設定頻道的成員都能點歌及控制播放，第一版沒有 DJ 角色或跳歌投票。
其他頻道與私訊的操作會被拒絕。

點歌依序播放，新點歌不會中斷正在播放的點歌。
但正在播 **YouTube Music 自動推薦**時，新的點歌解析完成後會立即切換。
廣告不會被跳過；遇到廣告中的推薦歌曲，先等待廣告結束再切換。
暫停時 `/skip` 會換到下一首，但保留暫停狀態。

佇列清空後由 YouTube Music 的原生推薦接續，沒有初始歌曲時不會憑空開始播放。
每首歌確認實際開始播放後，bot 會主動在固定頻道公告歌名、歌手、連結及點歌者；
推薦歌曲標示「自動推薦」。廣告、僅接受點歌、載入及暫停／繼續不會觸發新歌公告。
公告送出失敗最多重試三次，失敗原因寫在 log；可用 `/nowplaying` 查詢。

佇列與自動推薦開關只保留在記憶體；重啟會清空佇列、恢復預設自動推薦，
且不會自動接續上次音樂。設定及瀏覽器登入狀態會保留。
不支援整份歌單匯入；單曲連結附帶的歌單或時間參數會移除。

## 從原始碼開發與建置

需要 Python 3.12+、.NET SDK 10 與 WinApp CLI 0.6+。
專案固定使用最新穩定版 Windows App SDK 2.5.1、CommunityToolkit.Mvvm 8.4.2，
並保留 NuGet lock file。以下在 repository 目錄以 PowerShell 執行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m office_music_bot --setup
.\.venv\Scripts\python.exe -m office_music_bot
```

原始碼的 Python 入口只執行播放引擎／舊版主控台；開發原生 GUI：

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm OfficeMusicBot.spec
winapp run .\OfficeMusicDesktop --arch x64
```

專案預設保留打包開發身分與 manifest；發行版透過明確的 `Portable=true`
設定建置 self-contained 可攜資料夾，不需要安裝 MSIX 或信任開發憑證。
需要主控台或診斷輸出時，從 Python 執行：

```powershell
.\.venv\Scripts\python.exe -m office_music_bot
.\.venv\Scripts\python.exe -m office_music_bot --setup
.\.venv\Scripts\python.exe -m office_music_bot --check
.\.venv\Scripts\python.exe -m office_music_bot --probe-browser
```

`--check` 只驗證本機設定，不驗證 Discord token；
`--probe-browser` 只檢查真實 Edge 搜尋，不連 Discord 或播放音訊。
GUI exe 沒有終端輸出，命令列診斷請用上面的 Python 方式。

若已安裝 uv：

```powershell
uv venv .venv
uv pip install --python .venv\Scripts\python.exe -e ".[dev]"
```

執行測試、lint、播放引擎與原生 WinUI 可攜版建置：

```powershell
.\scripts\build.ps1
```

或個別執行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean OfficeMusicBot.spec
dotnet publish .\OfficeMusicDesktop\OfficeMusicDesktop.csproj -c Release -r win-x64 `
    -p:Platform=x64 -p:Portable=true -o .\dist\OfficeMusicDesktop
```

DOM fixture 測試會以無介面的 Edge 檢查實際 JavaScript 與播放器選擇器，不需要網路或登入。
PyInstaller 會包含 Python、套件及 Playwright driver，不包含 Edge、token 或 profile。
播放引擎執行時會解壓 Python runtime，因此不是零磁碟存取，也不是零設定檔。
原生介面透過私有 stdin/stdout 管線控制引擎；token 不放在命令列引數，
亦不進入正常 log。視窗與引擎分離，連線或引擎失敗後可保留視窗並重試。
exe 需在 Windows 建置；發行資料夾與 ZIP 放在 `dist`，不提交到 Git。

## 驗收與限制

自動測試涵蓋排隊、併發、推薦切換、暫停、同曲重播、廣告排除、公告去重、
輸入與頻道限制、DPAPI、單實例、GUI 設定保留、IPC 與錯誤保留佇列。
`tests\desktop\EngineFixture.cs` 可作為隔離的 GUI 測試假引擎，搭配
`scripts\test-winui-fixture.ps1 -AppProcessId <測試視窗PID>` 驗證實際 WinUI 控制項。
假引擎只能放在發行資料夾的**測試副本**，不可用來交付；不會連 Discord 或播放音訊。
這些測試**不能取代 Discord 與喇叭實機驗收**。

在目標電腦設定後，確認：

1. 以歌名與單曲連結點歌，確認喇叭有聲音，公告內容正確。
2. 連續點兩首，確認按順序播完；沒人點歌時能接續推薦。
3. 播推薦時點歌，確認切換並標示正確的點歌者。
4. 暫停後點歌不會出聲；關閉自動推薦後佇列播完停止。
5. 重複點同一首仍各自公告，暫停／繼續不會重複公告。
6. 關閉播放分頁後，bot 顯示錯誤而非假裝已播放。

YouTube Music 沒有此用途的官方播放控制 API，本程式控制普通網頁播放器，
網站更新可能導致搜尋或控制失效。歌曲／推薦可能受登入、地區、訂閱、網路、
廣告及公司 Edge 管理政策影響；程式不繞過限制或略過廣告。
瀏覽器 profile 的登入也可能被 Google 自動化登入政策限制。
不保證任意帳號、任意地區或任意網站版本都能播放。
辦公室／公開場所播放音樂的授權應另外確認，網頁可播放不等於取得公開播放權。

## 常見問題

- **沒有 slash commands**：確認 bot 以 `applications.commands` 邀請，server ID 正確，
  已成功連線，以及成員能在頻道使用 application commands。
- **Discord 拒絕 token**：在設定視窗更正後重試；不要將 token 分享出去。
- **找不到歌曲**：嘗試更精確的歌名／歌手或單曲連結，查看搜尋分頁是否卡在登入／同意畫面。
- **無法播放／沒有聲音**：查看播放分頁提示、喇叭輸出、Windows 音量混音程式與 Edge 分頁音量，
  必要時手動按一次網頁 Play，再使用 `/resume`。
- **推薦未開始**：確認 `/autoplay` 已開啟及網頁提供下一首推薦；可先重新點一首歌。
- **瀏覽器被關閉**：在視窗停止後重新啟動；不會自動重新登入或重建已關閉的工作階段。
- **播放引擎中斷／缺少檔案**：保持完整解壓資料夾，查看
  `%LOCALAPPDATA%\OfficeMusicBot\desktop.log` 與 `bot.log`；視窗會保留輸入並允許重試。
- **公告失敗**：確認頻道權限與連線，查看 `%LOCALAPPDATA%\OfficeMusicBot\bot.log`；
  不要上傳整個 profile 或設定目錄。