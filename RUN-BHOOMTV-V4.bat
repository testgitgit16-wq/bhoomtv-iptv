@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo BhoomTV Playwright Auto Scanner v4
echo ============================================
echo.
echo No manual Play click is required.
echo The scanner will open each channel and try to
echo activate players inside the page and iframes.
echo.

py -m pip install -r playwright_requirements.txt
if errorlevel 1 goto :error

py -m playwright install chromium
if errorlevel 1 goto :error

py bhoomtv_playwright_auto_v4.py
goto :done

:error
echo.
echo Scanner setup failed.
pause
exit /b 1

:done
echo.
echo Scanner finished.
echo Check:
echo   bhoomtv_playlist.m3u
echo   bhoomtv_captures.json
echo   bhoomtv_report.json
pause
