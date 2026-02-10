@echo off
REM Discord Bridge Manager - Windows 管理脚本
REM 提供服务管理功能（status/stop/restart/logs）

if "%1"=="" (
    echo 用法: manager.bat [command]
    echo.
    echo 命令:
    echo   start     启动监控控制台（守护进程）
    echo   stop      停止所有服务
    echo   restart   重启所有服务
    echo   logs      查看管理日志
    echo.
    echo 启动服务请使用: start.bat
    pause
    exit /b 0
)

python manager.py %*
