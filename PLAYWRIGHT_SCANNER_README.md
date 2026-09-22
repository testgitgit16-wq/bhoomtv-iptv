# BhoomTV Playwright Auto Scanner v4

This scanner uses the Playwright method that successfully captured a BhoomTV stream.

It:
- discovers Tamil and Tamil Local TV pages with pagination
- opens every BhoomTV /live/ channel
- automatically activates video players and common play controls
- inspects nested iframe/player frames
- captures HLS/DASH network requests
- captures Referer and User-Agent
- prefers canonical manifests such as stream.m3u8?id=...
- validates captured manifests
- creates bhoomtv_playlist.m3u

No manual Play click or F12 is required.

Run:
1. Extract the folder.
2. Close normal Chrome/Edge/Firefox windows if they interfere.
3. Double-click run.bat.

Outputs:
- bhoomtv_playlist.m3u
- bhoomtv_captures.json
- bhoomtv_report.json

Note: the browser runs on your Windows machine because that is the environment that can currently access the BhoomTV player normally.
