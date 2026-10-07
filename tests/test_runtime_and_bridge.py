import asyncio
import io
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from office_music_bot.bridge import handle_request, run_bridge
from office_music_bot.config import (
    Config,
    ConfigurationError,
    Settings,
    load_config,
    load_settings,
    save_settings,
)
from office_music_bot.runtime import BotRunner


@pytest.fixture(autouse=True)
def no_environment_credentials(monkeypatch):
    for name in ("OFFICE_MUSIC_TOKEN", "OFFICE_MUSIC_GUILD_ID", "OFFICE_MUSIC_CHANNEL_ID"):
        monkeypatch.delenv(name, raising=False)


def test_settings_reload_encrypted_and_partial_inputs_are_retained(tmp_path):
    settings = Settings("synthetic-test-token", "invalid", "")
    save_settings(tmp_path, settings)
    assert settings.token not in (tmp_path / "config.json").read_text()
    assert load_settings(tmp_path) == settings
    with pytest.raises(ConfigurationError, match="Server ID"):
        load_config(tmp_path)
    assert not list(tmp_path.glob("*.tmp"))


def test_empty_and_legacy_settings(tmp_path):
    save_settings(tmp_path, Settings("", "123", ""))
    assert load_settings(tmp_path) == Settings("", "123", "")
    save_settings(tmp_path, Settings("synthetic", "123", "456"))
    path = tmp_path / "config.json"
    saved = json.loads(path.read_text())
    saved.update(guild_id=123, channel_id=456)
    path.write_text(json.dumps(saved))
    assert load_settings(tmp_path) == Settings("synthetic", "123", "456")


def request(command="start", guild="invalid"):
    return {
        "id": 1,
        "command": command,
        "settings": {"token": "synthetic", "guild_id": guild, "channel_id": "456"},
    }


def test_failed_validation_saves_inputs_and_subsequent_retry_works(tmp_path):
    runner = BotRunner()
    runner.start = Mock()
    result = handle_request(request(), tmp_path, runner)
    assert not result["ok"]
    assert "Server ID" in result["message"]
    assert load_settings(tmp_path).guild_id == "invalid"
    runner.start.assert_not_called()
    assert handle_request(request(guild="123"), tmp_path, runner)["ok"]
    runner.start.assert_called_once()
    loaded = handle_request({"id": 3, "command": "load"}, tmp_path, runner)
    assert loaded["settings"]["guild_id"] == "123"


def test_bridge_save_failure_explicit_and_old_settings_preserved(tmp_path, monkeypatch):
    save_settings(tmp_path, Settings("previous-synthetic", "123", "456"))
    monkeypatch.setattr(
        "office_music_bot.bridge.save_settings", Mock(side_effect=PermissionError("test"))
    )
    result = handle_request(request("save"), tmp_path, BotRunner())
    assert not result["ok"]
    assert load_settings(tmp_path).token == "previous-synthetic"


def test_ipc_protocol_stays_alive_after_bad_input_and_bad_configuration(tmp_path):
    source = io.StringIO(
        "invalid-json\n"
        + json.dumps(request())
        + "\n"
        + json.dumps(request("save", "123"))
        + "\n"
        + '{"id":3,"command":"load"}\n{"id":4,"command":"quit"}\n'
    )
    output = io.StringIO()
    assert run_bridge(tmp_path, source=source, destination=output) == 0
    results = [json.loads(line) for line in output.getvalue().splitlines()]
    assert results[0]["kind"] == "error"
    assert not results[1]["ok"]
    assert results[2]["ok"]
    assert results[3]["settings"]["guild_id"] == "123"
    assert results[4]["ok"]


class FakeBot:
    def __init__(self, config, *, error=None):
        self.error = error
        self.fatal_error = None
        self.ready = False
        self.closed = False
        self.started = threading.Event()
        self.player = SimpleNamespace(
            snapshot=lambda: SimpleNamespace(
                current=None, queue=(), error=None, paused=False, autoplay=True, confirmed=False
            )
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def start(self, token):
        self.started.set()
        if self.error:
            raise self.error
        self.ready = True
        await asyncio.Future()

    def is_ready(self):
        return self.ready

    async def close(self):
        self.closed = True


def events(runner):
    values = []
    while not runner.events.empty():
        values.append(runner.events.get_nowait())
    return values


def test_invalid_token_can_retry_without_closing_desktop(tmp_path):
    calls = []

    def factory(config):
        bot = FakeBot(config, error=discord.LoginFailure("synthetic"))
        calls.append(bot)
        return bot

    runner = BotRunner(factory)
    config = Config("synthetic", 123, 456, tmp_path)
    runner.start(config)
    runner.thread.join(3)
    assert not runner.busy
    result = events(runner)
    assert any(e.kind == "error" and "token" in e.message for e in result)
    assert result[-1].kind == "finished"
    assert calls[0].closed
    runner.start(config)
    runner.thread.join(3)
    assert len(calls) == 2
    assert not runner.busy


def test_stop_during_connection_closes_owned_bot_without_thread_error(tmp_path):
    bots = []
    created = threading.Event()

    def factory(config):
        bot = FakeBot(config)
        bots.append(bot)
        created.set()
        return bot

    runner = BotRunner(factory)
    runner.start(Config("synthetic", 123, 456, tmp_path))
    assert created.wait(2)
    assert bots[0].started.wait(2)
    runner.stop()
    runner.thread.join(3)
    assert not runner.busy
    assert bots[0].closed
    result = events(runner)
    assert result[-1].kind == "finished"
    assert not any(e.kind == "error" for e in result)


def test_unexpected_backend_failure_is_recoverable(tmp_path):
    runner = BotRunner(lambda config: FakeBot(config, error=RuntimeError("synthetic")))
    runner.start(Config("synthetic", 123, 456, tmp_path))
    runner.thread.join(3)
    assert not runner.busy
    assert any("RuntimeError" in e.message for e in events(runner))


def test_bridge_login_needs_no_discord_settings_and_cannot_overlap(tmp_path):
    runner = BotRunner()
    runner.start_login = Mock()
    assert handle_request({"command": "login"}, tmp_path, runner)["ok"]
    runner.start_login.assert_called_once_with(tmp_path)
    runner.start_login.side_effect = ConfigurationError(
        "Another OfficeMusicBot instance is already running."
    )
    assert not handle_request({"command": "login"}, tmp_path, runner)["ok"]


@pytest.mark.parametrize("close_window", [False, True])
async def test_login_closes_browser_without_connecting_discord(
    tmp_path, monkeypatch, close_window
):
    page = Mock(is_closed=Mock(return_value=False))
    browser = Mock(
        context=SimpleNamespace(pages=[page]), start=AsyncMock(), close=AsyncMock()
    )
    monkeypatch.setattr("office_music_bot.runtime.MusicBrowser", Mock(return_value=browser))
    runner = BotRunner(Mock(side_effect=AssertionError("Discord must not start")))
    session = asyncio.create_task(runner._login_session(tmp_path))
    try:
        async with asyncio.timeout(2):
            while not any(event.kind == "login_ready" for event in events(runner)):
                await asyncio.sleep(0.01)
        if close_window:
            page.is_closed.return_value = True
        else:
            runner.stop()
        await asyncio.wait_for(session, 2)
        browser.start.assert_awaited_once_with(interactive=True)
        browser.close.assert_awaited_once()
        assert any(event.kind == "login_closed" for event in events(runner))
    finally:
        session.cancel()
        await asyncio.gather(session, return_exceptions=True)


async def test_stop_during_login_launch_cancels_and_closes_browser(tmp_path, monkeypatch):
    opening = asyncio.Event()

    async def start(**kwargs):
        opening.set()
        await asyncio.Future()

    browser = Mock(start=start, close=AsyncMock())
    monkeypatch.setattr("office_music_bot.runtime.MusicBrowser", Mock(return_value=browser))
    runner = BotRunner()
    session = asyncio.create_task(runner._login_session(tmp_path))
    await asyncio.wait_for(opening.wait(), 2)
    runner.stop()
    await asyncio.wait_for(session, 2)
    browser.close.assert_awaited_once()


def test_login_session_shares_instance_lock_and_can_retry(tmp_path, monkeypatch):
    from office_music_bot.config import InstanceLock

    runner = BotRunner()
    runner._login_session = AsyncMock()
    with InstanceLock(tmp_path):
        runner.start_login(tmp_path)
        runner.thread.join(3)
    assert not runner.busy
    assert any(event.kind == "error" for event in events(runner))
    runner._login_session.assert_not_awaited()
    runner.start_login(tmp_path)
    runner.thread.join(3)
    runner._login_session.assert_awaited_once_with(tmp_path)
    assert events(runner)[-1].kind == "finished"


@pytest.mark.asyncio
async def test_track_events_include_requester_changes_and_playback_errors(tmp_path):
    config = Config("synthetic", 123, 456, tmp_path)
    bot = FakeBot(config)
    snapshot = SimpleNamespace(
        current=SimpleNamespace(
            song=SimpleNamespace(
                title="Test song", artist="Test artist", url="https://example.test"
            ),
            requester="First requester",
        ),
        queue=(),
        error=None,
        paused=False,
        autoplay=True,
        confirmed=True,
    )
    bot.player.snapshot = lambda: snapshot
    runner = BotRunner(lambda config: bot)
    session = asyncio.create_task(runner._session(config))

    async def next_track():
        async with asyncio.timeout(3):
            while True:
                for event in events(runner):
                    if event.kind == "track":
                        return event.data
                await asyncio.sleep(0.01)

    try:
        track = await next_track()
        assert track["title"] == "Test song"
        assert track["confirmed"] is True
        assert track["requester"] == "First requester"
        snapshot.current.requester = "Second requester"
        assert (await next_track())["requester"] == "Second requester"
        snapshot.error = "Player blocked"
        assert (await next_track())["error"] == "Player blocked"
    finally:
        runner.stop()
        await asyncio.wait_for(session, 3)
