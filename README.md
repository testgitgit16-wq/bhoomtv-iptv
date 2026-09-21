# BhoomTV IPTV Auto Playlist

This project runs GitHub Actions to build a Tamil-focused IPTV playlist.

## Collection flow

1. Crawl the BhoomTV Tamil TV and Tamil Local TV directories for channel inventory and pagination.
2. Detect and report Cloudflare/access challenges without attempting to bypass them.
3. Import openly published direct HLS/DASH stream entries from the public Tamil Nadu IPTV catalog at https://iptv-org.github.io/iptv/subdivisions/in-tn.m3u
4. Validate HLS/DASH playlists and (for HLS) a media playlist/segment request.
5. Write only usable streams to bhoomtv_playlist.m3u.
6. Preserve the previous playlist when a run produces zero usable streams.

## Real-time Actions logging

The workflow prints:

- INVENTORY — channel pages discovered
- DIRECT SOURCE — streams obtained from the public fallback catalog
- CAPTURE — stream URLs captured from normally accessible BhoomTV pages
- VALIDATE — direct stream validation
- RUNNING TOTAL — scanned/captured/usable counts
- FINAL RESULT — final playlist statistics

## Files

- multi_channel_scraper.py — inventory, pagination, public catalog fallback, stream validation, logging, and M3U generation.
- requirements.txt — Python dependencies.
- .github/workflows/update_playlist.yml — automatic 6-hour schedule and manual workflow.
- bhoomtv_playlist.m3u — generated IPTV playlist.
- bhoomtv_report.json — latest run report.

## Important behavior

The project does not attempt to bypass Cloudflare or other access controls.

The fallback catalog is a separate public source of direct HLS/DASH URLs. Those URLs are validated before being placed in the generated playlist.

## Playlist URL

https://raw.githubusercontent.com/testgitgit16-wq/bhoomtv-iptv/main/bhoomtv_playlist.m3u

The scheduled workflow runs every 6 hours in UTC and can also be started manually from the Actions tab.
