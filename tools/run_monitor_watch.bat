@echo off
rem Lightweight dashboard refresher: regenerates dashboard.html every 5 minutes.
rem Read-only: parses local files only, makes zero network requests.
rem Output snapshot: output\1216826604_wayback\progress_snapshot.txt
cd /d D:\cursor_test\blog-downloader
python tools\progress_monitor.py --watch 300 >nul 2>&1