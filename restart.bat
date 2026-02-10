@echo off
REM Discord Bridge System Restart Script
REM 独立重启脚本，不依赖 manager

echo ========================================
echo   Discord Bridge System Restart
echo ========================================
echo.

REM Terminate specific Python processes by command line
echo [1/3] Terminating Discord Bridge processes...

wmic process where "name='python.exe' and commandline like '%%discord_bot.py%%'" delete >nul 2>&1
wmic process where "name='python.exe' and commandline like '%%claude_bridge.py%%'" delete >nul 2>&1

echo   - Discord Bot and Claude Bridge processes terminated

echo.
echo [2/3] Waiting for processes to exit...
timeout /t 2 /nobreak >nul

echo.
echo [3/3] Starting Discord Bridge services...

REM Start Discord Bot
start "Discord Bot" cmd /k python bot\discord_bot.py

timeout /t 2 /nobreak >nul

REM Start Claude Bridge
start "Claude Bridge" cmd /k python bridge\claude_bridge.py

echo.
echo ========================================
echo   System Restart Complete
echo ========================================
echo.
echo Note: Service windows will keep running
echo.
pause
