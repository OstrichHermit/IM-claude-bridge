@echo off
REM Discord Bridge System Restart Script
REM Close all related windows and restart services

echo ========================================
echo   Discord Bridge System Restart
echo ========================================
echo.

REM Close all related windows
echo [1/4] Closing all Discord Bridge windows...

taskkill /FI "WINDOWTITLE eq Discord Bot*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Claude Bridge*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Discord Bridge Startup*" /F >nul 2>&1

echo   - Service windows closed

REM Terminate all Python processes
echo [2/4] Terminating old Python processes...

for /f "tokens=2" %%a in ('tasklist ^| findstr /i "python.exe"') do (
    taskkill /F /PID %%a >nul 2>&1
    if errorlevel 1 (
        echo   - Process %%a not running or already closed
    ) else (
        echo   - Terminated process %%a
    )
)

echo.
echo [3/4] Waiting for processes to exit...
timeout /t 2 /nobreak >nul

echo.
echo [4/4] Starting Discord Bridge services...

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
