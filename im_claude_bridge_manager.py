"""
Discord Bridge 守护程序
Windows 环境下的进程管理、监控和智能重启
"""
import subprocess
import time
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from shared.logger import get_logger, cleanup_logs

log = get_logger("Manager", "manager")


class Manager:
    """守护程序管理器"""

    # 最大重试次数
    MAX_RESTART_RETRIES = 3

    def __init__(self):
        self.project_dir = Path(__file__).parent.resolve()
        self.stop_file = self.project_dir / ".manager.stop"
        self.restarting_file = self.project_dir / ".manager.restarting"
        self.retry_count_file = self.project_dir / ".manager.retry_count"

        # 确保日志目录存在
        from shared.logger import LOG_DIR
        LOG_DIR.mkdir(parents=True, exist_ok=True)

        # 启动时清理日志（保留最近1000行）
        cleanup_logs()

        # 启动时清空所有标记
        self.clear_all_flags()

    def clear_all_flags(self):
        """清空所有标记文件"""
        flags_cleared = []

        # 清空停止标记
        if self.stop_file.exists():
            self.stop_file.unlink()
            flags_cleared.append("停止标记")

        # 清空重启标记
        if self.restarting_file.exists():
            self.restarting_file.unlink()
            flags_cleared.append("重启标记")

        # 清空重试次数
        if self.retry_count_file.exists():
            self.retry_count_file.unlink()
            flags_cleared.append("重试次数")

        if flags_cleared:
            log.log(f"🧹 已清空标记: {', '.join(flags_cleared)}")

    def find_process_by_commandline(self, pattern):
        """通过命令行参数查找进程 PID（支持 python.exe 和 pythonw.exe）"""
        try:
            # 查找 python.exe 进程
            result = subprocess.run(
                ['wmic', 'process', 'where', "Name='python.exe'", 'get', 'ProcessId,CommandLine', '/format:csv'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            lines = result.stdout.strip().split('\n')
            for line in lines:
                if pattern.lower() in line.lower():
                    parts = line.split(',')
                    if len(parts) >= 3:
                        pid_str = parts[2].strip('"')
                        if pid_str.isdigit():
                            return int(pid_str)

            # 查找 pythonw.exe 进程
            result = subprocess.run(
                ['wmic', 'process', 'where', "Name='pythonw.exe'", 'get', 'ProcessId,CommandLine', '/format:csv'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                creationflags=subprocess.CREATE_NO_WINDOW
            )

            lines = result.stdout.strip().split('\n')
            for line in lines:
                if pattern.lower() in line.lower():
                    # 解析 CSV 格式: Node,CommandLine,ProcessId
                    parts = line.split(',')
                    if len(parts) >= 3:
                        pid_str = parts[2].strip('"')
                        if pid_str.isdigit():
                            return int(pid_str)
        except Exception as e:
            log.log(f"⚠️  查找进程失败 ({pattern}): {e}")

        return None

    def is_bot_running(self):
        """检查 Discord Bot 是否运行"""
        return self.find_process_by_commandline("discord_bot.py") is not None

    def is_weixin_bot_running(self):
        """检查微信 Bot 是否运行"""
        return self.find_process_by_commandline("weixin_bot.py") is not None

    def is_bridge_running(self):
        """检查 Claude Bridge 是否运行"""
        return self.find_process_by_commandline("claude_bridge.py") is not None

    def is_web_server_running(self):
        """检查 Web Server 是否运行"""
        return self.find_process_by_commandline("web_server.py") is not None

    def is_mcp_server_running(self):
        """检查 MCP Server 是否运行"""
        return self.find_process_by_commandline("mcp_server") is not None

    def get_retry_count(self):
        """获取当前重试次数"""
        if self.retry_count_file.exists():
            try:
                with open(self.retry_count_file, "r") as f:
                    return int(f.read().strip())
            except:
                pass
        return 0

    def set_retry_count(self, count):
        """设置重试次数"""
        with open(self.retry_count_file, "w") as f:
            f.write(str(count))

    def reset_retry_count(self):
        """重置重试次数"""
        if self.retry_count_file.exists():
            self.retry_count_file.unlink()

    def start_all(self):
        """使用 start.bat 启动所有服务"""
        log.log("🚀 使用 start.bat 启动所有服务...")

        # 删除停止标记
        if self.stop_file.exists():
            self.stop_file.unlink()
            log.log("🗑️  已删除停止标记")

        start_script = self.project_dir / "start.bat"

        if not start_script.exists():
            log.log(f"❌ 找不到 start.bat: {start_script}")
            return False

        try:
            # 在后台执行 start.bat
            subprocess.Popen(
                ["cmd", "/c", str(start_script)],
                cwd=self.project_dir,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            log.log("✅ start.bat 已执行")
            return True

        except Exception as e:
            log.log(f"❌ 执行 start.bat 失败: {e}")
            return False

    def stop_all(self):
        """停止所有服务"""
        log.log("🛑 停止所有服务...")

        stop_script = self.project_dir / "stop.bat"

        if not stop_script.exists():
            log.log(f"❌ 找不到 stop.bat: {stop_script}")
            return False

        try:
            subprocess.Popen(
                ["cmd", "/c", str(stop_script)],
                cwd=self.project_dir,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            log.log("✅ stop.bat 已执行")
            return True

        except Exception as e:
            log.log(f"❌ 执行 stop.bat 失败: {e}")
            return False

    def restart_all(self):
        """重启所有服务（创建重启标记）"""
        log.log("🔄 重启所有服务...")

        # 创建重启标记
        self.restarting_file.touch()
        log.log("📝 已创建重启标记")

        # 调用 restart.bat
        restart_script = self.project_dir / "restart.bat"

        if not restart_script.exists():
            log.log(f"❌ 找不到 restart.bat: {restart_script}")
            self.restarting_file.unlink()
            return False

        try:
            # 在后台执行 restart.bat
            subprocess.Popen(
                ["cmd", "/c", str(restart_script)],
                cwd=self.project_dir,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            log.log("✅ restart.bat 已执行")
            return True

        except Exception as e:
            log.log(f"❌ 执行 restart.bat 失败: {e}")
            self.restarting_file.unlink()
            return False

    def monitor_loop(self):
        """监控循环（智能重启逻辑）"""
        log.log("🔍 守护进程启动，开始监控...")
        log.log(f"📂 项目目录: {self.project_dir}")
        log.log(f"📋 日志文件: {log.log_file}")

        # 初始检查并报告状态
        bot_running = self.is_bot_running()
        weixin_bot_running = self.is_weixin_bot_running()
        bridge_running = self.is_bridge_running()
        web_server_running = self.is_web_server_running()
        mcp_server_running = self.is_mcp_server_running()

        if bot_running and weixin_bot_running and bridge_running and web_server_running and mcp_server_running:
            log.log("✅ 初始检查: 所有服务运行正常")
        else:
            if not bot_running:
                log.log("⚠️  初始检查: Discord Bot 未运行")
            if not weixin_bot_running:
                log.log("⚠️  初始检查: 微信 Bot 未运行")
            if not bridge_running:
                log.log("⚠️  初始检查: Claude Bridge 未运行")
            if not web_server_running:
                log.log("⚠️  初始检查: Web Server 未运行")
            if not mcp_server_running:
                log.log("⚠️  初始检查: MCP Server 未运行")

        while True:
            try:
                # 检查停止标记
                if self.stop_file.exists():
                    log.log("📋 检测到停止标记，守护进程退出")
                    break

                # 检查重启标记
                if self.restarting_file.exists():
                    log.log("🔄 检测到重启标记，延迟 30 秒后查询...")
                    time.sleep(30)

                    # 延迟后再次检查
                    if self.is_bot_running() and self.is_weixin_bot_running() and self.is_bridge_running() and self.is_web_server_running() and self.is_mcp_server_running():
                        log.log("✅ 重启成功，移除重启标记和重置重试次数")
                        if self.restarting_file.exists():
                            self.restarting_file.unlink()
                        self.reset_retry_count()
                        continue

                    # 重启未成功，获取当前重试次数
                    retry_count = self.get_retry_count()

                    if retry_count >= self.MAX_RESTART_RETRIES:
                        log.log(f"❌ 重启连续失败 {self.MAX_RESTART_RETRIES} 次，放弃重启")
                        if self.restarting_file.exists():
                            self.restarting_file.unlink()
                        self.reset_retry_count()
                        continue

                    # 执行重启操作
                    log.log(f"⚠️  进程未恢复，尝试手动重启（第 {retry_count + 1}/{self.MAX_RESTART_RETRIES} 次）")

                    # 统一使用 restart.bat
                    log.log(f"[第 {retry_count + 1} 次] 执行 restart.bat...")
                    subprocess.Popen(
                        ["cmd", "/c", str(self.project_dir / "restart.bat")],
                        cwd=self.project_dir,
                        creationflags=subprocess.CREATE_NO_WINDOW
                    )

                    # 增加重试次数
                    retry_count += 1
                    self.set_retry_count(retry_count)
                    log.log(f"📊 重试次数: {retry_count}/{self.MAX_RESTART_RETRIES}")

                    # 延迟后继续（给予重启时间）
                    time.sleep(30)
                    continue

                # 正常监控：检查进程状态
                bot_running = self.is_bot_running()
                weixin_bot_running = self.is_weixin_bot_running()
                bridge_running = self.is_bridge_running()
                web_server_running = self.is_web_server_running()
                mcp_server_running = self.is_mcp_server_running()

                if bot_running and weixin_bot_running and bridge_running and web_server_running and mcp_server_running:
                    # 所有进程正常，重置重试次数
                    if self.retry_count_file.exists():
                        self.reset_retry_count()
                elif not bot_running or not weixin_bot_running or not bridge_running or not web_server_running or not mcp_server_running:
                    # 发现进程挂了，创建重启标记并触发重启
                    if not bot_running:
                        log.log("⚠️  Discord Bot 未运行，触发重启...")
                    if not weixin_bot_running:
                        log.log("⚠️  微信 Bot 未运行，触发重启...")
                    if not bridge_running:
                        log.log("⚠️  Claude Bridge 未运行，触发重启...")
                    if not web_server_running:
                        log.log("⚠️  Web Server 未运行，触发重启...")
                    if not mcp_server_running:
                        log.log("⚠️  MCP Server 未运行，触发重启...")

                    # 创建重启标记
                    self.restarting_file.touch()
                    log.log("📝 已创建重启标记")

                # 等待 10 秒
                time.sleep(10)

            except KeyboardInterrupt:
                log.log("\n⚠️  收到中断信号，正在退出...")
                break
            except Exception as e:
                log.log(f"❌ 监控循环错误: {e}")
                time.sleep(5)

        log.log("👋 守护进程已退出")

    def show_logs(self):
        """显示日志（类似 tail -f）"""
        if not log.log_file.exists():
            print(f"📋 日志文件不存在: {log.log_file}")
            return

        print(f"📋 实时查看日志: {log.log_file}")
        print("按 Ctrl+C 退出\n")

        try:
            with open(log.log_file, "r", encoding="utf-8") as f:
                # 跳到文件末尾
                f.seek(0, 2)

                while True:
                    line = f.readline()
                    if line:
                        print(line.strip())
                    else:
                        time.sleep(0.1)
        except KeyboardInterrupt:
            print("\n✅ 已停止查看日志")


def main():
    """主函数"""
    if len(sys.argv) < 2:
        print("Discord Bridge Manager - Windows 守护程序")
        print()
        print("用法:")
        print("  python im_claude_bridge_manager.py console    # 在独立窗口启动监控控制台（推荐）")
        print("  python im_claude_bridge_manager.py start      # 启动监控控制台（独立窗口）")
        print("  python im_claude_bridge_manager.py start-all  # 启动所有服务")
        print("  python im_claude_bridge_manager.py stop       # 停止所有服务")
        print("  python im_claude_bridge_manager.py restart    # 重启所有服务")
        print("  python im_claude_bridge_manager.py logs       # 查看日志")
        sys.exit(1)

    command = sys.argv[1].lower()
    manager = Manager()

    if command == "console" or command == "start":
        # 在独立窗口中启动监控循环
        print()
        print("=" * 50)
        print("  IM Claude Bridge Manager Console")
        print("=" * 50)
        print()
        print("🔄 监控控制台正在运行，日志实时显示")
        print("💡 按 Ctrl+C 退出控制台（不会停止服务）")
        print("💡 在其他窗口使用 'manager.bat stop' 停止所有服务")
        print("💡 在其他窗口使用 'manager.bat start-all' 启动所有服务")
        print()

        # 清理可能存在的旧重启标记
        if manager.restarting_file.exists():
            manager.restarting_file.unlink()
            log.log("🗑️  已清理旧的重启标记")

        manager.monitor_loop()

    elif command == "start-all":
        # 启动所有服务
        if manager.start_all():
            print("\n✅ 所有服务已启动")
        else:
            print("\n❌ 启动失败")
            sys.exit(1)

    elif command == "stop":
        # 停止服务
        manager.stop_all()

    elif command == "restart":
        # 重启服务
        manager.restart_all()

    elif command == "logs":
        # 查看日志
        manager.show_logs()

    else:
        print(f"❌ 未知命令: {command}")
        print("可用命令: start, stop, restart, logs")
        sys.exit(1)


if __name__ == "__main__":
    main()
