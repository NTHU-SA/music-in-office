using System.Diagnostics;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;

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
            string directory = PortablePayload.Extract(payload, cacheRoot);
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
            using Process process = Process.Start(start)
                ?? throw new IOException("無法啟動程式。");
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
        using var mutex = new Mutex(false, $@"Local\OfficeMusicBot.Extract.{hash}");
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
                Trace.TraceWarning("A previous application extraction was interrupted.");
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
