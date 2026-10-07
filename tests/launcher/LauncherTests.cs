using System.IO.Compression;
using OfficeMusicLauncher;

internal static class LauncherTests
{
    private static void Main()
    {
        string root = Path.Combine(Path.GetTempPath(), $"OfficeMusicLauncherTests-{Guid.NewGuid():N}");
        try
        {
            byte[] payload = CreatePayload();
            string directory = Extract(payload, root);
            Assert(Directory.GetFiles(directory, "*", SearchOption.AllDirectories).Length ==
                PortablePayload.RequiredFiles.Length, "All payload files must be extracted.");
            Assert(Extract(payload, root) == directory, "Repeated launches must reuse the cache.");
            Assert(Extract(CreatePayload("new version"), root) != directory,
                "Different payloads must use separate caches.");
            Parallel.For(0, 4, _ => Extract(payload, Path.Combine(root, "concurrent")));
            Assert(!Directory.GetDirectories(root, "*.tmp", SearchOption.AllDirectories).Any(),
                "Extraction must not leave staging directories.");

            File.WriteAllText(Path.Combine(directory, "OfficeMusicDesktop.exe"), "tampered");
            ExpectFailure<InvalidDataException>(() => Extract(payload, root));
            File.WriteAllText(Path.Combine(directory, "OfficeMusicDesktop.exe"), "fixture");
            File.Delete(Path.Combine(directory, "App.xbf"));
            ExpectFailure<IOException>(() => Extract(payload, root));
            ExpectFailure<InvalidDataException>(() => Extract(CreatePayload(omitRequired: true), root));
            ExpectFailure<IOException>(() => Extract(CreatePayload(invalidPath: true), root));
            Assert(!File.Exists(Path.Combine(root, "escaped.txt")), "Paths must not escape the cache.");
            Console.WriteLine("Launcher tests passed: extraction, reuse, upgrades, concurrency, " +
                "tampering, missing files and invalid paths.");
        }
        finally
        {
            if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
        }
    }

    private static byte[] CreatePayload(string content = "fixture", bool omitRequired = false,
        bool invalidPath = false)
    {
        using var stream = new MemoryStream();
        using (var archive = new ZipArchive(stream, ZipArchiveMode.Create, leaveOpen: true))
        {
            foreach (string file in PortablePayload.RequiredFiles)
            {
                if (omitRequired && file == "App.xbf") continue;
                using var writer = new StreamWriter(archive.CreateEntry(file).Open());
                writer.Write(content);
            }
            if (invalidPath)
            {
                using var writer = new StreamWriter(archive.CreateEntry("../escaped.txt").Open());
                writer.Write(content);
            }
        }
        return stream.ToArray();
    }

    private static string Extract(byte[] payload, string root)
    {
        using var stream = new MemoryStream(payload);
        return PortablePayload.Extract(stream, root);
    }

    private static void Assert(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }

    private static void ExpectFailure<T>(Action action) where T : Exception
    {
        try { action(); }
        catch (T) { return; }
        throw new InvalidOperationException($"Expected {typeof(T).Name}.");
    }
}
