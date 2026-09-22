from __future__ import annotations

import json
import re
import time
import urllib.parse
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

BASE_URL = "https://bhoomtv.org"
CATEGORIES = [
    ("Tamil TV", f"{BASE_URL}/channel/tamil/"),
    ("Tamil Local TV", f"{BASE_URL}/channel/tamil-local-tv/"),
]
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
PAGE_TIMEOUT_MS = 30000
CHANNEL_WAIT_SECONDS = 12
REQUEST_TIMEOUT = 20
MAX_PAGES = 500
MAX_CAPTURE_CANDIDATES = 12

OUT_M3U = Path("bhoomtv_playlist.m3u")
OUT_JSON = Path("bhoomtv_captures.json")
OUT_REPORT = Path("bhoomtv_report.json")


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def canonical(value: str, base: str = BASE_URL) -> str:
    try:
        return urljoin(base + "/", value).split("#", 1)[0]
    except Exception:
        return ""


def is_bhoom_channel(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.hostname in {"bhoomtv.org", "www.bhoomtv.org"} and "/live/" in p.path
    except Exception:
        return False


def stream_type(url: str) -> str | None:
    low = url.lower()
    if ".m3u8" in low:
        return "HLS"
    if ".mpd" in low:
        return "DASH"
    return None


def stream_score(url: str) -> int:
    low = url.lower()
    score = 0
    if ".m3u8?id=" in low or ".mpd?id=" in low:
        score += 100
    if "stream.m3u8" in low:
        score += 20
    if "segment=" in low:
        score -= 100
    if "chunk" in low or "segment" in low:
        score -= 20
    return score


def discover_category(category_url: str, group: str) -> list[dict]:
    rows: dict[str, dict] = {}
    headers = {"User-Agent": USER_AGENT}
    next_url = category_url
    visited = set()

    print(f"\n=== CATEGORY: {group} ===", flush=True)

    with requests.Session() as session:
        while next_url and next_url not in visited and len(visited) < MAX_PAGES:
            visited.add(next_url)
            page_no = len(visited)
            print(f"[CATEGORY] page={page_no} url={next_url}", flush=True)

            try:
                response = session.get(next_url, headers=headers, timeout=REQUEST_TIMEOUT)
                if response.status_code != 200:
                    print(f"[CATEGORY] HTTP {response.status_code}", flush=True)
                    break

                soup = BeautifulSoup(response.text, "html.parser")
                found = 0
                for a in soup.select("a[href]"):
                    href = canonical(a.get("href", ""), next_url)
                    if not is_bhoom_channel(href) or href in rows:
                        continue

                    img = a.find("img")
                    title = clean_text(a.get_text(" ", strip=True))
                    if not title and img:
                        title = clean_text(img.get("alt", ""))
                    if not title:
                        title = urlparse(href).path.rstrip("/").split("/")[-1].replace("-", " ").title()

                    rows[href] = {
                        "title": title or "Tamil Channel",
                        "url": href,
                        "logo": canonical(img.get("src", ""), next_url) if img and img.get("src") else "",
                        "group": group,
                        "source_category": category_url,
                    }
                    found += 1

                print(f"[CATEGORY RESULT] found={found} total={len(rows)}", flush=True)

                next_link = None
                for a in soup.select("a[href]"):
                    label = clean_text(a.get_text(" ", strip=True)).lower()
                    rel = " ".join(a.get("rel") or []).lower()
                    classes = " ".join(a.get("class") or []).lower()
                    href = canonical(a.get("href", ""), next_url)
                    if "next" in rel or "next" in classes or label in {"next", "next page", "›", "»"}:
                        if href:
                            next_link = href
                            break

                if not next_link and found:
                    match = re.search(r"/page/(\d+)/?$", urlparse(next_url).path)
                    current = int(match.group(1)) if match else 1
                    base = re.sub(r"/page/\d+/?$", "", next_url.rstrip("/"))
                    next_link = f"{base}/page/{current + 1}/"

                if not found:
                    break
                next_url = next_link

            except Exception as exc:
                print(f"[CATEGORY ERROR] {exc}", flush=True)
                break

    return list(rows.values())


def safe_click(locator) -> bool:
    try:
        if locator.count() == 0:
            return False
        for i in range(min(locator.count(), 5)):
            item = locator.nth(i)
            if item.is_visible(timeout=800):
                item.click(timeout=2000, force=True)
                return True
    except Exception:
        return False
    return False


def activate_frame(frame) -> None:
    try:
        for selector in [
            "video",
            "button[aria-label*='play' i]",
            "[role='button'][aria-label*='play' i]",
            ".vjs-big-play-button",
            ".plyr__control--overlaid",
            ".jw-display-icon-container",
            ".jw-icon-playback",
            ".jw-icon-display",
            "[class*='play-button' i]",
            "[class*='play' i]"
        ]:
            locator = frame.locator(selector)
            safe_click(locator)

        frame.evaluate(
            """() => {
                for (const v of document.querySelectorAll('video')) {
                    try {
                        v.muted = true;
                        v.autoplay = true;
                        v.playsInline = true;
                        v.play().catch(() => {});
                    } catch (_) {}
                }
            }"""
        )
    except Exception:
        pass


def scan_channel(context, channel: dict) -> list[dict]:
    page = context.new_page()
    candidates: dict[str, dict] = {}

    def on_request(request) -> None:
        url = request.url
        kind = stream_type(url)
        if not kind or url in candidates:
            return
        if "googleads" in url.lower() or "doubleclick" in url.lower():
            return

        headers = request.all_headers()
        referer = headers.get("referer") or ""
        ua = headers.get("user-agent") or USER_AGENT
        candidates[url] = {
            "url": url,
            "type": kind,
            "referer": referer,
            "user_agent": ua,
        }
        print(
            f"    [CAPTURE] {channel['title']} -> {kind} {url}",
            flush=True,
        )

    page.on("request", on_request)

    try:
        print(f"  [OPEN] {channel['url']}", flush=True)
        page.goto(
            channel["url"],
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT_MS,
        )
    except PlaywrightTimeoutError:
        print("  [WARN] navigation timeout; continuing with loaded page", flush=True)
    except Exception as exc:
        print(f"  [WARN] navigation error: {exc}", flush=True)

    deadline = time.time() + CHANNEL_WAIT_SECONDS

    while time.time() < deadline and not candidates:
        try:
            frames = list(page.frames)
            print(f"    [FRAMES] {len(frames)}", flush=True)

            for frame in frames:
                frame_url = frame.url
                low = frame_url.lower()
                if not frame_url or low.startswith("about:") or "googleads" in low or "doubleclick" in low or "recaptcha" in low:
                    continue
                if frame is page.main_frame:
                    activate_frame(frame)
                else:
                    print(f"    [PLAYER FRAME] {frame_url}", flush=True)
                    activate_frame(frame)

            try:
                page.evaluate(
                    """() => {
                        document.querySelectorAll('iframe').forEach((el) => {
                            try { el.scrollIntoView({block:'center'}); } catch (_) {}
                        });
                        for (const v of document.querySelectorAll('video')) {
                            try { v.muted=true; v.play().catch(()=>{}); } catch (_) {}
                        }
                    }"""
                )
            except Exception:
                pass

            page.wait_for_timeout(1500)
        except Exception:
            time.sleep(0.5)

    # Keep the best few candidates.
    result = sorted(candidates.values(), key=lambda x: stream_score(x["url"]), reverse=True)
    result = result[:MAX_CAPTURE_CANDIDATES]

    # If request headers did not contain referer, use the actual player/channel URL.
    for item in result:
        if not item["referer"]:
            item["referer"] = channel["url"]
        if not item["user_agent"]:
            item["user_agent"] = USER_AGENT

    page.close()
    return result


def validate_stream(item: dict) -> tuple[bool, str]:
    headers = {
        "User-Agent": item["user_agent"],
        "Referer": item["referer"],
        "Accept": "application/vnd.apple.mpegurl,application/x-mpegURL,text/plain,*/*"
        if item["type"] == "HLS"
        else "application/dash+xml,application/xml,text/xml,*/*",
    }

    try:
        r = requests.get(
            item["url"],
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
    except Exception as exc:
        return False, f"REQUEST_ERROR:{exc}"

    if r.status_code != 200:
        return False, f"HTTP_{r.status_code}"

    body = r.text.lstrip()
    if item["type"] == "HLS" and not body.startswith("#EXTM3U"):
        return False, "INVALID_HLS"
    if item["type"] == "DASH" and "<MPD" not in body:
        return False, "INVALID_MPD"

    return True, "VALID_HLS" if item["type"] == "HLS" else "VALID_MPD"


def write_m3u(rows: list[dict]) -> int:
    lines = ["#EXTM3U"]
    for row in rows:
        title = str(row["title"]).replace('"', "&quot;")
        group = str(row["group"]).replace('"', "&quot;")
        url = row["stream"]["url"]
        referer = row["stream"]["referer"]
        ua = row["stream"]["user_agent"]

        lines.append(f'#EXTINF:-1 tvg-id="Tamil_{row["index"]}" group-title="{group}",{title}')
        lines.append(f"#EXTVLCOPT:http-referrer={referer}")
        lines.append(f"#EXTVLCOPT:http-user-agent={ua}")
        lines.append(f"{url}|Referer={urllib.parse.quote(referer, safe=':/?=&')}&User-Agent={urllib.parse.quote(ua, safe='')}")
        lines.append("")

    OUT_M3U.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return len(rows)


def main() -> None:
    inventory: list[dict] = []
    seen = set()

    for group, url in CATEGORIES:
        for row in discover_category(url, group):
            if row["url"] not in seen:
                seen.add(row["url"])
                inventory.append(row)

    print(f"\n[INVENTORY] {len(inventory)} channels", flush=True)

    captures = []
    usable_rows = []
    failures = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )

        for idx, channel in enumerate(inventory, 1):
            print(f"\n[{idx}/{len(inventory)}] {channel['title']} | {channel['url']}", flush=True)
            candidates = scan_channel(context, channel)

            if not candidates:
                print("  [CHANNEL RESULT] CAPTURED=0 USABLE=0", flush=True)
                failures.append({
                    "title": channel["title"],
                    "url": channel["url"],
                    "reason": "NO_STREAM_CAPTURED",
                })
                continue

            validations = []
            usable = []
            for n, item in enumerate(candidates, 1):
                ok, reason = validate_stream(item)
                validations.append({**item, "usable": ok, "reason": reason})
                print(
                    f"  [VALIDATE {n}/{len(candidates)}] {item['type']} "
                    f"USABLE={ok} REASON={reason}",
                    flush=True,
                )
                if ok:
                    usable.append(item)

            captures.extend(validations)

            if usable:
                best = sorted(usable, key=lambda x: stream_score(x["url"]), reverse=True)[0]
                usable_rows.append({
                    "index": len(usable_rows) + 1,
                    "title": channel["title"],
                    "group": channel["group"],
                    "channel_url": channel["url"],
                    "stream": best,
                })
                print(
                    f"  [CHANNEL RESULT] CAPTURED={len(candidates)} "
                    f"USABLE={len(usable)} KEEP={best['url']}",
                    flush=True,
                )
            else:
                failures.append({
                    "title": channel["title"],
                    "url": channel["url"],
                    "reason": "|".join(x["reason"] for x in validations),
                })
                print(
                    f"  [CHANNEL RESULT] CAPTURED={len(candidates)} USABLE=0",
                    flush=True,
                )

            print(
                f"  [RUNNING TOTAL] scanned={idx}/{len(inventory)} "
                f"captured={len(captures)} usable_channels={len(usable_rows)}",
                flush=True,
            )

        browser.close()

    written = write_m3u(usable_rows)
    OUT_JSON.write_text(json.dumps(captures, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_REPORT.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "source": BASE_URL,
                "stats": {
                    "inventory_channels": len(inventory),
                    "captured_stream_candidates": len(captures),
                    "channels_with_usable_streams": len(usable_rows),
                    "m3u_entries_written": written,
                    "failed_channels": len(failures),
                },
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n=== FINAL RESULT ===", flush=True)
    print(f"Inventory channels : {len(inventory)}", flush=True)
    print(f"Captured candidates: {len(captures)}", flush=True)
    print(f"Usable channels    : {len(usable_rows)}", flush=True)
    print(f"M3U entries        : {written}", flush=True)
    print(f"Failed channels    : {len(failures)}", flush=True)


if __name__ == "__main__":
    main()
