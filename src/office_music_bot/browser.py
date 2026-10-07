from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from aiohttp import WSMsgType, web

log = logging.getLogger(__name__)
PORT = 18765
EXTENSION_ID = "oogmlanepeobcjpkaipdhkjlaoncglph"
ORIGIN = f"chrome-extension://{EXTENSION_ID}"
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
HOSTS = {"music.youtube.com", "www.youtube.com", "youtube.com", "youtu.be"}


class PlaybackError(Exception):
    pass


def normalize_query(query: str) -> tuple[str, bool]:
    query = query.strip()
    if not query or len(query) > 300:
        raise PlaybackError("Enter a song/artist or single-song link (1-300 characters).")
    if query.startswith(("http:", "https:", "//")) or "://" in query:
        try:
            parsed = urlparse(query)
            valid_port = parsed.port in {None, 80, 443}
        except ValueError as exc:
            raise PlaybackError("Invalid YouTube link.") from exc
        if (
            parsed.scheme not in {"https", "http"}
            or parsed.hostname not in HOSTS
            or parsed.username
            or parsed.password
            or not valid_port
        ):
            raise PlaybackError("Only YouTube Music/YouTube single-song links are supported.")
        if parsed.hostname == "youtu.be":
            video_id = parsed.path.removeprefix("/")
        elif parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        else:
            video_id = ""
        if not VIDEO_ID.fullmatch(video_id):
            raise PlaybackError(
                "This is not a supported single-song link; playlists are not imported."
            )
        return f"https://music.youtube.com/watch?v={video_id}", True
    return query, False


@dataclass(frozen=True)
class Song:
    video_id: str
    title: str
    artist: str

    @property
    def url(self) -> str:
        return f"https://music.youtube.com/watch?v={self.video_id}"


@dataclass(frozen=True)
class Observation:
    song: Song | None
    position: float = 0
    paused: bool = True
    ended: bool = False
    advertisement: bool = False
    ready: bool = False
    generation: int = 0
    error: str | None = None


class MusicBrowser:
    """RPC to the explicitly installed extension, never to a debugging browser port."""

    def __init__(self, directory: Path, *, port: int = PORT):
        self.directory = directory
        self.port = port
        self._server: web.AppRunner | None = None
        self._socket: web.WebSocketResponse | None = None
        self._connected = asyncio.Event()
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0

    async def _connect(self, request: web.Request) -> web.WebSocketResponse:
        # The fixed manifest public key keeps the unpacked extension's origin stable.
        # This excludes websites/DNS rebinding; it is not protection from local malware.
        if request.host != f"127.0.0.1:{self.port}" or request.headers.get("Origin") != ORIGIN:
            raise web.HTTPForbidden()
        if self._socket is not None:
            raise web.HTTPConflict(text="An extension is already connected.")
        socket = web.WebSocketResponse(heartbeat=5, max_msg_size=65536)
        self._socket = socket
        try:
            await socket.prepare(request)
            self._connected.set()
            log.info("Chromium extension connected.")
            async for message in socket:
                if message.type == WSMsgType.TEXT:
                    try:
                        value = message.json()
                        if not isinstance(value, dict) or type(value.get("id")) is not int:
                            raise ValueError("Invalid RPC response")
                        future = self._pending.get(value["id"])
                        if future and not future.done():
                            if value.get("ok") is True:
                                future.set_result(value.get("data"))
                            else:
                                error = value.get("error")
                                future.set_exception(PlaybackError(
                                    error[:1000] if isinstance(error, str)
                                    else "Invalid extension response."
                                ))
                    except (ValueError, TypeError):
                        log.warning("Invalid extension response; closing connection.")
                        await socket.close(code=1008)
                elif message.type == WSMsgType.ERROR:
                    log.warning("Extension WebSocket failed.")
        finally:
            self._socket = None
            self._connected.clear()
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(PlaybackError(
                        "Extension disconnected. Keep Chromium open and use /resume to retry."
                    ))
            log.info("Chromium extension disconnected.")
        return socket

    async def start(self, *, interactive: bool = False) -> None:
        app = web.Application()
        app.router.add_get("/bridge", self._connect)
        self._server = web.AppRunner(app, access_log=None)
        try:
            await self._server.setup()
            await web.TCPSite(self._server, "127.0.0.1", self.port).start()
            async with asyncio.timeout(45):
                await self._connected.wait()
            await self._call("initialize", {"interactive": interactive})
        except TimeoutError as exc:
            await self.close()
            raise PlaybackError(
                "Install/enable Office Music Link in Chromium and keep the browser open, "
                "then restart. Waiting for extension at 127.0.0.1:18765 timed out."
            ) from exc
        except BaseException:
            await self.close()
            raise

    async def _call(self, command: str, args: dict | None = None) -> dict:
        socket = self._socket
        if socket is None or socket.closed:
            raise PlaybackError("Chromium extension is not connected. Open the browser and retry.")
        self._next_id += 1
        request_id = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await socket.send_json({"id": request_id, "command": command, "args": args or {}})
            async with asyncio.timeout(65):
                result = await future
            if not isinstance(result, dict):
                raise PlaybackError("Invalid extension result.")
            return result
        except TimeoutError as exc:
            # Never replay timed-out side effects after reconnecting.
            await socket.close(code=1011)
            raise PlaybackError(
                "Extension operation timed out. Check YouTube Music and retry."
            ) from exc
        except asyncio.CancelledError:
            await socket.close(code=1011)
            raise
        except (ConnectionError, RuntimeError) as exc:
            raise PlaybackError(
                "Extension connection closed. Reconnect Chromium and retry."
            ) from exc
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()

    @staticmethod
    def _song(state: dict) -> Song | None:
        video_id, title, artist = (state.get(key) for key in ("id", "title", "artist"))
        if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id):
            return None
        if not isinstance(title, str) or not title:
            return None
        if not isinstance(artist, str):
            raise PlaybackError("Invalid extension song metadata.")
        return Song(video_id, title[:500], artist[:500] or "Unknown artist")

    async def resolve(self, query: str) -> Song:
        value, is_link = normalize_query(query)
        song = self._song(await self._call("resolve", {"value": value, "isLink": is_link}))
        if song is None:
            raise PlaybackError("No playable song found. Try a more specific title/artist.")
        return song

    async def observe(self) -> Observation:
        state = await self._call("observe")
        try:
            for key in ("paused", "ended", "advertisement", "ready"):
                if type(state[key]) is not bool:
                    raise ValueError(key)
            if type(state["generation"]) is not int or state["generation"] < 0:
                raise ValueError("generation")
            position = state["position"]
            if type(position) not in (float, int) or not 0 <= position < 1e10:
                raise ValueError("position")
            if state["error"] is not None and not isinstance(state["error"], str):
                raise ValueError("error")
            return Observation(
                song=self._song(state), position=position, paused=state["paused"],
                ended=state["ended"], advertisement=state["advertisement"], ready=state["ready"],
                generation=state["generation"], error=state["error"],
            )
        except (KeyError, ValueError) as exc:
            raise PlaybackError("Invalid extension player state.") from exc

    async def policy(self, video_id: str | None, allow_next: bool, paused: bool) -> None:
        await self._call("policy", {"id": video_id, "next": allow_next, "paused": paused})

    async def play(self, song: Song, *, paused: bool = False) -> None:
        await self._call("play", {"url": song.url, "id": song.video_id, "paused": paused})

    async def pause(self) -> None:
        await self._call("pause")

    async def resume(self) -> None:
        await self._call("resume")

    async def next_recommendation(self) -> None:
        await self._call("next")

    async def clear_ended(self) -> None:
        await self._call("clearEnded")

    async def close(self) -> None:
        try:
            if self._socket and not self._socket.closed:
                await self._socket.close()
        finally:
            if self._server:
                await self._server.cleanup()
            self._server = None


async def probe(directory: Path) -> None:
    browser = MusicBrowser(directory)
    try:
        await browser.start()
        song = await browser.resolve("Rick Astley Never Gonna Give You Up")
        print(f"Resolved: {song.title} / {song.artist} / {song.url}")
        print("Extension/search probe only: no audible playback or Discord verified.")
    finally:
        await browser.close()
