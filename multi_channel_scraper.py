from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

BASE_URL = "https://bhoomtv.org"
CATEGORIES = [
    {"url": f"{BASE_URL}/channel/tamil/", "group": "Tamil TV"},
    {"url": f"{BASE_URL}/channel/tamil-local-tv/", "group": "Tamil Local TV"},
]

OUTPUT_M3U = Path("bhoomtv_playlist.m3u")
OUTPUT_REPORT = Path("bhoomtv_report.json")

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
)

PAGE_TIMEOUT = 30
CAPTURE_WAIT_SECONDS = 8
REQUEST_TIMEOUT = 20
MAX_CANDIDATES_PER_CHANNEL = 5
MAX_PAGES_WITHOUT_NEW_CHANNELS = 2
CATEGORY_RETRY_DELAYS = (5, 10, 20)
PAGE_GAP_SECONDS = 3

# Public stream catalog used only as a fallback when BhoomTV pages are unavailable.
# This does not bypass BhoomTV/Cloudflare; it imports openly published stream entries
# and validates them directly.
EXTERNAL_M3U_SOURCES = [
    {
        "url": "https://iptv-org.github.io/iptv/languages/tam.m3u",
        "group": "Tamil TV",
        "source": "iptv-org-tamil-language",
    },
    {
        "url": "https://iptv-org.github.io/iptv/subdivisions/in-tn.m3u",
        "group": "Tamil Local TV",
        "source": "iptv-org-tamil-nadu",
    },
]


CF_MARKERS = (
    "just a moment",
    "cf-chl-",
    "challenge-platform",
    "attention required",
    "cloudflare ray id",
    "verify you are human",
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def log(message: str) -> None:
    print(message, flush=True)


def canonical(url: str) -> str:
    return urljoin(BASE_URL + "/", url).split("#", 1)[0]


def channel_slug(url: str) -> str:
    path = urlparse(url).path.strip("/")
    return path.split("/")[-1] if path else "channel"


def redacted(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}{p.path}" + ("?<query>" if p.query else "")


def is_cloudflare(status: int, headers: dict[str, str], body: str) -> bool:
    low = body[:50000].lower()
    server = headers.get("server", "").lower()
    return any(marker in low for marker in CF_MARKERS) or (
        status in (403, 429, 503) and "cloudflare" in server
    )


def clean_name(value: str | None, fallback: str) -> str:
    text = " ".join((value or "").split()).strip()
    return text or fallback


def extract_channel_links(html: str, group: str, category_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    seen = set()

    for link in soup.find_all("a", href=True):
        href = canonical(link["href"])
        path = urlparse(href).path
        if "/live/" not in path or path.rstrip("/").endswith("/live"):
            continue
        if href in seen:
            continue

        image = link.find("img")
        title = clean_name(link.get_text(" ", strip=True), "")
        if not title and image:
            title = clean_name(image.get("alt"), "")
        if not title:
            title = channel_slug(href).replace("-", " ").title()

        logo = canonical(image.get("src")) if image and image.get("src") else ""

        rows.append({
            "title": title,
            "url": href,
            "logo": logo,
            "group": group,
            "source_category": category_url,
        })
        seen.add(href)

    return rows



def parse_m3u_entries(text: str, source: dict) -> list[dict]:
    """Parse a public M3U catalog into channel records with direct stream candidates."""
    entries = []
    pending = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        if line.startswith("#EXTINF:"):
            # Keep this deliberately tolerant: different public catalogs use
            # different attribute combinations.
            display = line.rsplit(",", 1)[-1].strip() if "," in line else "Unknown Channel"
            tvg_name = re.search(r'tvg-name="([^"]+)"', line, flags=re.I)
            tvg_logo = re.search(r'tvg-logo="([^"]+)"', line, flags=re.I)
            pending = {
                "title": clean_name(
                    tvg_name.group(1) if tvg_name else display,
                    display or "Unknown Channel",
                ),
                "display": display or "Unknown Channel",
                "logo": tvg_logo.group(1) if tvg_logo else "",
            }
            continue

        if pending and line.startswith(("http://", "https://")):
            rows = {
                "title": pending["title"],
                "url": f"{source['url']}#source-{len(entries)}",
                "logo": pending["logo"],
                "group": source["group"],
                "source_category": source["url"],
                "direct_candidates": [{
                    "url": line,
                    "kind": "HLS" if ".m3u8" in line.lower() else (
                        "DASH" if ".mpd" in line.lower() else "UNKNOWN"
                    ),
                    "referer": "",
                }],
                "external_source": source["source"],
            }
            if rows["direct_candidates"][0]["kind"] != "UNKNOWN":
                entries.append(rows)
            pending = None

    return entries

def find_next_page(html: str, current_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    current = canonical(current_url)
    current_path = urlparse(current).path

    for link in soup.find_all("a", href=True):
        href = canonical(link["href"])
        text = " ".join(link.get_text(" ", strip=True).lower().split())
        rel = {str(x).lower() for x in (link.get("rel") or [])}
        classes = " ".join(link.get("class") or []).lower()
        if (
            "next" in rel
            or "next" in classes
            or text in {"next", "next page", "›", "»", "older posts"}
        ) and href != current:
            if "/channel/" in urlparse(href).path:
                return href

    match = re.search(r"/page/(\d+)/?$", current_path)
    current_page = int(match.group(1)) if match else 1
    candidates = []

    for link in soup.find_all("a", href=True):
        href = canonical(link["href"])
        m = re.search(r"/page/(\d+)/?$", urlparse(href).path)
        if m and int(m.group(1)) > current_page:
            candidates.append((int(m.group(1)), href))

    if candidates:
        candidates.sort()
        return candidates[0][1]

    base = current.rstrip("/")
    if re.search(r"/page/\d+/?$", base):
        return canonical(re.sub(
            r"/page/\d+/?$",
            f"/page/{current_page + 1}/",
            base,
        ))
    return canonical(f"{base}/page/2/")


async def crawl_category(page, category: dict) -> tuple[list[dict], str | None]:
    current_url = category["url"]
    visited = set()
    channels = {}
    no_new_pages = 0
    blocked_reason = None

    log(f"\n=== CATEGORY: {category['group']} ===")

    while current_url and current_url not in visited:
        visited.add(current_url)
        page_no = len(visited)
        log(f"[CATEGORY] page={page_no} url={current_url}")

        challenged = False
        response = None
        html = ""
        title = ""
        status = 0

        for retry_no, delay in enumerate((0, *CATEGORY_RETRY_DELAYS), start=0):
            if retry_no:
                log(f"  [CATEGORY RETRY] attempt={retry_no + 1} after {delay}s")
                await asyncio.sleep(delay)
            try:
                response = await page.goto(
                    current_url,
                    wait_until="domcontentloaded",
                    timeout=PAGE_TIMEOUT * 1000,
                )
                await page.wait_for_timeout(1200)
                status = response.status if response else 0
                html = await page.content()
                title = (await page.title()).lower()
            except PlaywrightTimeoutError:
                log("  [CATEGORY ERROR] PAGE_TIMEOUT")
                if retry_no < len(CATEGORY_RETRY_DELAYS):
                    continue
                break
            except Exception as exc:
                log(f"  [CATEGORY ERROR] {exc}")
                if retry_no < len(CATEGORY_RETRY_DELAYS):
                    continue
                break

            challenged = is_cloudflare(status, {}, html) or any(
                marker in title for marker in CF_MARKERS
            )
            if not challenged:
                break
            log("  [CATEGORY CHALLENGE] access challenge detected; retrying normally")

        if challenged:
            blocked_reason = "CLOUDFLARE_CHALLENGE"
            log("  [CATEGORY BLOCKED] challenge persisted after normal retries")
            break

        if not html:
            break

        if status >= 400:
            log(f"  [CATEGORY HTTP] {status}; stopping")
            break

        rows = extract_channel_links(
            html,
            category["group"],
            category["url"],
        )
        before = len(channels)
        for row in rows:
            channels.setdefault(row["url"], row)
        added = len(channels) - before

        log(
            f"  [CATEGORY RESULT] found={len(rows)} "
            f"new={added} total={len(channels)}"
        )

        no_new_pages = no_new_pages + 1 if added == 0 else 0
        if no_new_pages >= MAX_PAGES_WITHOUT_NEW_CHANNELS:
            log("  [CATEGORY STOP] no new channels on consecutive pages")
            break

        next_url = find_next_page(html, current_url)
        if not next_url or next_url in visited:
            break
        await asyncio.sleep(PAGE_GAP_SECONDS)
        current_url = next_url

    log(
        f"[CATEGORY COMPLETE] pages={len(visited)} "
        f"channels={len(channels)} blocked={blocked_reason or 'NONE'}"
    )
    return list(channels.values()), blocked_reason


async def capture_streams(context, channel: dict) -> tuple[list[dict], str | None]:
    page = await context.new_page()
    candidates = []
    seen = set()
    challenge = False

    def record(
        url: str,
        headers: dict[str, str] | None = None,
        status: int | None = None,
        content_type: str = "",
    ) -> None:
        nonlocal challenge

        low = url.lower()
        if status in (403, 429, 503):
            challenge = True
        if "challenge" in low or "captcha" in low:
            challenge = True

        kind = None
        ctype = content_type.lower()
        if ".m3u8" in low or "mpegurl" in ctype:
            kind = "HLS"
        elif ".mpd" in low or "dash+xml" in ctype:
            kind = "DASH"

        if (
            kind is None
            or url in seen
            or len(candidates) >= MAX_CANDIDATES_PER_CHANNEL
        ):
            return

        seen.add(url)
        request_headers = headers or {}
        referer = request_headers.get("referer") or channel["url"]

        candidates.append({
            "url": url,
            "kind": kind,
            "referer": referer,
        })
        log(
            f"    [CAPTURE] {channel['title']} -> "
            f"{kind} {redacted(url)}"
        )

    def on_response(response) -> None:
        try:
            record(
                response.url,
                response.request.headers,
                response.status,
                response.headers.get("content-type", ""),
            )
        except Exception:
            pass

    page.on("response", on_response)

    try:
        response = await page.goto(
            channel["url"],
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT * 1000,
        )
        status = response.status if response else 0
        html = await page.content()
        title = (await page.title()).lower()

        if is_cloudflare(status, {}, html) or any(m in title for m in CF_MARKERS):
            log(f"    [BLOCKED] {channel['title']} -> access challenge")
            return [], "CLOUDFLARE_CHALLENGE"

        try:
            await page.evaluate(
                """() => {
                    for (const v of document.querySelectorAll('video')) {
                        try {
                            v.muted = true;
                            v.play().catch(() => {});
                        } catch (_) {}
                    }
                }"""
            )
        except Exception:
            pass

        await page.wait_for_timeout(CAPTURE_WAIT_SECONDS * 1000)

        html = await page.content()
        for url in re.findall(
            r'https?://[^\s\'"<>]+(?:\.m3u8|\.mpd)(?:\?[^\s\'"<>]*)?',
            html,
            flags=re.I,
        ):
            record(
                url.replace("&amp;", "&"),
                {"referer": channel["url"]},
                200,
            )

        if not candidates:
            return [], "CLOUDFLARE_OR_ACCESS_BLOCK" if challenge else "NO_STREAM_FOUND"

        return candidates, None

    except PlaywrightTimeoutError:
        return candidates, "PAGE_TIMEOUT"
    except Exception as exc:
        return candidates, f"PLAYWRIGHT_ERROR:{exc}"
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def validate_hls(client: httpx.AsyncClient, item: dict) -> tuple[bool, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": item["referer"],
    }

    try:
        response = await client.get(
            item["url"],
            headers=headers,
            follow_redirects=True,
        )
    except Exception as exc:
        return False, f"REQUEST_ERROR:{exc}"

    if is_cloudflare(response.status_code, dict(response.headers), response.text):
        return False, "CLOUDFLARE_OR_ACCESS_BLOCK"

    if response.status_code != 200 or not response.text.lstrip().startswith("#EXTM3U"):
        return False, f"INVALID_HLS_HTTP_{response.status_code}"

    media_url = item["url"]
    lines = [x.strip() for x in response.text.splitlines() if x.strip()]
    for i, line in enumerate(lines[:-1]):
        if line.startswith("#EXT-X-STREAM-INF:") and not lines[i + 1].startswith("#"):
            media_url = urljoin(item["url"], lines[i + 1])
            break

    try:
        media = await client.get(
            media_url,
            headers=headers,
            follow_redirects=True,
        )
    except Exception as exc:
        return False, f"MEDIA_REQUEST_ERROR:{exc}"

    if media.status_code != 200 or not media.text.lstrip().startswith("#EXTM3U"):
        return False, f"INVALID_MEDIA_PLAYLIST_{media.status_code}"

    segment = None
    for raw in media.text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            segment = line
            break

    if not segment:
        match = re.search(r'URI="([^"]+)"', media.text)
        segment = match.group(1) if match else None

    if segment:
        try:
            seg = await client.get(
                urljoin(media_url, segment),
                headers={**headers, "Range": "bytes=0-1023"},
                follow_redirects=True,
            )
            if seg.status_code not in (200, 206) or not seg.content:
                return False, f"SEGMENT_HTTP_{seg.status_code}"
        except Exception as exc:
            return False, f"SEGMENT_ERROR:{exc}"

    return True, "VALID_HLS"


async def validate_dash(client: httpx.AsyncClient, item: dict) -> tuple[bool, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": item["referer"],
    }

    try:
        response = await client.get(
            item["url"],
            headers=headers,
            follow_redirects=True,
        )
    except Exception as exc:
        return False, f"REQUEST_ERROR:{exc}"

    if is_cloudflare(response.status_code, dict(response.headers), response.text):
        return False, "CLOUDFLARE_OR_ACCESS_BLOCK"

    if response.status_code != 200 or "<MPD" not in response.text:
        return False, f"INVALID_MPD_HTTP_{response.status_code}"

    return True, "VALID_MPD"


def write_m3u(channels: list[dict]) -> int:
    lines = ["#EXTM3U"]
    count = 0

    for channel in sorted(channels, key=lambda x: x["title"].lower()):
        safe_title = channel["title"].replace('"', "&quot;")
        safe_group = channel["group"].replace('"', "&quot;")

        for stream in channel.get("usable_streams", []):
            attrs = (
                f'tvg-name="{safe_title}" '
                f'group-title="{safe_group}"'
            )
            if channel.get("logo"):
                attrs += f' tvg-logo="{channel["logo"].replace(chr(34), "&quot;")}"'

            lines.append(f"#EXTINF:-1 {attrs},{channel['title']}")

            referer = stream.get("referer") or channel["url"]
            lines.append(f"#EXTVLCOPT:http-referrer={referer}")
            lines.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
            lines.append(stream["url"])
            lines.append("")
            count += 1

    content = "\n".join(lines).rstrip() + "\n"

    # Never wipe an existing playlist during a temporary zero-stream run.
    if count > 0 or not OUTPUT_M3U.exists():
        OUTPUT_M3U.write_text(content, encoding="utf-8")
    else:
        log("[PROTECT] 0 usable streams; existing playlist preserved")

    return count


async def main() -> None:
    started = now()
    log("=== BHOOMTV IPTV AUTO SCRAPER ===")
    log(f"Started: {started}")
    log("Access challenges are detected and reported; they are not bypassed.")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(REQUEST_TIMEOUT),
        headers={"User-Agent": USER_AGENT},
    ) as client:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox"],
            )
            context = await browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1440, "height": 900},
                locale="en-US",
            )
            category_page = await context.new_page()

            inventory = {}
            blocked_categories = {}
            external_sources = []

            for category in CATEGORIES:
                rows, blocked = await crawl_category(category_page, category)
                for row in rows:
                    inventory[row["url"]] = row
                if blocked:
                    blocked_categories[category["group"]] = blocked
                log(f"[INVENTORY] unique_channels={len(inventory)}")

            # Add openly published direct-stream catalog entries as a fallback.
            for source in EXTERNAL_M3U_SOURCES:
                log(f"\n=== EXTERNAL SOURCE: {source['source']} ===")
                try:
                    response = await client.get(source["url"], follow_redirects=True)
                    if response.status_code != 200:
                        log(f"[EXTERNAL ERROR] HTTP {response.status_code} -> {source['url']}")
                        continue
                    rows = parse_m3u_entries(response.text, source)
                    added = 0
                    existing_stream_urls = {
                        item.get("direct_candidates", [{}])[0].get("url", "")
                        for item in inventory.values()
                        if item.get("direct_candidates")
                    }
                    for row in rows:
                        stream_url = row["direct_candidates"][0]["url"]
                        if stream_url in existing_stream_urls:
                            continue
                        inventory[row["url"]] = row
                        existing_stream_urls.add(stream_url)
                        added += 1
                    external_sources.append({
                        "source": source["source"],
                        "url": source["url"],
                        "entries_found": len(rows),
                        "entries_added": added,
                    })
                    log(f"[EXTERNAL RESULT] found={len(rows)} added={added} inventory={len(inventory)}")
                except Exception as exc:
                    log(f"[EXTERNAL ERROR] {source['source']} -> {exc}")

            await category_page.close()

            total = len(inventory)
            captured_total = 0
            usable_total = 0
            output_channels = []
            failures = []

            log(f"\n=== STREAM SCAN: {total} CHANNELS ===")

            for index, channel in enumerate(inventory.values(), start=1):
                log(
                    f"\n[{index}/{total}] {channel['title']} | "
                    f"{channel['url']}"
                )

                if channel.get("direct_candidates"):
                    candidates = channel["direct_candidates"]
                    capture_reason = None
                    log(f"  [DIRECT SOURCE] {channel.get('external_source', 'public catalog')} -> {len(candidates)} candidate(s)")
                else:
                    candidates, capture_reason = await capture_streams(
                        context,
                        channel,
                    )
                captured_total += len(candidates)
                log(f"  [CAPTURED] {len(candidates)} candidate stream(s)")

                usable_streams = []
                validations = []

                for stream_index, candidate in enumerate(candidates, start=1):
                    log(
                        f"  [VALIDATE {stream_index}/{len(candidates)}] "
                        f"{candidate['kind']} {redacted(candidate['url'])}"
                    )

                    if candidate["kind"] == "HLS":
                        usable, reason = await validate_hls(client, candidate)
                    else:
                        usable, reason = await validate_dash(client, candidate)

                    validations.append({
                        "url": candidate["url"],
                        "kind": candidate["kind"],
                        "usable": usable,
                        "reason": reason,
                    })

                    log(f"    [RESULT] USABLE={usable} REASON={reason}")

                    if usable:
                        usable_streams.append(candidate)

                usable_total += len(usable_streams)

                if usable_streams:
                    item = dict(channel)
                    item["usable_streams"] = usable_streams
                    output_channels.append(item)
                    log(
                        f"  [CHANNEL RESULT] CAPTURED={len(candidates)} "
                        f"USABLE={len(usable_streams)}"
                    )
                else:
                    reason = capture_reason or (
                        validations[0]["reason"]
                        if validations
                        else "NO_STREAM_FOUND"
                    )
                    failures.append({
                        "title": channel["title"],
                        "url": channel["url"],
                        "group": channel["group"],
                        "captured": len(candidates),
                        "usable": 0,
                        "reason": reason,
                        "validations": validations,
                    })
                    log(
                        f"  [CHANNEL RESULT] CAPTURED={len(candidates)} "
                        f"USABLE=0 REASON={reason}"
                    )

                log(
                    f"  [RUNNING TOTAL] scanned={index}/{total} "
                    f"captured={captured_total} usable={usable_total} "
                    f"channels_with_streams={len(output_channels)}"
                )

            written = write_m3u(output_channels)

            report = {
                "generated_at": now(),
                "source": BASE_URL,
                "categories": CATEGORIES,
                "external_sources": external_sources,
                "stats": {
                    "inventory_channels": total,
                    "scanned_channels": total,
                    "captured_stream_candidates": captured_total,
                    "usable_streams": usable_total,
                    "channels_with_usable_streams": len(output_channels),
                    "m3u_entries_written": written,
                    "failed_channels": len(failures),
                },
                "blocked_categories": blocked_categories,
                "failures": failures,
            }

            OUTPUT_REPORT.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            await context.close()
            await browser.close()

    log("\n=== FINAL RESULT ===")
    log(f"Inventory channels : {total}")
    log(f"Scanned channels   : {total}")
    log(f"Captured streams   : {captured_total}")
    log(f"Usable streams     : {usable_total}")
    log(f"Channels with M3U  : {len(output_channels)}")
    log(f"M3U entries written: {written}")
    log(f"Failed channels    : {len(failures)}")
    log("====================")


if __name__ == "__main__":
    asyncio.run(main())
