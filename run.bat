@echo off
chcp 65001 >nul
title jztz_v17
cd /d D:\UI\jztz_v17

:restart
echo [%date% %time%] Starting jztz_v17...
python web_app.py
echo [%date% %time%] Process exited, restarting in 10s...
timeout /t 10 /nobreak >nul
goto restart
