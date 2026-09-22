const STREAM_RE = /\.(m3u8|mpd)(?:$|[?#])/i;
const CANONICAL_RE = /\.(m3u8|mpd)\?[^#]*\bid=/i;
const MAX_ITEMS = 2000;
const CHANNEL_TIMEOUT_MS = 18000;
const PARALLEL_CHANNELS = 2;

const state = {
  running: false, stage: "idle", total: 0, completed: 0,
  current: [], inventory: []
};

async function getCaptures() {
  const data = await browser.storage.local.get({ captures: [] });
  return Array.isArray(data.captures) ? data.captures : [];
}
async function saveCapture(item) {
  const captures = await getCaptures();
  const key = [item.channelUrl, item.streamUrl, item.referer, item.userAgent].join("|");
  if (captures.some(x => [x.channelUrl, x.streamUrl, x.referer, x.userAgent].join("|") === key)) return;
  captures.unshift(item);
  await browser.storage.local.set({ captures: captures.slice(0, MAX_ITEMS) });
}
function isBhoomLive(url) {
  try {
    const u = new URL(url);
    return (u.hostname === "bhoomtv.org" || u.hostname === "www.bhoomtv.org") && u.pathname.startsWith("/live/");
  } catch (_) { return false; }
}
function rankStream(url) {
  const low = url.toLowerCase();
  let score = 0;
  if (CANONICAL_RE.test(url)) score += 100;
  if (low.includes("stream.m3u8")) score += 20;
  if (low.includes("segment=")) score -= 80;
  if (low.includes("token=")) score -= 10;
  if (low.includes(".mpd")) score += 10;
  return score;
}
async function sendStatus(extra = {}) {
  await browser.storage.local.set({
    autoStatus: {
      running: state.running, stage: state.stage, total: state.total,
      completed: state.completed, current: state.current.slice(),
      inventoryCount: state.inventory.length,
      capturedCount: (await getCaptures()).length,
      ...extra, updatedAt: new Date().toISOString()
    }
  });
}
async function openTab(url) { return browser.tabs.create({url, active: false}); }
async function waitForTab(tabId, ms) {
  return new Promise(resolve => {
    const timer = setTimeout(() => { browser.tabs.onUpdated.removeListener(listener); resolve(); }, ms);
    const listener = (id, info) => {
      if (id !== tabId || info.status !== "complete") return;
      clearTimeout(timer); browser.tabs.onUpdated.removeListener(listener); resolve();
    };
    browser.tabs.onUpdated.addListener(listener);
  });
}
async function categoryPage(url) {
  const tab = await openTab(url);
  try {
    await waitForTab(tab.id, 12000);
    return await browser.tabs.sendMessage(tab.id, {type: "extractCategory"}) || {links: [], next: null, challenged: false};
  } catch (e) {
    return {links: [], next: null, challenged: false, error: String(e)};
  } finally {
    try { await browser.tabs.remove(tab.id); } catch (_) {}
  }
}
async function crawlCategories() {
  const categories = [
    {group: "Tamil TV", url: "https://bhoomtv.org/channel/tamil/"},
    {group: "Tamil Local TV", url: "https://bhoomtv.org/channel/tamil-local-tv/"}
  ];
  const byUrl = new Map();
  for (const category of categories) {
    let next = category.url;
    const visited = new Set();
    let safety = 0;
    while (next && !visited.has(next) && safety++ < 500) {
      visited.add(next);
      state.stage = "inventory";
      await sendStatus({category: category.group, pageUrl: next});
      const result = await categoryPage(next);
      if (result.challenged && result.links.length === 0) {
        await sendStatus({error: "BhoomTV browser page returned a Cloudflare challenge."});
        break;
      }
      for (const row of result.links || []) {
        if (!byUrl.has(row.url)) byUrl.set(row.url, {...row, group: category.group});
      }
      if (!result.links || result.links.length === 0) break;
      next = result.next || null;
    }
  }
  state.inventory = Array.from(byUrl.values());
  state.total = state.inventory.length;
  await browser.storage.local.set({inventory: state.inventory});
  await sendStatus();
}
async function scanChannel(channel) {
  const tab = await openTab(channel.url);
  const started = Date.now();
  try {
    await waitForTab(tab.id, 10000);
    try { await browser.tabs.sendMessage(tab.id, {type: "playChannel"}); } catch (_) {}
    while (Date.now() - started < CHANNEL_TIMEOUT_MS) {
      const rows = (await getCaptures()).filter(x => x.channelUrl === channel.url);
      if (rows.some(x => rankStream(x.streamUrl) >= 100)) break;
      await new Promise(r => setTimeout(r, 1000));
    }
  } finally {
    try { await browser.tabs.remove(tab.id); } catch (_) {}
  }
}
async function runChannelScan() {
  state.stage = "streams";
  const queue = state.inventory.slice();
  let index = 0;
  async function worker() {
    while (index < queue.length && state.running) {
      const channel = queue[index++];
      state.current.push(channel.title);
      state.current = state.current.slice(-PARALLEL_CHANNELS);
      await sendStatus();
      await scanChannel(channel);
      state.current = state.current.filter(x => x !== channel.title);
      state.completed++;
      await sendStatus();
    }
  }
  await Promise.all(Array.from({length: PARALLEL_CHANNELS}, () => worker()));
}
async function startAutoScan() {
  if (state.running) return {ok:false, error:"Already running"};
  state.running = true; state.stage = "starting"; state.completed = 0; state.total = 0;
  state.inventory = []; state.current = [];
  await browser.storage.local.set({captures: []});
  await sendStatus();
  try {
    await crawlCategories();
    if (state.inventory.length) await runChannelScan();
    state.stage = "complete";
  } catch (e) {
    state.stage = "error";
    await sendStatus({error:String(e)});
  } finally {
    state.running = false; state.current = [];
    await sendStatus();
  }
  return {ok:true};
}
function canonicalM3U(rows) {
  const best = new Map();
  for (const row of rows) {
    if (!isBhoomLive(row.channelUrl) || !STREAM_RE.test(row.streamUrl)) continue;
    const cur = best.get(row.channelUrl);
    if (!cur || rankStream(row.streamUrl) > rankStream(cur.streamUrl)) best.set(row.channelUrl, row);
  }
  const lines = ["#EXTM3U"];
  for (const row of Array.from(best.values()).sort((a,b) => String(a.channelName).localeCompare(String(b.channelName)))) {
    const title = String(row.channelName || "BhoomTV").replace(/"/g, "&quot;");
    const referer = String(row.referer || row.channelUrl || "");
    const ua = String(row.userAgent || "Mozilla/5.0");
    const url = String(row.streamUrl || "");
    lines.push('#EXTINF:-1 group-title="Tamil",' + title);
    lines.push("#EXTVLCOPT:http-referrer=" + referer);
    lines.push("#EXTVLCOPT:http-user-agent=" + ua);
    lines.push(url + "|Referer=" + referer + "&User-Agent=" + ua);
    lines.push("");
  }
  return lines.join("\n").trim() + "\n";
}

browser.webRequest.onBeforeSendHeaders.addListener(async details => {
  if (!STREAM_RE.test(details.url) || details.tabId < 0) return;
  let tab;
  try { tab = await browser.tabs.get(details.tabId); } catch (_) { return; }
  if (!isBhoomLive(tab?.url || "")) return;
  const headers = details.requestHeaders || [];
  const get = name => {
    const h = headers.find(x => String(x.name || "").toLowerCase() === name.toLowerCase());
    return h ? String(h.value || "") : "";
  };
  const channel = state.inventory.find(x => x.url === tab.url);
  await saveCapture({
    channelName: channel?.title || tab.title || tab.url,
    channelUrl: tab.url,
    streamUrl: details.url,
    referer: get("Referer") || "",
    userAgent: get("User-Agent") || "",
    capturedAt: new Date().toISOString()
  });
  await sendStatus({lastCapture: details.url});
}, {urls:["<all_urls>"]}, ["requestHeaders", "extraHeaders"]);

browser.runtime.onMessage.addListener(async message => {
  if (!message || typeof message.type !== "string") return null;
  if (message.type === "startAutoScan") return startAutoScan();
  if (message.type === "stopAutoScan") { state.running=false; state.stage="stopping"; await sendStatus(); return {ok:true}; }
  if (message.type === "getStatus") {
    const s = await browser.storage.local.get({autoStatus:null});
    return s.autoStatus || {running:false, stage:"idle", total:0, completed:0, current:[], inventoryCount:0, capturedCount:(await getCaptures()).length};
  }
  if (message.type === "getCaptures") return {captures: await getCaptures()};
  if (message.type === "getM3U") return {m3u: canonicalM3U(await getCaptures())};
  if (message.type === "clearCaptures") { await browser.storage.local.set({captures:[]}); return {ok:true}; }
  if (message.type === "downloadM3U") {
    const blob = new Blob([canonicalM3U(await getCaptures())], {type:"audio/x-mpegurl"});
    const url = URL.createObjectURL(blob);
    await browser.downloads.download({url, filename:"bhoomtv-captured.m3u", saveAs:true});
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    return {ok:true};
  }
  if (message.type === "downloadJSON") {
    const blob = new Blob([JSON.stringify(await getCaptures(), null, 2)], {type:"application/json"});
    const url = URL.createObjectURL(blob);
    await browser.downloads.download({url, filename:"bhoomtv-captured.json", saveAs:true});
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    return {ok:true};
  }
  return null;
});
sendStatus();
