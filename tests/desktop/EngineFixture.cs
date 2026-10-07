#:property TargetFramework=net10.0
#:property PublishAot=false

using System.Text;
using System.Text.Json;

Console.InputEncoding = Encoding.UTF8;
Console.OutputEncoding = new UTF8Encoding(false);
var settings = new { token = "synthetic-ui-token", guild_id = "123", channel_id = "456" };

void Send(object packet) => Console.WriteLine(JsonSerializer.Serialize(packet));
object Track(string error = "", bool paused = false, bool autoplay = true,
             string? requester = "Office Alice", int queueCount = 3) => new
{
    kind = "track",
    data = new
    {
        title = "GUI fixture song",
        artist = "Test artist",
        requester,
        url = "https://example.test/song",
        queue_count = queueCount,
        autoplay,
        confirmed = true,
        paused,
        error
    }
};

while (Console.ReadLine() is { } line)
{
    using var document = JsonDocument.Parse(line);
    var request = document.RootElement;
    long id = request.GetProperty("id").GetInt64();
    string? command = request.GetProperty("command").GetString();
    if (command == "load")
    {
        Send(new { kind = "response", id, ok = true, settings });
        continue;
    }
    if (command is "save" or "start")
    {
        var input = request.GetProperty("settings");
        settings = new
        {
            token = input.GetProperty("token").GetString()!,
            guild_id = input.GetProperty("guild_id").GetString()!,
            channel_id = input.GetProperty("channel_id").GetString()!
        };
    }
    if (command == "stop") Send(new { kind = "finished" });
    Send(new { kind = "response", id, ok = true, message = "Fixture settings saved." });
    if (command == "quit") break;
    if (command != "start") continue;
    if (settings.guild_id == "888")
    {
        Send(new { kind = "track", data = new { title = (string?)null } });
        continue;
    }
    if (settings.guild_id == "999") Environment.Exit(12);
    if (settings.guild_id == "456")
    {
        Send(new { kind = "error", message = "Immediate fixture connection failure." });
        Send(new { kind = "finished" });
        continue;
    }
    Send(new { kind = "ready", message = "Fixture connection ready; no real Discord/audio." });
    Send(Track());
    if (settings.guild_id == "321")
    {
        Send(new { kind = "paused", message = "Fixture playback paused." });
        Send(Track(paused: true, autoplay: false, requester: null, queueCount: 0));
    }
    if (settings.guild_id == "789")
    {
        Send(new { kind = "playback_error", message = "Fixture player blocked." });
        Send(Track("Fixture player blocked."));
    }
}
