const STREAM_RE = /\.(m3u8|mpd)(?:$|[?#])/i;
const MAX_ITEMS = 500;

async function getCaptures() {
  const data = await browser.storage.local.get({ captures: [] });
  return Array.isArray(data.captures) ? data.captures : [];
}

async function saveCapture(item) {
  const captures = await getCaptures();
  const key = [item.channelUrl, item.streamUrl, item.referer, item.userAgent].join("|");
  const exists = captures.some((x) => [x.channelUrl, x.streamUrl, x.referer, x.userAgent].join("|") === key);
  if (exists) return;

  captures.unshift(item);
  await browser.storage.local.set({ captures: captures.slice(0, MAX_ITEMS) });
}

async function bhoomContext(tabId) {
  if (typeof tabId !== "number" || tabId < 0) return null;

  try {
    const tab = await browser.tabs.get(tabId);
    const url = String(tab.url || "");
    const parsed = new URL(url);

    if (parsed.hostname !== "bhoomtv.org" && parsed.hostname !== "www.bhoomtv.org") {
      return null;
    }
    if (!parsed.pathname.startsWith("/live/")) {
      return null;
    }

    const slug = parsed.pathname.split("/").filter(Boolean).pop() || "channel";
    const fallbackName = slug.replace(/[-_]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

    return {
      channelUrl: url,
      channelName: (tab.title || "").replace(/\s+/g, " ").trim() || fallbackName
    };
  } catch (_) {
    return null;
  }
}

browser.webRequest.onBeforeRequest.addListener(
  async (details) => {
    if (!STREAM_RE.test(details.url)) return;
    const context = await bhoomContext(details.tabId);
    if (!context) return;

    const existing = await browser.storage.local.get({ pending: {} });
    const pending = existing.pending && typeof existing.pending === "object" ? existing.pending : {};
    pending[details.requestId] = {
      streamUrl: details.url,
      tabId: details.tabId,
      channelName: context.channelName,
      channelUrl: context.channelUrl,
      seenAt: new Date().toISOString()
    };
    await browser.storage.local.set({ pending });
  },
  { urls: ["<all_urls>"] }
);

browser.webRequest.onBeforeSendHeaders.addListener(
  async (details) => {
    if (!STREAM_RE.test(details.url)) return;

    const context = await bhoomContext(details.tabId);
    if (!context) return;

    const headers = details.requestHeaders || [];
    const getHeader = (name) => {
      const wanted = name.toLowerCase();
      const found = headers.find((h) => String(h.name || "").toLowerCase() === wanted);
      return found ? String(found.value || "") : "";
    };

    const item = {
      channelName: context.channelName,
      channelUrl: context.channelUrl,
      streamUrl: details.url,
      referer: getHeader("Referer") || context.channelUrl,
      userAgent: getHeader("User-Agent") || navigator.userAgent,
      capturedAt: new Date().toISOString(),
      requestId: details.requestId
    };

    await saveCapture(item);

    const existing = await browser.storage.local.get({ pending: {} });
    const pending = existing.pending && typeof existing.pending === "object" ? existing.pending : {};
    delete pending[details.requestId];
    await browser.storage.local.set({ pending });
  },
  { urls: ["<all_urls>"] },
  ["requestHeaders"]
);

browser.runtime.onMessage.addListener(async (message) => {
  if (!message || typeof message.type !== "string") return null;

  if (message.type === "getCaptures") {
    return { captures: await getCaptures() };
  }

  if (message.type === "clearCaptures") {
    await browser.storage.local.set({ captures: [] });
    return { ok: true };
  }

  if (message.type === "removeCapture") {
    const captures = await getCaptures();
    const index = Number(message.index);
    if (Number.isInteger(index) && index >= 0 && index < captures.length) {
      captures.splice(index, 1);
      await browser.storage.local.set({ captures });
    }
    return { ok: true };
  }

  return null;
});
