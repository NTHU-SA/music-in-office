using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace OfficeMusicDesktop.Services;

public sealed class BotSettings
{
    public string Token { get; init; } = "";
    public string GuildId { get; init; } = "";
    public string ChannelId { get; init; } = "";
}

public sealed class PlayerInfo
{
    public string Title { get; init; } = "";
    public string Artist { get; init; } = "";
    public string? Requester { get; init; }
    public string Url { get; init; } = "";
    public int QueueCount { get; init; }
    public bool Autoplay { get; init; }
    public bool Confirmed { get; init; }
    public bool Paused { get; init; }
    public string Error { get; init; } = "";
}

public sealed class BackendPacket
{
    public string Kind { get; init; } = "";
    public long? Id { get; init; }
    public bool Ok { get; init; }
    public string Message { get; init; } = "";
    public BotSettings? Settings { get; init; }
    public PlayerInfo? Data { get; init; }
}

public sealed class BackendException(string message) : Exception(message);

public sealed class BackendClient : IAsyncDisposable
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        PropertyNameCaseInsensitive = true,
        RespectNullableAnnotations = true
    };
    private readonly ConcurrentDictionary<long, TaskCompletionSource<BackendPacket>> _pending = new();
    private readonly SemaphoreSlim _startup = new(1);
    private readonly SemaphoreSlim _writing = new(1);
    private Process? _process;
    private Task? _reading;
    private Task? _stderr;
    private long _nextId;
    private bool _closing;
    private static readonly object LogLock = new();

    public event Action<BackendPacket>? EventReceived;

    internal static void Log(string message)
    {
        try
        {
            lock (LogLock)
            {
                var directory = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "OfficeMusicBot");
                Directory.CreateDirectory(directory);
                File.AppendAllText(Path.Combine(directory, "desktop.log"),
                    $"{DateTimeOffset.Now:O} {message}{Environment.NewLine}");
            }
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            Trace.TraceError($"Desktop diagnostic could not be saved: {exception.GetType().Name}");
        }
    }

    private async Task EnsureStartedAsync()
    {
        await _startup.WaitAsync();
        try
        {
            if (_process is { HasExited: false }) return;
            if (_reading is not null) await _reading;
            if (_stderr is not null) await _stderr;
            _process?.Dispose();
            string path = Path.Combine(AppContext.BaseDirectory, "Backend", "OfficeMusicEngine.exe");
            if (!File.Exists(path))
                throw new BackendException("找不到播放引擎。請解壓完整的程式資料夾，不要只複製 GUI 執行檔。");
            var info = new ProcessStartInfo(path)
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardInput = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardInputEncoding = new UTF8Encoding(false),
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8,
                WorkingDirectory = AppContext.BaseDirectory
            };
            info.ArgumentList.Add("--bridge");
            _process = Process.Start(info) ??
                throw new BackendException("播放引擎無法啟動。請檢查程式資料夾及防毒軟體的提示。");
            _reading = ReadAsync(_process);
            _stderr = DrainErrorsAsync(_process);
        }
        finally { _startup.Release(); }
    }

    public async Task<BackendPacket> RequestAsync(string command, BotSettings? settings = null)
    {
        await EnsureStartedAsync();
        long id = Interlocked.Increment(ref _nextId);
        var completion = new TaskCompletionSource<BackendPacket>(
            TaskCreationOptions.RunContinuationsAsynchronously);
        _pending[id] = completion;
        try
        {
            await _writing.WaitAsync();
            try
            {
                string json = JsonSerializer.Serialize(new { id, command, settings }, JsonOptions);
                await _process!.StandardInput.WriteLineAsync(json);
                await _process.StandardInput.FlushAsync();
            }
            finally { _writing.Release(); }
            var packet = await completion.Task.WaitAsync(TimeSpan.FromSeconds(20));
            if (!packet.Ok) throw new BackendException(packet.Message);
            return packet;
        }
        catch (TimeoutException)
        {
            throw new BackendException("播放引擎沒有回應。設定仍保留，請停止後重試或重新開啟程式。");
        }
        catch (IOException)
        {
            throw new BackendException("播放引擎連線已中斷。視窗與輸入會保留，請重試。");
        }
        finally { _pending.TryRemove(id, out _); }
    }

    private async Task ReadAsync(Process process)
    {
        string failure = "播放引擎已停止。視窗與設定仍保留，請重試。";
        try
        {
            while (await process.StandardOutput.ReadLineAsync() is { } line)
            {
                var packet = JsonSerializer.Deserialize<BackendPacket>(line, JsonOptions);
                if (packet is null) throw new JsonException();
                if (packet.Kind == "response" && packet.Id is { } id)
                {
                    if (_pending.TryRemove(id, out var completion))
                        completion.TrySetResult(packet);
                }
                else EventReceived?.Invoke(packet);
            }
        }
        catch (Exception exception) when (exception is IOException or JsonException or
                                         InvalidOperationException)
        {
            Log($"Engine transport failed: {exception.GetType().Name}");
            failure = "播放引擎通訊失敗。視窗與輸入仍保留，請重試。";
        }
        finally
        {
            if (!_closing)
            {
                if (!process.HasExited)
                {
                    Log("Terminating owned engine after its IPC channel closed.");
                    process.Kill(entireProcessTree: true);
                }
                await process.WaitForExitAsync();
                Log($"Engine exited unexpectedly: code {process.ExitCode}");
                failure += $"（結束碼 {process.ExitCode}；詳細記錄請查看 desktop.log / bot.log。）";
            }
            foreach (var entry in _pending)
                if (_pending.TryRemove(entry.Key, out var completion))
                    completion.TrySetException(new BackendException(failure));
            if (!_closing)
                EventReceived?.Invoke(new BackendPacket { Kind = "engine_error", Message = failure });
        }
    }

    private static async Task DrainErrorsAsync(Process process)
    {
        try
        {
            bool reported = false;
            while (await process.StandardError.ReadLineAsync() is not null)
            {
                if (!reported)
                    Log("Engine emitted startup diagnostics; inspect bot.log and its exit code.");
                reported = true;
            }
        }
        catch (IOException exception) { Log($"Engine diagnostic pipe failed: {exception.GetType().Name}"); }
    }

    public async ValueTask DisposeAsync()
    {
        _closing = true;
        if (_process is { HasExited: false } process)
        {
            try
            {
                await RequestAsync("quit");
                using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(25));
                await process.WaitForExitAsync(timeout.Token);
            }
            catch (Exception exception) when (exception is BackendException or IOException or
                                             OperationCanceledException or InvalidOperationException)
            {
                Log($"Engine graceful shutdown failed: {exception.GetType().Name}");
                if (!process.HasExited) process.Kill(entireProcessTree: true);
                await process.WaitForExitAsync();
            }
        }
        if (_reading is not null) await _reading;
        if (_stderr is not null) await _stderr;
        _process?.Dispose();
        _process = null;
    }
}
