"""
Claude Code 桥接服务
从消息队列获取消息并转发给 Claude Code CLI
"""
import asyncio
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# 添加 shared 目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.config import Config
from shared.message_queue import MessageQueue, Message, MessageDirection, MessageStatus


class ClaudeBridge:
    """Claude Code 桥接服务"""

    def __init__(self, config: Config):
        """初始化桥接服务"""
        self.config = config
        self.message_queue = MessageQueue(config.database_path)
        self.running = False

    async def process_message(self, message: Message) -> bool:
        """处理单条消息"""
        print(f"[消息 #{message.id}] 开始处理: {message.content[:50]}...")

        # 获取或创建会话工作目录
        session_key, working_dir = self.message_queue.get_or_create_session(
            self.config.session_mode,
            message.discord_channel_id,
            message.discord_user_id,
            self.config.working_directory
        )

        if session_key:
            print(f"[消息 #{message.id}] 使用会话: {session_key}")
            print(f"[消息 #{message.id}] 工作目录: {working_dir}")

        try:
            # 调用 Claude Code CLI
            response = await self.call_claude_cli(
                message.content,
                session_key,
                working_dir,
                username=message.username,
                user_id=message.discord_user_id,
                is_dm=message.is_dm
            )

            if response:
                # 更新消息，添加响应
                self.message_queue.update_status(
                    message.id,
                    MessageStatus.PROCESSING,  # 保持 PROCESSING 状态，等待 Discord Bot 发送
                    response=response
                )

                print(f"[消息 #{message.id}] 处理成功")
                return True
            else:
                # 响应为空
                self.message_queue.update_status(
                    message.id,
                    MessageStatus.COMPLETED,  # 直接标记为完成
                    response="(Claude 没有返回响应)"
                )
                print(f"[消息 #{message.id}] 处理完成（无响应）")
                return True

        except Exception as e:
            error_msg = f"处理失败: {str(e)}"
            print(f"❌ [消息 #{message.id}] {error_msg}")

            # 更新消息状态为失败
            self.message_queue.update_status(
                message.id,
                MessageStatus.FAILED,
                error=error_msg
            )
            return False

    async def call_claude_cli(self, prompt: str, session_key: Optional[str] = None, working_dir: str = None, username: str = None, user_id: int = None, is_dm: bool = False) -> Optional[str]:
        """
        调用 Claude Code CLI
        使用 claude -p 参数进行非交互式调用

        Args:
            prompt: 用户提示词
            session_key: 会话 key（可选），用于保持对话上下文
            working_dir: 工作目录，每个会话使用独立目录以保持对话历史
            username: 发送者用户名（频道模式下需要）
            user_id: 发送者用户 ID（频道模式下需要）
            is_dm: 是否为私聊消息
        """
        retries = 0
        max_retries = self.config.max_retries

        # 使用传入的 working_dir，如果没有则使用默认配置
        cwd = working_dir or self.config.working_directory

        # 在频道模式下，附加发送者信息到提示词
        if not is_dm and username and user_id:
            prompt = f"{username}（{user_id}）说：{prompt}"

        while retries < max_retries:
            try:
                print(f"🤖 调用 Claude Code CLI (尝试 {retries + 1}/{max_retries})...")
                print(f"📝 提示词: {prompt[:80]}{'...' if len(prompt) > 80 else ''}")

                # 构建命令参数
                cmd_args = ['-p']  # print 模式：直接输出响应并退出

                # 如果需要保持会话，使用 --continue 参数
                if session_key:
                    cmd_args.append('-c')  # continue：继续最近的对话

                # 添加提示词
                cmd_args.append(prompt)

                # 使用 claude 命令进行非交互式调用
                process = await asyncio.create_subprocess_exec(
                    self.config.claude_executable,
                    *cmd_args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd  # 使用会话专用的工作目录
                )

                try:
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(),
                        timeout=self.config.claude_timeout
                    )

                    if process.returncode == 0:
                        response = stdout.decode('utf-8', errors='replace').strip()

                        # 如果响应为空，检查是否有 stderr 输出
                        if not response:
                            stderr_output = stderr.decode('utf-8', errors='replace').strip()
                            if stderr_output:
                                print(f"⚠️  Claude 输出了警告信息: {stderr_output}")

                        print(f"✅ Claude 响应成功 (长度: {len(response) if response else 0} 字符)")
                        return response if response else "(Claude 没有返回文本响应)"
                    else:
                        # 命令执行失败
                        error_output = stderr.decode('utf-8', errors='replace').strip()
                        error_msg = f"Claude Code 返回错误码 {process.returncode}"
                        if error_output:
                            error_msg += f": {error_output}"

                        raise Exception(error_msg)

                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                    raise Exception(f"Claude Code 超时（超过 {self.config.claude_timeout} 秒）")

            except FileNotFoundError:
                # claude 命令不存在
                error_msg = (
                    f"找不到 Claude Code CLI: '{self.config.claude_executable}'\n"
                    f"请确保已安装 Claude Code 并在 PATH 中可访问\n"
                    f"安装指南: https://claude.ai/code"
                )
                print(f"❌ {error_msg}")
                raise Exception(error_msg)

            except Exception as e:
                retries += 1
                print(f"❌ 调用失败 (尝试 {retries}/{max_retries}): {e}")

                if retries >= max_retries:
                    raise Exception(f"经过 {max_retries} 次重试后仍然失败: {str(e)}")

                # 指数退避：等待 2^retries 秒后重试
                wait_time = 2 ** retries
                print(f"⏳ {wait_time} 秒后重试...")
                await asyncio.sleep(wait_time)

        return None

    async def run(self):
        """运行桥接服务主循环"""
        self.running = True
        print("🚀 Claude Code 桥接服务已启动")
        print(f"📥 轮询间隔: {self.config.poll_interval}ms")
        print(f"⏱️  超时时间: {self.config.claude_timeout}秒")
        print(f"🔄 最大重试: {self.config.max_retries}次")

        while self.running:
            try:
                # 从队列获取待处理的消息
                message = self.message_queue.get_next_pending(
                    MessageDirection.TO_CLAUDE
                )

                if message:
                    # 处理消息
                    await self.process_message(message)
                else:
                    # 没有消息时等待
                    await asyncio.sleep(self.config.poll_interval / 1000)

                # 定期清理旧消息
                self.message_queue.cleanup_old_messages(
                    self.config.message_retention_hours
                )

            except KeyboardInterrupt:
                print("\n⚠️  收到中断信号，正在停止...")
                self.running = False
                break
            except Exception as e:
                print(f"❌ 主循环错误: {e}")
                await asyncio.sleep(5)  # 出错后等待一段时间

        print("✓ Claude Code 桥接服务已停止")


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
