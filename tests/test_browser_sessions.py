import asyncio
import base64
import hashlib
import json
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

from office_music_bot.browser import (
    EXTENSION_ID,
    ORIGIN,
    MusicBrowser,
    PlaybackError,
    Song,
)


@pytest.fixture
async def service(tmp_path):
    browser = MusicBrowser(tmp_path, port=0)
    app = web.Application()
    app.router.add_get("/bridge", browser._connect)
    browser._server = web.AppRunner(app, access_log=None)
    await browser._server.setup()
    site = web.TCPSite(browser._server, "127.0.0.1", 0)
    await site.start()
    browser.port = site._server.sockets[0].getsockname()[1]
    async with aiohttp.ClientSession() as client:
        yield browser, client, f"http://127.0.0.1:{browser.port}/bridge"
    await browser.close()


def test_manifest_identity_matches_server():
    manifest = json.loads(Path("extension/manifest.json").read_text())
    digest = hashlib.sha256(base64.b64decode(manifest["key"])).hexdigest()[:32]
    extension_id = "".join(chr(ord("a") + int(char, 16)) for char in digest)
    assert extension_id == EXTENSION_ID
    assert int(manifest["minimum_chrome_version"]) >= 120
    assert manifest["permissions"] == ["scripting", "storage", "alarms"]


@pytest.mark.parametrize("origin", [None, "null", "https://music.youtube.com",
                                     "https://evil.example", "chrome-extension://wrong"])
async def test_rejects_untrusted_origins(service, origin):
    browser, client, url = service
    with pytest.raises(aiohttp.WSServerHandshakeError) as error:
        await client.ws_connect(url, origin=origin)
    assert error.value.status == 403
    assert browser._socket is None


async def test_rejects_dns_rebinding_host_and_second_browser(service):
    browser, client, url = service
    with pytest.raises(aiohttp.WSServerHandshakeError) as error:
        await client.ws_connect(url, origin=ORIGIN, headers={"Host": "evil.example"})
    assert error.value.status == 403
    async with client.ws_connect(url, origin=ORIGIN):
        assert browser._connected.is_set()
        with pytest.raises(aiohttp.WSServerHandshakeError) as error:
            await client.ws_connect(url, origin=ORIGIN)
        assert error.value.status == 409


async def respond(socket, data=None, *, error=None):
    command = await socket.receive_json()
    await socket.send_json({
        "id": command["id"], "ok": error is None, "data": data or {}, "error": error,
    })
    return command


async def test_song_resolution_and_protocol_error_are_explicit(service):
    browser, client, url = service
    async with client.ws_connect(url, origin=ORIGIN) as socket:
        resolving = asyncio.create_task(browser.resolve(" https://youtu.be/aaaaaaaaaaa "))
        command = await respond(socket, {"id": "aaaaaaaaaaa", "title": "Song", "artist": ""})
        assert command["args"] == {
            "value": "https://music.youtube.com/watch?v=aaaaaaaaaaa", "isLink": True,
        }
        assert await resolving == Song("aaaaaaaaaaa", "Song", "Unknown artist")
        operation = asyncio.create_task(browser.resume())
        await respond(socket, error="Click Play once in Chromium.")
        with pytest.raises(PlaybackError, match="Click Play"):
            await operation
        assert not browser._pending


async def test_disconnect_fails_inflight_and_reconnect_does_not_replay(service):
    browser, client, url = service
    socket = await client.ws_connect(url, origin=ORIGIN)
    pending = asyncio.create_task(browser.play(Song("aaaaaaaaaaa", "Song", "Artist")))
    assert (await socket.receive_json())["command"] == "play"
    await socket.close()
    with pytest.raises(PlaybackError, match="disconnected"):
        await pending
    async with client.ws_connect(url, origin=ORIGIN) as reconnected:
        pause = asyncio.create_task(browser.pause())
        assert (await respond(reconnected))["command"] == "pause"
        await pause
    assert not browser._pending


async def test_observation_validates_types_and_reports_ads(service):
    browser, client, url = service
    state = {
        "id": "aaaaaaaaaaa", "title": "Song", "artist": "Artist", "position": 12.5,
        "paused": False, "ended": False, "advertisement": True, "ready": True,
        "generation": 2, "error": None,
    }
    async with client.ws_connect(url, origin=ORIGIN) as socket:
        pending = asyncio.create_task(browser.observe())
        await respond(socket, state)
        observation = await pending
        assert observation.advertisement and observation.position == 12.5
        state["paused"] = "false"
        pending = asyncio.create_task(browser.observe())
        await respond(socket, state)
        with pytest.raises(PlaybackError, match="Invalid extension player state"):
            await pending


async def test_malformed_reply_disconnects_and_fails_operation(service):
    browser, client, url = service
    async with client.ws_connect(url, origin=ORIGIN) as socket:
        pending = asyncio.create_task(browser.resume())
        await socket.receive_json()
        await socket.send_str("not json")
        with pytest.raises(PlaybackError, match="disconnected"):
            await pending


async def test_closed_adapter_and_canceled_start_cleanup(tmp_path):
    browser = MusicBrowser(tmp_path, port=0)
    with pytest.raises(PlaybackError, match="not connected"):
        await browser.observe()
    opening = asyncio.create_task(browser.start())
    while browser._server is None or not browser._server.addresses:
        await asyncio.sleep(0.01)
    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await opening
    assert browser._server is None
