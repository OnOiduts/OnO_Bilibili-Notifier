@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
call python src\cleanup.py >nul 2>&1
cd /d "%~dp0"

echo.
echo   ==========================================================
echo    Bili Notify Bot - Install Dependencies
echo   ==========================================================
echo.

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
  echo   [X] Python not found.
  echo.
  echo       Please install Python 3.8 or newer:
  echo       https://www.python.org/downloads/
  echo.
  pause
  exit /b 1
)

rem ---------- [1/3] core deps (required) ----------
echo   [1/3] Core dependencies (required) ...
echo.
%PYCMD% -m pip install -r src\requirements-core.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
  echo   ... mirror failed, retrying from pypi.org
  %PYCMD% -m pip install -r src\requirements-core.txt
)
if errorlevel 1 (
  echo   ... one more try, installing for current user only
  %PYCMD% -m pip install --user -r src\requirements-core.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
)
if errorlevel 1 (
  echo.
  echo   [X] Core dependencies failed. Bot and panel cannot start.
  echo       Please copy the red errors above and report them.
  echo.
  pause
  exit /b 1
)

rem ---------- [2/3] optional deps (one by one) ----------
echo.
echo   [2/3] Optional dependencies (failure does not block) ...
echo.

%PYCMD% -m pip install qrcode -i https://pypi.tuna.tsinghua.edu.cn/simple >nul 2>nul
if errorlevel 1 (echo     [ ] qrcode       terminal QR login - NOT installed) else (echo     [x] qrcode       terminal QR login)

%PYCMD% -m pip install Pillow -i https://pypi.tuna.tsinghua.edu.cn/simple >nul 2>nul
if errorlevel 1 (echo     [ ] Pillow       faster QR image - NOT installed, built-in fallback used) else (echo     [x] Pillow       faster QR image)

%PYCMD% -m pip install playwright -i https://pypi.tuna.tsinghua.edu.cn/simple >nul 2>nul
if errorlevel 1 (
  echo     [ ] playwright   browser login - NOT installed
  set "HAS_PW="
) else (
  echo     [x] playwright   browser login
  set "HAS_PW=1"
)

rem ---------- [3/3] browser engine ----------
echo.
echo   [3/3] Browser engine ...
echo.
if not defined HAS_PW goto :SKIP_ENGINE

set "LOCAL_BROWSER="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "LOCAL_BROWSER=1"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "LOCAL_BROWSER=1"
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "LOCAL_BROWSER=1"
if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" set "LOCAL_BROWSER=1"
if defined LOCAL_BROWSER goto :HAVE_ENGINE

echo     No Chrome / Edge detected on this machine.
echo     Browser login needs an engine: ~150MB download, a few minutes.
echo     Without it only browser login is unavailable.
echo.
set "ANSWER="
set /p "ANSWER=    Download chromium engine now? [y/N] "
if /i "%ANSWER%"=="y" goto :DL_ENGINE
echo     Skipped. Re-run this script and pick y if you need it later.
goto :VERIFY

:DL_ENGINE
echo     Downloading, please wait ...
%PYCMD% -m playwright install chromium
if errorlevel 1 (echo     [ ] engine download failed, browser login unavailable) else (echo     [x] chromium engine ready)
goto :VERIFY

:HAVE_ENGINE
echo     Chrome / Edge found - browser login works, no download needed.
goto :VERIFY

:SKIP_ENGINE
echo     playwright not installed, skipping engine check.
goto :VERIFY

rem ---------- verify ----------
:VERIFY
echo.
echo   ----------------------------------------------------------
echo    Verifying core dependencies
echo   ----------------------------------------------------------
%PYCMD% -c "import importlib.util as u,sys;ms=[('botpy','QQ bot SDK'),('aiohttp','HTTP client'),('yaml','config file'),('flask','web panel')];bad=[n for n,d in ms if u.find_spec(n) is None];print(chr(10).join(('  [ ] '+d+'  MISSING') if n in bad else ('  [x] '+d) for n,d in ms));sys.exit(1 if bad else 0)"
if errorlevel 1 (
  echo.
  echo   Some core dependencies are missing. Please re-run this script.
) else (
  echo.
  echo   Core dependencies OK. You can run launch bot now.
)
echo.
call python src\cleanup.py
pause
