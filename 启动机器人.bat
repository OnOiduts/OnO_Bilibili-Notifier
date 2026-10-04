@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
set "PYCMD="
where python >nul 2>nul
if not errorlevel 1 set "PYCMD=python"
if not defined PYCMD (
  where py >nul 2>nul
  if not errorlevel 1 set "PYCMD=py -3"
)
if not defined PYCMD (
  where python3 >nul 2>nul
  if not errorlevel 1 set "PYCMD=python3"
)
if not defined PYCMD (
  echo.
  echo   [X] Python not found.
  echo.
  echo       Please install Python 3.8+
  echo       https://www.python.org/downloads/
  echo.
  pause
  exit /b 1
)

echo.
echo   [*] Checking for leftover processes...
%PYCMD% "%~dp0src\cleanup.py" --stop
echo.

%PYCMD% "%~dp0src\start.py"
set "RC=%ERRORLEVEL%"

rem RC=2 credentials incomplete, RC=3 login failed.
rem In both cases the user must fix settings in the web panel,
rem so we MUST NOT kill the panel here. (Old versions always ran
rem cleanup, which killed webui.py and left the browser with a
rem stale page and connection-refused on every API call.)
rem Any non-zero exit means the user may need the panel to fix things.
rem Only a clean exit (0, e.g. Ctrl+C) should tear the panel down.
if "%RC%"=="0" goto :DO_CLEANUP
goto :KEEP_PANEL

:DO_CLEANUP

echo.
echo   [*] Cleaning up...
%PYCMD% "%~dp0src\cleanup.py" --quiet
echo.
pause
exit /b 0

:KEEP_PANEL
echo.
echo   [!] Robot did not start (exit code %RC%). Panel stays up.
echo.
echo      Panel: http://127.0.0.1:8088
echo      Fix AppID / AppSecret there, save, then rerun this script.
echo      (Closing this window will not kill the panel right away,
echo       so you can keep editing. Next run cleans up stale procs.)
echo.
pause
exit /b %RC%
