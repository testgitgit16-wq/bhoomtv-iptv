let captures = [];

const $ = (id) => document.getElementById(id);

function escapeAttr(value) {
  return String(value || "").replace(/"/g, "&quot;");
}

function toM3U(rows) {
  const lines = ["#EXTM3U"];

  for (const row of rows) {
    const title = row.channelName || "BhoomTV";
    const referer = row.referer || row.channelUrl || "";
    const ua = row.userAgent || navigator.userAgent;
    const url = row.streamUrl || "";

    lines.push(
      '#EXTINF:-1 group-title="Tamil", ' + title
    );
    lines.push("#EXTVLCOPT:http-referrer=" + referer);
    lines.push("#EXTVLCOPT:http-user-agent=" + ua);
    lines.push(url + "|Referer=" + referer + "&User-Agent=" + ua);
    lines.push("");
  }

  return lines.join("\n").trim() + "\n";
}

function render() {
  const box = $("items");
  box.innerHTML = "";
  $("status").textContent = captures.length + " capture(s)";

  captures.forEach((row, index) => {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML =
      "<b>" + escapeAttr(row.channelName) + "</b>" +
      "<div class='url'>" + escapeAttr(row.streamUrl) + "</div>" +
      "<div>Referer: " + escapeAttr(row.referer) + "</div>" +
      "<div>User-Agent: " + escapeAttr(row.userAgent) + "</div>" +
      "<button data-index='" + index + "'>Remove</button>";
    box.appendChild(card);
  });

  box.querySelectorAll("button[data-index]").forEach((button) => {
    button.addEventListener("click", async () => {
      await browser.runtime.sendMessage({
        type: "removeCapture",
        index: Number(button.dataset.index)
      });
      await refresh();
    });
  });
}

async function refresh() {
  const result = await browser.runtime.sendMessage({ type: "getCaptures" });
  captures = Array.isArray(result?.captures) ? result.captures : [];
  render();
}

async function copyM3U() {
  if (!captures.length) return;
  await navigator.clipboard.writeText(toM3U(captures));
  $("status").textContent = "M3U copied to clipboard";
}

async function downloadText(filename, content, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  await browser.downloads.download({
    url,
    filename,
    saveAs: true
  });
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

$("refresh").addEventListener("click", refresh);
$("copy").addEventListener("click", copyM3U);
$("download").addEventListener("click", async () => {
  if (captures.length) await downloadText("bhoomtv-captured.m3u", toM3U(captures), "audio/x-mpegurl");
});
$("json").addEventListener("click", async () => {
  if (captures.length) await downloadText("bhoomtv-captured.json", JSON.stringify(captures, null, 2), "application/json");
});
$("clear").addEventListener("click", async () => {
  await browser.runtime.sendMessage({ type: "clearCaptures" });
  await refresh();
});

refresh();
