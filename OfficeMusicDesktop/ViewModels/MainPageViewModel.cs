using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.UI.Dispatching;
using OfficeMusicDesktop.Services;

namespace OfficeMusicDesktop.ViewModels;

public partial class MainPageViewModel : ObservableObject
{
    private readonly BackendClient _backend = new();
    private readonly DispatcherQueue _dispatcher = DispatcherQueue.GetForCurrentThread();
    private readonly DispatcherQueueTimer _saveTimer;
    private bool _loading = true;
    private bool _dirty;
    private bool _sessionFailed;
    private bool _closed;
    private readonly SemaphoreSlim _saving = new(1);

    [ObservableProperty] public partial string Token { get; set; } = "";
    [ObservableProperty] public partial string GuildId { get; set; } = "";
    [ObservableProperty] public partial string ChannelId { get; set; } = "";
    [ObservableProperty] public partial string ConnectionStatus { get; set; } = "尚未連線";
    [ObservableProperty] public partial string ConnectionDetail { get; set; } = "填入設定後啟動，讓大家在 Discord 點歌。";
    [ObservableProperty] public partial string SongTitle { get; set; } = "今天，想聽什麼？";
    [ObservableProperty] public partial string SongArtist { get; set; } = "到指定的 Discord 頻道，用 /play 點一首歌。";
    [ObservableProperty] public partial string SongSource { get; set; } = "音樂會從這台電腦播放";
    [ObservableProperty] public partial string PlaybackStatus { get; set; } = "等待點歌";
    [ObservableProperty] public partial string QueueText { get; set; } = "待播 0 首";
    [ObservableProperty] public partial string AutoplayText { get; set; } = "自動推薦已開啟";
    [ObservableProperty] public partial string SavedStatus { get; set; } = "正在讀取上次設定…";
    [ObservableProperty] public partial string ErrorMessage { get; set; } = "";
    [ObservableProperty] public partial bool HasError { get; set; }
    [ObservableProperty] public partial string StartLabel { get; set; } = "儲存並啟動";
    [ObservableProperty] public partial bool SettingsExpanded { get; set; } = true;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanEdit))]
    [NotifyCanExecuteChangedFor(nameof(StartCommand), nameof(StopCommand), nameof(LoginCommand), nameof(FinishLoginCommand))]
    public partial bool IsRunning { get; set; }

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanEdit))]
    [NotifyCanExecuteChangedFor(nameof(StartCommand), nameof(StopCommand), nameof(LoginCommand), nameof(FinishLoginCommand))]
    public partial bool IsBusy { get; set; } = true;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanEdit))]
    [NotifyCanExecuteChangedFor(nameof(StartCommand), nameof(StopCommand), nameof(LoginCommand), nameof(FinishLoginCommand))]
    public partial bool IsAuthenticating { get; set; }

    public bool CanEdit => !IsRunning && !IsBusy && !IsAuthenticating;
    private bool CanStart() => CanEdit;
    private bool CanStop() => (IsRunning || IsAuthenticating) && !IsBusy;
    private bool CanFinishLogin() => IsAuthenticating && !IsBusy;

    public MainPageViewModel()
    {
        _backend.EventReceived += packet => _dispatcher.TryEnqueue(() => Apply(packet));
        _saveTimer = _dispatcher.CreateTimer();
        _saveTimer.Interval = TimeSpan.FromMilliseconds(650);
        _saveTimer.IsRepeating = false;
        _saveTimer.Tick += async (_, _) => await SaveAsync();
    }

    public async Task InitializeAsync()
    {
        try
        {
            var packet = await _backend.RequestAsync("load");
            if (packet.Settings is not { } settings)
                throw new BackendException("無法讀取設定。請重新輸入並儲存。");
            Token = settings.Token;
            GuildId = settings.GuildId;
            ChannelId = settings.ChannelId;
            SavedStatus = "設定已載入。Token 由 Windows 使用者加密保護。";
        }
        catch (Exception exception) { ShowError(exception); }
        finally { _loading = false; IsBusy = false; }
    }

    partial void OnTokenChanged(string value) => Edited();
    partial void OnGuildIdChanged(string value) => Edited();
    partial void OnChannelIdChanged(string value) => Edited();

    private void Edited()
    {
        if (_loading || _closed) return;
        _dirty = true;
        SavedStatus = "設定已修改，正在儲存…";
        _saveTimer.Stop();
        _saveTimer.Start();
    }

    private BotSettings Settings() => new()
    {
        Token = Token.Trim(), GuildId = GuildId.Trim(), ChannelId = ChannelId.Trim()
    };

    private async Task<bool> SaveAsync()
    {
        if (_closed || !_dirty) return true;
        await _saving.WaitAsync();
        try
        {
            if (!_dirty || IsRunning) return true;
            var settings = Settings();
            var packet = await _backend.RequestAsync("save", settings);
            _dirty = Token.Trim() != settings.Token || GuildId.Trim() != settings.GuildId ||
                     ChannelId.Trim() != settings.ChannelId;
            SavedStatus = _dirty ? "設定已修改，等待儲存…" : packet.Message;
            return true;
        }
        catch (Exception exception)
        {
            SavedStatus = "設定尚未儲存。請處理下方錯誤後重試。";
            ShowError(exception);
            return false;
        }
        finally { _saving.Release(); }
    }

    [RelayCommand(CanExecute = nameof(CanStart))]
    private async Task StartAsync()
    {
        IsBusy = true;
        HasError = false;
        StartLabel = "儲存並啟動";
        _sessionFailed = false;
        _saveTimer.Stop();
        await _saving.WaitAsync();
        try
        {
            IsRunning = true;
            ConnectionStatus = "正在連線";
            ConnectionDetail = "正在連線至 Discord，使用無視窗 Edge 播放。";
            var packet = await _backend.RequestAsync("start", Settings());
            _dirty = false;
            SavedStatus = packet.Message;
        }
        catch (Exception exception)
        {
            _sessionFailed = true;
            IsRunning = false;
            ConnectionStatus = "連線未完成";
            ShowError(exception);
        }
        finally { _saving.Release(); IsBusy = false; }
    }

    [RelayCommand(CanExecute = nameof(CanStart))]
    private async Task LoginAsync()
    {
        IsBusy = true;
        HasError = false;
        _sessionFailed = false;
        _saveTimer.Stop();
        await _saving.WaitAsync();
        try
        {
            IsAuthenticating = true;
            ConnectionStatus = "正在開啟登入視窗";
            ConnectionDetail = "請自行登入 Google，完成後關閉登入視窗。登入期間請勿讓他人使用。";
            if (_dirty)
            {
                var saved = await _backend.RequestAsync("save", Settings());
                _dirty = false;
                SavedStatus = saved.Message;
            }
            await _backend.RequestAsync("login");
        }
        catch (Exception exception)
        {
            _sessionFailed = true;
            IsAuthenticating = false;
            ConnectionStatus = "登入視窗未開啟";
            ShowError(exception);
        }
        finally { _saving.Release(); IsBusy = false; }
    }

    [RelayCommand(CanExecute = nameof(CanFinishLogin))]
    private Task FinishLoginAsync() => StopAsync();

    [RelayCommand(CanExecute = nameof(CanStop))]
    private async Task StopAsync()
    {
        IsBusy = true;
        try
        {
            ConnectionStatus = "正在停止";
            ConnectionDetail = "正在關閉 bot 與專用 Edge，設定與登入狀態會保留。";
            await _backend.RequestAsync("stop");
        }
        catch (Exception exception)
        {
            ShowError(exception);
            IsRunning = false;
            IsAuthenticating = false;
        }
        finally { IsBusy = false; }
    }

    private void Apply(BackendPacket packet)
    {
        if (_closed) return;
        switch (packet.Kind)
        {
            case "login_opening":
            case "login_ready":
                IsAuthenticating = true;
                ConnectionStatus = packet.Kind == "login_ready" ? "請完成 YouTube 登入" : "正在開啟登入視窗";
                ConnectionDetail = packet.Message;
                break;
            case "login_closed":
                ConnectionDetail = packet.Message;
                break;
            case "ready":
            case "paused":
                IsRunning = true;
                SettingsExpanded = false;
                ConnectionStatus = "Discord 已連線";
                ConnectionDetail = packet.Kind == "paused" ? "播放已暫停，使用 /resume 繼續。" : packet.Message;
                PlaybackStatus = packet.Kind == "paused" ? "已暫停" : PlaybackStatus;
                HasError = false;
                break;
            case "connecting":
                ConnectionStatus = "正在連線";
                ConnectionDetail = packet.Message;
                break;
            case "stopping":
                ConnectionStatus = "正在停止";
                ConnectionDetail = packet.Message;
                break;
            case "playback_error":
                ConnectionStatus = "Discord 已連線";
                PlaybackStatus = "播放需要處理";
                ShowError(new BackendException(packet.Message +
                    "\n可用 /resume 重試；若需要登入或處理網頁提示，請停止後按「YouTube 登入」。"));
                break;
            case "error":
            case "engine_error":
                _sessionFailed = true;
                SettingsExpanded = true;
                ConnectionStatus = "連線已中斷";
                ShowError(new BackendException(packet.Message));
                if (packet.Kind == "engine_error")
                {
                    IsRunning = false;
                    IsAuthenticating = false;
                }
                break;
            case "finished":
                bool wasAuthenticating = IsAuthenticating;
                IsAuthenticating = false;
                IsRunning = false;
                SettingsExpanded = true;
                if (!_sessionFailed)
                {
                    ConnectionStatus = "已停止";
                    ConnectionDetail = wasAuthenticating ?
                        "登入視窗已關閉；登入狀態如有建立會保留。按「儲存並啟動」開始無視窗播放。" :
                        "設定已保留。隨時可以重新啟動。";
                    PlaybackStatus = "等待點歌";
                    SongTitle = "今天，想聽什麼？";
                    SongArtist = "在 Discord 使用 /play 開始播放。";
                    SongSource = "音樂會從這台電腦播放";
                    QueueText = "待播 0 首";
                    AutoplayText = "自動推薦已開啟";
                }
                break;
            case "track" when packet.Data is { } data:
                SongTitle = data.Title.Length > 0 ? data.Title : "今天，想聽什麼？";
                SongArtist = data.Artist.Length > 0 ? data.Artist : "在 Discord 使用 /play 點歌。";
                SongSource = data.Title.Length == 0 ? "音樂會從這台電腦播放" :
                    data.Requester is null ? "YouTube Music 自動推薦" : $"點歌：{data.Requester}";
                PlaybackStatus = data.Error.Length > 0 ? "播放需要處理" :
                    data.Paused ? "已暫停" : data.Title.Length == 0 ?
                    "等待點歌" : data.Confirmed ? "正在播放" : "準備播放";
                QueueText = $"待播 {data.QueueCount} 首";
                AutoplayText = data.Autoplay ? "自動推薦已開啟" : "自動推薦已關閉";
                break;
        }
    }

    public void ShowError(Exception exception)
    {
        BackendClient.Log($"UI operation failed: {exception.GetType().Name}");
        ErrorMessage = exception is BackendException ? exception.Message :
            $"操作未完成（{exception.GetType().Name}）。設定與視窗會保留，請重試。";
        HasError = true;
        StartLabel = "儲存並重試";
    }

    public async Task<bool> SaveBeforeCloseAsync()
    {
        _saveTimer.Stop();
        return await SaveAsync();
    }

    public async Task CloseAsync()
    {
        _closed = true;
        _saveTimer.Stop();
        await _backend.DisposeAsync();
    }
}
