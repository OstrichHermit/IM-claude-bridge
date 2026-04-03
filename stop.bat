@echo off
cd /d "%~dp0"
REM Discord Bridge System Stop Script
python "%~dp0scripts\kill_bridge.py" >nul 2>&1
