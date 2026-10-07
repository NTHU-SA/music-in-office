import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from office_music_bot.browser import PlaybackError, Song, normalize_query
from office_music_bot.config import Config, ConfigurationError, InstanceLock, _protect, load_config
from office_music_bot.discord_bot import MusicBot, allowed_channel, now_playing_message
from office_music_bot.player import Playing


@pytest.mark.parametrize(
    "value",
    [
        "https://music.youtube.com/watch?v=aaaaaaaaaaa",
        "https://www.youtube.com/watch?v=aaaaaaaaaaa&list=not-imported",
        "https://youtu.be/aaaaaaaaaaa?t=10",
        "http://youtube.com/watch?v=aaaaaaaaaaa",
    ],
)
def test_links_normalized_to_single_music_song(value):
    assert normalize_query(value) == ("https://music.youtube.com/watch?v=aaaaaaaaaaa", True)


@pytest.mark.parametrize(
    "value",
    [
        "",
        " " * 3,
        "a" * 301,
        "https://evil.example/watch?v=aaaaaaaaaaa",
        "https://music.youtube.com.evil.example/watch?v=aaaaaaaaaaa",
        "https://music.youtube.com/playlist?list=123",
        "https://youtu.be/short",
        "file:///C:/secret",
        "https://user@youtube.com/watch?v=aaaaaaaaaaa",
        "https://youtube.com:invalid/watch?v=aaaaaaaaaaa",
        "//youtube.com/watch?v=aaaaaaaaaaa",
        "https://youtube.com/watch?v=aaaaaaaaaa!",
    ],
)
def test_bad_queries_are_explicit_errors(value):
    with pytest.raises(PlaybackError):
        normalize_query(value)


def test_unicode_search():
    assert normalize_query("  周杰倫 晴天  ") == ("周杰倫 晴天", False)


def test_missing_configuration_actionable(tmp_path, monkeypatch):
    for name in ("OFFICE_MUSIC_TOKEN", "OFFICE_MUSIC_GUILD_ID", "OFFICE_MUSIC_CHANNEL_ID"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ConfigurationError, match="--setup"):
        load_config(tmp_path)


def test_environment_configuration_and_no_token_repr(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFICE_MUSIC_TOKEN", "synthetic-test-token")
    monkeypatch.setenv("OFFICE_MUSIC_GUILD_ID", "123")
    monkeypatch.setenv("OFFICE_MUSIC_CHANNEL_ID", "456")
    config = load_config(tmp_path)
    assert config.guild_id == 123
    assert config.channel_id == 456
    assert "synthetic-test-token" not in repr(config)


@pytest.mark.parametrize("value", ["", "-1", "0", "wrong", "１", str(2**64)])
def test_invalid_discord_ids(value, tmp_path, monkeypatch):
    monkeypatch.setenv("OFFICE_MUSIC_TOKEN", "synthetic")
    monkeypatch.setenv("OFFICE_MUSIC_GUILD_ID", value)
    monkeypatch.setenv("OFFICE_MUSIC_CHANNEL_ID", "456")
    with pytest.raises(ConfigurationError, match="Server ID"):
        load_config(tmp_path)


def test_invalid_json(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps([]))
    with pytest.raises(ConfigurationError, match="Invalid config"):
        load_config(tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="DPAPI requires Windows")
def test_dpapi_round_trip():
    plaintext = b"synthetic-token"
    protected = _protect(plaintext)
    assert plaintext not in protected
    assert _protect(protected, decrypt=True) == plaintext


@pytest.mark.skipif(os.name != "nt", reason="Windows locking")
def test_single_instance(tmp_path):
    with InstanceLock(tmp_path):
        with pytest.raises(ConfigurationError, match="already running"):
            with InstanceLock(tmp_path):
                pass
    with InstanceLock(tmp_path):
        pass


def test_channel_gate_and_command_surface():
    config = Config("synthetic", 123, 456, Path("."))
    bot = MusicBot(config)
    assert {command.name for command in bot.tree.get_commands()} == {
        "play",
        "queue",
        "nowplaying",
        "pause",
        "resume",
        "skip",
        "autoplay",
    }
    assert not bot.intents.message_content
    assert not bot.intents.voice_states
    assert allowed_channel(config, 123, 456)
    assert not allowed_channel(config, 123, 789)
    assert not allowed_channel(config, None, 456)


def test_announcement_contents_and_mentions():
    song = Song("aaaaaaaaaaa", "@everyone Song", "**Artist**")
    message = now_playing_message(Playing(song, "@here Alice", 1))
    assert song.url in message
    assert "@everyone" not in message
    assert "@here" not in message
    assert "Alice" in message
    assert "Artist" in message
    automatic = now_playing_message(Playing(song, None, 2))
    assert "自動推薦" in automatic
    assert "點歌：" not in automatic


async def test_tree_rejects_wrong_channel_without_command_execution():
    bot = MusicBot(Config("synthetic", 123, 456, Path(".")))
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.guild_id = 123
    interaction.channel_id = 789
    interaction.response.send_message = AsyncMock()
    assert not await bot.tree.interaction_check(interaction)
    interaction.response.send_message.assert_awaited_once()


@pytest.mark.parametrize("send_allowed", [True, False])
async def test_setup_fetches_guild_roles_before_permission_check(send_allowed):
    bot = MusicBot(Config("synthetic", 123, 456, Path(".")))
    guild = MagicMock(spec=discord.Guild)
    guild.id = 123
    channel = MagicMock(spec=discord.TextChannel)
    channel.guild = guild
    channel.permissions_for.return_value = discord.Permissions(
        view_channel=True, send_messages=send_allowed
    )
    guild.fetch_channel = AsyncMock(return_value=channel)
    guild.fetch_member = AsyncMock()
    bot.fetch_guild = AsyncMock(return_value=guild)
    bot._connection.user = MagicMock(spec=discord.ClientUser)
    bot._connection.user.id = 999
    bot.tree.sync = AsyncMock()
    bot.browser.start = AsyncMock()
    if send_allowed:
        await bot.setup_hook()
        bot.tree.sync.assert_awaited_once_with(guild=bot.guild_object)
        bot.browser.start.assert_awaited_once()
    else:
        with pytest.raises(ConfigurationError, match="Send Messages"):
            await bot.setup_hook()
        bot.browser.start.assert_not_awaited()
    bot.fetch_guild.assert_awaited_once_with(123)
    guild.fetch_channel.assert_awaited_once_with(456)
    guild.fetch_member.assert_awaited_once_with(999)
