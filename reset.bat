@echo off
REM 清理数据库并重启服务

echo ========================================
echo   清理并重启 Discord Claude Bridge
echo ========================================
echo.

echo [1/3] 停止运行中的服务...
taskkill /F /IM python.exe /FI "WINDOWTITLE eq Discord Bot*" 2>nul
taskkill /F /IM python.exe /FI "WINDOWTITLE eq Claude Bridge*" 2>nul
timeout /t 2 /nobreak >nul

echo [2/3] 清理消息数据库...
if exist "shared\messages.db" (
    del /F /Q "shared\messages.db"
    echo    已删除旧数据库
) else (
    echo    没有找到旧数据库
)

echo [3/3] 重新启动服务...
start "Discord Bot" python bot/discord_bot.py
timeout /t 2 /nobreak >nul
start "Claude Bridge" python bridge/claude_bridge.py

echo.
echo ========================================
echo   ✓ 服务已重启！
echo ========================================
echo.
echo 数据库已清空，可以开始新的对话。
echo.
pause
