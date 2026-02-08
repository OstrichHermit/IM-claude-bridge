#!/usr/bin/env python3
"""
Discord Claude Bridge - 服务启动脚本
同时启动 Discord Bot 和 Claude Bridge 服务
"""
import subprocess
import sys
import os
from pathlib import Path


def start_bot():
    """启动 Discord Bot"""
    project_root = Path(__file__).parent.parent.parent.parent
    bot_path = project_root / "discord-claude-bridge" / "bot" / "discord_bot.py"

    print(f"🤖 启动 Discord Bot: {bot_path}")
    return subprocess.Popen(
        [sys.executable, str(bot_path)],
        cwd=str(project_root / "discord-claude-bridge")
    )


def start_bridge():
    """启动 Claude Bridge"""
    project_root = Path(__file__).parent.parent.parent.parent
    bridge_path = project_root / "discord-claude-bridge" / "bridge" / "claude_bridge.py"

    print(f"🔗 启动 Claude Bridge: {bridge_path}")
    return subprocess.Popen(
        [sys.executable, str(bridge_path)],
        cwd=str(project_root / "discord-claude-bridge")
    )


def main():
    """主函数"""
    print("=" * 50)
    print("Discord Claude Bridge - 服务启动")
    print("=" * 50)

    try:
        # 启动 Discord Bot
        bot_process = start_bot()
        print(f"✅ Discord Bot 已启动 (PID: {bot_process.pid})")

        # 启动 Claude Bridge
        bridge_process = start_bridge()
        print(f"✅ Claude Bridge 已启动 (PID: {bridge_process.pid})")

        print("\n📝 服务正在运行，按 Ctrl+C 停止...")

        # 等待进程
        bot_process.wait()
        bridge_process.wait()

    except KeyboardInterrupt:
        print("\n\n⚠️  收到停止信号，正在关闭服务...")
        bot_process.terminate()
        bridge_process.terminate()
        bot_process.wait()
        bridge_process.wait()
        print("✅ 服务已停止")


if __name__ == "__main__":
    main()
