using System.Diagnostics;
using System.IO.Compression;
using OfficeMusicLauncher;

internal static class LauncherTests
{
    private static void Main(string[] args)
    {
        if (args is ["--hold-lock", string name])
        {
            using var mutex = new Mutex(false, name);
            mutex.WaitOne();
            Console.WriteLine("locked");
            Console.ReadLine();
            mutex.ReleaseMutex();
            return;
        }

        string root = Path.Combine(Path.GetTempPath(),
            $"OfficeMusicLauncherTests-{Guid.NewGuid():N}");
        try
        {
            TestMutexNames();
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
            TestCleanup(root);
            Console.WriteLine("Launcher tests passed: cross-process locks, extraction, reuse, " +
                "upgrades, concurrency, tampering, missing files, invalid paths and safe cleanup.");
        }
        finally
        {
            if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
        }
    }

    private static void TestMutexNames()
    {
        string sid = PortablePayload.CurrentUserSid();
        string otherSid = sid == "S-1-5-18" ? "S-1-5-19" : "S-1-5-18";
        string hash = new string('a', 64);
        string cacheName = PortablePayload.CacheMutexName(sid);
        string extractionName = PortablePayload.ExtractionMutexName(sid, hash);
        Assert(cacheName == $@"Global\OfficeMusicBot.{sid}.Cache" &&
            extractionName == $@"Global\OfficeMusicBot.{sid}.Extract.{hash}",
            "Both locks must use the same cross-session, SID-keyed naming scheme.");
        Assert(cacheName == PortablePayload.CacheMutexName(sid) &&
            extractionName == PortablePayload.ExtractionMutexName(sid, hash),
            "The same user and payload must share the same lock.");
        Assert(cacheName != PortablePayload.CacheMutexName(otherSid) &&
            extractionName != PortablePayload.ExtractionMutexName(otherSid, hash),
            "Different users must have independent locks.");

        WithHeldLockInChild(cacheName, () =>
        {
            using var otherUser = new Mutex(false, PortablePayload.CacheMutexName(otherSid));
            Assert(otherUser.WaitOne(0), "Another user's cache lock must remain independent.");
            otherUser.ReleaseMutex();
        });
        WithHeldLockInChild(extractionName, () => { });
    }

    private static void WithHeldLockInChild(string name, Action whileHeld)
    {
        var start = new ProcessStartInfo(Environment.ProcessPath!)
        {
            UseShellExecute = false,
            RedirectStandardInput = true,
            RedirectStandardOutput = true
        };
        start.ArgumentList.Add("--hold-lock");
        start.ArgumentList.Add(name);
        using Process child = Process.Start(start)
            ?? throw new InvalidOperationException("Could not start lock holder.");
        try
        {
            Task<string?> ready = child.StandardOutput.ReadLineAsync();
            Assert(ready.Wait(TimeSpan.FromSeconds(15)) && ready.Result == "locked",
                "The child process must acquire the named lock.");
            using var contender = new Mutex(false, name);
            Assert(!contender.WaitOne(0), "A second process must not acquire the held lock.");
            whileHeld();
            child.StandardInput.WriteLine();
            Assert(child.WaitForExit(15_000) && child.ExitCode == 0,
                "The child process must release the named lock.");
            Assert(contender.WaitOne(0), "The lock must be available after child exit.");
            contender.ReleaseMutex();
        }
        finally
        {
            if (!child.HasExited)
            {
                child.StandardInput.WriteLine();
                if (!child.WaitForExit(5_000)) child.Kill();
            }
        }
    }

    private static void TestCleanup(string root)
    {
        string cache = Path.Combine(root, "cleanup");
        string obsolete = Extract(CreatePayload("obsolete"), cache);
        string runningDesktop = Extract(CreatePayload("running desktop"), cache);
        string runningBackend = Extract(CreatePayload("running backend"), cache);
        string recent = Extract(CreatePayload("recent"), cache);
        string fresh = Extract(CreatePayload("fresh unused"), cache);
        string current = Extract(CreatePayload("current"), cache);
        Directory.SetLastWriteTimeUtc(obsolete, DateTime.UtcNow.AddDays(-5));
        Directory.SetLastWriteTimeUtc(runningDesktop, DateTime.UtcNow.AddDays(-4));
        Directory.SetLastWriteTimeUtc(runningBackend, DateTime.UtcNow.AddDays(-3));
        Directory.SetLastWriteTimeUtc(recent, DateTime.UtcNow.AddHours(-1));
        Directory.SetLastWriteTimeUtc(fresh, DateTime.UtcNow.AddHours(-6));
        Directory.SetLastWriteTimeUtc(current, DateTime.UtcNow);
        string unrelated = Path.Combine(cache, "not-a-payload");
        Directory.CreateDirectory(unrelated);
        File.WriteAllText(Path.Combine(unrelated, "keep"), "settings");
        string unverified = Path.Combine(cache, new string('a', 64));
        Directory.CreateDirectory(unverified);
        File.WriteAllText(Path.Combine(unverified, "keep"), "unknown");
        string outside = Path.Combine(root, "user-profile");
        Directory.CreateDirectory(outside);
        File.WriteAllText(Path.Combine(outside, "keep"), "profile");

        PortablePayload.Cleanup(cache, current,
        [
            Path.Combine(runningDesktop, "OfficeMusicDesktop.exe"),
            Path.Combine(runningBackend, "Backend", "OfficeMusicEngine.exe")
        ]);
        Assert(!Directory.Exists(obsolete), "Unused verified old payload should be removed.");
        Assert(Directory.Exists(runningDesktop) && Directory.Exists(runningBackend),
            "Running desktop and backend payloads must be preserved.");
        Assert(Directory.Exists(recent) && Directory.Exists(current),
            "Current and most recent payloads must be retained.");
        Assert(Directory.Exists(fresh),
            "Unused payloads less than 24 hours old must be retained even if not most recent.");
        Assert(File.Exists(Path.Combine(unrelated, "keep")) &&
            File.Exists(Path.Combine(unverified, "keep")) &&
            File.Exists(Path.Combine(outside, "keep")),
            "Unverified directories and user data must be untouched.");

        Task crossProcessCleanup = Task.CompletedTask;
        WithHeldLockInChild(PortablePayload.CacheMutexName(PortablePayload.CurrentUserSid()), () =>
        {
            crossProcessCleanup = Task.Run(() => PortablePayload.Cleanup(cache, current,
                [Path.Combine(runningDesktop, "OfficeMusicDesktop.exe"),
                 Path.Combine(runningBackend, "Backend", "OfficeMusicEngine.exe")]));
            Assert(!crossProcessCleanup.Wait(200),
                "Cleanup must wait for the cache lock held by another process.");
        });
        crossProcessCleanup.GetAwaiter().GetResult();

        Task cleanup;
        using (PortablePayload.LockCache(cache))
        {
            cleanup = Task.Run(() => PortablePayload.Cleanup(cache, current,
                [Path.Combine(runningDesktop, "OfficeMusicDesktop.exe"),
                 Path.Combine(runningBackend, "Backend", "OfficeMusicEngine.exe")]));
            Assert(!cleanup.Wait(200), "Cleanup must wait for an in-progress launch.");
        }
        cleanup.GetAwaiter().GetResult();
        PortablePayload.Cleanup(cache, current, []);
        Assert(!Directory.Exists(runningDesktop) && !Directory.Exists(runningBackend),
            "Previously running payloads should eventually be reclaimed.");
        Directory.SetLastWriteTimeUtc(fresh, DateTime.UtcNow.AddDays(-2));
        Directory.SetLastWriteTimeUtc(recent, DateTime.UtcNow.AddHours(-25));
        PortablePayload.Cleanup(cache, current, []);
        Assert(!Directory.Exists(fresh), "Unused payload should expire after the grace period.");
        Assert(Directory.Exists(recent),
            "The most recent previous payload must survive beyond the grace period.");

        string blocked = Extract(CreatePayload("blocked"), cache);
        Directory.SetLastWriteTimeUtc(blocked, DateTime.UtcNow.AddDays(-7));
        using (File.Open(Path.Combine(blocked, "OfficeMusicDesktop.exe"), FileMode.Open,
            FileAccess.Read, FileShare.None))
            PortablePayload.Cleanup(cache, current, []);
        string log = Path.Combine(root, "launcher.log");
        Assert(File.Exists(log) && File.ReadAllText(log).Contains("Could not remove obsolete payload"),
            "Deletion failures must be written to launcher.log.");

        string extractionBlocked = Extract(CreatePayload("extraction blocked"), cache);
        Directory.SetLastWriteTimeUtc(extractionBlocked, DateTime.UtcNow.AddDays(-7));
        WithHeldLockInChild(PortablePayload.ExtractionMutexName(
            PortablePayload.CurrentUserSid(), Path.GetFileName(extractionBlocked)), () =>
        {
            PortablePayload.Cleanup(cache, current, []);
            Assert(Directory.Exists(extractionBlocked),
                "Cleanup must not delete a payload locked by extraction in another process.");
        });
        PortablePayload.Cleanup(cache, current, []);
        Assert(!Directory.Exists(extractionBlocked),
            "Cleanup must reclaim the payload once its extraction lock is released.");
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
