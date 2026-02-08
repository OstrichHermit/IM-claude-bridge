@echo off
REM Discord Bridge 系统重启脚本
REM 功能：结束当前运行的 Python 进程，然后重新启动服务

echo ========================================
echo   Discord Bridge 系统重启
echo ========================================
echo.

REM 查找并结束与 discord-claude-bridge 相关的 Python 进程
echo [1/3] 正在查找并结束旧的 Python 进程...

for /f "tokens=2" %%a in ('tasklist ^| findstr /i "python.exe"') do (
    taskkill /F /PID %%a >nul 2>&1
    if errorlevel 1 (
        echo   - 进程 %%a 未运行或已结束
    ) else (
        echo   - 已结束进程 %%a
    )
)

echo.
echo [2/3] 等待进程完全退出...
timeout /t 2 /nobreak >nul

echo.
echo [3/3] 启动 Discord Bridge 服务...
start.bat

echo.
echo ========================================
echo   系统重启完成
echo ========================================
