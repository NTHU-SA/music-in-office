using System.ComponentModel;
using System.Diagnostics;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Principal;

namespace OfficeMusicLauncher;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        bool check = args is ["--check"];
        try
        {
            using Stream payload = Assembly.GetExecutingAssembly()
                .GetManifestResourceStream("OfficeMusicPayload.zip")
                ?? throw new InvalidDataException("找不到內含的程式元件。");
            string cacheRoot = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "OfficeMusicBot", "App");
            string directory;
            Process process;
            using (PortablePayload.LockCache(cacheRoot))
            {
                directory = PortablePayload.Extract(payload, cacheRoot);
                var start = new ProcessStartInfo(Path.Combine(directory,
                    check ? @"Backend\OfficeMusicEngine.exe" : "OfficeMusicDesktop.exe"))
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WorkingDirectory = directory
                };
                if (check)
                {
                    start.ArgumentList.Add("--version");
                    start.RedirectStandardOutput = true;
                    start.RedirectStandardError = true;
                }
                else
                {
                    foreach (string arg in args) start.ArgumentList.Add(arg);
                }
                process = Process.Start(start)
                    ?? throw new IOException("無法啟動程式。");
                PortablePayload.Cleanup(cacheRoot, directory);
            }
            using (process)
            {
                if (!check) return 0;

                Task<string> output = process.StandardOutput.ReadToEndAsync();
                Task<string> error = process.StandardError.ReadToEndAsync();
                if (!process.WaitForExit(60_000))
                {
                    process.Kill(entireProcessTree: true);
                    throw new TimeoutException("內含的播放引擎啟動檢查逾時。");
                }
                Task.WaitAll(output, error);
                if (process.ExitCode != 0)
                    throw new IOException($"內含的播放引擎啟動檢查失敗：{error.Result}");
                Console.WriteLine(output.Result.Trim());
                Console.WriteLine($"Verified application: {directory}");
                return 0;
            }
        }
        catch (Exception exception)
        {
            string message = $"Office Music Bot 無法啟動。\n\n{exception.Message}\n\n" +
                "請重新下載 EXE。若展開的元件損壞，請關閉程式並刪除 " +
                "%LOCALAPPDATA%\\OfficeMusicBot\\App 後重試。" +
                "設定與瀏覽器登入資料存放在其他位置，不受影響。";
            Console.Error.WriteLine(message);
            if (!check) MessageBoxW(IntPtr.Zero, message, "Office Music Bot", 0x10);
            return 1;
        }
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    private static extern int MessageBoxW(IntPtr window, string text, string caption, uint type);
}

internal static class PortablePayload
{
    internal static readonly string[] RequiredFiles =
    [
        "OfficeMusicDesktop.exe", "OfficeMusicDesktop.pri", "App.xbf",
        "MainPage.xbf", "MainWindow.xbf", "Assets/AppIcon.ico",
        "Backend/OfficeMusicEngine.exe"
    ];

    internal static string Extract(Stream payload, string cacheRoot)
    {
        string hash = Convert.ToHexStringLower(SHA256.HashData(payload));
        payload.Position = 0;
        string directory = Path.Combine(cacheRoot, hash);
        using var mutex = new Mutex(false, ExtractionMutexName(CurrentUserSid(), hash));
        bool acquired = false;
        try
        {
            try
            {
                acquired = mutex.WaitOne(TimeSpan.FromMinutes(2));
            }
            catch (AbandonedMutexException)
            {
                acquired = true;
                LogWarning(cacheRoot, "A previous application extraction was interrupted.");
            }
            if (!acquired) throw new TimeoutException("另一個程式仍在展開元件，請稍後重試。");

            using var archive = new ZipArchive(payload, ZipArchiveMode.Read, leaveOpen: true);
            foreach (string file in RequiredFiles)
                if (archive.GetEntry(file) is null)
                    throw new InvalidDataException($"內含的程式缺少元件：{file}");

            if (!Directory.Exists(directory))
            {
                Directory.CreateDirectory(cacheRoot);
                string staging = Path.Combine(cacheRoot, $"{hash}.{Guid.NewGuid():N}.tmp");
                try
                {
                    archive.ExtractToDirectory(staging);
                    Verify(archive, staging);
                    Directory.Move(staging, directory);
                }
                finally
                {
                    if (Directory.Exists(staging)) Directory.Delete(staging, recursive: true);
                }
            }
            else
            {
                Verify(archive, directory);
            }
            return directory;
        }
        finally
        {
            if (acquired) mutex.ReleaseMutex();
        }
    }

    internal static IDisposable LockCache(string cacheRoot)
    {
        var mutex = new Mutex(false, CacheMutexName(CurrentUserSid()));
        try
        {
            bool acquired;
            try { acquired = mutex.WaitOne(TimeSpan.FromMinutes(2)); }
            catch (AbandonedMutexException)
            {
                acquired = true;
                LogWarning(cacheRoot, "A previous cache operation was interrupted.");
            }
            if (!acquired) throw new TimeoutException("另一個程式仍在處理元件，請稍後重試。");
            return new CacheLock(mutex);
        }
        catch
        {
            mutex.Dispose();
            throw;
        }
    }

    internal static string CurrentUserSid()
    {
        using WindowsIdentity identity = WindowsIdentity.GetCurrent();
        return identity.User?.Value
            ?? throw new InvalidOperationException("The current Windows identity has no SID.");
    }

    internal static string CacheMutexName(string sid) => MutexName(sid, "Cache");

    internal static string ExtractionMutexName(string sid, string hash) =>
        MutexName(sid, $"Extract.{hash}");

    private static string MutexName(string sid, string resource) =>
        $@"Global\OfficeMusicBot.{sid}.{resource}";

    internal static void Cleanup(string cacheRoot, string currentDirectory,
        IReadOnlyCollection<string>? runningExecutables = null)
    {
        try
        {
            using var cacheLock = LockCache(cacheRoot);
            if (!Directory.Exists(cacheRoot)) return;
            if ((File.GetAttributes(cacheRoot) & FileAttributes.ReparsePoint) != 0)
            {
                LogWarning(cacheRoot, $"Skipping reparse-point payload cache: {cacheRoot}");
                return;
            }

            string[] directories = Directory.GetDirectories(cacheRoot)
                .Where(path => IsHash(Path.GetFileName(path)) &&
                    Path.GetFullPath(path).StartsWith(
                        Path.GetFullPath(cacheRoot) + Path.DirectorySeparatorChar,
                        StringComparison.OrdinalIgnoreCase) &&
                    RequiredFiles.All(file => File.Exists(Path.Combine(path, file))) &&
                    !HasReparsePoint(path))
                .ToArray();
            string? recent = directories
                .Where(path => !path.Equals(currentDirectory, StringComparison.OrdinalIgnoreCase))
                .OrderByDescending(Directory.GetLastWriteTimeUtc)
                .FirstOrDefault();
            var running = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            if (runningExecutables is not null)
            {
                foreach (string executable in runningExecutables)
                    running.Add(Path.GetFullPath(executable));
            }
            else
            {
                foreach (string name in new[] { "OfficeMusicDesktop", "OfficeMusicEngine" })
                {
                    foreach (Process process in Process.GetProcessesByName(name))
                    {
                        using (process)
                        {
                            try
                            {
                                string? executable = process.MainModule?.FileName;
                                if (executable is null)
                                    throw new IOException($"Cannot determine executable for {name} (PID {process.Id}).");
                                running.Add(Path.GetFullPath(executable));
                            }
                            catch (InvalidOperationException exception)
                            {
                                if (!process.HasExited)
                                    throw new IOException(
                                        $"Cannot inspect {name} (PID {process.Id}).", exception);
                                LogWarning(cacheRoot, $"{name} exited during cache inspection.");
                            }
                        }
                    }
                }
            }
            foreach (string path in directories)
            {
                if (path.Equals(currentDirectory, StringComparison.OrdinalIgnoreCase) ||
                    path.Equals(recent, StringComparison.OrdinalIgnoreCase) ||
                    Directory.GetLastWriteTimeUtc(path) > DateTime.UtcNow.AddHours(-24) ||
                    running.Any(exe => exe.StartsWith(path + Path.DirectorySeparatorChar,
                        StringComparison.OrdinalIgnoreCase)))
                    continue;
                try
                {
                    using var extraction = new Mutex(false,
                        ExtractionMutexName(CurrentUserSid(), Path.GetFileName(path)));
                    bool acquired;
                    try { acquired = extraction.WaitOne(0); }
                    catch (AbandonedMutexException) { acquired = true; }
                    if (!acquired) continue;
                    try { Directory.Delete(path, recursive: true); }
                    finally { extraction.ReleaseMutex(); }
                }
                catch (Exception exception) when (exception is IOException or
                    UnauthorizedAccessException)
                {
                    LogWarning(cacheRoot, $"Could not remove obsolete payload {path}: {exception}");
                }
            }
        }
        catch (Exception exception) when (exception is IOException or
            UnauthorizedAccessException or Win32Exception or TimeoutException)
        {
            LogWarning(cacheRoot, $"Could not inspect obsolete payloads: {exception}");
        }
    }

    internal static void LogWarning(string cacheRoot, string message)
    {
        try
        {
            string directory = Path.GetDirectoryName(cacheRoot)
                ?? throw new IOException("Payload cache has no parent directory.");
            Directory.CreateDirectory(directory);
            File.AppendAllText(Path.Combine(directory, "launcher.log"),
                $"{DateTimeOffset.Now:O} WARNING {message}{Environment.NewLine}");
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            Trace.TraceWarning($"Launcher warning could not be saved: {message} ({exception})");
        }
    }

    private static bool IsHash(string name) =>
        name.Length == 64 && name.All(c => c is >= '0' and <= '9' or >= 'a' and <= 'f');

    private static bool HasReparsePoint(string path)
    {
        if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0) return true;
        foreach (string entry in Directory.EnumerateFileSystemEntries(path))
        {
            FileAttributes attributes = File.GetAttributes(entry);
            if ((attributes & FileAttributes.ReparsePoint) != 0 ||
                ((attributes & FileAttributes.Directory) != 0 && HasReparsePoint(entry)))
                return true;
        }
        return false;
    }

    private sealed class CacheLock(Mutex mutex) : IDisposable
    {
        public void Dispose()
        {
            mutex.ReleaseMutex();
            mutex.Dispose();
        }
    }

    private static void Verify(ZipArchive archive, string directory)
    {
        string root = Path.GetFullPath(directory) + Path.DirectorySeparatorChar;
        foreach (ZipArchiveEntry entry in archive.Entries)
        {
            string path = Path.GetFullPath(Path.Combine(directory, entry.FullName));
            if (!path.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("內含的程式元件路徑不正確。");
            if (entry.Name.Length == 0) continue;
            using Stream expected = entry.Open();
            using Stream actual = File.OpenRead(path);
            if (actual.Length != entry.Length ||
                !SHA256.HashData(expected).AsSpan().SequenceEqual(SHA256.HashData(actual)))
                throw new InvalidDataException($"展開的程式元件已損壞：{entry.FullName}");
        }
    }
}
