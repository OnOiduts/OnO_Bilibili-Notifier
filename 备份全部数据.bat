@echo off
chcp 65001 >nul
title Backup All - Bili Notify Bot
echo.
echo   ==========================================
echo     Backup ALL  (settings + creds + subs)
echo   ==========================================
echo.
python src\tools\backup_all.py
echo.
echo   Done. Press any key to close...
pause >nul
