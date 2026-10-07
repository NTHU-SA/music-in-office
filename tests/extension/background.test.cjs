const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("extension/background.js", "utf8");

function fixture() {
    const tabState = new Map();
    const storage = {};
    const commands = [];
    const sockets = [];
    let nextId = 1;
    class Socket {
        static OPEN = 1;
        constructor(url) {
            this.url = url;
            this.readyState = 0;
            this.sent = [];
            sockets.push(this);
        }
        open() { this.readyState = 1; this.onopen(); }
        send(message) { this.sent.push(JSON.parse(message)); }
        close() { this.readyState = 3; this.onclose(); }
    }
    const listener = {addListener() {}};
    const chrome = {
        tabs: {
            async create({url}) {
                const tab = {id: nextId++, url: "", pendingUrl: url, status: "loading", reads: 0};
                tabState.set(tab.id, tab);
                return {...tab};
            },
            async get(id) {
                const tab = tabState.get(id);
                if (!tab) throw new Error("Tab closed");
                if (++tab.reads >= 3) {
                    tab.url = tab.pendingUrl;
                    tab.status = "complete";
                }
                return {...tab};
            },
            async update(id, update) {
                const tab = tabState.get(id);
                if (!tab) throw new Error("Tab closed");
                if (update.url) {
                    tab.pendingUrl = update.url;
                    tab.status = "loading";
                    tab.reads = 0;
                }
                return {...tab};
            }
        },
        scripting: {
            async executeScript(options) {
                const tab = tabState.get(options.target.tabId);
                assert.equal(tab.status, "complete", "Never script a pending/new document");
                if (!options.args) return [{result: true}];
                commands.push({tabId: tab.id, command: options.args[0], args: options.args[1]});
                return [{result: {ok: true, data: {}}}];
            }
        },
        storage: {session: {
            async get() { return structuredClone(storage); },
            async set(value) { Object.assign(storage, structuredClone(value)); }
        }},
        action: {async setBadgeText() {}, async setTitle() {}, onClicked: listener},
        alarms: {create() {}, onAlarm: listener},
        runtime: {onStartup: listener, onInstalled: listener}
    };
    const context = {
        chrome, WebSocket: Socket, console, Date, Promise, JSON, Object, encodeURIComponent,
        setTimeout: fn => { if (fn.name !== "connect") fn(); },
        setInterval() {}, clearInterval() {}
    };
    vm.runInNewContext(source, context);
    return {context, tabState, storage, commands, sockets};
}

test("initial connection waits for new tab commit before injecting commands", async () => {
    const f = fixture();
    await f.context.dispatch("initialize", {interactive: false}, 0);
    assert.equal(f.tabState.size, 2);
    assert.deepEqual(f.commands.map(value => value.command), [
        "initialize", "initialize", "initialize"
    ]);
    assert.equal(f.commands[0].args.role, "playback");
    assert.equal(f.commands[2].args.role, "search");
});

test("navigation waits for new document and reuses owned tabs", async () => {
    const f = fixture();
    await f.context.dispatch("initialize", {interactive: false}, 0);
    await f.context.dispatch("play", {
        url: "https://music.youtube.com/watch?v=aaaaaaaaaaa", id: "aaaaaaaaaaa", paused: true
    }, 0);
    assert.equal(f.tabState.size, 2);
    assert.deepEqual(f.commands.slice(-2).map(value => value.command), ["policy", "play"]);
    assert.equal(f.tabState.get(f.storage.tabs.playback).url,
        "https://music.youtube.com/watch?v=aaaaaaaaaaa");
});

test("closed or repurposed owned tabs are replaced, never script a personal site", async () => {
    const f = fixture();
    await f.context.dispatch("initialize", {interactive: false}, 0);
    f.tabState.delete(f.storage.tabs.search);
    await f.context.dispatch("resolve", {value: "Song", isLink: false}, 0);
    assert.equal(f.storage.tabs.search, 3);
    const playback = f.tabState.get(f.storage.tabs.playback);
    playback.url = playback.pendingUrl = "https://example.com/";
    await f.context.dispatch("pause", {}, 0);
    assert.equal(f.storage.tabs.playback, 4);
    assert.equal(f.commands.at(-1).command, "pause");
});

test("connection epoch rejects commands queued for a previous session", async () => {
    const f = fixture();
    f.sockets[0].open();
    await assert.rejects(f.context.dispatch("play", {
        url: "https://music.youtube.com/watch?v=aaaaaaaaaaa"
    }, 0), /disconnected/);
    assert.equal(f.commands.some(value => value.command === "play"), false);
    f.sockets[0].close();
});

test("disconnect during policy does not navigate or play a stale request", async () => {
    const f = fixture();
    const socket = f.sockets[0];
    socket.open();
    await f.context.dispatch("initialize", {interactive: false}, 1);
    const original = f.context.chrome.scripting.executeScript;
    f.context.chrome.scripting.executeScript = async options => {
        const result = await original(options);
        if (options.args?.[0] === "policy") socket.close();
        return result;
    };
    await assert.rejects(f.context.dispatch("play", {
        url: "https://music.youtube.com/watch?v=aaaaaaaaaaa", id: "aaaaaaaaaaa", paused: false
    }, 1), /disconnected/);
    assert.equal(f.tabState.get(f.storage.tabs.playback).url, "https://music.youtube.com/");
    assert.equal(f.commands.some(value => value.command === "play"), false);
});

test("interactive initialization opens login mode, not a muted search tab", async () => {
    const f = fixture();
    await f.context.dispatch("initialize", {interactive: true}, 0);
    assert.equal(f.tabState.size, 1);
    assert.equal(f.commands.at(-1).args.role, "login");
});
