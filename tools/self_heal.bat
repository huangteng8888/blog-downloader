@echo off
rem ============================================================
rem  wu2198 backfill - self-heal patrol launcher (called by Windows Task Scheduler)
rem  Interpreter priority:
rem    1. fxz runtime python  (same env as the currently verified crawl)
rem    2. system Python311
rem    3. "python" from PATH
rem  NOTE: keep this file ASCII-only. Chinese comments get mangled under the
rem        OEM codepage (GBK) and cmd tries to execute them as commands.
rem  All output goes to output/1216826604_wayback/heal.log ; console is silent.
rem ============================================================
setlocal
set "PYTHONIOENCODING=utf-8"
set "PY="

if exist "C:\Program Files\fxz-desktop\resources\runtimes\python\python.exe" set "PY=C:\Program Files\fxz-desktop\resources\runtimes\python\python.exe"
if not defined PY if exist "C:\Users\ht\AppData\Local\Programs\Python\Python311\python.exe" set "PY=C:\Users\ht\AppData\Local\Programs\Python\Python311\python.exe"
if not defined PY set "PY=python"

"%PY%" "%~dp0self_heal.py" %* >nul 2>&1
exit /b %ERRORLEVEL%