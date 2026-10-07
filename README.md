# Office Music Bot

在 Discord 點歌，由辦公室 Windows 電腦的喇叭播放 YouTube Music。
原生 WinUI 3 介面負責設定／狀態，**Chromium 擴充套件負責控制普通網頁分頁**。
不加入 Discord 語音頻道、不下載歌曲、不跳過廣告；不再使用 Playwright 或 headless。

## 安裝與自動連線

從 [Releases](https://github.com/NTHU-SA/music-in-office/releases) 下載兩個獨立檔案：

| 檔案 | 用途 |
| --- | --- |
| `OfficeMusicBot.exe` | Windows x64 單一啟動檔，包含介面與 Python 引擎，不包含瀏覽器／runtime |
| `OfficeMusicLink-extension.zip` | Edge／Chrome 的 Manifest V3 擴充套件 |

目標電腦需 Windows 10/11、Chromium 120+（例如新版 Edge／Chrome）、
[.NET Desktop Runtime 10 x64](https://dotnet.microsoft.com/download/dotnet/10.0)
與 [Windows App Runtime 2.5 x64](https://learn.microsoft.com/windows/apps/windows-app-sdk/downloads)。
這兩個 runtime 改用系統安裝，避免每份下載都重複攜帶它們。

1. 解壓 extension ZIP 到固定資料夾，不要刪掉或移動它。
2. 開啟 `edge://extensions` 或 `chrome://extensions`，啟用開發人員模式，
   選「載入解壓縮」並指定含 `manifest.json` 的資料夾。
   ID 應為 `oogmlanepeobcjpkaipdhkjlaoncglph`；保留 manifest 的 `key`。
3. 保持瀏覽器開啟，執行 `OfficeMusicBot.exe`，填入 Discord 設定。
   如需登入，按「YouTube 登入」，在擴充套件的專用分頁自行登入；
   完成後按「完成登入並關閉」（結束登入連線，不關閉你的瀏覽器）。
4. 按「儲存並啟動」。擴充套件會自動重試 `127.0.0.1:18765`，
   建立／重用專用播放分頁和靜音搜尋分頁，無需設定除錯埠或手動配對。
   圖示徽章 `ON` 表示已連線，`OFF` 表示本機服務未啟動，`!` 表示操作錯誤；
   滑鼠停在圖示上可看錯誤，點圖示可開啟專用播放分頁。

ZIP 是開發人員模式的安裝包，不是 Chrome Web Store 套件；
Chrome／Edge 不保證允許直接拖入自行封裝的 CRX，因此目前提供 ZIP。
企業政策可能禁止安裝或限制背景播放。

**第一次可能需要在專用分頁手動按 Play／允許網站音訊**，再從 Discord 使用 `/resume`。
擴充套件不會繞過瀏覽器 autoplay、Google 登入、地區或訂閱限制。
播放前會等待目標歌曲載入；若 `play()` 因新的載入請求中斷，會有限度重試。
只有瀏覽器拒絕播放權限時才提示手動按 Play／允許音訊；其他失敗請檢查專用分頁後使用 `/resume`。
exe 與瀏覽器都要保持開啟，分頁可以在背景，但不會隱藏帳號介面。
避免讓瀏覽器的睡眠分頁／節能功能凍結專用播放分頁。

單一 EXE 首次執行會將內嵌的程式資源解壓到
`%LOCALAPPDATA%\OfficeMusicBot\App\<payload hash>`，再啟動 WinUI。
這是**單一下載／啟動檔**，不是零磁碟存取；更新會使用另一個版本目錄，
後續啟動會驗證全部資源的完整性並重用快取，不覆蓋執行中的舊版。
同一 Windows 帳號在不同登入工作階段啟動時，會共用跨工作階段的展開與清理鎖；
不同帳號則使用各自的鎖與快取。
啟動時保留目前版本、最近一版、仍有視窗或引擎執行的舊版及最近 24 小時內的快取。
其他已確認且超過 24 小時的舊版元件目錄會安全清除；無法確認或刪除時保留目錄，
並在 `%LOCALAPPDATA%\OfficeMusicBot\launcher.log` 記錄警告。設定不在 cache 裡，
升級與清理不會刪除設定或瀏覽器登入資料。

## Discord 設定

在 [Discord Developer Portal](https://discord.com/developers/applications) 建立 application，
取得 **Bot Token**，以 `bot` 與 `applications.commands` scopes 邀請到目標伺服器。
固定的一般文字頻道需要 View Channel、Send Messages；不需要管理員、
Message Content Intent 或語音權限。Discord 開發者模式可以複製 Server ID 與 Channel ID。
不要將 token 貼到 Discord、GitHub 或 shell 歷史。

設定會自動儲存，啟動前先保存輸入，再驗證；錯誤留在視窗內，可修改後重試。
只有 Discord 實際就緒才顯示已連線。運行期間鎖定設定，先停止再修改。
停止／關閉 exe 會停止 bot、暫停專用分頁並清空佇列，**不關閉整個瀏覽器**。
同一 Windows 使用者不可同時執行兩個工作階段。
瀏覽器斷線、關閉或 extension 重載後會自動重新連線，但不自動重送播放命令；
確認專用分頁可用後使用 `/resume` 重試，避免意外重複播放。
若初次啟動等待 extension 45 秒仍未連上，介面顯示錯誤，啟用 extension 後重新啟動 bot。
搜尋與播放控制分開依序排程，點歌解析或等待搜尋分頁廣告時，不阻塞 `/pause` 等播放控制。
RPC 被呼叫端取消或逾時時會中斷擴充套件連線，避免已取消的操作稍後才執行；
重新連線後仍需明確重試，不會自動重送命令。

## Discord 指令

| 指令 | 行為 |
| --- | --- |
| `/play query:歌名或歌手` | 搜尋第一個正式歌曲結果，回覆歌名／歌手並排隊 |
| `/play query:單曲連結` | 支援 YouTube Music／YouTube watch／youtu.be，移除歌單與時間參數 |
| `/queue page:1` | 每頁最多 10 首待播點歌 |
| `/nowplaying` | 查詢歌曲、暫停、自動推薦與錯誤 |
| `/pause` | 暫停；新點歌不解除暫停 |
| `/resume` | 繼續或重試，必要時先在瀏覽器處理登入／同意／音訊提示 |
| `/skip` | 跳至下一首，保留暫停狀態；佇列空時依自動推薦設定處理 |
| `/autoplay enabled:True` | 開啟 YouTube Music 原生推薦，預設開啟 |
| `/autoplay enabled:False` | 沒有待播歌曲時，播完目前歌曲就停止 |

僅指定伺服器／頻道能使用指令；成員皆可控制，沒有 DJ 角色或跳歌投票。
點歌依序播放，不中斷現有點歌；正在播推薦時，新點歌解析完成後立即切換。
廣告不會略過，既有廣告需先播完；搜尋分頁保持靜音，確認非廣告的指定 metadata 才排隊。
沒有初始歌曲時不會憑空開始推薦。不支援整份歌單匯入。

實際開始播放才公告歌名、歌手、連結及點歌者；推薦標示「自動推薦」。
廣告、載入、暫停／繼續不會重複公告；同曲再次點歌會視為新的播放。
公告失敗最多重試三次並記 log。佇列與推薦開關只在記憶體，重啟不接續上次播放。

## 本機服務與帳號安全

服務**僅綁定 `127.0.0.1:18765`**，只接受固定 extension Origin 和 loopback Host
的 WebSocket，不開放 LAN、不提供任意執行碼、設定或 Discord token API。
第二個瀏覽器 extension 連線會被拒絕，請只在需要播放的瀏覽器 profile 啟用。
連線中斷或超過 8 秒沒有心跳時，專用播放分頁會暫停；
搜尋分頁的靜音保護在同源重新載入後仍有效。普通個人音樂分頁不會被接管。

Origin 檢查防止普通網站連線／DNS rebinding，**不能防止同一使用者下的惡意本機程式**
偽造 Origin；服務不是帳號安全隔離邊界。
設定與 log 在 `%LOCALAPPDATA%\OfficeMusicBot`，token 使用 Windows DPAPI 加密，
僅原 Windows 使用者可解密。token 不放在命令列或正常 log。
可透過 `OFFICE_MUSIC_TOKEN`、`OFFICE_MUSIC_GUILD_ID`、`OFFICE_MUSIC_CHANNEL_ID`
預填；GUI 修改後以表單為準，舊版主控台則保留環境變數覆寫。

Google 登入由正常的瀏覽器 profile 管理，不再建立 `edge-profile`，
也不自動搬移舊登入 cookie。原有專用 profile 可在停止程式後自行保留或刪除。
程式不索取或代填 Google 密碼，也不能保證登入永不過期或帳號有 Premium。
共用電腦請使用獨立 Windows 使用者並鎖定桌面；背景分頁不代表帳號安全。

## 開發與建置

需要 Python 3.12+、Node.js 22+、.NET SDK 10；WinUI 開發使用 WinApp CLI 0.6+。
Windows App SDK 2.5.1、CommunityToolkit.Mvvm 8.4.2 及 NuGet lock file 保留。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\scripts\build.ps1
```

同一次 build 執行 Python／extension／launcher 測試與 lint，打包不含 Playwright／Node driver
的 Python 引擎，發行 framework-dependent WinUI，再將必要資源嵌入單一 launcher EXE。
最終輸出 `dist\OfficeMusicBot.exe` 與 `dist\OfficeMusicLink-extension.zip`；
`dist\OfficeMusicDesktop`、`dist\OfficeMusicPayload.zip`、launcher 目錄是中間產物，不需要分發。
build 會輸出兩個交付檔案的實際 MiB，並驗證引擎啟動與 EXE 資源解壓。
launcher 測試涵蓋展開、快取重用、升級、併發、跨行程鎖、損壞、路徑驗證及安全清理；
最終 EXE 的 `--check` 驗證所有內含檔案與引擎啟動，不連 Discord 或播放音訊。
不使用不安全的 WinUI trimming，也不將瀏覽器、Google cookie 或設定打包。

開發時以「載入解壓縮」選 repository 的 `extension`，更新後重新載入擴充套件。
Python 入口保留診斷與舊版主控台模式：

```powershell
.\.venv\Scripts\python.exe -m office_music_bot --setup
.\.venv\Scripts\python.exe -m office_music_bot --check
.\.venv\Scripts\python.exe -m office_music_bot --login
.\.venv\Scripts\python.exe -m office_music_bot --probe-browser
.\.venv\Scripts\python.exe -m office_music_bot
```

Python 的 `--check` 只驗證本機設定；`--login` 不需要 Discord 設定，完成後 Ctrl+C。
`--probe-browser` 使用已安裝 extension 搜尋，不連 Discord／不播放音訊。
GUI 開發先建置引擎，再使用 `winapp run .\OfficeMusicDesktop --arch x64`。
原生介面與引擎仍以私有 stdin/stdout 管線連線；引擎失敗不會關閉設定視窗。

## 自動發布

推送 `vMAJOR.MINOR.PATCH` tag 會觸發 `.github\workflows\release.yml`。
Windows runner 一起建置兩個獨立交付檔案，成功後 Release 附上 EXE、extension ZIP、
各自 SHA-256 與自動更新說明。使用內建 `GITHUB_TOKEN`，不需另設發布 token。

發布前同步 `pyproject.toml`、Python `__init__.py`、desktop／launcher `.csproj`、
extension `manifest.json` 的版本；appx manifest 使用四段版本（例如 `0.1.2.0`）。
版本與 tag 不一致會停止發布。EXE／ZIP 未簽署，不會自動安裝 extension 或 runtime。

## 限制與實機驗收

自動測試涵蓋播放政策、搜尋／廣告靜音、推薦、同曲重播、心跳失效、斷線重連、
loopback Origin／Host 限制、排隊、DPAPI、單實例、設定保留及 IPC。
這些不能取代真實 Google／Discord／喇叭驗收：安裝 extension，登入後點兩首歌，
確認順序與公告；再確認推薦切換、暫停後點歌、關閉推薦、同曲重播、
停止 exe 暫停分頁，以及重開瀏覽器／exe 後可連線並以 `/resume` 重試。

YouTube Music 沒有官方網頁控制 API，網站更新可能讓搜尋／播放器選擇器失效。
遇到錯誤，點 extension 圖示查看專用分頁，處理登入／同意／音訊提示後重試。
檢查 Windows 預設喇叭與瀏覽器音量；連線問題確認 port 18765 未被其他程式佔用。
查看 `%LOCALAPPDATA%\OfficeMusicBot\desktop.log`、`bot.log` 與 `launcher.log`，
不要分享 token、設定或瀏覽器 profile。公開場所音樂授權需另行確認。
