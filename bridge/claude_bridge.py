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
from shared.message_queue import MessageQueue, Message, MessageDirection, MessageStatus, MessageTag


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

        # ========== 检查消息标签，决定会话模式 ==========
        use_temp_session = False
        temp_session_key = None
        temp_session_id = None

        if message.tag in (MessageTag.TASK.value, MessageTag.REMINDER.value):
            # 任务或提醒标签：生成临时会话
            import uuid
            temp_session_key = f"temp_{message.id}"
            temp_session_id = str(uuid.uuid4())
            use_temp_session = True
            print(f"[消息 #{message.id}] 检测到特殊标签 '{message.tag}'，使用临时会话模式")
            print(f"[消息 #{message.id}] 临时 Session Key: {temp_session_key}")
            print(f"[消息 #{message.id}] 临时 Session ID: {temp_session_id}")

        # 获取或创建全局会话工作目录
        if use_temp_session:
            # 临时会话：使用全局工作目录（不创建独立目录）
            session_key = temp_session_key
            session_id = temp_session_id
            session_created = False  # 标记为首次模式
            working_dir = self.config.working_directory
        else:
            # 普通会话：使用原有逻辑
            session_key, session_id, session_created, working_dir = self.message_queue.get_or_create_session(
                self.config.working_directory
            )

        if session_key:
            print(f"[消息 #{message.id}] ========== 会话信息 ==========")
            print(f"[消息 #{message.id}] 会话 Key: {session_key}")
            print(f"[消息 #{message.id}] 会话 ID: {session_id}")
            print(f"[消息 #{message.id}] 会话已创建: {session_created}")
            print(f"[消息 #{message.id}] CLI 调用模式: {'--session-id (首次)' if use_temp_session else '-r (续会)'}")
            print(f"[消息 #{message.id}] 工作目录: {working_dir}")
            print(f"[消息 #{message.id}] ===============================")

        # 先更新状态为 PROCESSING（无 response），让 Discord Bot 知道正在调用 Claude
        self.message_queue.update_status(message.id, MessageStatus.PROCESSING)
        print(f"[消息 #{message.id}] 已更新状态为 PROCESSING")

        try:
            # 调用 Claude Code CLI（传递 message_id 用于实时更新状态）
            response = await self.call_claude_cli(
                message.content,
                session_key,
                session_id,
                session_created,
                working_dir,
                username=message.username,
                user_id=message.discord_user_id,
                is_dm=message.is_dm,
                message_id=message.id,
                channel_id=message.discord_channel_id,
                message_tag=message.tag  # 传递消息标签
            )

            if response:
                # 注意：会话已在 AI 开始工作时标记为已创建（call_claude_cli 内部处理）
                # 这里不需要再标记

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

    async def call_claude_cli(self, prompt: str, session_key: Optional[str] = None, session_id: Optional[str] = None, session_created: bool = False, working_dir: str = None, username: str = None, user_id: int = None, is_dm: bool = False, message_id: int = None, channel_id: int = None, message_tag: str = None) -> Optional[str]:
        """
        调用 Claude Code CLI
        使用 claude -p 参数进行非交互式调用
        使用流式输出实时检测 AI 开始工作

        Args:
            prompt: 用户提示词
            session_key: 会话 key（可选），用于保持对话上下文
            session_id: 会话 ID（可选），用于指定或创建 Claude Code 会话
            session_created: 会话是否已创建（首次为 False，后续为 True）
            working_dir: 工作目录，每个会话使用独立目录以保持对话历史
            username: 发送者用户名（频道模式下需要）
            user_id: 发送者用户 ID（频道模式下需要）
            is_dm: 是否为私聊消息
            message_id: 消息 ID，用于实时更新状态
            channel_id: 频道 ID（频道模式下需要）
            message_tag: 消息标签（task/reminder/default），用于设置特殊消息结构
        """
        import json

        retries = 0
        max_retries = self.config.max_retries

        # 使用传入的 working_dir，如果没有则使用默认配置
        cwd = working_dir or self.config.working_directory

        # ========== 根据消息标签构建独立的消息结构 ==========
        if message_tag == MessageTag.TASK.value:
            # 任务消息：结构化格式
            prompt = self._build_task_prompt(prompt, username, user_id, is_dm, channel_id)
            print(f"[消息标签] 使用任务消息结构")
        elif message_tag == MessageTag.REMINDER.value:
            # 提醒消息：结构化格式
            prompt = self._build_reminder_prompt(prompt, username, user_id, is_dm, channel_id)
            print(f"[消息标签] 使用提醒消息结构")
        else:
            # 默认消息：原有格式
            prompt = self._build_default_prompt(prompt, username, user_id, is_dm, channel_id, session_created)
        # ===================================

        while retries < max_retries:
            try:
                print(f"🤖 调用 Claude Code CLI (尝试 {retries + 1}/{max_retries})...")
                print(f"📝 提示词: {prompt[:80]}{'...' if len(prompt) > 80 else ''}")

                # 构建命令参数
                cmd_args = ['-p']  # print 模式：直接输出响应并退出
                cmd_args.append('--verbose')  # 启用详细输出
                cmd_args.append('--output-format')
                cmd_args.append('stream-json')  # 使用流式 JSON 输出

                # ========== 会话处理逻辑（动态从数据库读取状态）==========
                # 🔥 关键：每次重试都从数据库读取最新状态，而不是使用传入的固定值
                if session_key:
                    # 重新查询数据库，获取最新的 session_created 状态
                    import sqlite3
                    from datetime import datetime

                    conn = sqlite3.connect(self.message_queue.db_path)
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT session_created FROM sessions WHERE session_key = ?
                    """, (session_key,))
                    row = cursor.fetchone()
                    conn.close()

                    current_session_created = bool(row[0]) if row else False
                else:
                    current_session_created = session_created

                if current_session_created:
                    # 续会模式：使用 -r <session_id> 继续会话
                    cmd_args.extend(['-r', session_id])
                    print(f"🔄 [续会模式] 使用 -r {session_id} 继续会话")
                else:
                    # 首次调用：使用 --session-id 指定会话
                    if session_id:
                        cmd_args.extend(['--session-id', session_id])
                        print(f"🆕 [首次模式] 使用 --session-id {session_id} 创建新会话")
                    else:
                        print(f"⚠️  警告：session_id 为空，将使用 Claude 默认会话")
                # ===================================

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

                ai_started_notified = False  # 标记是否已通知 AI 开始工作
                response_lines = []

                try:
                    # 🔥 方案2：按块读取而不是按行读取，解决 "chunk is longer than limit" 问题
                    buffer = b''  # 缓冲区，累积不完整的数据
                    chunk_size = 4096  # 每次读取 4KB

                    while True:
                        # AI 开始工作前，使用较短超时(30秒)；AI 开始后，不限制超时
                        read_timeout = None if ai_started_notified else 30.0

                        try:
                            if read_timeout is None:
                                # AI 已开始，无超时限制
                                chunk = await process.stdout.read(chunk_size)
                            else:
                                # AI 未开始，有超时限制
                                chunk = await asyncio.wait_for(
                                    process.stdout.read(chunk_size),
                                    timeout=read_timeout
                                )
                        except asyncio.TimeoutError:
                            # AI 未开始就超时，真正超时
                            raise

                        if not chunk:  # EOF
                            break

                        # 将新数据添加到缓冲区
                        buffer += chunk

                        # 按行处理缓冲区中的数据
                        while b'\n' in buffer:
                            # 分割出一行
                            line_bytes, buffer = buffer.split(b'\n', 1)

                            if not line_bytes:
                                continue

                            line_str = line_bytes.decode('utf-8', errors='replace').strip()

                            if not line_str:
                                continue

                            # 解析 JSON 行
                            try:
                                data = json.loads(line_str)

                                # 检测 AI 开始工作事件
                                if not ai_started_notified and data.get('type') == 'system' and data.get('subtype') == 'init':
                                    print(f"🚀 [消息 #{message_id}] AI 开始工作")
                                    # 立即更新状态为 AI_STARTED
                                    if message_id:
                                        self.message_queue.update_status(message_id, MessageStatus.AI_STARTED)

                                    # 🔥 关键修改：AI 开始工作时就标记会话为已创建（写入数据库）
                                    if not session_created and session_key:
                                        self.message_queue.mark_session_created(session_key)
                                        print(f"✅ [消息 #{message_id}] 会话已在 AI 开始工作时标记为创建")

                                    ai_started_notified = True

                                # 收集 assistant 消息作为响应
                                elif data.get('type') == 'assistant' and data.get('message'):
                                    message_data = data.get('message', {})
                                    if message_data.get('content'):
                                        for content_item in message_data['content']:
                                            if content_item.get('type') == 'text':
                                                text = content_item.get('text', '')
                                                response_lines.append(text)

                            except json.JSONDecodeError:
                                # 不是 JSON 行，可能是普通文本输出
                                pass

                    # 循环结束后，处理缓冲区剩余的数据（如果有）
                    if buffer:
                        try:
                            line_str = buffer.decode('utf-8', errors='replace').strip()
                            if line_str:
                                data = json.loads(line_str)

                                if not ai_started_notified and data.get('type') == 'system' and data.get('subtype') == 'init':
                                    print(f"🚀 [消息 #{message_id}] AI 开始工作")
                                    if message_id:
                                        self.message_queue.update_status(message_id, MessageStatus.AI_STARTED)

                                    if not session_created and session_key:
                                        self.message_queue.mark_session_created(session_key)
                                        print(f"✅ [消息 #{message_id}] 会话已在 AI 开始工作时标记为创建")

                                    ai_started_notified = True

                                elif data.get('type') == 'assistant' and data.get('message'):
                                    message_data = data.get('message', {})
                                    if message_data.get('content'):
                                        for content_item in message_data['content']:
                                            if content_item.get('type') == 'text':
                                                text = content_item.get('text', '')
                                                response_lines.append(text)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            pass

                    # 等待进程结束
                    if ai_started_notified:
                        # AI 已开始，无超时限制，等待多久都可以
                        returncode = await process.wait()
                    else:
                        # AI 未开始就结束了，使用配置的超时
                        returncode = await asyncio.wait_for(
                            process.wait(),
                            timeout=self.config.claude_timeout
                        )

                    if returncode == 0:
                        response = '\n'.join(response_lines).strip()

                        print(f"✅ Claude 响应成功 (长度: {len(response) if response else 0} 字符)")
                        return response if response else "(Claude 没有返回文本响应)"
                    else:
                        # 命令执行失败，读取 stderr
                        stderr_output = await process.stderr.read()
                        error_output = stderr_output.decode('utf-8', errors='replace').strip()
                        error_msg = f"Claude Code 返回错误码 {returncode}"
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

    def _build_sender_info(self, username: str, user_id: int, is_dm: bool, channel_id: int) -> str:
        """构建发送者信息"""
        if is_dm:
            return f"{username}（{user_id}）在私聊中说："
        elif channel_id:
            return f"{username}（{user_id}）在频道（{channel_id}）中说："
        else:
            return f"{username}（{user_id}）说："

    def _build_task_prompt(self, content: str, username: str, user_id: int, is_dm: bool, channel_id: int) -> str:
        """构建任务消息结构"""
        sender_info = self._build_sender_info(username, user_id, is_dm, channel_id)
        if is_dm:
            return f"""🔔 定时任务已触发！
            
任务创建人：{username}（{user_id}）
任务内容：{content}

请按以下步骤执行：
1、仔细阅读并遵守 CLAUDE.md 中的要求，按要求进行会话启动流程；
2、理解任务需求；
3、加载相关Skill或Mcp服务；
4、直接执行并完成任务；
5、完成后回复消息。"""
        
        else:return f"""🔔 定时任务已触发！
        
任务创建人：{username}（{user_id}） 
任务创建频道：{channel_id}
任务内容：{content}

请按以下步骤执行：
1、仔细阅读并遵守 CLAUDE.md 中的要求，按要求进行会话启动流程；
2、理解任务需求；
3、加载相关Skill或Mcp服务；
4、直接执行并完成任务；
5、完成后回复消息。"""

    def _build_reminder_prompt(self, content: str, username: str, user_id: int, is_dm: bool, channel_id: int) -> str:
        """构建提醒消息结构"""
        sender_info = self._build_sender_info(username, user_id, is_dm, channel_id)
        if is_dm:
            return f"""🔔 定时提醒已触发！
            
提醒创建人：{username}（{user_id}）
提醒内容：{content}

请按以下步骤执行：
1、仔细阅读并遵守 CLAUDE.md 中的要求，按要求进行会话启动流程；
2、直接回复需要提醒的内容。"""
        
        else:return f"""🔔 定时提醒已触发！
        
提醒创建人：{username}（{user_id}）
提醒内容：{content}
提醒创建频道：{channel_id}

请按以下步骤执行：
1、仔细阅读并遵守 CLAUDE.md 中的要求，按要求进行会话启动流程；
2、直接回复需要提醒的内容。"""

    def _build_default_prompt(self, content: str, username: str, user_id: int, is_dm: bool, channel_id: int, session_created: bool) -> str:
        """构建默认消息结构（原有格式）"""
        sender_info = self._build_sender_info(username, user_id, is_dm, channel_id)

        # 如果是首次对话且启用了提示词注入，添加前缀
        if self.config.auto_load_enabled and not session_created:
            return f"{self.config.auto_load_prompt_text}{sender_info}{content}"
        else:
            return f"{sender_info}{content}"

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
        """运行桥接服务主循环"""
        self.running = True
        print("🚀 Claude Code 桥接服务已启动")
        print(f"📥 轮询间隔: {self.config.poll_interval}ms")
        print(f"⏱️  超时时间: {self.config.claude_timeout}秒")
        print(f"🔄 最大重试: {self.config.max_retries}次")

        # 启动时清理旧的 PENDING 消息
        await self.cleanup_pending_messages()

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
