import asyncio
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from office_music_bot.browser import (
    GUARD_SCRIPT,
    NEXT_BUTTON,
    PLAY_BUTTON,
    SEARCH_TAB_SCRIPT,
    SONG_TYPE_SCRIPT,
    STATE_SCRIPT,
    MusicBrowser,
    Song,
)


@pytest.fixture
async def page():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="msedge", headless=True)
        try:
            yield await browser.new_page()
        finally:
            await browser.close()


@pytest.fixture
async def guarded_search_page():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="msedge", headless=True)
        context = await browser.new_context()
        await context.add_init_script(GUARD_SCRIPT)
        page = await context.new_page()
        await page.add_init_script(SEARCH_TAB_SCRIPT)
        try:
            yield page
        finally:
            await browser.close()


async def fixture_player(page):
    await page.set_content(
        """
        <div id="movie_player"></div>
        <ytmusic-player-bar>
          <span class="title">Song title</span>
          <span class="byline"><a>Artist name</a> • Album</span>
        </ytmusic-player-bar>
        <video></video>
        """
    )
    await page.evaluate(
        """() => {
            window.videoId = 'aaaaaaaaaaa';
            document.querySelector('#movie_player').getVideoData = () => ({
                video_id: window.videoId, title: 'Fallback title', author: 'Fallback artist'
            });
            const video = document.querySelector('video');
            window.media = {position: 1, paused: false, ready: 4, ended: false, pauses: 0};
            Object.defineProperties(video, {
                currentTime: {get: () => window.media.position},
                paused: {get: () => window.media.paused},
                readyState: {get: () => window.media.ready},
                ended: {get: () => window.media.ended}
            });
            video.pause = () => {window.media.paused = true; window.media.pauses++;};
        }"""
    )
    await page.evaluate(GUARD_SCRIPT)


async def test_state_reads_metadata_and_advertisement(page):
    await fixture_player(page)
    state = await page.evaluate(STATE_SCRIPT)
    assert state["title"] == "Song title"
    assert state["artist"] == "Artist name"
    assert state["position"] == 1
    assert state["ready"]
    assert not state["advertisement"]
    await page.locator("#movie_player").evaluate("(e) => e.classList.add('ad-showing')")
    assert (await page.evaluate(STATE_SCRIPT))["advertisement"]
    await page.locator("ytmusic-player-bar").evaluate("(e) => e.remove()")
    state = await page.evaluate(STATE_SCRIPT)
    assert state["title"] == "Fallback title"
    assert state["artist"] == "Fallback artist"


async def test_guard_blocks_unwanted_next_and_allows_current(page):
    await fixture_player(page)
    await page.evaluate(
        """() => {
            Object.assign(window.__officeMusic, {allowedId:'aaaaaaaaaaa', paused:false});
            document.querySelector('video').dispatchEvent(new Event('playing'));
        }"""
    )
    assert not await page.evaluate("window.media.paused")
    await page.evaluate(
        """() => {
            window.videoId = 'bbbbbbbbbbb';
            document.querySelector('video').dispatchEvent(new Event('playing'));
        }"""
    )
    assert await page.evaluate("window.media.paused")
    assert (await page.evaluate(STATE_SCRIPT))["generation"] == 2
    await page.evaluate(
        """() => {
            window.__officeMusic.allowNext = true; window.media.paused = false;
            document.querySelector('video').dispatchEvent(new Event('playing'));
        }"""
    )
    assert not await page.evaluate("window.media.paused")


async def test_ads_do_not_mark_song_ended_and_pause_protects_ads(page):
    await fixture_player(page)
    await page.evaluate(
        """() => {
            document.querySelector('#movie_player').classList.add('ad-showing');
            window.__officeMusic.paused = false;
            document.querySelector('video').dispatchEvent(new Event('ended'));
            document.querySelector('video').dispatchEvent(new Event('playing'));
        }"""
    )
    assert not (await page.evaluate(STATE_SCRIPT))["ended"]
    assert not await page.evaluate("window.media.paused")
    await page.evaluate(
        """() => {
            window.__officeMusic.paused = true;
            document.querySelector('video').dispatchEvent(new Event('playing'));
        }"""
    )
    assert await page.evaluate("window.media.paused")


async def test_ended_song_blocks_next_ad_when_autoplay_disabled(page):
    await fixture_player(page)
    await page.evaluate(
        """() => {
            Object.assign(window.__officeMusic, {allowedId:'aaaaaaaaaaa', paused:false});
            document.querySelector('video').dispatchEvent(new Event('ended'));
            document.querySelector('#movie_player').classList.add('ad-showing');
            document.querySelector('video').dispatchEvent(new Event('playing'));
        }"""
    )
    assert await page.evaluate("window.media.paused")


async def test_existing_ad_can_finish_when_request_arrives(page):
    await fixture_player(page)
    await page.evaluate(
        """() => {
            document.querySelector('#movie_player').classList.add('ad-showing');
            Object.assign(window.__officeMusic, {paused:false, ended:true});
        }"""
    )
    adapter = MusicBrowser(Path("."))
    adapter.page = page
    await adapter.policy("aaaaaaaaaaa", False, False)
    await page.evaluate("document.querySelector('video').dispatchEvent(new Event('playing'))")
    assert not await page.evaluate("window.media.paused")


@pytest.mark.parametrize("modern", [False, True])
async def test_transport_selectors_for_both_player_variants(page, modern):
    if modern:
        markup = """
        <ytmusic-wiz-player-controls>
          <div class="ytmusicPlayerControlsPlayPauseButton"><button>Play</button></div>
          <div class="ytmusicPlayerControlsNextButton"><button>Next</button></div>
        </ytmusic-wiz-player-controls>
        """
    else:
        markup = """
        <ytmusic-player-bar>
          <button id="play-pause-button">Play</button>
          <button class="next-button">Next</button>
        </ytmusic-player-bar>
        """
    await page.set_content(markup)
    assert await page.locator(PLAY_BUTTON).count() == 1
    assert await page.locator(NEXT_BUTTON).count() == 1


async def test_search_resolution_ignores_promoted_video_and_hidden_suggestion(page):
    await page.set_content(
        """
        <ytmusic-search-page>
          <ytmusic-responsive-list-item-renderer style="display:none">
            <a href="watch?v=ccccccccccc">Hidden suggestion</a>
          </ytmusic-responsive-list-item-renderer>
          <ytmusic-responsive-list-item-renderer>
            <a href="watch?v=bbbbbbbbbbb">Promoted video</a>
          </ytmusic-responsive-list-item-renderer>
          <ytmusic-responsive-list-item-renderer>
            <a href="watch?v=aaaaaaaaaaa">Actual song</a>
            <div class="secondary-flex-columns"><a href="channel/artist">Artist</a></div>
          </ytmusic-responsive-list-item-renderer>
        </ytmusic-search-page>
        """
    )
    await page.evaluate(
        """() => {
            const rows = document.querySelectorAll('ytmusic-responsive-list-item-renderer');
            for (let i=0;i<rows.length;i++) {
                rows[i].data = {flexColumns:[{musicResponsiveListItemFlexColumnRenderer:{
                    text:{runs:[{navigationEndpoint:{watchEndpoint:{
                        watchEndpointMusicSupportedConfigs:{watchEndpointMusicConfig:{
                            musicVideoType:i===2?'MUSIC_VIDEO_TYPE_ATV':'MUSIC_VIDEO_TYPE_UGC'
                        }}
                    }}}]}
                }}]};
            }
        }"""
    )
    rows = page.locator("ytmusic-search-page ytmusic-responsive-list-item-renderer:visible")
    assert await rows.count() == 2
    assert await rows.nth(0).evaluate(SONG_TYPE_SCRIPT) == "MUSIC_VIDEO_TYPE_UGC"
    assert await rows.nth(1).evaluate(SONG_TYPE_SCRIPT) == "MUSIC_VIDEO_TYPE_ATV"


@pytest.mark.parametrize(
    ("url", "initial_id", "ad"),
    [
        ("https://youtu.be/aaaaaaaaaaa", "bbbbbbbbbbb", True),
        ("https://www.youtube.com/watch?v=aaaaaaaaaaa&list=playlist", "bbbbbbbbbbb", False),
        ("https://music.youtube.com/watch?v=aaaaaaaaaaa", "aaaaaaaaaaa", True),
    ],
)
@pytest.mark.parametrize(
    ("artist", "expected_artist"), [("Artist", "Artist"), ("", "Unknown artist")]
)
async def test_link_resolution_waits_for_requested_non_ad_metadata(
    guarded_search_page, url, initial_id, ad, artist, expected_artist
):
    page = guarded_search_page

    async def fulfill(route):
        await route.fulfill(
            body=f"""
            <div id="movie_player" class="{"ad-showing" if ad else ""}"></div>
            <ytmusic-player-bar>
              <span class="title">Stale ad title</span><span class="byline">Stale ad artist</span>
            </ytmusic-player-bar>
            <video></video>
            <script>
              window.videoId = '{initial_id}';
              window.videoTitle = 'Advertisement';
              window.mediaPauses = 0;
              document.querySelector('video').pause = () => window.mediaPauses++;
              document.querySelector('#movie_player').getVideoData = () => ({{
                video_id: window.videoId, title: window.videoTitle, author: '{artist}'
              }});
            </script>
            """,
        )

    await page.route("https://music.youtube.com/watch?v=aaaaaaaaaaa", fulfill)
    adapter = MusicBrowser(Path("."))
    adapter.search_page = page
    pending = asyncio.create_task(adapter.resolve(url))
    await page.locator("#movie_player").wait_for(state="attached")
    assert not pending.done()
    await page.evaluate(
        """() => {
            const video = document.querySelector('video');
            video.muted = false;
            video.volume = 1;
            video.dispatchEvent(new Event('playing'));
        }"""
    )
    assert await page.evaluate("document.querySelector('video').muted")
    assert await page.evaluate("document.querySelector('video').volume") == 0
    if ad:
        assert await page.evaluate("window.mediaPauses") == 0
    else:
        assert await page.evaluate("window.mediaPauses") > 0
    await page.evaluate(
        """() => {
            window.videoId = 'aaaaaaaaaaa';
            window.videoTitle = 'Requested song';
        }"""
    )
    if ad:
        await page.wait_for_timeout(100)
        assert not pending.done()
        await page.locator("#movie_player").evaluate("(e) => e.classList.remove('ad-showing')")
        await page.evaluate("document.querySelector('video').dispatchEvent(new Event('playing'))")
        assert await page.evaluate("window.mediaPauses") > 0
    assert await pending == Song("aaaaaaaaaaa", "Requested song", expected_artist)


async def test_observe_closed_browser_has_actionable_error(page):
    from office_music_bot.browser import PlaybackError

    adapter = MusicBrowser(Path("."))
    adapter.page = page
    await page.close()
    with pytest.raises(PlaybackError, match="closed"):
        await adapter.observe()
