@echo off
REM Discord Claude Bridge - Unified Startup Script (Windows)

echo ========================================
echo   Discord Claude Bridge Starting
echo ========================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found, please install Python 3.8+
    pause
    exit /b 1
)

REM Check config file
if not exist "config\config.yaml" (
    echo [ERROR] Config file not found!
    echo Please copy config\config.example.yaml to config\config.yaml
    echo and fill in your Discord Bot Token.
    pause
    exit /b 1
)

REM Check dependencies
echo [1/4] Checking dependencies...
python -c "import discord, yaml" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing dependencies...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies
        pause
        exit /b 1
    )
)

REM Create shared directory if not exists
if not exist "shared" mkdir shared

echo [2/5] Starting Discord Bot...
start "Discord Bot" python bot/discord_bot.py

echo [3/5] Starting Claude Bridge...
start "Claude Bridge" python bridge/claude_bridge.py

echo [4/5] Starting Manager (monitor)...
start "Manager" python manager.py start

REM Check if Weixin Bot is enabled and start it
echo [5/5] Checking Weixin Bot config...
for /f "delims=" %%i in ('python -c "import yaml; config = yaml.safe_load(open(r'config\config.yaml', encoding='utf-8')); result = config.get('weixin', {}).get('enabled', False); print('1' if result else '0')"') do set WEIXIN_RESULT=%%i

if "%WEIXIN_RESULT%"=="1" (
    echo [5/5] Starting Weixin Bot...
    start "Weixin Bot" python bot/weixin_bot.py
    set WEIXIN_ENABLED=True
) else (
    echo [5/5] Weixin Bot disabled in config, skipping...
    set WEIXIN_ENABLED=False
)

echo.
echo ========================================
echo   All Services Started Successfully
echo ========================================
echo.
echo Running services:
echo   - Discord Bot
echo   - Claude Bridge
echo   - Manager (monitor)
if "%WEIXIN_RESULT%"=="1" (
    echo   - Weixin Bot
)
echo.
echo To stop all services, use: manager.bat stop
echo.
pause
