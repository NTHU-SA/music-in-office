const ENDPOINT = "ws://127.0.0.1:18765/bridge";
let socket;
let heartbeat;
let connecting = false;
let epoch = 0;
let playbackChain = Promise.resolve();
let searchChain = Promise.resolve();
let tabStorageChain = Promise.resolve();
let tabs = {};
const managing = {};
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function requireEpoch(activeEpoch) {
    if (activeEpoch !== null && activeEpoch !== epoch) {
        throw new Error("Local service disconnected.");
    }
}

async function badge(text, title) {
    await chrome.action.setBadgeText({text});
    await chrome.action.setTitle({title: `Office Music Link: ${title}`});
}

async function managed(role, activeEpoch) {
    requireEpoch(activeEpoch);
    if (managing[role]?.epoch === activeEpoch) return managing[role].pending;
    const pending = findOrCreateManaged(role, activeEpoch);
    const entry = {epoch: activeEpoch, pending};
    managing[role] = entry;
    try {
        return await pending;
    } finally {
        if (managing[role] === entry) delete managing[role];
    }
}

async function findOrCreateManaged(role, activeEpoch) {
    const stored = await chrome.storage.session.get("tabs");
    requireEpoch(activeEpoch);
    tabs = {...stored.tabs, ...tabs};
    const id = tabs[role];
    if (id) {
        let tab;
        try {
            tab = await chrome.tabs.get(id);
        } catch (error) {
            requireEpoch(activeEpoch);
            console.warn("Managed tab unavailable", error.message);
        }
        requireEpoch(activeEpoch);
        if (tab?.url?.startsWith("https://music.youtube.com/")) return id;
        if (!tab?.url && tab?.pendingUrl === "https://music.youtube.com/") {
            await loaded(id, "https://music.youtube.com/", activeEpoch);
            await run(id, "initialize", {role}, activeEpoch);
            return id;
        }
    }
    const tab = await chrome.tabs.create({url: "https://music.youtube.com/", active: false});
    tabs[role] = tab.id;
    const save = tabStorageChain.then(() => chrome.storage.session.set({tabs: {...tabs}}));
    tabStorageChain = save.catch(() => {});
    await save;
    await loaded(tab.id, "https://music.youtube.com/", activeEpoch);
    requireEpoch(activeEpoch);
    await run(tab.id, "initialize", {role}, activeEpoch);
    return tab.id;
}

async function run(tabId, command, args = {}, activeEpoch = null) {
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
        requireEpoch(activeEpoch);
        const tab = await chrome.tabs.get(tabId);
        if (!tab.url?.startsWith("https://music.youtube.com/")) {
            throw new Error("Managed tab left YouTube Music. Restart the bot.");
        }
        // Retry only script readiness, not an operation that already started.
        const [ready] = await chrome.scripting.executeScript({
            target: {tabId}, world: "MAIN",
            func: () => !!window.OfficeMusicExtension
        });
        if (ready?.result) {
            requireEpoch(activeEpoch);
            const [result] = await chrome.scripting.executeScript({
                target: {tabId}, world: "MAIN",
                func: async (command, args) => {
                    try {
                        return {ok: true, data: await window.OfficeMusicExtension.run(command, args)};
                    } catch (error) {
                        return {ok: false, error: error.message};
                    }
                }, args: [command, args]
            });
            if (!result?.result?.ok) {
                throw new Error(result?.result?.error || "YouTube Music did not respond.");
            }
            return result.result.data;
        }
        await sleep(150);
    }
    throw new Error("YouTube Music did not load. Check network/login and restart.");
}

async function navigate(tabId, url, activeEpoch) {
    requireEpoch(activeEpoch);
    await chrome.tabs.update(tabId, {url});
    await loaded(tabId, url, activeEpoch);
}

function sameRoute(actual, expected) {
    if (!actual) return false;
    const current = new URL(actual);
    const target = new URL(expected);
    if (current.origin !== target.origin || current.pathname !== target.pathname) return false;
    const key = target.pathname === "/watch" ? "v" :
        target.pathname === "/search" ? "q" : null;
    return key === null || current.searchParams.get(key) === target.searchParams.get(key);
}

async function loaded(tabId, url, activeEpoch) {
    // Wait for the new document rather than accidentally commanding the old one.
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
        requireEpoch(activeEpoch);
        const tab = await chrome.tabs.get(tabId);
        requireEpoch(activeEpoch);
        if (tab.status === "complete" && !tab.pendingUrl && sameRoute(tab.url, url)) return;
        if (tab.status === "complete" && !tab.pendingUrl &&
            !tab.url?.startsWith("https://music.youtube.com/")) {
            throw new Error("YouTube Music redirected outside the music site. " +
                "Check login/consent in the managed tab, then retry.");
        }
        await sleep(150);
    }
    throw new Error("YouTube Music navigation timed out before reaching the requested page. " +
        "Check network/login/consent in the managed tab, then retry.");
}

async function dispatch(command, args, activeEpoch) {
    if (command === "resolve") {
        const search = await managed("search", activeEpoch);
        requireEpoch(activeEpoch);
        await run(search, "initialize", {role: "search"}, activeEpoch);
        const url = args.isLink ? args.value :
            `https://music.youtube.com/search?q=${encodeURIComponent(args.value)}`;
        await navigate(search, url, activeEpoch);
        return run(search, "resolve", args, activeEpoch);
    }
    const playback = await managed("playback", activeEpoch);
    requireEpoch(activeEpoch);
    if (command === "initialize") {
        await run(playback, "initialize", {role: args.interactive ? "login" : "playback"}, activeEpoch);
        requireEpoch(activeEpoch);
        if (args.interactive) {
            await chrome.tabs.update(playback, {active: true});
        } else {
            await managed("search", activeEpoch);
        }
        return {};
    }
    if (command === "play") {
        await run(playback, "policy", {id: args.id, next: false, paused: args.paused}, activeEpoch);
        await navigate(playback, args.url, activeEpoch);
        return run(playback, "play", args, activeEpoch);
    }
    return run(playback, command, args, activeEpoch);
}

async function connect() {
    if (connecting || socket?.readyState === WebSocket.OPEN) return;
    connecting = true;
    const current = new WebSocket(ENDPOINT);
    socket = current;
    current.onopen = () => {
        connecting = false;
        const activeEpoch = ++epoch;
        playbackChain = Promise.resolve();
        searchChain = Promise.resolve();
        badge("ON", "connected").catch(console.error);
        heartbeat = setInterval(() => {
            if (current.readyState !== WebSocket.OPEN) return;
            current.send(JSON.stringify({id: 0, ok: true, data: {}}));
            for (const tabId of Object.values(tabs)) {
                run(tabId, "heartbeat").catch(error => console.warn(error.message));
            }
        }, 2000);
        current.onmessage = event => {
            let request;
            let search;
            try {
                request = JSON.parse(event.data);
                search = request.command === "resolve";
            } catch (error) {
                console.error("Invalid local service message:", error.message);
                current.close();
                return;
            }
            const pending = search ? searchChain : playbackChain;
            const next = pending.then(async () => {
                if (activeEpoch !== epoch) return;
                try {
                    const data = await dispatch(request.command, request.args, activeEpoch);
                    if (activeEpoch === epoch && current.readyState === WebSocket.OPEN) {
                        current.send(JSON.stringify({id: request.id, ok: true, data}));
                        await badge("ON", "connected");
                    }
                } catch (error) {
                    console.error("Office Music operation failed:", error.message);
                    if (activeEpoch === epoch && current.readyState === WebSocket.OPEN) {
                        current.send(JSON.stringify({id: request.id, ok: false, error: error.message}));
                        await badge("!", error.message);
                    }
                }
            }).catch(error => {
                console.error("Invalid local service message:", error.message);
                current.close();
            });
            if (search) searchChain = next;
            else playbackChain = next;
        };
    };
    current.onclose = () => {
        if (socket !== current) return;
        connecting = false;
        epoch++;
        clearInterval(heartbeat);
        badge("OFF", "start the desktop bot to connect").catch(console.error);
        for (const tabId of Object.values(tabs)) {
            run(tabId, "disconnect").catch(error => console.warn(error.message));
        }
        setTimeout(connect, 1000);
    };
    current.onerror = () => current.close();
}

chrome.alarms.create("reconnect", {periodInMinutes: 0.5});
chrome.alarms.onAlarm.addListener(() => connect());
chrome.runtime.onStartup.addListener(() => connect());
chrome.runtime.onInstalled.addListener(() => connect());
chrome.action.onClicked.addListener(async () => {
    try {
        const tabId = await managed("playback", null);
        await chrome.tabs.update(tabId, {active: true});
        await connect();
    } catch (error) {
        console.error(error);
        await badge("!", error.message);
    }
});
connect();
