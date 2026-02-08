@echo off
REM Discord Claude Bridge Startup Script (Windows)

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
echo [1/3] Checking Python dependencies...
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

echo [2/3] Starting Discord Bot...
start "Discord Bot" python bot/discord_bot.py

timeout /t 2 /nobreak >nul

echo [3/3] Starting Claude Bridge...
start "Claude Bridge" python bridge/claude_bridge.py

echo.
echo ========================================
echo   All Services Started!
echo ========================================
echo.
echo Note: Closing this window will NOT stop the services.
echo To stop, close the individual service windows.
echo.
pause
