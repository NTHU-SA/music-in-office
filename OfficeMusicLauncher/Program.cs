using System.Diagnostics;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;

internal static class Program
{
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int MessageBoxW(IntPtr window, string text, string caption, uint type);

    private static int Main(string[] args)
    {
        try
        {
            using var payload = Assembly.GetExecutingAssembly().GetManifestResourceStream("DesktopPayload")
                ?? throw new IOException("The desktop payload is missing.");
            string hash = Convert.ToHexString(SHA256.HashData(payload)).ToLowerInvariant();
            payload.Position = 0;
            string directory = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "OfficeMusicBot", "app", hash);
            using var mutex = new Mutex(false, $"Local\\OfficeMusicPayload-{hash}");
            bool acquired;
            try { acquired = mutex.WaitOne(TimeSpan.FromSeconds(60)); }
            catch (AbandonedMutexException) { acquired = true; }
            if (!acquired) throw new IOException("Another process is extracting the app. Please retry.");
            try
            {
                string marker = Path.Combine(directory, ".complete");
                if (!File.Exists(marker))
                {
                    Directory.CreateDirectory(directory);
                    using var archive = new ZipArchive(payload, ZipArchiveMode.Read);
                    archive.ExtractToDirectory(directory, overwriteFiles: true);
                    File.WriteAllText(marker, hash);
                }
            }
            finally { mutex.ReleaseMutex(); }
            string desktop = Path.Combine(directory, "OfficeMusicDesktop.exe");
            string engine = Path.Combine(directory, "Backend", "OfficeMusicEngine.exe");
            if (!File.Exists(desktop) || !File.Exists(engine))
                throw new IOException("The extracted app is incomplete. Re-download OfficeMusicBot.exe.");
            if (args is ["--verify-payload"])
                return 0;
            using var process = Process.Start(new ProcessStartInfo(desktop)
            {
                UseShellExecute = false,
                WorkingDirectory = directory
            }) ?? throw new IOException("The desktop app could not start.");
            process.WaitForExit();
            if (process.ExitCode != 0)
                throw new IOException($"The app exited with code {process.ExitCode}. " +
                    "Install .NET Desktop Runtime 10 (x64) and Windows App Runtime 2.5 (x64). " +
                    "See %LOCALAPPDATA%\\OfficeMusicBot\\desktop.log for details.");
            return 0;
        }
        catch (Exception exception) when (
            exception is IOException or UnauthorizedAccessException or InvalidDataException
            or System.ComponentModel.Win32Exception)
        {
            MessageBoxW(IntPtr.Zero, exception.Message, "Office Music Bot", 0x10);
            return 1;
        }
    }
}
