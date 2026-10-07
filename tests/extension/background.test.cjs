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
    let now = 0;
    let click;
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
                if (++tab.reads >= 3 && tab.pendingUrl) {
                    tab.url = tab.pendingUrl;
                    delete tab.pendingUrl;
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
        action: {async setBadgeText() {}, async setTitle() {},
            onClicked: {addListener(handler) { click = handler; }}},
        alarms: {create() {}, onAlarm: listener},
        runtime: {onStartup: listener, onInstalled: listener}
    };
    const context = {
        chrome, WebSocket: Socket, console, Date: {now: () => now},
        Promise, JSON, Object, URL, encodeURIComponent,
        setTimeout: (fn, ms) => { if (fn.name !== "connect") { now += ms; fn(); } },
        setInterval() {}, clearInterval() {}
    };
    vm.runInNewContext(source, context);
    return {context, tabState, storage, commands, sockets, click: () => click()};
}

async function until(predicate) {
    for (let attempt = 0; attempt < 100; attempt++) {
        if (predicate()) return;
        await new Promise(resolve => setImmediate(resolve));
    }
    assert.fail("Timed out waiting for extension operation");
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

test("navigation accepts rewritten encoding and added parameters for the same route", async () => {
    for (const [requested, committed] of [
        ["https://music.youtube.com/search?q=Japanese%20song",
         "https://music.youtube.com/search?q=Japanese+song&feature=search"],
        ["https://music.youtube.com/search?q=%E6%97%A5%E6%96%87%E6%AD%8C",
         "https://music.youtube.com/search?feature=search&q=日文歌"],
        ["https://music.youtube.com/watch?v=aaaaaaaaaaa",
         "https://music.youtube.com/watch?v=aaaaaaaaaaa&list=RDAMVMaaaaaaaaaaa"],
        ["https://music.youtube.com/", "https://music.youtube.com/?hl=zh-TW"]
    ]) {
        const f = fixture();
        f.tabState.set(1, {id: 1, url: committed, status: "complete"});
        f.context.chrome.tabs.get = async () => ({...f.tabState.get(1)});
        await f.context.loaded(1, requested, null);
    }
});

test("navigation rejects a different song, search, path or origin", async () => {
    for (const [requested, committed, error] of [
        ["https://music.youtube.com/watch?v=aaaaaaaaaaa",
         "https://music.youtube.com/watch?v=bbbbbbbbbbb", /navigation timed out/],
        ["https://music.youtube.com/search?q=New",
         "https://music.youtube.com/search?q=Old", /navigation timed out/],
        ["https://music.youtube.com/search?q=New",
         "https://music.youtube.com/", /navigation timed out/],
        ["https://music.youtube.com/watch?v=aaaaaaaaaaa",
         "https://accounts.google.com/", /login\/consent/]
    ]) {
        const f = fixture();
        f.context.chrome.tabs.get = async () => ({url: committed, status: "complete"});
        await assert.rejects(f.context.loaded(1, requested, null), error);
    }
});

test("pending navigation cannot mistake the old completed document for the new one", async () => {
    const f = fixture();
    const url = "https://music.youtube.com/watch?v=aaaaaaaaaaa";
    let reads = 0;
    f.context.chrome.tabs.get = async () => {
        reads++;
        return {url, status: "complete", ...(reads < 3 ? {pendingUrl: url} : {})};
    };
    await f.context.loaded(1, url, null);
    assert.equal(reads, 3);
});

test("disconnect during fresh-tab navigation prevents stale initialization", async () => {
    const f = fixture();
    const socket = f.sockets[0];
    socket.open();
    const get = f.context.chrome.tabs.get;
    f.context.chrome.tabs.get = async id => {
        const tab = await get(id);
        if (id === 1 && socket.readyState === 1) socket.close();
        return tab;
    };
    await assert.rejects(f.context.dispatch("initialize", {interactive: true}, 1),
        /disconnected/);
    assert.equal(f.tabState.size, 1);
    assert.equal(f.commands.some(value => value.command === "initialize"), false);
});

test("browser action can finish fresh-tab setup without an RPC epoch", async () => {
    const f = fixture();
    f.sockets[0].open();
    const get = f.context.chrome.tabs.get;
    f.context.chrome.tabs.get = async id => {
        const tab = await get(id);
        if (f.sockets[0].readyState === 1) f.sockets[0].close();
        return tab;
    };
    await f.click();
    assert.equal(f.commands.filter(value => value.command === "initialize").length, 1);
    assert.equal(f.storage.tabs.playback, 1);
});

test("disconnect during tab creation retains ownership without stale initialization", async () => {
    const f = fixture();
    f.sockets[0].open();
    const create = f.context.chrome.tabs.create;
    f.context.chrome.tabs.create = async options => {
        const tab = await create(options);
        f.sockets[0].close();
        return tab;
    };
    await assert.rejects(f.context.dispatch("initialize", {interactive: true}, 1),
        /disconnected/);
    assert.equal(f.storage.tabs.playback, 1);
    assert.equal(f.commands.some(value => value.command === "initialize"), false);
});

test("a reconnect does not reuse or clear a stale managed-tab promise", async () => {
    const f = fixture();
    f.sockets[0].open();
    const get = f.context.chrome.tabs.get;
    let releaseOld;
    let releaseNew;
    let blockedOld = false;
    f.context.chrome.tabs.get = async id => {
        if (id === 1 && !blockedOld) {
            blockedOld = true;
            await new Promise(resolve => { releaseOld = resolve; });
        }
        return get(id);
    };
    const executeScript = f.context.chrome.scripting.executeScript;
    f.context.chrome.scripting.executeScript = async options => {
        if (options.args?.[0] === "initialize" && !releaseNew) {
            await new Promise(resolve => { releaseNew = resolve; });
        }
        return executeScript(options);
    };
    const oldRequest = f.context.dispatch("initialize", {interactive: true}, 1);
    await until(() => !!releaseOld);
    f.sockets[0].close();
    await f.context.connect();
    f.sockets[1].open();
    const newRequest = f.context.dispatch("initialize", {interactive: true}, 3);
    await until(() => !!releaseNew);
    releaseOld();
    await assert.rejects(oldRequest, /disconnected/);
    const concurrent = f.context.dispatch("pause", {}, 3);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(f.commands.some(value => value.command === "pause"), false);
    releaseNew();
    await Promise.all([newRequest, concurrent]);
    assert.deepEqual(f.commands.filter(value => value.command === "initialize")
        .map(value => value.tabId), [1, 1]);
    assert.equal(f.storage.tabs.playback, 1);
    assert.equal(f.tabState.size, 1);
});

test("disconnect during navigation prevents waiting or running the new command", async () => {
    const f = fixture();
    f.sockets[0].open();
    await f.context.dispatch("initialize", {interactive: false}, 1);
    const update = f.context.chrome.tabs.update;
    f.context.chrome.tabs.update = async (...args) => {
        const result = await update(...args);
        f.sockets[0].close();
        return result;
    };
    await assert.rejects(f.context.dispatch("play", {
        url: "https://music.youtube.com/watch?v=aaaaaaaaaaa", id: "aaaaaaaaaaa", paused: false
    }, 1), /disconnected/);
    assert.equal(f.commands.some(value => value.command === "play"), false);
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

test("unresolved search does not delay pause, and playback commands stay ordered", async () => {
    const f = fixture();
    await f.context.dispatch("initialize", {interactive: false}, 0);
    const socket = f.sockets[0];
    socket.open();
    const executeScript = f.context.chrome.scripting.executeScript;
    let finishSearch;
    let finishPause;
    f.context.chrome.scripting.executeScript = async options => {
        const result = await executeScript(options);
        if (options.args?.[0] === "resolve") {
            return new Promise(resolve => { finishSearch = () => resolve(result); });
        }
        if (options.args?.[0] === "pause") {
            return new Promise(resolve => { finishPause = () => resolve(result); });
        }
        return result;
    };
    const send = (id, command, args = {}) =>
        socket.onmessage({data: JSON.stringify({id, command, args})});
    send(1, "resolve", {value: "Song", isLink: false});
    await until(() => !!finishSearch);
    send(2, "pause");
    await until(() => !!finishPause);
    send(3, "resume");
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(f.commands.some(value => value.command === "resume"), false);
    assert.equal(socket.sent.some(reply => reply.id === 1 || reply.id === 2), false);

    finishPause();
    await until(() => socket.sent.some(reply => reply.id === 3));
    assert.deepEqual(socket.sent.map(reply => reply.id), [2, 3]);
    assert.deepEqual(f.commands.slice(-3).map(value => value.command), ["resolve", "pause", "resume"]);
    assert.equal(f.commands.find(value => value.command === "pause").tabId, f.storage.tabs.playback);
    finishSearch();
    await until(() => socket.sent.some(reply => reply.id === 1));
    assert.deepEqual(socket.sent.map(reply => reply.id), [2, 3, 1]);
});

test("simultaneous initialization and search retain both managed tabs", async () => {
    const f = fixture();
    const socket = f.sockets[0];
    socket.open();
    socket.onmessage({data: JSON.stringify({id: 1, command: "initialize",
        args: {interactive: false}})});
    socket.onmessage({data: JSON.stringify({id: 2, command: "resolve",
        args: {value: "Song", isLink: false}})});
    await until(() => socket.sent.length === 2);
    assert.equal(f.tabState.size, 2);
    assert.notEqual(f.storage.tabs.playback, f.storage.tabs.search);
    assert.deepEqual(socket.sent.map(reply => reply.ok), [true, true]);
});

test("invalid RPC messages close the socket instead of escaping the message handler", () => {
    for (const message of ["not JSON", "null"]) {
        const f = fixture();
        const socket = f.sockets[0];
        socket.open();
        assert.doesNotThrow(() => socket.onmessage({data: message}));
        assert.equal(socket.readyState, 3);
    }
});
