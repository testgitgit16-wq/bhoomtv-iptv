@echo off
setlocal
cd /d "%~dp0"

echo Installing/updating dependencies...
py -m pip install -r requirements.txt

echo Installing Playwright Chromium...
py -m playwright install chromium

echo.
echo Starting BhoomTV automated Playwright scanner...
echo You do NOT need to click Play manually.
echo Keep the scanner browser window open while it works.
echo.

py bhoomtv_playwright_auto_v4.py

echo.
pause
