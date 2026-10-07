# Office Music Bot

在 Discord 點歌，從辦公室 Windows 電腦的喇叭播放 YouTube Music。
這是使用 **WinUI 3／Windows App SDK** 的原生 Windows 程式，
採用 Mica 標題列、系統亮／暗色與原生控制項，**不會加入 Discord 語音頻道，也不下載歌曲**。

## 使用前準備

- Windows 10/11、Microsoft Edge，以及可連線至 Discord / YouTube Music 的網路。
- 電腦的預設音訊輸出設定為辦公室喇叭；exe 要保持開啟，平常使用無視窗（headless）Edge 播放。
- YouTube Music 的登入、廣告、地區或訂閱限制仍然適用，必要時停止 bot，再開啟專用登入視窗手動處理。
- 一個 Discord bot、一個伺服器及一個固定的**一般文字頻道**。

在 [Discord Developer Portal](https://discord.com/developers/applications) 建立 application，
於 Bot 頁面取得 bot token。在 OAuth2 URL Generator 選擇 `bot` 與
`applications.commands`，將 bot 邀請至目標伺服器。Bot 在點歌頻道需要
**View Channel**、**Send Messages**；不需要 Administrator、Message Content Intent
或語音權限。成員需要能在該頻道使用 application commands。

Discord 設定 → 進階 → 開啟開發者模式，再對伺服器與頻道按右鍵複製 ID。
Token 只在辦公室電腦設定，不要貼到 Discord、GitHub 或對話中。

## 執行 exe

從 [GitHub Releases](https://github.com/NTHU-SA/music-in-office/releases) 下載
`OfficeMusicBot.exe`，直接雙擊執行。
也可以完成下方建置後，執行 `dist\OfficeMusicBot.exe`。
搬到辦公室電腦時，**只需複製這一個 exe**，不需手動解壓或安裝。
單檔版包含 .NET、Windows App SDK 與播放引擎，
目標電腦不需要另外安裝 Python 或 .NET，但需要已安裝 Edge。

WinUI 仍需要實體資源檔，因此這是**單檔自解壓啟動器**，不是完全不落地的程式：
首次執行會自動將內含元件展開至 `%LOCALAPPDATA%\OfficeMusicBot\App\<內容雜湊>`，
後續執行驗證並重用同一份元件；新版使用獨立目錄，不會覆蓋執行中的舊版。
首次啟動需要較多時間與磁碟空間。.NET／Python runtime 也會使用使用者的暫存目錄。
不需要管理員權限；設定、log 與登入 profile 仍沿用原本位置，升級不會清除。

雙擊會開啟原生視窗，不會出現額外的主控台。填入 Bot Token（遮蔽顯示）、
Server ID、Channel ID。若要使用已訂閱的 YouTube Premium／Music Premium，
先按「YouTube 登入」，自行在專用 Edge 登入 Google、處理同意提示並確認訂閱可用；
程式不會代填、儲存或索取 Google 密碼。
完成後按「完成登入並關閉」，再按「儲存並啟動」。
播放使用同一個專用 profile，但以 headless 模式執行，不會留下可操作帳號的瀏覽器視窗。
播放與登入不能同時執行；登入視窗不會自動開啟，登入期間請勿讓他人使用電腦。
所有點歌與控制指令都在設定的 Discord 頻道使用。

```powershell
.\dist\OfficeMusicBot.exe
```

視窗會顯示 Discord 是否已成功連線、目前歌名／歌手、點歌者或自動推薦、
播放／暫停狀態、待播首數與自動推薦開關。啟動中的狀態不會假裝已連線，
只有 Discord 實際就緒後才顯示「Discord 已連線」。

**設定會在輸入後自動儲存**，重新開啟視窗會帶入上次內容；也會讀取舊版已儲存的設定。
尚未填完的 ID 也能先保留，啟動前才檢查格式。啟動前會先儲存，因此 token 被拒絕、
ID 填錯或網路出問題，都不會讓已輸入的內容消失。
錯誤顯示在同一個視窗內，不會自動關閉；修改後按「儲存並重試」即可。
播放期間的錯誤也會顯示在視窗，可在 Discord 使用 `/resume` 重試。
若需要登入或處理網頁提示，按「停止」→「YouTube 登入」，處理後關閉登入視窗再啟動。
停止／重新啟動會清空佇列。設定在運行期間鎖定，先停止 bot 再修改。

亦可透過本機環境變數 `OFFICE_MUSIC_TOKEN`、`OFFICE_MUSIC_GUILD_ID`、
`OFFICE_MUSIC_CHANNEL_ID` 預填 GUI；在視窗修改後，以表單內容啟動。
舊版主控台模式仍以環境變數覆寫設定。不要在共用終端機或 shell 歷史紀錄貼入 token。

設定、log 與 Edge profile 放在 `%LOCALAPPDATA%\OfficeMusicBot`。
儲存的 token 由 Windows DPAPI 保護，僅原 Windows 使用者可解密；
這不代表同一使用者下的惡意程式無法存取它。瀏覽器登入狀態同樣是敏感資料，
不要分享 profile、設定或整個資料目錄。程式不會使用或修改你的個人 Edge profile。

**Headless 不是帳號安全邊界**：使用同一個 Windows 帳號的人仍可能重新開啟專用 profile
或存取登入資料，也能操作本程式的登入按鈕；本程式沒有管理員密碼／權限分級。
如果辦公室其他人也能操作這台電腦，請以獨立的 Windows 使用者執行 bot，
不要讓他人登入該使用者；需要時鎖定桌面，再從 Discord 點歌。
僅隱藏／最小化瀏覽器或換成 headless 不能取代 Windows 帳號與存取權限隔離。
登入 cookie 會保留，但 Google 可能讓工作階段失效、要求驗證或再次登入，
因此不能保證永久只登入一次；程式也不會驗證或替你取得 Premium。

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
| `/resume` | 繼續播放或重試；需處理登入提示時，先由管理者停止並開啟登入視窗 |
| `/skip` | 跳到下一首點歌，沒有點歌時依自動推薦開關決定推薦或停止 |
| `/autoplay enabled:True` | 開啟 YouTube Music 自動推薦，預設開啟 |
| `/autoplay enabled:False` | 目前歌曲播完後，若沒有點歌就停止 |

所有可使用設定頻道的成員都能點歌及控制播放，第一版沒有 DJ 角色或跳歌投票。
其他頻道與私訊的操作會被拒絕。

點歌依序播放，新點歌不會中斷正在播放的點歌。
但正在播 **YouTube Music 自動推薦**時，新的點歌解析完成後會立即切換。
廣告不會被跳過；點歌或繼續播放遇到廣告時，先等待廣告結束再切換，待播歌曲仍保留在佇列。
解析單曲連結時，搜尋分頁會靜音並等待廣告結束，確認取得指定歌曲資訊才加入佇列。
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
.\.venv\Scripts\python.exe -m office_music_bot --login
.\.venv\Scripts\python.exe -m office_music_bot --probe-browser
```

`--check` 只驗證本機設定，不驗證 Discord token；
`--probe-browser` 只檢查真實 Edge 搜尋，不連 Discord 或播放音訊。
`--login` 不需要 Discord 設定，只開啟專用可見 Edge；自行登入後關閉所有登入視窗即可結束。
正常播放與搜尋 probe 都使用 headless Edge；保留音訊輸出並允許播放器啟動，
不會開啟遠端除錯 TCP 連接埠。請不要自行加上遠端除錯連接埠或分享 profile。
GUI exe 沒有終端輸出，命令列診斷請用上面的 Python 方式。

若已安裝 uv：

```powershell
uv venv .venv
uv pip install --python .venv\Scripts\python.exe -e ".[dev]"
```

執行測試、lint、播放引擎、原生 WinUI 與單檔啟動器建置：

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
上述個別 publish 只產生開發用資料夾；請用 `scripts\build.ps1` 產生單檔發行版。
建置會測試展開、快取重用、升級、併發、損壞與路徑檢查，並以最終單一 exe 的
`--check` 驗證所有內含檔案及播放引擎啟動，不會連 Discord 或播放音訊。
原生介面透過私有 stdin/stdout 管線控制引擎；token 不放在命令列引數，
亦不進入正常 log。視窗與引擎分離，連線或引擎失敗後可保留視窗並重試。
exe 需在 Windows 建置；單檔 exe、內部元件 ZIP 與開發用資料夾放在 `dist`，不提交到 Git。

## 自動發布版本

GitHub Actions 的 `Release` workflow 在推送 `vMAJOR.MINOR.PATCH` tag 時自動執行，
例如 `v0.1.1`。Windows runner 會執行測試與 lint、以 PyInstaller 建置播放引擎，
再編譯 self-contained WinUI x64 可攜版並嵌入單檔啟動器。所有步驟成功後才建立
GitHub Release，附上 `OfficeMusicBot.exe`、SHA-256 checksum 與自動產生的更新說明。
不需要額外設定發布 token，workflow 使用 GitHub 提供的 `GITHUB_TOKEN`。

發布前，將 `pyproject.toml`、`src\office_music_bot\__init__.py`、
`OfficeMusicDesktop\OfficeMusicDesktop.csproj`、`OfficeMusicLauncher\OfficeMusicLauncher.csproj`
的版本同步更新，
並將 `OfficeMusicDesktop\Package.appxmanifest` 設為對應的四段版本
（例如 `0.1.1.0`）。版本與 tag 不一致時，workflow 會中止而不發布。
確認包含 workflow 與版本更新的 commit 已合併至預計發布的分支，再執行：

```powershell
git tag -a v0.1.1 -m "Release v0.1.1"
git push origin v0.1.1
```

在 repository 的 Actions 頁面確認 `Release` workflow 成功後，即可從 Releases 下載。
EXE 未經程式碼簽署；目標電腦仍須安裝 Edge。

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
6. 登入後關閉登入視窗，啟動 bot，確認沒有可見 Edge 視窗但仍能從喇叭播放。
   停止後再次啟動，確認登入狀態仍有效；工作階段失效時能停止並重新登入。

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
- **找不到歌曲**：嘗試更精確的歌名／歌手或單曲連結；若懷疑登入／同意提示，停止 bot 後按「YouTube 登入」檢查。
- **無法播放／沒有聲音**：檢查喇叭輸出與 Windows 音量混音程式的 Edge 音量，並用 `/resume` 重試；
  必要時停止 bot，在登入視窗處理提示並測試網頁 Play，關閉後再啟動。
- **推薦未開始**：確認 `/autoplay` 已開啟及網頁提供下一首推薦；可先重新點一首歌。
- **瀏覽器工作階段中斷**：在視窗停止後重新啟動；不會自動開啟可見登入視窗或重新登入。
- **單檔版無法展開／元件損壞**：關閉程式，確認磁碟空間並重新下載 exe；
  可刪除 `%LOCALAPPDATA%\OfficeMusicBot\App` 後重新執行，以重建元件快取。
  不要刪除整個 `%LOCALAPPDATA%\OfficeMusicBot`，以免清除設定與登入狀態。
- **播放引擎中斷／缺少檔案**：重新下載並執行單檔 exe；開發版需保留完整資料夾。查看
  `%LOCALAPPDATA%\OfficeMusicBot\desktop.log` 與 `bot.log`；視窗會保留輸入並允許重試。
- **公告失敗**：確認頻道權限與連線，查看 `%LOCALAPPDATA%\OfficeMusicBot\bot.log`；
  不要上傳整個 profile 或設定目錄。