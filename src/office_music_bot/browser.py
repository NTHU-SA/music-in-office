from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, quote, urljoin, urlparse

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError


class PlaybackError(Exception):
    pass


VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
HOSTS = {"music.youtube.com", "www.youtube.com", "youtube.com", "youtu.be"}
PLAY_BUTTON = (
    "ytmusic-player-bar #play-pause-button:visible, "
    "ytmusic-wiz-player-controls .ytmusicPlayerControlsPlayPauseButton button:visible"
)
NEXT_BUTTON = (
    "ytmusic-player-bar .next-button:visible, ytmusic-player-bar #next-button:visible, "
    "ytmusic-wiz-player-controls .ytmusicPlayerControlsNextButton button:visible"
)
SONG_TYPE_SCRIPT = """
e => e.data?.flexColumns?.[0]
    ?.musicResponsiveListItemFlexColumnRenderer?.text?.runs?.[0]
    ?.navigationEndpoint?.watchEndpoint
    ?.watchEndpointMusicSupportedConfigs
    ?.watchEndpointMusicConfig?.musicVideoType
"""


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


STATE_SCRIPT = r"""
() => {
    const bar = document.querySelector('ytmusic-player-bar');
    const video = document.querySelector('video');
    const player = document.querySelector('#movie_player');
    const data = player?.getVideoData?.() || {};
    const ad = !!player?.classList.contains('ad-showing');
    const title = bar?.querySelector('.title')?.textContent?.trim() || '';
    const artist = bar?.querySelector('.byline a, .subtitle a')?.textContent?.trim()
        || data.author || bar?.querySelector('.byline, .subtitle')?.textContent?.trim() || '';
    const id = data.video_id || new URL(location.href).searchParams.get('v') || '';
    const guard = window.__officeMusic;
    const error = document.querySelector('yt-playability-error-supported-renderers')
        ?.textContent?.trim();
    return {id, title: title || data.title || '', artist,
        dataId: data.video_id || '', dataTitle: data.title || '',
        dataArtist: data.author || '',
        position: video?.currentTime || 0,
        paused: video?.paused ?? true, ended: video?.ended || !!guard?.ended,
        advertisement: ad, ready: (video?.readyState || 0) >= 2,
        generation: guard?.generation || 0, error: error || null};
}
"""

GUARD_SCRIPT = r"""
(() => {
    const state = window.__officeMusic = {
        allowedId: null, allowNext: false, paused: true, ended: false, generation: 0,
        lastId: null, finishAd: false
    };
    const isAd = () => document.querySelector('#movie_player')?.classList.contains('ad-showing');
    const identity = () => document.querySelector('#movie_player')?.getVideoData?.()?.video_id;
    const check = video => {
        if (window.__officeMusicSearch) {
            video.muted = true;
            video.volume = 0;
            if (!isAd()) video.pause();
            return;
        }
        if (isAd()) {
            if (state.paused || (state.ended && !state.allowNext && !state.finishAd)) video.pause();
            return;
        }
        state.finishAd = false;
        const id = identity();
        if (id && id !== state.lastId) {
            state.lastId = id;
            state.generation++;
        }
        if (state.paused || (!state.allowNext && id && id !== state.allowedId)) {
            video.pause();
        }
    };
    document.addEventListener('ended', event => {
        if (event.target instanceof HTMLVideoElement && !isAd()) state.ended = true;
    }, true);
    for (const name of ['play', 'playing', 'loadedmetadata', 'timeupdate', 'volumechange']) {
        document.addEventListener(name, event => {
            if (event.target instanceof HTMLVideoElement) check(event.target);
        }, true);
    }
})();
"""

SEARCH_TAB_SCRIPT = r"""
(() => {
    window.__officeMusicSearch = true;
    for (const [property, forced] of [['muted', true], ['volume', 0]]) {
        const descriptor = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, property);
        Object.defineProperty(HTMLMediaElement.prototype, property, {
            configurable: true,
            enumerable: descriptor.enumerable,
            get() { return descriptor.get.call(this); },
            set(_) { descriptor.set.call(this, forced); }
        });
    }
    document.addEventListener('play', event => {
        if (event.target instanceof HTMLMediaElement) {
            event.target.muted = true;
            event.target.volume = 0;
        }
    }, true);
})();
"""


class MusicBrowser:
    def __init__(self, directory: Path):
        self.directory = directory
        self.playwright: Playwright | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.search_page: Page | None = None

    async def start(self) -> None:
        try:
            self.playwright = await async_playwright().start()
            self.context = await self.playwright.chromium.launch_persistent_context(
                str(self.directory / "edge-profile"),
                channel="msedge",
                headless=False,
                no_viewport=True,
            )
            await self.context.add_init_script(GUARD_SCRIPT)
            self.page = (
                self.context.pages[0] if self.context.pages else await self.context.new_page()
            )
            await self.page.goto("https://music.youtube.com", wait_until="domcontentloaded")
            self.search_page = await self.context.new_page()
            await self.search_page.add_init_script(SEARCH_TAB_SCRIPT)
        except PlaywrightError as exc:
            await self.close()
            raise PlaybackError(
                "Cannot open YouTube Music in Edge. Check Edge installation, network and "
                "enterprise policies, then restart the bot."
            ) from exc

    def _page(self) -> Page:
        if not self.page or self.page.is_closed():
            raise PlaybackError("The music browser was closed. Restart the bot to reconnect it.")
        return self.page

    async def resolve(self, query: str) -> Song:
        value, is_link = normalize_query(query)
        page = self.search_page
        if not page or page.is_closed():
            raise PlaybackError("The search browser tab was closed. Restart the bot.")
        try:
            if is_link:
                await page.goto(value, wait_until="domcontentloaded", timeout=20000)
                video_id = parse_qs(urlparse(value).query)["v"][0]
                try:
                    await page.wait_for_function(
                        """id => {
                            const player = document.querySelector('#movie_player');
                            const data = player?.getVideoData?.();
                            return !!document.querySelector(
                                'yt-playability-error-supported-renderers'
                            ) || (!player?.classList.contains('ad-showing') &&
                                data?.video_id === id && !!data.title);
                        }""",
                        arg=video_id,
                        timeout=40000,
                    )
                except PlaywrightError as exc:
                    raise PlaybackError(
                        "Requested song metadata is unavailable. Wait for any advertisement "
                        "to finish, or check login, connectivity and the link."
                    ) from exc
                state = await page.evaluate(STATE_SCRIPT)
                if state["error"]:
                    raise PlaybackError("This song is unavailable. Check the YouTube Music page.")
                if state["advertisement"] or state["dataId"] != video_id or not state["dataTitle"]:
                    raise PlaybackError(
                        "The requested song is not active yet. Wait for the advertisement "
                        "to finish and try again."
                    )
                song = Song(
                    video_id,
                    state["dataTitle"],
                    state["dataArtist"] or "Unknown artist",
                )
            else:
                await page.goto(
                    f"https://music.youtube.com/search?q={quote(value, safe='')}",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                rows = page.locator(
                    "ytmusic-search-page ytmusic-responsive-list-item-renderer:visible"
                )
                await rows.first.wait_for(timeout=15000)
                song = None
                for row in await rows.all():
                    # The live page puts unrelated promoted videos ahead of song results.
                    kind = await row.evaluate(SONG_TYPE_SCRIPT)
                    if kind != "MUSIC_VIDEO_TYPE_ATV":
                        continue
                    link = row.locator('a[href*="watch?v="]').first
                    if await link.count():
                        href = await link.get_attribute("href")
                        if not href:
                            continue
                        absolute = urljoin("https://music.youtube.com/", href)
                        normalized, _ = normalize_query(absolute)
                        video_id = parse_qs(urlparse(normalized).query)["v"][0]
                        title = (await link.inner_text()).strip()
                        artist_links = row.locator(
                            '.secondary-flex-columns a[href*="channel/"], '
                            '.secondary-flex-columns a[href*="browse/"]'
                        )
                        artist = (
                            (await artist_links.first.inner_text()).strip()
                            if await artist_links.count()
                            else ""
                        )
                        if title:
                            song = Song(video_id, title, artist or "Unknown artist")
                            break
            if not song:
                raise PlaybackError(
                    "No playable song found. Try a more specific title/artist or single-song link."
                )
            return song
        except PlaywrightError as exc:
            raise PlaybackError(
                "Could not resolve the song. Check login/consent, connectivity or site changes "
                "in the search tab."
            ) from exc

    @staticmethod
    def _song(state: dict) -> Song | None:
        if VIDEO_ID.fullmatch(state["id"]) and state["title"]:
            return Song(state["id"], state["title"], state["artist"] or "Unknown artist")
        return None

    async def observe(self) -> Observation:
        try:
            state = await self._page().evaluate(STATE_SCRIPT)
            return Observation(
                song=self._song(state),
                position=state["position"],
                paused=state["paused"],
                ended=state["ended"],
                advertisement=state["advertisement"],
                ready=state["ready"],
                generation=state["generation"],
                error=state["error"],
            )
        except PlaywrightError as exc:
            raise PlaybackError(
                "Cannot read the music player. Check Edge and restart the bot."
            ) from exc

    async def policy(self, video_id: str | None, allow_next: bool, paused: bool) -> None:
        try:
            await self._page().evaluate(
                """args => {
                    const s = window.__officeMusic;
                    if (!s) throw new Error('Missing playback guard');
                    s.allowedId = args.id; s.allowNext = args.next; s.paused = args.paused;
                    s.finishAd = !args.paused &&
                        !!document.querySelector('#movie_player')?.classList.contains('ad-showing');
                    if (args.paused) document.querySelector('video')?.pause();
                }""",
                {"id": video_id, "next": allow_next, "paused": paused},
            )
            # Native radio and the media guard both enforce the coordinator's next-track policy.
            toggle = self._page().locator("#automix:visible").first
            if await toggle.count():
                checked = await toggle.evaluate("(e) => !!e.checked")
                if checked != allow_next:
                    await toggle.click(timeout=5000)
        except PlaywrightError as exc:
            raise PlaybackError("Cannot apply playback controls. Restart the bot.") from exc

    async def play(self, song: Song, *, paused: bool = False) -> None:
        page = self._page()
        try:
            # Reload also makes deliberate replays of the same video a new occurrence.
            await page.goto(song.url, wait_until="domcontentloaded", timeout=30000)
            await self.policy(song.video_id, False, paused)
            await page.wait_for_selector("video", state="attached", timeout=15000)
            await page.evaluate("() => { window.__officeMusic.ended = false; }")
            if not paused:
                await self.resume()
        except PlaywrightError as exc:
            raise PlaybackError(
                "Song could not start. Check the music tab for unavailable content or login."
            ) from exc

    async def pause(self) -> None:
        try:
            await self._page().evaluate(
                """() => {
                    window.__officeMusic.paused = true;
                    document.querySelector('video')?.pause();
                }"""
            )
        except PlaywrightError as exc:
            raise PlaybackError("Cannot pause the music browser.") from exc

    async def resume(self) -> None:
        try:
            page = self._page()
            await page.evaluate("() => { window.__officeMusic.paused = false; }")
            if await page.locator("video").evaluate("(video) => video.paused"):
                await page.locator(PLAY_BUTTON).first.click(timeout=5000)
            await page.wait_for_function(
                "() => document.querySelector('video')?.paused === false", timeout=10000
            )
        except PlaywrightError as exc:
            raise PlaybackError(
                "Playback blocked. Manually press Play in the Edge music tab, then use /resume."
            ) from exc

    async def next_recommendation(self) -> None:
        try:
            await self._page().evaluate("() => { window.__officeMusic.ended = false; }")
            button = self._page().locator(NEXT_BUTTON).first
            await button.click(timeout=5000)
        except PlaywrightError as exc:
            raise PlaybackError(
                "No next recommendation is available. Check YouTube Music or request another song."
            ) from exc

    async def clear_ended(self) -> None:
        try:
            await self._page().evaluate("() => { window.__officeMusic.ended = false; }")
        except PlaywrightError as exc:
            raise PlaybackError("Cannot update playback state.") from exc

    async def close(self) -> None:
        try:
            if self.context:
                await self.context.close()
        finally:
            if self.playwright:
                await self.playwright.stop()
            self.context = None
            self.playwright = None


async def probe(directory: Path) -> None:
    browser = MusicBrowser(directory)
    try:
        await browser.start()
        song = await browser.resolve("Rick Astley Never Gonna Give You Up")
        print(f"Resolved: {song.title} / {song.artist} / {song.url}")
        print("Browser/search probe only: no audible playback or Discord integration verified.")
        await asyncio.sleep(1)
    finally:
        await browser.close()
