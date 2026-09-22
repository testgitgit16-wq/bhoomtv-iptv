# BhoomTV IPTV Auto Playlist

This project runs GitHub Actions to build a Tamil-focused IPTV playlist **from BhoomTV only**.

## Collection flow

1. Crawl the BhoomTV Tamil TV and Tamil Local TV directories for channel inventory and pagination.
2. Detect and report Cloudflare/access challenges without attempting to bypass them.
3. Open each discovered BhoomTV channel page normally.
4. Capture HLS/DASH stream references exposed during normal page loading.
5. Validate captured streams and write only usable BhoomTV streams to bhoomtv_playlist.m3u.
6. Preserve the previous playlist when a run produces zero usable streams.

## Real-time Actions logging

The workflow prints:

- INVENTORY — BhoomTV channels discovered
- CAPTURE — stream URLs captured from BhoomTV pages
- VALIDATE — direct stream validation
- RUNNING TOTAL — scanned/captured/usable counts
- FINAL RESULT — final playlist statistics

## Files

- multi_channel_scraper.py — BhoomTV inventory, pagination, browser capture, validation, logging, and M3U generation.
- requirements.txt — Python dependencies.
- .github/workflows/update_playlist.yml — automatic 6-hour schedule and manual workflow.
- bhoomtv_playlist.m3u — generated IPTV playlist.
- bhoomtv_report.json — latest run report.

## Important behavior

Only **https://bhoomtv.org/** is used as the channel/stream source.

The project does not import IPTV-org, other M3U playlists, or third-party channel catalogs.

The project does not attempt to bypass Cloudflare or other access controls. When BhoomTV blocks the GitHub runner, the block is recorded in the Actions log/report.

If a run discovers zero usable streams, the previous playlist is preserved.

## Playlist URL

https://raw.githubusercontent.com/testgitgit16-wq/bhoomtv-iptv/main/bhoomtv_playlist.m3u

The scheduled workflow runs every 6 hours in UTC and can also be started manually from the Actions tab.
