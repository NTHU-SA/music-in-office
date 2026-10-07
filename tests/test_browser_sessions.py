from unittest.mock import AsyncMock, Mock

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from office_music_bot.browser import GUARD_SCRIPT, SEARCH_TAB_SCRIPT, MusicBrowser, PlaybackError


@pytest.mark.parametrize("interactive", [False, True])
async def test_playback_is_headless_and_login_is_explicit(tmp_path, monkeypatch, interactive):
    page = Mock(spec=Page)
    page.goto = AsyncMock()
    search = Mock(spec=Page)
    search.add_init_script = AsyncMock()
    context = Mock(
        pages=[page], new_page=AsyncMock(return_value=search),
        add_init_script=AsyncMock(), close=AsyncMock(),
    )
    launch = AsyncMock(return_value=context)
    playwright = Mock(chromium=Mock(launch_persistent_context=launch), stop=AsyncMock())
    monkeypatch.setattr(
        "office_music_bot.browser.async_playwright",
        Mock(return_value=Mock(start=AsyncMock(return_value=playwright))),
    )
    browser = MusicBrowser(tmp_path)
    await browser.start(interactive=interactive)
    launch.assert_awaited_once_with(
        str(tmp_path / "edge-profile"),
        channel="msedge",
        headless=not interactive,
        no_viewport=True,
        ignore_default_args=["--mute-audio"],
        args=["--autoplay-policy=no-user-gesture-required"],
    )
    page.goto.assert_awaited_once_with(
        "https://music.youtube.com", wait_until="domcontentloaded"
    )
    if interactive:
        context.add_init_script.assert_not_awaited()
        context.new_page.assert_not_awaited()
    else:
        context.add_init_script.assert_awaited_once_with(GUARD_SCRIPT)
        search.add_init_script.assert_awaited_once_with(SEARCH_TAB_SCRIPT)
    await browser.close()
    context.close.assert_awaited_once()
    playwright.stop.assert_awaited_once()


async def test_login_launch_failure_is_reported_and_driver_closed(tmp_path, monkeypatch):
    playwright = Mock(
        chromium=Mock(launch_persistent_context=AsyncMock(side_effect=PlaywrightError("test"))),
        stop=AsyncMock(),
    )
    monkeypatch.setattr(
        "office_music_bot.browser.async_playwright",
        Mock(return_value=Mock(start=AsyncMock(return_value=playwright))),
    )
    with pytest.raises(PlaybackError, match="Cannot open"):
        await MusicBrowser(tmp_path).start(interactive=True)
    playwright.stop.assert_awaited_once()


async def test_real_headless_edge_keeps_profile_and_does_not_mute_audio(tmp_path, monkeypatch):
    original_goto = Page.goto

    async def offline_goto(page, url, **kwargs):
        return await original_goto(page, "data:text/html,<title>Offline fixture</title>", **kwargs)

    monkeypatch.setattr(Page, "goto", offline_goto)
    for first_run in (True, False):
        browser = MusicBrowser(tmp_path)
        try:
            await browser.start()
            await original_goto(browser.page, "edge://version")
            arguments = await browser.page.locator("#command_line").inner_text()
            assert "--headless" in arguments
            assert "--mute-audio" not in arguments
            assert "--autoplay-policy=no-user-gesture-required" in arguments
            assert "--remote-debugging-port" not in arguments
            if first_run:
                await browser.context.add_cookies([{
                    "name": "fixture", "value": "synthetic",
                    "domain": "example.test", "path": "/", "expires": 2_000_000_000,
                }])
            else:
                cookies = await browser.context.cookies("https://example.test")
                assert any(cookie["name"] == "fixture" for cookie in cookies)
        finally:
            await browser.close()
