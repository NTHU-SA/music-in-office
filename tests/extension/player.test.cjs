const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("extension/player.js", "utf8");

function fixture(saved) {
    let now = 0;
    const listeners = {};
    const stored = new Map(saved ? [["office-music-policy", JSON.stringify(saved)]] : []);
    class Media {
        constructor() {
            this.paused = false;
            this.ended = false;
            this.currentTime = 4;
            this.readyState = 4;
        }
        get muted() { return this._muted || false; }
        set muted(value) { this._muted = value; }
        get volume() { return this._volume ?? 1; }
        set volume(value) { this._volume = value; }
        pause() { this.paused = true; }
        async play() {
            this.paused = false;
            listeners.play({target: this});
        }
    }
    let media = new Media();
    let advertisement = false;
    let info = {video_id: "aaaaaaaaaaa", title: "Fallback", author: "Artist"};
    let rows = [];
    const next = {getClientRects: () => [1], click() { next.clicked = true; }};
    const bar = {querySelector: selector => ({
        textContent: selector === ".title" ? "Song title" : "Artist name"
    })};
    const document = {
        querySelector(selector) {
            return {
                video: media,
                "#movie_player": {
                    classList: {contains: () => advertisement}, getVideoData: () => info
                },
                "ytmusic-player-bar": bar
            }[selector] || null;
        },
        querySelectorAll(selector) {
            return selector.includes("responsive-list-item") ? rows : [next];
        },
        addEventListener(name, listener) { listeners[name] = listener; }
    };
    const context = {
        document, HTMLMediaElement: Media, HTMLVideoElement: Media, console, URL,
        location: {href: "https://music.youtube.com/watch?v=aaaaaaaaaaa",
                   origin: "https://music.youtube.com"},
        sessionStorage: {getItem: key => stored.get(key), setItem: (key, value) => stored.set(key, value)},
        Date: {now: () => now},
        setInterval(fn) { context.check = fn; }, setTimeout
    };
    context.window = context;
    vm.runInNewContext(source, context);
    return {
        context, media, next, stored,
        run: (command, args = {}) => context.OfficeMusicExtension.run(command, args),
        event: name => listeners[name]({target: media}),
        ad: value => { advertisement = value; },
        info: value => { info = value; },
        rows: value => { rows = value; },
        replaceMedia: () => { media = new Media(); return media; },
        time: value => { now = value; }
    };
}

test("unmanaged personal tabs are never paused or muted", () => {
    const f = fixture();
    f.event("playing");
    f.time(20000);
    f.context.check();
    assert.equal(f.media.paused, false);
    assert.equal(f.media.muted, false);
});

test("policy allows current song but blocks unrequested next song", async () => {
    const f = fixture();
    await f.run("initialize", {role: "playback"});
    await f.run("policy", {id: "aaaaaaaaaaa", next: false, paused: false});
    f.media.paused = false;
    f.event("playing");
    assert.equal(f.media.paused, false);
    f.info({video_id: "bbbbbbbbbbb", title: "Next", author: "Artist"});
    f.event("playing");
    assert.equal(f.media.paused, true);
    assert.equal((await f.run("observe")).generation, 2);
    await f.run("policy", {id: "aaaaaaaaaaa", next: true, paused: false});
    f.media.paused = false;
    f.event("playing");
    assert.equal(f.media.paused, false);
});

test("ads never count as ended songs and cannot bypass pause", async () => {
    const f = fixture();
    await f.run("initialize", {role: "playback"});
    await f.run("policy", {id: "aaaaaaaaaaa", next: false, paused: false});
    f.ad(true);
    f.media.paused = false;
    f.event("ended");
    f.event("playing");
    assert.equal((await f.run("observe")).ended, false);
    assert.equal(f.media.paused, false);
    await f.run("pause");
    f.media.paused = false;
    f.event("playing");
    assert.equal(f.media.paused, true);
});

test("ended song blocks next ad, but an already running ad can finish", async () => {
    const f = fixture();
    await f.run("initialize", {role: "playback"});
    await f.run("policy", {id: "aaaaaaaaaaa", next: false, paused: false});
    f.event("ended");
    f.ad(true);
    f.media.paused = false;
    f.event("playing");
    assert.equal(f.media.paused, true);
    await f.run("policy", {id: "bbbbbbbbbbb", next: false, paused: false});
    f.media.paused = false;
    f.event("playing");
    assert.equal(f.media.paused, false);
});

test("search tab remains muted across navigation and ads", async () => {
    const f = fixture();
    await f.run("initialize", {role: "search"});
    f.ad(true);
    f.media.paused = false;
    f.media.muted = false;
    f.media.volume = 1;
    f.event("playing");
    assert.equal(f.media.muted, true);
    assert.equal(f.media.volume, 0);
    assert.equal(f.media.paused, false);
    const saved = JSON.parse(f.stored.get("office-music-policy"));
    const reload = fixture(saved);
    reload.media.muted = false;
    assert.equal(reload.media.muted, true);
    reload.event("playing");
    assert.equal(reload.media.paused, true);
});

test("disconnect and missing heartbeat pause playback without resuming on reconnect", async () => {
    for (const disconnect of [true, false]) {
        const f = fixture();
        await f.run("initialize", {role: "playback"});
        await f.run("policy", {id: "aaaaaaaaaaa", next: true, paused: false});
        f.media.paused = false;
        if (disconnect) await f.run("disconnect");
        else {
            f.time(9000);
            f.context.check();
        }
        assert.equal(f.media.paused, true);
        await f.run("heartbeat");
        f.media.paused = false;
        f.event("playing");
        assert.equal(f.media.paused, true);
    }
});

test("autoplay rejection is actionable and persists paused state", async () => {
    const f = fixture();
    await f.run("initialize", {role: "playback"});
    f.media.play = async () => { throw Object.assign(new Error("Autoplay denied"),
        {name: "NotAllowedError"}); };
    await assert.rejects(f.run("resume"), /Click Play once/);
    assert.equal(JSON.parse(f.stored.get("office-music-policy")).paused, true);
    assert.equal(f.media.paused, true);
});

test("resume waits for the requested song to be ready before playing", async () => {
    const f = fixture();
    await f.run("initialize", {role: "playback"});
    f.media.readyState = 0;
    let calls = 0;
    const play = f.media.play.bind(f.media);
    f.media.play = async () => { calls++; await play(); };
    const pending = f.run("play", {id: "bbbbbbbbbbb", paused: false});
    await new Promise(resolve => setTimeout(resolve, 120));
    assert.equal(calls, 0);
    f.media.readyState = 4;
    await new Promise(resolve => setTimeout(resolve, 120));
    assert.equal(calls, 0);
    f.info({video_id: "bbbbbbbbbbb", title: "Requested", author: "Artist"});
    await pending;
    assert.equal(calls, 1);
    assert.equal(f.media.paused, false);
});

test("load interruptions retry against the current media element", async () => {
    const f = fixture();
    await f.run("initialize", {role: "playback"});
    let replacement;
    f.media.play = async () => {
        replacement = f.replaceMedia();
        replacement.paused = true;
        throw Object.assign(new Error("The play() request was interrupted by a new load request."),
            {name: "AbortError"});
    };
    await f.run("play", {id: "aaaaaaaaaaa", paused: false});
    assert.equal(replacement.paused, false);
    assert.equal(JSON.parse(f.stored.get("office-music-policy")).paused, false);
});

test("repeated load interruptions stop after three attempts without autoplay advice", async () => {
    const f = fixture();
    await f.run("initialize", {role: "playback"});
    let calls = 0;
    f.media.play = async () => {
        calls++;
        throw Object.assign(new Error("New load request"), {name: "AbortError"});
    };
    await assert.rejects(f.run("resume"), error =>
        /Playback failed.*New load request/.test(error.message) &&
        !error.message.includes("Click Play once"));
    assert.equal(calls, 3);
    assert.equal(JSON.parse(f.stored.get("office-music-policy")).paused, true);
    assert.equal(f.media.paused, true);
});

test("non-permission playback errors are not retried or labeled autoplay blocks", async () => {
    const f = fixture();
    await f.run("initialize", {role: "playback"});
    let calls = 0;
    f.media.play = async () => {
        calls++;
        throw Object.assign(new Error("Unsupported media"), {name: "NotSupportedError"});
    };
    await assert.rejects(f.run("resume"), /Playback failed.*Unsupported media/);
    assert.equal(calls, 1);
    assert.equal(JSON.parse(f.stored.get("office-music-policy")).paused, true);
});

test("pause during a load interruption prevents further play attempts", async () => {
    const f = fixture();
    await f.run("initialize", {role: "playback"});
    let calls = 0;
    f.media.play = async () => {
        calls++;
        await f.run("pause");
        throw Object.assign(new Error("Interrupted by pause"), {name: "AbortError"});
    };
    await assert.rejects(f.run("resume"), /Playback was paused or disconnected/);
    assert.equal(calls, 1);
    assert.equal(f.media.paused, true);
});

test("loading timeout persists paused state without attempting playback", async () => {
    const f = fixture();
    await f.run("initialize", {role: "playback"});
    f.media.readyState = 0;
    let calls = 0;
    f.media.play = async () => { calls++; };
    const pending = assert.rejects(f.run("resume"), /still loading/);
    f.time(12000);
    await pending;
    assert.equal(calls, 0);
    assert.equal(JSON.parse(f.stored.get("office-music-policy")).paused, true);
    assert.equal(f.media.paused, true);
});

test("failure to start after play resolves also restores paused policy", async () => {
    const f = fixture();
    await f.run("initialize", {role: "playback"});
    f.media.play = async () => {};
    const pending = assert.rejects(f.run("resume"), /YouTube Music did not start/);
    await new Promise(resolve => setTimeout(resolve, 120));
    f.time(12000);
    await pending;
    assert.equal(JSON.parse(f.stored.get("office-music-policy")).paused, true);
    assert.equal(f.media.paused, true);
});

test("paused deliberate replay stays paused and creates a new generation on reload", async () => {
    const f = fixture({role: "playback", allowedId: "aaaaaaaaaaa",
        allowNext: false, paused: true, generation: 3});
    await f.run("play", {id: "aaaaaaaaaaa", paused: true});
    const observed = await f.run("observe");
    assert.equal(observed.paused, true);
    assert.equal(observed.generation, 4);
    assert.equal(observed.title, "Song title");
});

test("login mode permits manual playback and disconnect pauses it", async () => {
    const f = fixture();
    await f.run("initialize", {role: "login"});
    f.media.paused = false;
    f.event("playing");
    assert.equal(f.media.paused, false);
    await f.run("disconnect");
    assert.equal(f.media.paused, true);
});

test("search ignores hidden suggestions and promoted videos", async () => {
    const f = fixture();
    await f.run("initialize", {role: "search"});
    const row = (kind, title, visible = true) => ({
        getClientRects: () => visible ? [1] : [],
        data: {flexColumns: [{musicResponsiveListItemFlexColumnRenderer: {
            text: {runs: [{navigationEndpoint: {watchEndpoint: {
                watchEndpointMusicSupportedConfigs: {watchEndpointMusicConfig: {musicVideoType: kind}}
            }}}]}
        }}]},
        querySelector: selector => selector.startsWith("a[") ? {
            getAttribute: () => "watch?v=aaaaaaaaaaa", textContent: title
        } : {textContent: "Artist"}
    });
    f.rows([row("MUSIC_VIDEO_TYPE_ATV", "Hidden", false),
            row("MUSIC_VIDEO_TYPE_UGC", "Promoted"), row("MUSIC_VIDEO_TYPE_ATV", "Actual song")]);
    const song = await f.run("resolve", {isLink: false});
    assert.equal(song.title, "Actual song");
    assert.equal(song.artist, "Artist");
});

test("link metadata waits for ad end and requested identity", async () => {
    const f = fixture();
    await f.run("initialize", {role: "search"});
    f.ad(true);
    f.info({video_id: "bbbbbbbbbbb", title: "Ad", author: "Sponsor"});
    let resolved = false;
    const pending = f.run("resolve", {
        isLink: true, value: "https://music.youtube.com/watch?v=aaaaaaaaaaa"
    }).then(value => { resolved = true; return value; });
    await new Promise(resolve => setTimeout(resolve, 120));
    assert.equal(resolved, false);
    f.info({video_id: "aaaaaaaaaaa", title: "Requested", author: "Artist"});
    await new Promise(resolve => setTimeout(resolve, 120));
    assert.equal(resolved, false);
    f.ad(false);
    assert.equal((await pending).title, "Requested");
});
