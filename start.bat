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

echo [2/4] Starting Discord Bot...
start "Discord Bot" python bot/discord_bot.py

echo [3/4] Starting Claude Bridge...
start "Claude Bridge" python bridge/claude_bridge.py

echo [4/4] Starting Manager (monitor)...
start "Manager" python manager.py start

echo.
echo ========================================
echo   All Services Started Successfully
echo ========================================
echo.
echo Running services:
echo   - Discord Bot
echo   - Claude Bridge
echo   - Manager (monitor)
echo.
echo To stop all services, use: manager.bat stop
echo.
pause
