@echo off
REM Discord Bridge System Stop Script

REM Terminate Discord Bot (python and pythonw)
wmic process where "name='python.exe' and commandline like '%%discord_bot.py%%'" delete >nul 2>&1
wmic process where "name='pythonw.exe' and commandline like '%%discord_bot.py%%'" delete >nul 2>&1

REM Terminate Claude Bridge
wmic process where "name='python.exe' and commandline like '%%claude_bridge.py%%'" delete >nul 2>&1
wmic process where "name='pythonw.exe' and commandline like '%%claude_bridge.py%%'" delete >nul 2>&1

REM Terminate Weixin Bot
wmic process where "name='python.exe' and commandline like '%%weixin_bot.py%%'" delete >nul 2>&1
wmic process where "name='pythonw.exe' and commandline like '%%weixin_bot.py%%'" delete >nul 2>&1

REM Terminate Web Server
wmic process where "name='python.exe' and commandline like '%%web_server.py%%'" delete >nul 2>&1
wmic process where "name='pythonw.exe' and commandline like '%%web_server.py%%'" delete >nul 2>&1

REM Terminate Manager
wmic process where "name='python.exe' and commandline like '%%im_claude_bridge_manager.py%%'" delete >nul 2>&1
wmic process where "name='pythonw.exe' and commandline like '%%im_claude_bridge_manager.py%%'" delete >nul 2>&1

REM Terminate MCP Server
wmic process where "name='python.exe' and commandline like '%%mcp_server%%'" delete >nul 2>&1
wmic process where "name='pythonw.exe' and commandline like '%%mcp_server%%'" delete >nul 2>&1
