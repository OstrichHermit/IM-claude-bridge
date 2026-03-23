@echo off
REM Discord Bridge System Restart Script
REM 独立重启脚本，不依赖 manager

echo ========================================
echo   Discord Bridge System Restart
echo ========================================
echo.

REM Terminate specific Python processes by command line
echo [1/4] Terminating Discord Bridge processes...

wmic process where "name='python.exe' and commandline like '%%discord_bot.py%%'" delete >nul 2>&1
wmic process where "name='python.exe' and commandline like '%%claude_bridge.py%%'" delete >nul 2>&1
wmic process where "name='python.exe' and commandline like '%%weixin_bot.py%%'" delete >nul 2>&1

echo   - Discord Bot process terminated
echo   - Claude Bridge process terminated
echo   - Weixin Bot process terminated

echo.
echo [2/4] Waiting for processes to exit...
timeout /t 2 /nobreak >nul

echo.
echo [3/4] Starting Discord Bridge services...

REM Start Discord Bot
start "Discord Bot" cmd /k python bot\discord_bot.py

timeout /t 2 /nobreak >nul

REM Start Claude Bridge
start "Claude Bridge" cmd /k python bridge\claude_bridge.py

echo.
echo [4/4] Checking Weixin Bot config...

REM Check if Weixin Bot is enabled and start it
for /f "delims=" %%i in ('python -c "import yaml; config = yaml.safe_load(open(r'config\config.yaml', encoding='utf-8')); result = config.get('weixin', {}).get('enabled', False); print('1' if result else '0')"') do set WEIXIN_RESULT=%%i

if "%WEIXIN_RESULT%"=="1" (
    echo [4/4] Starting Weixin Bot...
    start "Weixin Bot" cmd /k python bot\weixin_bot.py
    set WEIXIN_ENABLED=True
) else (
    echo [4/4] Weixin Bot disabled in config, skipping...
    set WEIXIN_ENABLED=False
)

echo.
echo ========================================
echo   System Restart Complete
echo ========================================
echo.
echo Running services:
echo   - Discord Bot
echo   - Claude Bridge
if "%WEIXIN_RESULT%"=="1" (
    echo   - Weixin Bot
)
echo.
echo Note: Service windows will keep running
echo.
pause
