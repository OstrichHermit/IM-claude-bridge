@echo off
cd /d "%~dp0"

REM Find Python executable
for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable.replace('\\python.exe','\\pythonw.exe'))" 2^>nul') do set PYTHONW=%%i
if not defined PYTHONW set PYTHONW=pythonw.exe

REM Kill all IM-claude-bridge related Python processes (exclude manager and web_server)
python "%~dp0scripts\kill_bridge.py" im_claude_bridge_manager web_server >nul 2>&1

timeout /t 2 /nobreak >nul

start "" /b "%PYTHONW%" "%~dp0bot\discord_bot.py"
timeout /t 1 /nobreak >nul
start "" /b "%PYTHONW%" "%~dp0bridge\claude_bridge.py"
timeout /t 1 /nobreak >nul
start "" /b "%PYTHONW%" "%~dp0mcp_server\server.py"

for /f "delims=" %%i in ('python -c "import yaml; config = yaml.safe_load(open(r'%~dp0config\config.yaml', encoding='utf-8')); result = config.get('weixin', {}).get('enabled', False); print('1' if result else '0')"') do set WEIXIN_RESULT=%%i

if "%WEIXIN_RESULT%"=="1" (
    start "" /b "%PYTHONW%" "%~dp0bot\weixin_bot.py"
)
