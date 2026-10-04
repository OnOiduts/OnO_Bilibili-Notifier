@echo off
chcp 65001 >nul
title Restore All - Bili Notify Bot
echo.
echo   ==========================================
echo     Restore ALL  (settings + creds + subs)
echo   ==========================================
echo.
python src\tools\restore_all.py
echo.
echo   Done. Press any key to close...
pause >nul
