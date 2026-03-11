"""
Claude Code 桥接服务
从消息队列获取消息并转发给 Claude Code CLI（并发架构）
"""
import asyncio
import sys
import time
from pathlib import Path
from typing import Dict

# 添加 shared 目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.config import Config
from shared.message_queue import MessageQueue, Message, MessageDirection
from bridge.session_worker import SessionWorker


class ClaudeBridge:
    """Claude Code 桥接服务（并发架构）"""

    def __init__(self, config: Config):
        """初始化桥接服务"""
        self.config = config
        self.message_queue = MessageQueue(config.database_path)
        self.running = False

        # 🔥 并发架构：Worker Pool
        self.session_workers: Dict[str, SessionWorker] = {}  # {session_key: SessionWorker}
        self.max_concurrent_sessions = config.max_concurrent_sessions
        self.worker_idle_timeout = config.worker_idle_timeout

    async def cleanup_pending_messages(self):
        """清理上次崩溃时留下的 PENDING 消息（避免重启后重复处理）"""
        import sqlite3
        try:
            conn = sqlite3.connect(self.message_queue.db_path)
            cursor = conn.cursor()

            # 查询 PENDING 消息数量
            cursor.execute("SELECT COUNT(*) FROM messages WHERE status = 'pending'")
            pending_count = cursor.fetchone()[0]

            if pending_count > 0:
                print(f"🧹 发现 {pending_count} 条待处理的消息（PENDING），正在跳过...")

                # 将 PENDING 状态的消息标记为 SKIPPED
                cursor.execute("""
                    UPDATE messages
                    SET status = 'skipped',
                        updated_at = CURRENT_TIMESTAMP,
                        error = 'Bridge 重启：消息被跳过，避免重复处理'
                    WHERE status = 'pending'
                """)

                affected = cursor.rowcount
                conn.commit()
                print(f"✅ 已跳过 {affected} 条旧消息")
            else:
                print("✓ 没有发现 PENDING 状态的消息")

            conn.close()

        except Exception as e:
            print(f"⚠️ 清理 PENDING 消息时出错: {e}")

    async def run(self):
        """
        运行桥接服务主循环（并发架构）

        架构说明：
        - 主调度器：扫描 PENDING 消息，按 session 分组，分配到对应 Worker
        - Worker Pool：每个 session 一个 Worker，并发处理不同 session 的消息
        - Worker Manager：清理空闲的 Worker，释放资源
        """
        self.running = True
        print("🚀 Claude Code 桥接服务已启动（并发架构）")
        print(f"📥 轮询间隔: {self.config.poll_interval}ms")
        print(f"⏱️  超时时间: {self.config.claude_timeout}秒")
        print(f"🔄 最大重试: {self.config.max_retries}次")
        print(f"🎢 并发模式: {self.config.session_mode}")
        print(f"⚡ 最大并发 session 数: {self.max_concurrent_sessions}")

        # 启动时清理旧的 PENDING 消息
        await self.cleanup_pending_messages()

        # 🔥 启动并发架构的任务
        scheduler_task = asyncio.create_task(self._scheduler_loop())
        worker_manager_task = asyncio.create_task(self._worker_manager_loop())

        print("✅ 并发架构已启动")

        # 等待任务完成（或收到停止信号）
        try:
            await asyncio.gather(scheduler_task, worker_manager_task)
        except asyncio.CancelledError:
            print("⚠️  收到取消信号，正在停止...")
            self.running = False
        except Exception as e:
            print(f"❌ 主循环错误: {e}")
        finally:
            # 清理所有 Workers
            await self._cleanup_all_workers()

        print("✓ Claude Code 桥接服务已停止")

    async def _scheduler_loop(self):
        """
        主调度器循环

        功能：
        1. 扫描所有 PENDING 消息
        2. 按 session_key 分组
        3. 分配消息到对应的 Worker
        """
        print("📋 主调度器已启动")

        while self.running:
            try:
                # 1. 获取所有 PENDING 消息（按 session 分组）
                messages_by_session = self.message_queue.get_pending_messages_by_session()

                if messages_by_session:
                    # 只在有消息时才输出日志
                    total_messages = sum(len(msgs) for msgs in messages_by_session.values())
                    print(f"📦 扫描到 {total_messages} 条 PENDING 消息，涉及 {len(messages_by_session)} 个 session")

                    # 2. 为每个 session 分配消息
                    for session_key, messages in messages_by_session.items():
                        try:
                            # 获取或创建 Worker
                            worker = await self._get_or_create_worker(session_key)

                            # 将消息加入 Worker 的队列
                            for message in messages:
                                await worker.enqueue(message)

                            print(f"  📌 [{session_key}]: 已分配 {len(messages)} 条消息")

                        except Exception as e:
                            print(f"❌ 分配消息到 Worker [{session_key}] 失败: {e}")

                # 3. 没有消息时静默等待
                await asyncio.sleep(self.config.poll_interval / 1000)

            except asyncio.CancelledError:
                print("⚠️  调度器收到取消信号")
                break
            except Exception as e:
                print(f"❌ 调度器错误: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(5)  # 出错后等待一段时间

        print("✓ 主调度器已退出")

    async def _get_or_create_worker(self, session_key: str) -> SessionWorker:
        """
        获取或创建 Session Worker

        Args:
            session_key: 会话标识

        Returns:
            SessionWorker 实例
        """
        # 如果 Worker 已存在，直接返回
        if session_key in self.session_workers:
            return self.session_workers[session_key]

        # 检查并发限制
        if self.max_concurrent_sessions > 0:
            while len(self.session_workers) >= self.max_concurrent_sessions:
                print(f"⚠️  已达到最大并发数 ({self.max_concurrent_sessions})，等待空闲 Worker...")
                await self._wait_for_worker_slot()

        # 创建新 Worker
        worker = SessionWorker(session_key, self.config, self.message_queue)
        await worker.start()
        self.session_workers[session_key] = worker

        print(f"✅ Worker 已创建: {session_key} (当前 Worker 数: {len(self.session_workers)})")

        return worker

    async def _wait_for_worker_slot(self):
        """等待有空闲 Worker 槽位（当达到最大并发数时）"""
        # 等待一小段时间后重试
        await asyncio.sleep(0.5)

    async def _worker_manager_loop(self):
        """
        Worker 管理循环

        功能：
        - 定期清理空闲的 Worker
        - 监控 Worker 状态
        """
        print("🔧 Worker 管理器已启动")

        while self.running:
            try:
                # 定期清理空闲的 Worker
                await self._cleanup_idle_workers()

                # 每 60 秒检查一次
                await asyncio.sleep(60)

            except asyncio.CancelledError:
                print("⚠️  Worker 管理器收到取消信号")
                break
            except Exception as e:
                print(f"❌ Worker 管理器错误: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(5)

        print("✓ Worker 管理器已退出")

    async def _cleanup_idle_workers(self):
        """清理空闲的 Worker"""
        current_time = time.time()
        idle_workers = []

        # 检查每个 Worker 是否空闲
        for session_key, worker in list(self.session_workers.items()):
            if worker.is_idle(current_time, self.worker_idle_timeout):
                idle_workers.append(session_key)

        # 清理空闲的 Workers
        for session_key in idle_workers:
            try:
                worker = self.session_workers.pop(session_key)
                await worker.stop()
                print(f"🧹 Worker 已清理: {session_key} (空闲超时)")
            except Exception as e:
                print(f"❌ 清理 Worker [{session_key}] 失败: {e}")

    async def _cleanup_all_workers(self):
        """清理所有 Worker（停止服务时调用）"""
        print("🧹 正在清理所有 Workers...")

        for session_key, worker in list(self.session_workers.items()):
            try:
                await worker.stop()
                print(f"✅ Worker 已停止: {session_key}")
            except Exception as e:
                print(f"❌ 停止 Worker [{session_key}] 失败: {e}")

        self.session_workers.clear()
        print("✅ 所有 Workers 已清理")


def main():
    """主函数"""
    try:
        # 加载配置
        config = Config()

        # 创建并启动桥接服务
        bridge = ClaudeBridge(config)
        asyncio.run(bridge.run())

    except FileNotFoundError as e:
        print(f"❌ 配置错误: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 启动失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
