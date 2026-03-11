"""
Session Worker - 每个 session 的独立消息处理器
负责串行处理同一个 session 的消息
"""
import asyncio
import time
import uuid
from typing import Optional
from pathlib import Path

from shared.config import Config
from shared.message_queue import MessageQueue, Message, MessageStatus, MessageTag


class SessionWorker:
    """每个 session 的独立 worker"""

    def __init__(self, session_key: str, config: Config, message_queue: MessageQueue):
        """
        初始化 Session Worker

        Args:
            session_key: 会话标识（如 "global", "channel_123", "dm_456"）
            config: 配置对象
            message_queue: 消息队列对象
        """
        self.session_key = session_key
        self.config = config
        self.message_queue = message_queue

        # 消息队列（asyncio.Queue 用于异步处理）
        self.queue = asyncio.Queue()

        # Worker 状态
        self.task: Optional[asyncio.Task] = None  # asyncio 任务
        self.running = False  # 是否正在运行
        self.current_message_id: Optional[int] = None  # 当前正在处理的消息 ID
        self.last_activity_time: float = time.time()  # 最后活动时间

    async def start(self):
        """启动 worker 处理循环"""
        if not self.running:
            self.running = True
            self.task = asyncio.create_task(self._run())
            print(f"✅ Worker 已启动: {self.session_key}")

    async def stop(self):
        """停止 worker"""
        self.running = False

        # 等待当前消息处理完成
        if self.task and not self.task.done():
            # 设置超时，避免无限等待
            try:
                await asyncio.wait_for(self.task, timeout=5.0)
            except asyncio.TimeoutError:
                print(f"⚠️  Worker {self.session_key} 停止超时，强制取消")
                self.task.cancel()
                try:
                    await self.task
                except asyncio.CancelledError:
                    pass

        print(f"🛑 Worker 已停止: {self.session_key}")

    async def enqueue(self, message: Message):
        """
        将消息加入处理队列

        Args:
            message: 要处理的消息
        """
        await self.queue.put(message)
        self.last_activity_time = time.time()  # 更新活动时间

    async def _run(self):
        """Worker 的主循环"""
        print(f"🔄 Worker {self.session_key} 开始运行")

        while self.running:
            try:
                # 从队列获取消息（带超时，避免永久阻塞）
                try:
                    message = await asyncio.wait_for(
                        self.queue.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    # 超时继续，检查 running 状态
                    continue

                # 更新活动时间
                self.last_activity_time = time.time()

                # 处理消息
                await self._process_message(message)

            except asyncio.CancelledError:
                # 任务被取消，正常退出
                break
            except Exception as e:
                print(f"❌ Worker {self.session_key} 处理消息时出错: {e}")
                import traceback
                traceback.print_exc()

        print(f"✅ Worker {self.session_key} 已退出")

    async def _process_message(self, message: Message) -> bool:
        """
        处理单条消息（从 ClaudeBridge 迁移的逻辑）

        Args:
            message: 要处理的消息

        Returns:
            是否处理成功
        """
        self.current_message_id = message.id
        print(f"[Worker {self.session_key}] [消息 #{message.id}] 开始处理: {message.content[:50]}...")

        # ========== 检查消息标签，决定会话模式 ==========
        use_temp_session = False
        temp_session_key = None
        temp_session_id = None

        if message.tag in (MessageTag.TASK.value, MessageTag.REMINDER.value):
            # 任务或提醒标签：生成临时会话
            temp_session_key = f"temp_{message.id}"
            temp_session_id = str(uuid.uuid4())
            use_temp_session = True
            print(f"[消息 #{message.id}] 检测到特殊标签 '{message.tag}'，使用临时会话模式")
            print(f"[消息 #{message.id}] 临时 Session Key: {temp_session_key}")
            print(f"[消息 #{message.id}] 临时 Session ID: {temp_session_id}")

        # 获取或创建会话工作目录
        if use_temp_session:
            # 临时会话（task/reminder）：使用临时 session_id 和基础工作目录
            session_key = temp_session_key
            session_id = temp_session_id
            session_created = False
            working_dir = self.config.working_directory
        else:
            # 普通会话（default）：根据模式获取 session
            session_key, session_id, session_created, working_dir = self.message_queue.get_or_create_session(
                self.config.working_directory,
                channel_id=message.discord_channel_id,
                user_id=message.discord_user_id,
                is_dm=message.is_dm,
                use_temp_session=False,
                temp_session_key=None,
                session_mode=self.config.session_mode
            )

        if session_key:
            print(f"[消息 #{message.id}] ========== 会话信息 ==========")
            print(f"[消息 #{message.id}] 会话 Key: {session_key}")
            print(f"[消息 #{message.id}] 会话 ID: {session_id}")
            print(f"[消息 #{message.id}] 会话已创建: {session_created}")
            print(f"[消息 #{message.id}] CLI 调用模式: {'--session-id (首次)' if use_temp_session else '-r (续会)'}")
            print(f"[消息 #{message.id}] 工作目录: {working_dir}")
            print(f"[消息 #{message.id}] ===============================")

        # 先更新状态为 PROCESSING
        self.message_queue.update_status(message.id, MessageStatus.PROCESSING)
        print(f"[消息 #{message.id}] 已更新状态为 PROCESSING")

        try:
            # 调用 Claude Code CLI
            response = await self._call_claude_cli(
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
                message_tag=message.tag,
                attachments=message.attachments
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
                    MessageStatus.COMPLETED,
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
        finally:
            self.current_message_id = None

    async def _call_claude_cli(
        self,
        prompt: str,
        session_key: Optional[str],
        session_id: Optional[str],
        session_created: bool,
        working_dir: str,
        username: str = None,
        user_id: int = None,
        is_dm: bool = False,
        message_id: int = None,
        channel_id: int = None,
        message_tag: str = None,
        attachments: list = None
    ) -> Optional[str]:
        """
        调用 Claude Code CLI（从 ClaudeBridge 迁移）

        这个方法实现了和 ClaudeBridge.call_claude_cli 相同的逻辑，
        但在 SessionWorker 中运行，实现不同 session 的并发处理。
        """
        import json

        retries = 0
        max_retries = self.config.max_retries

        # 使用传入的 working_dir
        cwd = working_dir or self.config.working_directory

        # 构建提示词
        if message_tag == MessageTag.TASK.value:
            prompt = self._build_task_prompt(prompt, username, user_id, is_dm, channel_id)
            print(f"[消息标签] 使用任务消息结构")
        elif message_tag == MessageTag.REMINDER.value:
            prompt = self._build_reminder_prompt(prompt, username, user_id, is_dm, channel_id)
            print(f"[消息标签] 使用提醒消息结构")
        else:
            sender_info = self._build_sender_info(username, user_id, is_dm, channel_id, attachments)

            if self.config.auto_load_enabled and not session_created:
                prompt = f"{self.config.auto_load_prompt_text}{sender_info}{prompt}"
            else:
                prompt = f"{sender_info}{prompt}"

        while retries < max_retries:
            try:
                print(f"🤖 [Worker {self.session_key}] 调用 Claude Code CLI (尝试 {retries + 1}/{max_retries})...")
                print(f"📝 提示词: {prompt[:80]}{'...' if len(prompt) > 80 else ''}")

                # 构建命令参数
                cmd_args = ['-p']
                cmd_args.append('--verbose')
                cmd_args.append('--output-format')
                cmd_args.append('stream-json')

                # 会话处理逻辑
                if session_key:
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
                    cmd_args.extend(['-r', session_id])
                    print(f"🔄 [续会模式] 使用 -r {session_id} 继续会话")
                else:
                    if session_id:
                        cmd_args.extend(['--session-id', session_id])
                        print(f"🆕 [首次模式] 使用 --session-id {session_id} 创建新会话")
                    else:
                        print(f"⚠️  警告：session_id 为空，将使用 Claude 默认会话")

                cmd_args.append(prompt)

                # 使用 claude 命令进行非交互式调用
                process = await asyncio.create_subprocess_exec(
                    self.config.claude_executable,
                    *cmd_args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd
                )

                ai_started_notified = False
                response_lines = []
                partial_response = ""
                last_update_time = 0
                aborted = False

                try:
                    # 按块读取
                    buffer = b''
                    chunk_size = 4096

                    while True:
                        # 检查是否收到中止信号
                        if message_id and self.message_queue.is_aborting(message_id):
                            print(f"🛑 [消息 #{message_id}] 检测到中止请求，正在终止进程...")
                            aborted = True
                            break

                        read_timeout = None if ai_started_notified else float(self.config.claude_timeout)

                        try:
                            if read_timeout is None:
                                chunk = await process.stdout.read(chunk_size)
                            else:
                                chunk = await asyncio.wait_for(
                                    process.stdout.read(chunk_size),
                                    timeout=read_timeout
                                )
                        except asyncio.TimeoutError:
                            raise Exception(f"Claude Code 启动超时（超过 {self.config.claude_timeout} 秒）")

                        if not chunk:
                            break

                        buffer += chunk

                        while b'\n' in buffer:
                            line_bytes, buffer = buffer.split(b'\n', 1)

                            if not line_bytes:
                                continue

                            line_str = line_bytes.decode('utf-8', errors='replace').strip()

                            if not line_str:
                                continue

                            try:
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

                                                partial_response = '\n'.join(response_lines)
                                                current_time = time.time()
                                                if message_id and current_time - last_update_time > 0.1:
                                                    self.message_queue.update_streaming_response(message_id, partial_response)
                                                    last_update_time = current_time

                            except json.JSONDecodeError:
                                pass

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

                                                partial_response = '\n'.join(response_lines)
                                                current_time = time.time()
                                                if message_id and current_time - last_update_time > 0.1:
                                                    self.message_queue.update_streaming_response(message_id, partial_response)
                                                    last_update_time = current_time

                        except (json.JSONDecodeError, UnicodeDecodeError):
                            pass

                    if aborted:
                        print(f"🛑 [消息 #{message_id}] 正在中止进程...")
                        process.terminate()
                        try:
                            returncode = await asyncio.wait_for(process.wait(), timeout=5.0)
                            print(f"✅ [消息 #{message_id}] 进程已优雅终止 (退出码: {returncode})")
                        except asyncio.TimeoutError:
                            print(f"⚠️ [消息 #{message_id}] 进程未在 5 秒内退出，强制终止...")
                            process.kill()
                            await process.wait()
                            print(f"✅ [消息 #{message_id}] 进程已强制终止")

                        if message_id:
                            partial_response = '\n'.join(response_lines).strip()
                            abort_msg = partial_response if partial_response else "(响应被用户中止)"
                            self.message_queue.update_status(
                                message_id,
                                MessageStatus.COMPLETED,
                                response=abort_msg
                            )
                            print(f"✅ [消息 #{message_id}] 已更新消息状态为 COMPLETED（中止）")

                        return partial_response if partial_response else "(响应被用户中止)"

                    returncode = await process.wait()

                    if returncode == 0:
                        response = '\n'.join(response_lines).strip()

                        if message_id and response:
                            self.message_queue.update_streaming_response(message_id, response)

                        print(f"✅ Claude 响应成功 (长度: {len(response) if response else 0} 字符)")
                        return response if response else "(Claude 没有返回文本响应)"
                    else:
                        stderr_output = await process.stderr.read()
                        error_output = stderr_output.decode('utf-8', errors='replace').strip()
                        error_msg = f"Claude Code 返回错误码 {returncode}"
                        if error_output:
                            error_msg += f": {error_output}"

                        raise Exception(error_msg)

                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                    raise Exception(f"Claude Code 启动超时（超过 {self.config.claude_timeout} 秒）")

            except FileNotFoundError:
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

                wait_time = 2 ** retries
                print(f"⏳ {wait_time} 秒后重试...")
                await asyncio.sleep(wait_time)

        return None

    def _build_task_prompt(self, content: str, username: str, user_id: int, is_dm: bool, channel_id: int) -> str:
        """构建任务消息结构"""
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
        else:
            return f"""🔔 定时任务已触发！

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
        if is_dm:
            return f"""🔔 定时提醒已触发！

提醒创建人：{username}（{user_id}）
提醒内容：{content}

请按以下步骤执行：
1、仔细阅读并遵守 CLAUDE.md 中的要求，按要求进行会话启动流程；
2、直接回复需要提醒的内容。"""
        else:
            return f"""🔔 定时提醒已触发！

提醒创建人：{username}（{user_id}）
提醒内容：{content}
提醒创建频道：{channel_id}

请按以下步骤执行：
1、仔细阅读并遵守 CLAUDE.md 中的要求，按要求进行会话启动流程；
2、直接回复需要提醒的内容。"""

    def _build_sender_info(self, username: str, user_id: int, is_dm: bool, channel_id: int, attachments: list = None) -> str:
        """构建发送者信息"""
        sender_base = f"{username}（{user_id}）"

        if attachments:
            filenames_str = '、'.join([a.filename for a in attachments])

            if is_dm:
                sender_info = f"{sender_base}在私聊中引用了文件名为 {filenames_str} 的已下载附件，并说："
            elif channel_id:
                sender_info = f"{sender_base}在频道（{channel_id}）中引用了文件名为 {filenames_str} 的已下载附件，并说："
            else:
                sender_info = f"{sender_base}引用了文件名为 {filenames_str} 的已下载附件，并说："
        else:
            if is_dm:
                sender_info = f"{sender_base}在私聊中说："
            elif channel_id:
                sender_info = f"{sender_base}在频道（{channel_id}）中说："
            else:
                sender_info = f"{sender_base}说："

        return sender_info

    def is_idle(self, current_time: float, timeout: int) -> bool:
        """
        检查 worker 是否空闲

        Args:
            current_time: 当前时间
            timeout: 空闲超时时间（秒）

        Returns:
            是否空闲（超过超时时间没有活动）
        """
        if timeout == 0:
            return False  # 超时时间为 0 表示永不清理

        time_since_last_activity = current_time - self.last_activity_time
        queue_empty = self.queue.empty()

        return queue_empty and time_since_last_activity > timeout

    def get_status(self) -> dict:
        """
        获取 worker 状态信息

        Returns:
            包含状态信息的字典
        """
        return {
            "session_key": self.session_key,
            "running": self.running,
            "queue_size": self.queue.qsize(),
            "current_message_id": self.current_message_id,
            "last_activity_time": self.last_activity_time,
            "idle_time": time.time() - self.last_activity_time
        }
