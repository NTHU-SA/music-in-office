from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from .browser import Observation, PlaybackError, Song

log = logging.getLogger(__name__)


class Browser(Protocol):
    async def resolve(self, query: str) -> Song: ...
    async def observe(self) -> Observation: ...
    async def policy(self, video_id: str | None, allow_next: bool, paused: bool) -> None: ...
    async def play(self, song: Song, *, paused: bool = False) -> None: ...
    async def pause(self) -> None: ...
    async def resume(self) -> None: ...
    async def next_recommendation(self) -> None: ...
    async def clear_ended(self) -> None: ...


@dataclass(frozen=True)
class Request:
    song: Song
    requester: str


@dataclass(frozen=True)
class Playing:
    song: Song
    requester: str | None
    occurrence: int


@dataclass(frozen=True)
class Snapshot:
    current: Playing | None
    queue: tuple[Request, ...]
    paused: bool
    autoplay: bool
    error: str | None
    confirmed: bool


class Player:
    def __init__(
        self,
        browser: Browser,
        announce: Callable[[Playing], Awaitable[None]],
        report_error: Callable[[str], Awaitable[None]],
    ):
        self.browser = browser
        self.announce = announce
        self.report_error = report_error
        self.lock = asyncio.Lock()
        self.request_lock = asyncio.Lock()
        self.queue: deque[Request] = deque()
        self.current: Playing | None = None
        self.paused = False
        self.autoplay = True
        self.error: str | None = None
        self.confirmed = False
        self._occurrence = 0
        self._previous: Observation | None = None
        self._announced: int | None = None
        self._waiting_recommendation = False
        self._started_at = time.monotonic()
        self._notification: Playing | None = None
        self._notification_attempts = 0
        self._notification_retry_at = 0.0
        self._failed_request: Request | None = None
        self._deferred_ad = False

    def snapshot(self) -> Snapshot:
        return Snapshot(
            self.current, tuple(self.queue), self.paused, self.autoplay, self.error, self.confirmed
        )

    def _set_current(self, song: Song, requester: str | None) -> None:
        self._occurrence += 1
        self.current = Playing(song, requester, self._occurrence)
        self.confirmed = False
        self._previous = None
        self._started_at = time.monotonic()
        self._waiting_recommendation = False
        self._notification = None
        self._notification_attempts = 0
        self._notification_retry_at = 0.0

    async def _policy(self) -> None:
        await self.browser.policy(
            self.current.song.video_id if self.current else None,
            self.autoplay and not self.queue and not self.error,
            self.paused or bool(self.error) or (self.current is None and not self._deferred_ad),
        )

    async def request(self, query: str, requester: str) -> Request:
        # Serialize resolutions too, so slow searches cannot reorder requests.
        async with self.request_lock:
            try:
                async with asyncio.timeout(65):
                    song = await self.browser.resolve(query)
            except TimeoutError as exc:
                raise PlaybackError("Song search timed out. Please try again.") from exc
            request = Request(song, requester)
            async with self.lock:
                self.queue.append(request)
                try:
                    if not self.error:
                        if not self.paused and (
                            self.current is None or self.current.requester is None
                        ):
                            await self._start_request()
                        else:
                            await self._policy()
                except PlaybackError as exc:
                    await self._block(str(exc))
            return request

    async def _start_request(self) -> None:
        request = self.queue[0]
        observed = await self.browser.observe()
        if observed.advertisement:
            self._deferred_ad = True
            await self._policy()
            if observed.paused and observed.ready and not self.paused:
                await self.browser.resume()
            return
        self._deferred_ad = False
        # Keep the request queued on navigation/start failure.
        try:
            await self.browser.play(request.song, paused=self.paused)
        except PlaybackError:
            self._failed_request = request
            raise
        self.queue.popleft()
        self._failed_request = None
        self._set_current(request.song, request.requester)
        await self._policy()

    async def pause(self) -> None:
        async with self.lock:
            self.paused = True
            try:
                await self.browser.pause()
                await self._policy()
            except PlaybackError as exc:
                await self._block(str(exc))
                raise

    async def resume(self) -> None:
        async with self.lock:
            self.paused = False
            self.error = None
            try:
                if self.queue and (
                    not self.current
                    or self.current.requester is None
                    or self._failed_request is not None
                ):
                    await self._start_request()
                    if self._deferred_ad:
                        self._started_at = time.monotonic()
                        self._previous = None
                elif self.current:
                    await self._policy()
                    await self.browser.resume()
                    self._started_at = time.monotonic()
                else:
                    await self._policy()
            except PlaybackError as exc:
                await self._block(str(exc))
                raise

    async def set_autoplay(self, enabled: bool) -> None:
        async with self.lock:
            self.autoplay = enabled
            try:
                await self._policy()
            except PlaybackError as exc:
                await self._block(str(exc))
                raise

    async def skip(self) -> None:
        async with self.lock:
            if not self.current and not self.queue:
                raise PlaybackError("There is no song to skip.")
            if (await self.browser.observe()).advertisement:
                raise PlaybackError("Wait for the advertisement to finish before skipping.")
            self.error = None
            if self._failed_request and self.queue and self.queue[0] is self._failed_request:
                self.queue.popleft()
                self._failed_request = None
            try:
                if self.queue:
                    await self._start_request()
                elif self.autoplay and self.current:
                    await self._begin_recommendation()
                else:
                    await self.browser.pause()
                    self.current = None
                    self.confirmed = False
                    self._notification = None
                    await self._policy()
            except PlaybackError as exc:
                await self._block(str(exc))
                raise

    async def _begin_recommendation(self) -> None:
        self._waiting_recommendation = True
        self._started_at = time.monotonic()
        self.confirmed = False
        self._notification = None
        await self._policy()
        await self.browser.next_recommendation()

    async def _block(self, message: str) -> None:
        is_new = message != self.error
        self.error = message
        self.paused = True
        self._notification = None
        log.error("Playback suspended: %s", message)
        try:
            await self.browser.pause()
        except PlaybackError:
            log.warning("Browser could not be paused because its connection is unavailable.")
        if is_new:
            await self.report_error(message)

    async def tick(self) -> None:
        async with self.lock:
            if self.error:
                return
            observed = await self.browser.observe()
            if observed.error:
                raise PlaybackError(
                    "YouTube Music reports unavailable playback. Resolve the prompt in Edge "
                    "then use /resume or /skip."
                )
            if self.paused:
                await self.browser.pause()
                self._previous = None
                return
            if observed.advertisement and not self.current and self.queue:
                await self._start_request()
            if not self.current:
                if observed.advertisement:
                    previous = self._previous
                    if (
                        previous is None
                        or not previous.advertisement
                        or previous.position != observed.position
                    ):
                        self._started_at = time.monotonic()
                    elif time.monotonic() - self._started_at > 60:
                        raise PlaybackError(
                            "Advertisement playback stalled. Check Edge/network, then use /resume."
                        )
                    self._previous = observed
                    return
                if self.queue:
                    await self._start_request()
                    return
                self._deferred_ad = False
                await self._policy()
                return
            if observed.advertisement:
                previous = self._previous
                if (
                    previous is None
                    or not previous.advertisement
                    or previous.position != observed.position
                ):
                    self._started_at = time.monotonic()
                elif time.monotonic() - self._started_at > 60:
                    raise PlaybackError(
                        "Advertisement playback stalled. Check Edge/network, then use /resume."
                    )
                self._previous = observed
                return
            old = self._previous
            changed = observed.song and observed.song.video_id != self.current.song.video_id
            if self.queue and (changed or observed.ended or self.current.requester is None):
                await self._start_request()
                return
            if changed:
                if self.autoplay:
                    self._set_current(observed.song, None)
                    await self.browser.clear_ended()
                    await self._policy()
                else:
                    await self.browser.pause()
                    self.current = None
                    self.confirmed = False
                    self._notification = None
                    await self._policy()
                    return
            elif observed.ended and not self._waiting_recommendation:
                if self.autoplay:
                    await self._begin_recommendation()
                else:
                    await self.browser.pause()
                    self.current = None
                    self.confirmed = False
                    self._notification = None
                    await self._policy()
                return
            if self._waiting_recommendation:
                if time.monotonic() - self._started_at > 45:
                    raise PlaybackError("No recommendation started. Request a song or use /resume.")
                return
            advancing = (
                old is not None
                and not old.advertisement
                and old.song is not None
                and observed.song is not None
                and old.song.video_id == observed.song.video_id
                and old.generation == observed.generation
                and observed.position > old.position
                and not observed.paused
                and observed.ready
            )
            if advancing and observed.song:
                self.confirmed = True
                # Prefer actual player metadata over earlier search metadata.
                self.current = Playing(
                    observed.song, self.current.requester, self.current.occurrence
                )
                if self._announced != self.current.occurrence:
                    self._notification = self.current
                self._started_at = time.monotonic()
            elif time.monotonic() - self._started_at > 45:
                raise PlaybackError(
                    "Playback did not start or stalled. Check login, network or Play in Edge, "
                    "then use /resume."
                )
            self._previous = observed

    async def deliver_notification(self) -> None:
        async with self.lock:
            event = self._notification
            if not event or time.monotonic() < self._notification_retry_at:
                return
            # Delivery stays serialized with transitions; retry only unsent announcements.
            try:
                await self.announce(event)
            except NotificationError as exc:
                self._notification_attempts += 1
                if self._notification_attempts >= 3:
                    log.error(
                        "Now-playing announcement failed after 3 attempts: %s. "
                        "Check Discord permissions/connectivity; use /nowplaying for this song.",
                        exc,
                    )
                    self._announced = event.occurrence
                    self._notification = None
                else:
                    log.warning("Announcement delivery failed: %s", exc)
                    self._notification_retry_at = time.monotonic() + 5 * self._notification_attempts
                return
            self._announced = event.occurrence
            self._notification = None

    async def run(self) -> None:
        while True:
            try:
                async with asyncio.timeout(70):
                    await self.tick()
            except (PlaybackError, TimeoutError) as exc:
                async with self.lock:
                    await self._block(str(exc) or "Playback observation timed out.")
            await self.deliver_notification()
            await asyncio.sleep(0.5)


class NotificationError(Exception):
    pass
