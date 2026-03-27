"""
统一日志模块

提供项目所有组件共享的日志功能，输出到控制台和日志文件。

日志文件映射（5个）：
  discord_bot.log  — discord_bot.py, streaming_queue.py
  weixin_bot.log   — weixin_bot.py, weixin_media.py, weixin_client.py, weixin_qr_login.py
  claude_bridge.log — claude_bridge.py, session_worker.py, context_token_storage.py
  manager.log      — im_claude_bridge_manager.py, cron_scheduler.py, web_server.py, file_mapping.py
  mcp_server.log   — mcp_server/server.py
"""
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

# 日志目录
LOG_DIR = Path(__file__).parent.parent / "logs"

# 日志文件映射：逻辑名 → 文件名
LOG_FILES = {
    "discord":   "discord_bot.log",
    "weixin":    "weixin_bot.log",
    "bridge":    "claude_bridge.log",
    "manager":   "manager.log",
    "mcp_server": "mcp_server.log",
}

# 确保 logs 目录存在
LOG_DIR.mkdir(parents=True, exist_ok=True)


class Logger:
    """统一日志器"""

    def __init__(self, component: str, log_name: str):
        """
        Args:
            component: 组件标识，用于日志前缀显示，如 "DiscordBot"、"SessionWorker"
            log_name:  日志文件逻辑名，必须是 LOG_FILES 中的 key
        """
        self.component = component
        if log_name not in LOG_FILES:
            raise ValueError(f"未知日志名: {log_name}，可选: {list(LOG_FILES.keys())}")
        self.log_file = LOG_DIR / LOG_FILES[log_name]

    def log(self, message: str):
        """同时输出到控制台和写入日志文件"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] {message}\n"
        print(log_line.rstrip())
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception as e:
            print(f"⚠️  写入日志失败: {e}")


def get_logger(component: str, log_name: str) -> Logger:
    """
    获取日志器实例

    Args:
        component: 组件标识，如 "DiscordBot"、"CronScheduler"
        log_name:  日志文件逻辑名，如 "discord"、"weixin"、"bridge"、"manager"、"mcp_server"

    Returns:
        Logger 实例

    示例:
        log = get_logger("DiscordBot", "discord")
        log.log("Bot 已启动")
    """
    return Logger(component, log_name)


def cleanup_logs(max_lines: int = 1000):
    """
    清理所有日志文件，每个文件只保留最近 max_lines 行

    在桥接系统启动时调用
    """
    for filename in LOG_FILES.values():
        log_path = LOG_DIR / filename
        if not log_path.exists():
            continue
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > max_lines:
                kept = lines[-max_lines:]
                with open(log_path, "w", encoding="utf-8") as f:
                    f.writelines(kept)
                print(f"[日志清理] {filename}: {len(lines)} → {len(kept)} 行")
        except Exception as e:
            print(f"[日志清理] 清理 {filename} 失败: {e}")
