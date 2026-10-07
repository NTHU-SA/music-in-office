from __future__ import annotations

import asyncio
import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import discord

from .browser import MusicBrowser, PlaybackError
from .config import Config, ConfigurationError, InstanceLock
from .discord_bot import MusicBot

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeEvent:
    kind: str
    message: str = ""
    data: dict | None = None


def error_message(error: BaseException) -> str:
    if isinstance(error, discord.LoginFailure):
        return "Discord 拒絕這個 token。請確認填入的是 Bot Token，修改後按「儲存並重試」。"
    if isinstance(error, discord.Forbidden):
        return "Bot 沒有存取權。請確認已邀請到伺服器，並允許查看及傳送訊息。"
    if isinstance(error, discord.NotFound):
        return "找不到伺服器或頻道。請確認兩個 ID，並確認 bot 已加入該伺服器。"
    if isinstance(error, discord.HTTPException):
        return f"Discord 連線失敗（HTTP {error.status}）。請檢查網路及 bot 的權限後重試。"
    if isinstance(error, ConfigurationError):
        text = str(error)
        if "already running" in text:
            return "另一個 Office Music Bot 正在執行。請先停止它，再重試。"
        if "Server ID" in text:
            return "Server ID 需要是有效的數字 ID。開啟 Discord 開發者模式後複製伺服器 ID。"
        if "Channel ID" in text:
            return "Channel ID 需要是有效的數字 ID。請對點歌文字頻道按右鍵複製 ID。"
        if "token" in text.lower():
            return "請填入有效的 Bot Token；不能留白或含有空白。"
        return f"設定無法使用：{text}"
    if isinstance(error, PlaybackError):
        return f"播放器需要處理：{error}。處理後可重新啟動，視窗與設定不會消失。"
    if isinstance(error, (TimeoutError, OSError)):
        return "連線或本機存取失敗。請檢查網路、Edge 與設定資料夾的寫入權限後重試。"
    return f"執行發生錯誤（{type(error).__name__}）。請重試；若持續發生，查看 bot.log。"


class BotRunner:
    def __init__(self, factory: Callable[[Config], MusicBot] = MusicBot):
        self.factory = factory
        self.events: queue.SimpleQueue[RuntimeEvent] = queue.SimpleQueue()
        self.thread: threading.Thread | None = None
        self._stop_requested = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None

    @property
    def busy(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, config: Config) -> None:
        if self.busy:
            raise ConfigurationError("Another OfficeMusicBot instance is already running.")
        self._stop_requested.clear()
        self.events.put(RuntimeEvent("connecting", "正在連線至 Discord…"))
        self.thread = threading.Thread(
            target=self._thread_main, args=(config,), name="office-music-bot", daemon=False
        )
        self.thread.start()

    def start_login(self, directory: Path) -> None:
        if self.busy:
            raise ConfigurationError("Another OfficeMusicBot instance is already running.")
        self._stop_requested.clear()
        self.events.put(
            RuntimeEvent("login_opening", "正在等待 Chromium 擴充套件；請保持瀏覽器開啟。")
        )
        self.thread = threading.Thread(
            target=self._thread_main, args=(directory,), name="youtube-login", daemon=False
        )
        self.thread.start()

    def stop(self) -> None:
        self._stop_requested.set()
        loop, event = self._loop, self._stop_event
        if loop and event:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                if self.busy:
                    log.warning("Runtime is already shutting down.")

    def _thread_main(self, config: Config | Path) -> None:
        try:
            directory = config if isinstance(config, Path) else config.directory
            with InstanceLock(directory):
                asyncio.run(
                    self._login_session(directory)
                    if isinstance(config, Path)
                    else self._session(config)
                )
        except Exception as exc:
            log.error("Bot session failed: %s", type(exc).__name__)
            self.events.put(RuntimeEvent("error", error_message(exc)))
        finally:
            self._loop = None
            self._stop_event = None
            self.events.put(RuntimeEvent("finished"))

    async def _login_session(self, directory: Path) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        if self._stop_requested.is_set():
            return
        browser = MusicBrowser(directory)
        opening = asyncio.create_task(browser.start(interactive=True))
        stopping = asyncio.create_task(self._stop_event.wait())
        try:
            done, _ = await asyncio.wait(
                [opening, stopping], return_when=asyncio.FIRST_COMPLETED
            )
            if stopping in done:
                return
            await opening
            self.events.put(
                RuntimeEvent(
                    "login_ready",
                    "請在擴充套件的專用分頁自行登入並確認 Premium；完成後按「完成登入並關閉」。"
                    "此時瀏覽器可操作，請勿讓他人使用。",
                )
            )
            await self._stop_event.wait()
        finally:
            opening.cancel()
            stopping.cancel()
            await asyncio.gather(opening, stopping, return_exceptions=True)
            await browser.close()
        self.events.put(
            RuntimeEvent(
                "login_closed",
                "登入連線已結束；瀏覽器分頁與登入狀態會保留，可以啟動 bot。",
            )
        )

    async def _session(self, config: Config) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        if self._stop_requested.is_set():
            return
        async with self.factory(config) as bot:
            connection = asyncio.create_task(bot.start(config.token))
            stopping = asyncio.create_task(self._stop_event.wait())
            previous = None
            try:
                while not connection.done():
                    done, _ = await asyncio.wait(
                        [connection, stopping], timeout=0.3, return_when=asyncio.FIRST_COMPLETED
                    )
                    if stopping in done:
                        self.events.put(RuntimeEvent("stopping", "正在停止 bot 與擴充套件播放…"))
                        connection.cancel()
                        await asyncio.gather(connection, return_exceptions=True)
                        async with asyncio.timeout(20):
                            await bot.close()
                        break
                    if bot.is_ready():
                        snapshot = bot.player.snapshot()
                        title = (
                            f"{snapshot.current.song.title} — {snapshot.current.song.artist}"
                            if snapshot.current
                            else "尚未播放歌曲；到 Discord 點歌頻道使用 /play。"
                        )
                        state = (
                            "playback_error"
                            if snapshot.error
                            else "paused"
                            if snapshot.paused
                            else "ready"
                        )
                        value = (state, snapshot.error or "", title, len(snapshot.queue))
                        current = snapshot.current
                        value += (
                            snapshot.autoplay,
                            snapshot.confirmed,
                            current.requester if current else None,
                            current.song.url if current else "",
                        )
                        if value != previous:
                            self.events.put(
                                RuntimeEvent(state, snapshot.error or "已連線，接受點歌中。")
                            )
                            self.events.put(
                                RuntimeEvent(
                                    "track",
                                    title,
                                    {
                                        "title": current.song.title if current else "",
                                        "artist": current.song.artist if current else "",
                                        "url": current.song.url if current else "",
                                        "requester": current.requester if current else None,
                                        "queue_count": len(snapshot.queue),
                                        "autoplay": snapshot.autoplay,
                                        "confirmed": snapshot.confirmed,
                                        "paused": snapshot.paused,
                                        "error": snapshot.error or "",
                                    },
                                )
                            )
                            previous = value
                    elif previous != "disconnected":
                        self.events.put(RuntimeEvent("connecting", "正在連線或重新連線至 Discord…"))
                        previous = "disconnected"
                if connection.done() and not self._stop_requested.is_set():
                    await connection
                    if bot.fatal_error:
                        raise PlaybackError(bot.fatal_error)
            finally:
                stopping.cancel()
                connection.cancel()
                await asyncio.gather(stopping, connection, return_exceptions=True)
