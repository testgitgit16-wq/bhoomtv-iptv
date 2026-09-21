# BhoomTV IPTV Auto Playlist

This project runs a GitHub Actions job that crawls the BhoomTV Tamil and Tamil Local TV category pages, discovers publicly accessible HLS/DASH stream references exposed by normal page loading, validates captured streams, and publishes `bhoomtv_playlist.m3u`.

## Files

- `multi_channel_scraper.py` — category pagination, browser capture, stream validation, logging, and M3U generation.
- `requirements.txt` — Python dependencies.
- `.github/workflows/update_playlist.yml` — automatic 6-hour schedule and manual workflow.
- `bhoomtv_playlist.m3u` — generated IPTV playlist.
- `bhoomtv_report.json` — captured/usable/failure report from the latest run.

## Important behavior

The scraper does not attempt to bypass Cloudflare or other access controls. Those conditions are reported in the Actions log and report.

If a run discovers zero usable streams, an existing playlist is preserved instead of being replaced with an empty playlist.

## Playlist URL

After the first successful run:

`https://raw.githubusercontent.com/testgitgit16-wq/bhoomtv-iptv/main/bhoomtv_playlist.m3u`

The workflow runs every 6 hours in UTC and can also be started manually from the Actions tab.
