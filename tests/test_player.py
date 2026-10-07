import asyncio
from dataclasses import replace

import pytest

from office_music_bot.browser import Observation, PlaybackError, Song
from office_music_bot.player import NotificationError, Player

A = Song("aaaaaaaaaaa", "Song A", "Artist A")
B = Song("bbbbbbbbbbb", "Song B", "Artist B")
C = Song("ccccccccccc", "Song C", "Artist C")


class FakeBrowser:
    def __init__(self):
        self.state = Observation(None)
        self.plays = []
        self.policies = []
        self.next_calls = 0
        self.play_error = False
        self.observe_error = False
        self.resolved = {"a": A, "b": B, "c": C}

    async def resolve(self, query):
        if query not in self.resolved:
            raise PlaybackError("No song")
        return self.resolved[query]

    async def observe(self):
        if self.observe_error:
            raise PlaybackError("Browser closed")
        return self.state

    async def policy(self, video_id, allow_next, paused):
        self.policies.append((video_id, allow_next, paused))

    async def play(self, song, *, paused=False):
        if self.play_error:
            raise PlaybackError("Cannot play")
        self.plays.append(song)
        self.state = Observation(song, paused=paused, ready=True)

    async def pause(self):
        self.state = replace(self.state, paused=True)

    async def resume(self):
        self.state = replace(self.state, paused=False)

    async def next_recommendation(self):
        self.next_calls += 1
        self.state = Observation(C, paused=self.policies[-1][2], ready=True, generation=1)

    async def clear_ended(self):
        self.state = replace(self.state, ended=False)


@pytest.fixture
def rig():
    browser = FakeBrowser()
    announcements = []
    errors = []

    async def announce(event):
        announcements.append(event)

    async def report(message):
        errors.append(message)

    return Player(browser, announce, report), browser, announcements, errors


async def advance(player, browser):
    await player.tick()
    browser.state = replace(browser.state, position=browser.state.position + 1)
    await player.tick()
    await player.deliver_notification()


async def test_idle_does_not_autoplay(rig):
    player, browser, announcements, _ = rig
    await player.tick()
    assert player.autoplay
    assert browser.next_calls == 0
    assert not announcements


async def test_requests_fifo_do_not_interrupt_requested_song(rig):
    player, browser, _, _ = rig
    await player.request("a", "Alice")
    await player.request("b", "Bob")
    await player.request("c", "Carol")
    assert browser.plays == [A]
    assert [item.song for item in player.snapshot().queue] == [B, C]
    assert browser.policies[-1] == (A.video_id, False, False)
    browser.state = replace(browser.state, ended=True)
    await player.tick()
    assert browser.plays == [A, B]
    assert player.current.requester == "Bob"


async def test_pending_request_wins_over_native_transition(rig):
    player, browser, announcements, _ = rig
    await player.request("a", "Alice")
    await player.request("b", "Bob")
    browser.state = Observation(C, position=1, ready=True, paused=False)
    await player.tick()
    assert browser.plays == [A, B]
    assert player.current.song == B
    assert not announcements


async def test_recommendation_interrupted_immediately(rig):
    player, browser, announcements, _ = rig
    await player.request("a", "Alice")
    browser.state = Observation(C, ready=True, paused=False)
    await advance(player, browser)
    assert player.current.requester is None
    assert announcements[-1].song == C
    await player.request("b", "Bob")
    assert browser.plays == [A, B]
    assert player.current.requester == "Bob"


async def test_autoplay_recommendation_after_queue_finishes(rig):
    player, browser, announcements, _ = rig
    await player.request("a", "Alice")
    browser.state = replace(browser.state, ended=True)
    await player.tick()
    assert browser.next_calls == 1
    await advance(player, browser)
    assert announcements[-1].song == C
    assert announcements[-1].requester is None
    await player.tick()
    assert browser.next_calls == 1


async def test_disable_autoplay_does_not_interrupt_but_stops_at_end(rig):
    player, browser, announcements, _ = rig
    await player.request("a", "Alice")
    await player.set_autoplay(False)
    assert player.current.song == A
    assert not browser.state.paused
    assert browser.policies[-1] == (A.video_id, False, False)
    browser.state = replace(browser.state, ended=True)
    await player.tick()
    assert browser.state.paused
    assert player.current is None
    assert not announcements
    assert browser.next_calls == 0


async def test_disabled_autoplay_blocks_unexpected_next(rig):
    player, browser, announcements, _ = rig
    await player.request("a", "Alice")
    await player.set_autoplay(False)
    browser.state = Observation(C, ready=True, paused=False, position=1)
    await player.tick()
    assert browser.state.paused
    assert player.current is None
    assert not announcements


async def test_pausing_prevents_request_start_and_auto_advance(rig):
    player, browser, _, _ = rig
    await player.pause()
    await player.request("a", "Alice")
    assert not browser.plays
    await player.resume()
    assert browser.plays == [A]
    await player.pause()
    browser.state = replace(browser.state, ended=True)
    await player.request("b", "Bob")
    await player.tick()
    assert browser.plays == [A]
    assert browser.next_calls == 0
    assert player.snapshot().paused


async def test_paused_recommendation_waits_until_resume(rig):
    player, browser, _, _ = rig
    await player.request("a", "Alice")
    browser.state = Observation(C, ready=True, paused=False)
    await advance(player, browser)
    await player.pause()
    await player.request("b", "Bob")
    assert browser.plays == [A]
    await player.resume()
    assert browser.plays == [A, B]


async def test_announcement_requires_advance_and_excludes_ads(rig):
    player, browser, announcements, _ = rig
    await player.request("a", "Alice")
    await player.tick()
    await player.deliver_notification()
    assert not announcements
    browser.state = replace(browser.state, advertisement=True, position=1)
    await advance(player, browser)
    assert not announcements
    browser.state = replace(browser.state, advertisement=False, position=0)
    await advance(player, browser)
    assert len(announcements) == 1
    assert announcements[0].requester == "Alice"


async def test_pause_seek_metadata_refresh_do_not_duplicate(rig):
    player, browser, announcements, _ = rig
    await player.request("a", "Alice")
    await advance(player, browser)
    await player.pause()
    await player.resume()
    browser.state = replace(browser.state, position=30, song=replace(A, title="Updated"))
    await advance(player, browser)
    assert len(announcements) == 1
    assert player.snapshot().current.song.title == "Updated"


async def test_same_song_requested_again_is_new_occurrence(rig):
    player, browser, announcements, _ = rig
    await player.request("a", "Alice")
    await advance(player, browser)
    await player.request("a", "Bob")
    browser.state = replace(browser.state, ended=True)
    await player.tick()
    await advance(player, browser)
    assert len(announcements) == 2
    assert announcements[0].occurrence != announcements[1].occurrence
    assert announcements[1].requester == "Bob"


async def test_failed_start_keeps_request_and_reports_block(rig):
    player, browser, announcements, errors = rig
    browser.play_error = True
    await player.request("a", "Alice")
    assert [item.song for item in player.queue] == [A]
    assert player.error == "Cannot play"
    assert player.paused
    assert not announcements
    assert errors == ["Cannot play"]
    browser.play_error = False
    await player.resume()
    assert not player.error
    assert browser.plays == [A]


async def test_failed_search_does_not_disturb_playback(rig):
    player, browser, _, _ = rig
    await player.request("a", "Alice")
    with pytest.raises(PlaybackError, match="No song"):
        await player.request("missing", "Bob")
    assert browser.plays == [A]
    assert not player.queue
    assert not player.paused


async def test_concurrent_requests_keep_resolution_order(rig):
    player, browser, _, _ = rig
    original = browser.resolve

    async def slow(query):
        await asyncio.sleep(0.02 if query == "a" else 0)
        return await original(query)

    browser.resolve = slow
    await asyncio.gather(player.request("a", "Alice"), player.request("b", "Bob"))
    assert browser.plays == [A]
    assert player.queue[0].song == B


async def test_skip_preserves_pause_and_uses_queue(rig):
    player, browser, _, _ = rig
    await player.request("a", "Alice")
    await player.request("b", "Bob")
    await player.pause()
    await player.skip()
    assert player.current.song == B
    assert browser.state.paused


async def test_skip_with_autoplay_off_stops(rig):
    player, browser, _, _ = rig
    await player.request("a", "Alice")
    await player.set_autoplay(False)
    await player.skip()
    assert player.current is None
    assert browser.state.paused


async def test_notification_retries_are_bounded(rig):
    player, browser, _, _ = rig
    calls = []

    async def fail(event):
        calls.append(event)
        raise NotificationError("Disconnected")

    player.announce = fail
    await player.request("a", "Alice")
    await advance(player, browser)
    for _ in range(5):
        player._notification_retry_at = 0
        browser.state = replace(browser.state, position=browser.state.position + 1)
        await player.tick()
        await player.deliver_notification()
    assert len(calls) == 3


async def test_browser_failure_suspends_once_and_keeps_queue(rig):
    player, browser, _, errors = rig
    await player.request("a", "Alice")
    await player.request("b", "Bob")
    browser.observe_error = True
    task = asyncio.create_task(player.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert player.error == "Browser closed"
    assert [item.song for item in player.queue] == [B]
    assert errors == ["Browser closed"]


async def test_new_request_during_recommendation_ad_waits_without_skipping_ad(rig):
    player, browser, _, _ = rig
    await player.request("a", "Alice")
    browser.state = Observation(C, ready=True, paused=False)
    await advance(player, browser)
    browser.state = replace(browser.state, advertisement=True)
    await player.request("b", "Bob")
    await player.tick()
    assert browser.plays == [A]
    with pytest.raises(PlaybackError, match="advertisement"):
        await player.skip()
    browser.state = replace(browser.state, advertisement=False)
    await player.tick()
    assert browser.plays == [A, B]


async def test_skip_failed_first_request_discards_only_that_request(rig):
    player, browser, _, _ = rig
    browser.play_error = True
    await player.request("a", "Alice")
    await player.request("b", "Bob")
    browser.play_error = False
    await player.skip()
    assert browser.plays == [B]
    assert not player.queue
    assert player.paused
    assert not player.error


async def test_skip_paused_recommendation_retains_pause(rig):
    player, browser, _, _ = rig
    await player.request("a", "Alice")
    await player.pause()
    await player.skip()
    assert browser.next_calls == 1
    assert player.paused
    assert browser.state.paused
    assert browser.policies[-1] == (A.video_id, True, True)


async def test_playback_start_timeout_is_explicit(rig):
    player, browser, announcements, _ = rig
    await player.request("a", "Alice")
    browser.state = replace(browser.state, paused=True)
    player._started_at -= 46
    with pytest.raises(PlaybackError, match="did not start"):
        await player.tick()
    assert not announcements


async def test_stalled_confirmed_song_is_reported(rig):
    player, browser, _, _ = rig
    await player.request("a", "Alice")
    await advance(player, browser)
    assert player.confirmed
    player._started_at -= 46
    with pytest.raises(PlaybackError, match="stalled"):
        await player.tick()


async def test_stalled_ad_is_reported_without_skipping(rig):
    player, browser, announcements, _ = rig
    await player.request("a", "Alice")
    browser.state = replace(browser.state, advertisement=True)
    await player.tick()
    player._started_at -= 61
    with pytest.raises(PlaybackError, match="Advertisement playback stalled"):
        await player.tick()
    assert not announcements
    assert browser.next_calls == 0
