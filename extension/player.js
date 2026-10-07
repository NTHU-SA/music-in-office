(() => {
    if (window.OfficeMusicExtension) return;
    const KEY = "office-music-policy";
    let state;
    try {
        state = JSON.parse(sessionStorage.getItem(KEY) || "null");
    } catch (error) {
        console.error("Invalid Office Music tab state", error);
    }
    state ||= {role: null, allowedId: null, allowNext: false, paused: true, generation: 0};
    state.ended = false;
    let lastId = null;
    let lastHeartbeat = Date.now();
    let searchMuted = false;
    const save = () => sessionStorage.setItem(KEY, JSON.stringify(state));
    const video = () => document.querySelector("video");
    const player = () => document.querySelector("#movie_player");
    const isAd = () => !!player()?.classList.contains("ad-showing");
    const data = () => player()?.getVideoData?.() || {};
    const visible = element => !!element?.getClientRects().length;
    const wait = async (predicate, message, timeout = 12000) => {
        const end = Date.now() + timeout;
        while (Date.now() < end) {
            const result = predicate();
            if (result) return result;
            await new Promise(resolve => setTimeout(resolve, 100));
        }
        throw new Error(message);
    };
    function muteSearch() {
        if (searchMuted) return;
        searchMuted = true;
        for (const [property, forced] of [["muted", true], ["volume", 0]]) {
            const descriptor = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, property);
            Object.defineProperty(HTMLMediaElement.prototype, property, {
                configurable: true, enumerable: descriptor.enumerable,
                get() { return descriptor.get.call(this); },
                set() { descriptor.set.call(this, forced); }
            });
        }
    }
    function check() {
        const media = video();
        if (!state.role || !media) return;
        if (state.role === "search") {
            muteSearch();
            media.muted = true;
            media.volume = 0;
            if (!isAd()) media.pause();
            return;
        }
        if (state.role === "login") return;
        if (Date.now() - lastHeartbeat > 8000) {
            state.paused = true;
            save();
        }
        if (isAd()) {
            if (state.paused || (state.ended && !state.allowNext && !state.finishAd)) media.pause();
            return;
        }
        state.finishAd = false;
        const id = data().video_id;
        if (id && id !== lastId) {
            lastId = id;
            state.generation++;
            save();
        }
        if (state.paused || (!state.allowNext && id && id !== state.allowedId)) media.pause();
    }
    if (state.role === "search") muteSearch();
    for (const name of ["play", "playing", "loadedmetadata", "timeupdate", "volumechange"]) {
        document.addEventListener(name, event => {
            if (event.target instanceof HTMLMediaElement) check();
        }, true);
    }
    document.addEventListener("ended", event => {
        if (state.role === "playback" && event.target instanceof HTMLVideoElement && !isAd()) {
            state.ended = true;
        }
    }, true);
    setInterval(check, 1000);
    function observe() {
        check();
        const bar = document.querySelector("ytmusic-player-bar");
        const info = data();
        const media = video();
        return {
            id: info.video_id || new URL(location.href).searchParams.get("v") || "",
            title: bar?.querySelector(".title")?.textContent?.trim() || info.title || "",
            artist: bar?.querySelector(".byline a, .subtitle a")?.textContent?.trim()
                || info.author || bar?.querySelector(".byline, .subtitle")?.textContent?.trim() || "",
            position: media?.currentTime || 0, paused: media?.paused ?? true,
            ended: !!media?.ended || !!state.ended, advertisement: isAd(),
            ready: (media?.readyState || 0) >= 2, generation: state.generation,
            error: document.querySelector("yt-playability-error-supported-renderers")
                ?.textContent?.trim() || null
        };
    }
    async function resume() {
        state.paused = false;
        save();
        const media = await wait(video, "Music player unavailable. Check login/consent.");
        try {
            await media.play();
        } catch (error) {
            state.paused = true;
            save();
            throw new Error("Playback blocked. Click Play once in the managed YouTube Music tab, " +
                "allow site sound/autoplay, then use /resume. " + error.message);
        }
        await wait(() => !media.paused, "YouTube Music did not start. Check the music tab.");
    }
    async function run(command, args) {
        lastHeartbeat = Date.now();
        switch (command) {
            case "initialize":
                state.role = args.role;
                state.paused = true;
                state.allowNext = false;
                state.allowedId = null;
                state.ended = false;
                save();
                check();
                return {};
            case "heartbeat": return {};
            case "disconnect":
            case "pause":
                state.paused = true;
                save();
                video()?.pause();
                return {};
            case "observe": return observe();
            case "policy": {
                state.allowedId = args.id;
                state.allowNext = args.next;
                state.paused = args.paused;
                state.finishAd = !args.paused && isAd();
                save();
                const toggle = document.querySelector("#automix");
                if (visible(toggle) && !!toggle.checked !== args.next) toggle.click();
                check();
                return {};
            }
            case "play":
                state.allowedId = args.id;
                state.allowNext = false;
                state.paused = args.paused;
                state.ended = false;
                save();
                await wait(video, "Music player unavailable. Check login/consent.");
                if (args.paused) video().pause();
                else await resume();
                return {};
            case "resume": await resume(); return {};
            case "clearEnded": state.ended = false; return {};
            case "next": {
                state.ended = false;
                const button = [...document.querySelectorAll(
                    "ytmusic-player-bar .next-button, ytmusic-player-bar #next-button, " +
                    "ytmusic-wiz-player-controls .ytmusicPlayerControlsNextButton button"
                )].find(visible);
                if (!button || button.disabled) throw new Error("No next recommendation available.");
                button.click();
                return {};
            }
            case "resolve": {
                if (state.role !== "search") throw new Error("Missing managed search tab.");
                if (args.isLink) {
                    const id = new URL(args.value).searchParams.get("v");
                    await wait(() => {
                        if (observe().error) throw new Error(observe().error);
                        return !isAd() && data().video_id === id && data().title;
                    }, "Song metadata unavailable. Check login, advertisements or the link.", 40000);
                    const info = data();
                    return {id, title: info.title, artist: info.author || "Unknown artist"};
                }
                return wait(() => {
                    for (const row of document.querySelectorAll(
                        "ytmusic-search-page ytmusic-responsive-list-item-renderer"
                    )) {
                        if (!visible(row)) continue;
                        const kind = row.data?.flexColumns?.[0]
                            ?.musicResponsiveListItemFlexColumnRenderer?.text?.runs?.[0]
                            ?.navigationEndpoint?.watchEndpoint?.watchEndpointMusicSupportedConfigs
                            ?.watchEndpointMusicConfig?.musicVideoType;
                        if (kind !== "MUSIC_VIDEO_TYPE_ATV") continue;
                        const link = row.querySelector('a[href*="watch?v="]');
                        if (!link) continue;
                        const url = new URL(link.getAttribute("href"), location.origin);
                        const id = url.searchParams.get("v");
                        const title = link.textContent.trim();
                        if (!/^[A-Za-z0-9_-]{11}$/.test(id) || !title) continue;
                        const artist = row.querySelector(
                            '.secondary-flex-columns a[href*="channel/"], ' +
                            '.secondary-flex-columns a[href*="browse/"]'
                        )?.textContent.trim() || "Unknown artist";
                        return {id, title, artist};
                    }
                    return null;
                }, "No song found. Check login/consent or try a more specific song.", 15000);
            }
            default: throw new Error("Unknown Office Music command.");
        }
    }
    window.OfficeMusicExtension = {run};
})();
