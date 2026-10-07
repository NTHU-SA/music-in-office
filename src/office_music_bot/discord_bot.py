from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

import discord
from discord import app_commands

from .browser import MusicBrowser, PlaybackError
from .config import Config, ConfigurationError
from .player import NotificationError, Player, Playing

log = logging.getLogger(__name__)


def allowed_channel(config: Config, guild_id: int | None, channel_id: int | None) -> bool:
    return guild_id == config.guild_id and channel_id == config.channel_id


def clean(text: str, limit: int = 200) -> str:
    return discord.utils.escape_markdown(discord.utils.escape_mentions(text))[:limit]


def now_playing_message(playing: Playing) -> str:
    source = f"點歌：{clean(playing.requester)}" if playing.requester else "YouTube Music 自動推薦"
    return (
        f"🎵 **正在播放：{clean(playing.song.title)}**\n"
        f"歌手：{clean(playing.song.artist)}\n{source}\n<{playing.song.url}>"
    )


class CommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        client = self.client
        if not isinstance(client, MusicBot) or not allowed_channel(
            client.config, interaction.guild_id, interaction.channel_id
        ):
            await interaction.response.send_message("請在指定的點歌頻道使用指令。", ephemeral=True)
            return False
        if not client.is_ready():
            await interaction.response.send_message("Bot 尚在連線，請稍後再試。", ephemeral=True)
            return False
        return True

    async def on_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        original = error.original if isinstance(error, app_commands.CommandInvokeError) else error
        if isinstance(original, (PlaybackError, TimeoutError)):
            message = f"無法完成播放操作：{original or '操作逾時，請重試。'}"
            log.warning("Command failed: %s", original)
        else:
            # Do not dump HTTP requests, token-bearing interaction objects or raw browser data.
            log.error("Unexpected command failure: %s", type(original).__name__)
            message = "指令失敗，請查看這台電腦的 bot 視窗或 bot.log。"
        if interaction.response.is_done():
            await interaction.followup.send(message[:1900], ephemeral=True)
        else:
            await interaction.response.send_message(message[:1900], ephemeral=True)


class MusicBot(discord.Client):
    def __init__(self, config: Config):
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.config = config
        self.tree = CommandTree(self)
        self.browser = MusicBrowser(config.directory)
        self.player = Player(self.browser, self.announce, self.report_error)
        self.channel: discord.TextChannel | None = None
        self.worker: asyncio.Task | None = None
        self.fatal_error: str | None = None
        self.guild_object = discord.Object(id=config.guild_id)
        self._register_commands()

    def _register_commands(self) -> None:
        @self.tree.command(name="play", description="以歌名、歌手或單曲連結點歌")
        @app_commands.describe(query="歌名／歌手，或 YouTube Music／YouTube 單曲連結")
        async def play(interaction: discord.Interaction, query: str) -> None:
            await interaction.response.defer(thinking=True)
            async with asyncio.timeout(120):
                request = await self.player.request(query, interaction.user.display_name)
            state = self.player.snapshot()
            status = f"\n⚠️ 播放暫停：{state.error}" if state.error else ""
            if state.paused and not state.error:
                status = "\n目前已暫停，使用 /resume 開始播放。"
            await interaction.followup.send(
                f"已接受點歌：**{clean(request.song.title)}** — {clean(request.song.artist)}"
                f"\n<{request.song.url}>{status}"[:1900]
            )

        @self.tree.command(name="queue", description="查看點歌佇列")
        @app_commands.describe(page="頁碼，每頁最多 10 首")
        async def queue(interaction: discord.Interaction, page: app_commands.Range[int, 1] = 1):
            state = self.player.snapshot()
            pending = state.queue
            if not pending:
                text = "目前沒有待播點歌。"
            else:
                pages = (len(pending) + 9) // 10
                if page > pages:
                    await interaction.response.send_message(
                        f"目前只有 {pages} 頁。", ephemeral=True
                    )
                    return
                start = (page - 1) * 10
                lines = [
                    f"{index + 1}. {clean(item.song.title, 80)} — "
                    f"{clean(item.song.artist, 40)}（{clean(item.requester, 30)}）"
                    for index, item in enumerate(pending[start : start + 10], start)
                ]
                text = f"待播 {len(pending)} 首，第 {page}/{pages} 頁\n" + "\n".join(lines)
            await interaction.response.send_message(text[:1900])

        @self.tree.command(name="nowplaying", description="查看目前歌曲與播放狀態")
        async def nowplaying(interaction: discord.Interaction):
            state = self.player.snapshot()
            if not state.current:
                text = "目前沒有播放歌曲。"
            elif state.confirmed:
                text = now_playing_message(state.current)
            else:
                text = (
                    f"等待播放：**{clean(state.current.song.title)}**\n<{state.current.song.url}>"
                )
            text += f"\n自動推薦：{'開啟' if state.autoplay else '關閉'}"
            if state.error:
                text += f"\n⚠️ {state.error}"
            elif state.paused:
                text += "\n⏸ 已暫停"
            await interaction.response.send_message(text[:1900])

        @self.tree.command(name="pause", description="暫停辦公室播放")
        async def pause(interaction: discord.Interaction):
            await interaction.response.defer(thinking=True)
            async with asyncio.timeout(15):
                await self.player.pause()
            await interaction.followup.send("⏸ 已暫停。")

        @self.tree.command(name="resume", description="繼續播放；處理瀏覽器提示後可重試")
        async def resume(interaction: discord.Interaction):
            await interaction.response.defer(thinking=True)
            async with asyncio.timeout(60):
                await self.player.resume()
            await interaction.followup.send("▶️ 已解除暫停；新歌實際開始時會自動公告。")

        @self.tree.command(name="skip", description="跳過目前歌曲")
        async def skip(interaction: discord.Interaction):
            await interaction.response.defer(thinking=True)
            async with asyncio.timeout(60):
                await self.player.skip()
            await interaction.followup.send("⏭ 已跳過。")

        @self.tree.command(name="autoplay", description="切換佇列播完後的 YouTube Music 自動推薦")
        @app_commands.describe(enabled="是否開啟自動推薦")
        async def autoplay(interaction: discord.Interaction, enabled: bool):
            await interaction.response.defer(thinking=True)
            async with asyncio.timeout(15):
                await self.player.set_autoplay(enabled)
            await interaction.followup.send(
                "自動推薦已開啟。" if enabled else "自動推薦已關閉；目前歌曲仍會播完。"
            )

    async def setup_hook(self) -> None:
        # setup_hook runs before Gateway guild/role caches are populated.
        guild = await self.fetch_guild(self.config.guild_id)
        channel = await guild.fetch_channel(self.config.channel_id)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != self.config.guild_id:
            raise ConfigurationError("Configure a normal text channel in the specified server.")
        if not self.user:
            raise ConfigurationError("Discord did not return the bot identity.")
        member = await channel.guild.fetch_member(self.user.id)
        permissions = channel.permissions_for(member)
        if not (permissions.view_channel and permissions.send_messages):
            raise ConfigurationError(
                "Bot needs View Channel and Send Messages in the song channel."
            )
        self.channel = channel
        self.tree.copy_global_to(guild=self.guild_object)
        await self.tree.sync(guild=self.guild_object)
        await self.browser.start()
        log.info("Edge ready. Complete YouTube Music login/consent manually in the music tab.")

    async def on_ready(self) -> None:
        if self.worker is None:
            self.worker = asyncio.create_task(self._run_player(), name="music-player")
        log.info("Discord connected. Song channel: %s", self.config.channel_id)

    async def _run_player(self) -> None:
        try:
            await self.player.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.critical("Playback worker stopped unexpectedly: %s", type(exc).__name__)
            self.fatal_error = "Internal playback error; restart OfficeMusicBot."
            await self.report_error(self.fatal_error)
            await self.close()

    async def _send(self, message: str) -> None:
        if not self.channel or not self.is_ready():
            raise NotificationError("Discord is disconnected.")
        try:
            async with asyncio.timeout(15):
                await self.channel.send(
                    message[:1900], allowed_mentions=discord.AllowedMentions.none()
                )
        except (discord.HTTPException, TimeoutError) as exc:
            raise NotificationError(f"Discord send failed ({type(exc).__name__}).") from exc

    async def announce(self, playing: Playing) -> None:
        await self._send(now_playing_message(playing))
        log.info("Playing: %s / %s", playing.song.title, playing.song.artist)

    async def report_error(self, message: str) -> None:
        try:
            await self._send(
                f"⚠️ 辦公室播放器暫停：{message}\n處理 Edge 提示後用 /resume；"
                "若瀏覽器已關閉，請重新啟動 exe。"
            )
        except NotificationError as exc:
            log.error("Could not send playback error to Discord: %s", exc)

    async def close(self) -> None:
        if self.worker and self.worker is not asyncio.current_task():
            self.worker.cancel()
            with suppress(asyncio.CancelledError):
                await self.worker
        try:
            await self.browser.close()
        finally:
            await super().close()
