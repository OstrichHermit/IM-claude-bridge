@echo off
cd /d "%~dp0"

if not exist "config\config.yaml" (
    echo [ERROR] Config file not found!
    exit /b 1
)

REM Find Python executable - try py launcher first, then direct python
for /f "delims=" %%i in ('py -0 2^>nul') do set PY_FOUND=%%i
if defined PY_FOUND (
    for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable.replace('\\python.exe','\\pythonw.exe'))" 2^>nul') do set PYTHONW=%%i
)

REM Fallback: use direct path if py launcher failed
if not defined PYTHONW (
    set PYTHONW=pythonw.exe
)

python -c "import discord, yaml, fastapi" >nul 2>&1
if errorlevel 1 (
    pip install -r requirements.txt
)

if not exist "shared" mkdir shared
if not exist "logs" mkdir logs

start "" /b "%PYTHONW%" "%~dp0bot\discord_bot.py"
start "" /b "%PYTHONW%" "%~dp0bridge\claude_bridge.py"
start "" /b "%PYTHONW%" "%~dp0im_claude_bridge_manager.py" console
start "" /b "%PYTHONW%" "%~dp0web\web_server.py"

for /f "delims=" %%i in ('python -c "import yaml; config = yaml.safe_load(open(r'%~dp0config\config.yaml', encoding='utf-8')); result = config.get('weixin', {}).get('enabled', False); print('1' if result else '0')"') do set WEIXIN_RESULT=%%i

if "%WEIXIN_RESULT%"=="1" (
    start "" /b "%PYTHONW%" "%~dp0bot\weixin_bot.py"
)

echo All services started!
