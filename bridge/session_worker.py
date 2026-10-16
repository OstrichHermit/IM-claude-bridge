"""
Session Worker - 每个 session 的独立消息处理器
负责串行处理同一个 session 的消息
"""
import asyncio
import os
import re
import subprocess
import sys
import time
import uuid
import traceback
from typing import Optional
from pathlib import Path

from shared.config import Config
from shared.message_queue import MessageQueue, Message, MessageStatus, MessageTag
from shared.logger import get_logger
from datetime import datetime


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
        self._log = get_logger(f"Worker-{session_key}", "bridge")

        # AskUserQuestion 拦截信号（_call_claude_cli 写入，_process_message 读取后清空）
        self._pending_ask_payload: Optional[dict] = None

    async def start(self):
        """启动 worker 处理循环"""
        if not self.running:
            self.running = True
            self.task = asyncio.create_task(self._run())
            self._log.log(f"✅ Worker 已启动: {self.session_key}")

    async def stop(self):
        """停止 worker"""
        self.running = False

        # 等待当前消息处理完成
        if self.task and not self.task.done():
            # 设置超时，避免无限等待
            try:
                await asyncio.wait_for(self.task, timeout=5.0)
            except asyncio.TimeoutError:
                self._log.log(f"⚠️  Worker {self.session_key} 停止超时，强制取消")
                self.task.cancel()
                try:
                    await self.task
                except asyncio.CancelledError:
                    pass

        self._log.log(f"🛑 Worker 已停止: {self.session_key}")

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
        self._log.log(f"🔄 Worker {self.session_key} 开始运行")

        while self.running:
            try:
                # ====== AskUserQuestion 挂起检查 ======
                # 如果 session 有 pending_ask，不消费新消息（让 queue 自动累积）。
                # 等 Discord 端 clear_pending_ask 后才会继续处理。
                if self.message_queue.get_pending_ask(self.session_key):
                    await asyncio.sleep(0.5)  # 短暂等待，避免 busy loop
                    continue

                # 从队列获取消息（带超时，避免永久阻塞）
                try:
                    message = await asyncio.wait_for(
                        self.queue.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    # 超时继续，检查 running 状态
                    continue

                # 更新活动时间（消息出队时）
                self.last_activity_time = time.time()

                # ====== 排队消息合并：把队列中等待的其余消息并入本条一次性处理 ======
                await self._merge_queued_messages(message)

                # 处理消息
                await self._process_message(message)

                # 更新活动时间（消息处理完成后，确保完整的空闲窗口期）
                self.last_activity_time = time.time()

            except asyncio.CancelledError:
                # 任务被取消，正常退出
                break
            except Exception as e:
                self._log.log(f"❌ Worker {self.session_key} 处理消息时出错: {e}")
                self._log.log(traceback.format_exc())

        self._log.log(f"✅ Worker {self.session_key} 已退出")

    def _drain_queue(self) -> list:
        """取出队列中当前所有等待的消息（worker 按 session 隔离，队列内全是本 session 消息）"""
        drained = []
        while True:
            try:
                drained.append(self.queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return drained

    async def _merge_queued_messages(self, message: Message):
        """把队列中等待的其余消息合并进当前消息，一次性处理

        当前消息出队时，立即收走队列中已排队的其余消息（不等待）合并处理；
        晚到的消息正常排队，等本条处理完再被取出合并。
        同时捞回此前被引导模式自动打断、内容未送达 AI 的消息（避免连发时丢消息）。

        拼接按发送时间排列：更早块（捞回）→ 主消息 → 更晚块（合并），
        块内条目也是旧→新。合并时把完整条目时序持久化到 recovered_groups，
        本条若再被自动打断，下一条捞回时直接按存储还原（不依赖字符串解析）。
        被合并消息标记 SKIPPED 并写入一条空文本序列，让发送器走完完成流程
        （停 typing、标 COMPLETED），复用现有清理机制。
        """
        try:
            merged = self._drain_queue()
            # 捞回被自动打断、内容未送达 AI 的消息
            recovered = self.message_queue.get_auto_aborted_for_merge(message)

            if not merged and not recovered:
                return

            self_net = message.content.strip()
            parts = []
            groups = []

            if recovered:
                entries = []
                for m in recovered:
                    entries.extend(self._flatten_recovered_entries(m))
                entry_count = sum(e.count(" 说：") for e in entries)
                parts.append(
                    f"[此前被自动打断的消息] 以下是引导模式下被后续消息打断、AI 未处理到的 {entry_count} 条用户消息，请一并了解："
                    + "\n" + "\n".join(entries)
                )
                groups.extend(entries)

            if self_net:
                parts.append(self_net)
                groups.append(f"{message.username} 说：{self_net}")
                if message.attachments:
                    for att in message.attachments:
                        groups.append(f"（附件：{att.local_filename or att.filename}，路径 {att.url}）")

            if merged:
                merged_lines = []
                for m in merged:
                    merged_lines.append(f"{m.username} 说：{m.content}")
                    if m.attachments:
                        for att in m.attachments:
                            merged_lines.append(f"（附件：{att.local_filename or att.filename}，路径 {att.url}）")
                parts.append(
                    f"[排队消息合并] 以下是本条消息处理排队期间用户连续发来的 {len(merged)} 条消息，请一并处理："
                    + "\n" + "\n".join(merged_lines)
                )
                groups.extend(merged_lines)

            message.content = "\n\n".join(parts)
            # 持久化合并内容与完整条目时序：本条若再被自动打断，下一条捞回时才能带回完整链条
            self.message_queue.update_message_content(message.id, message.content, recovered_groups=groups)

            if recovered:
                self._log.log(f"🔗 [消息 #{message.id}] 已捞回 {len(recovered)} 条被自动打断的消息合并处理: {[m.id for m in recovered]}")

            # 微信回复使用最新的 context_token（最后一条消息携带；无排队合并时沿用本条自己的）
            if message.channel_type == 'weixin' and merged and merged[-1].context_token:
                message.context_token = merged[-1].context_token

            for m in merged:
                self.message_queue.update_status(
                    m.id, MessageStatus.SKIPPED,
                    error=f"已合并到消息 #{message.id} 一起处理"
                )
                self.message_queue.add_message_sequence(m.id, 0, 0, 'text', {'text': ''})
            if merged:
                self._log.log(f"🔗 [消息 #{message.id}] 已合并 {len(merged)} 条排队消息一起处理: {[m.id for m in merged]}")

        except Exception as e:
            self._log.log(f"⚠️ 排队消息合并失败（按原样单独处理）: {e}")

    @staticmethod
    def _flatten_recovered_entries(m: Message) -> list:
        """把被捞回消息的累积内容拍平为「X 说：」条目列表（旧→新）

        优先用合并时持久化的 recovered_groups（完整条目时序）；
        无（未合并过或旧数据）时按字符串标记解析降级：旧布局 content 为
        "净原文 + 若干历史块"（越靠后的块越早），反转后即旧→新。
        """
        if m.recovered_groups:
            return list(m.recovered_groups)
        parts = m.content.split("\n\n[此前被自动打断的消息] ")
        entries = []
        net = parts[0].strip()
        if net:
            entries.append(f"{m.username} 说：{net}")
            if m.attachments:
                for att in m.attachments:
                    entries.append(f"（附件：{att.local_filename or att.filename}，路径 {att.url}）")
        older = []
        for part in parts[1:]:
            if not part.strip():
                continue
            body = part.split("\n", 1)
            if body[0].startswith("以下是引导模式下被后续消息打断、AI 未处理到的 ") and len(body) > 1:
                body_text = body[1].strip()
            else:
                # 非标准块头（如用户原文里碰巧含标记），原样保留不丢内容
                body_text = part.strip()
            if body_text:
                older.append(body_text)
        # 旧布局中块体越靠后越早，反转得到旧→新，排在净原文（本条，最新）之前
        return list(reversed(older)) + entries

    async def _process_message(self, message: Message) -> bool:
        """
        处理单条消息（从 ClaudeBridge 迁移的逻辑）

        Args:
            message: 要处理的消息

        Returns:
            是否处理成功
        """
        self.current_message_id = message.id

        # ========== 检查消息标签，决定会话模式 ==========
        use_temp_session = False
        temp_session_key = None
        temp_session_id = None

        if message.tag in (MessageTag.TASK.value, MessageTag.REMINDER.value):
            # 任务或提醒标签：生成临时会话
            temp_session_key = f"temp_{message.id}"
            temp_session_id = str(uuid.uuid4())
            use_temp_session = True

        # 获取或创建会话工作目录
        if use_temp_session:
            # 临时会话（task/reminder）：使用临时 session_id 和基础工作目录
            session_key = temp_session_key
            session_id = temp_session_id
            session_created = False
            working_dir = self.config.working_directory
        else:
            # 普通会话（default）：获取 session（固定使用 session 模式）
            session_key, session_id, session_created, working_dir = self.message_queue.get_or_create_session(
                self.config.working_directory,
                channel_id=message.discord_channel_id,
                user_id=message.discord_user_id,
                is_dm=message.is_dm,
                use_temp_session=False,
                temp_session_key=None
            )

        # 先更新状态为 PROCESSING
        self.message_queue.update_status(message.id, MessageStatus.PROCESSING)

        # 清空上一次的 pending_ask 信号（避免遗留）
        self._pending_ask_payload = None

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
                attachments=message.attachments,
                channel_type=message.channel_type,
                pending_history=message.pending_history
            )

            # ====== AskUserQuestion 拦截后处理：保留 PROCESSING + 写 pending_ask ======
            if self._pending_ask_payload is not None:
                pending_payload = self._pending_ask_payload
                # 附加 message_id（Discord 端可能用到）
                pending_payload['message_id'] = message.id

                # 写挂起状态到 sessions 表
                self.message_queue.set_pending_ask(session_key, pending_payload)

                # 保留 message.status = PROCESSING，附带部分响应（如果有）
                partial = response if response else ""
                self.message_queue.update_status(
                    message.id,
                    MessageStatus.PROCESSING,
                    response=partial if partial else None
                )

                self._log.log(
                    f"[AskUserQuestion] Session {session_key} suspended, "
                    f"waiting for user answer (message #{message.id})"
                )

                # 清空信号，避免下次消息误判
                self._pending_ask_payload = None
                return True  # 处理"成功"（被拦截不视为失败）

            # 引导模式自动打断且无部分响应：中止分支已标记 SKIPPED（待捞回），直接返回（不写占位响应）
            if response is None and message.id:
                status = self.message_queue.get_message_status(message.id)
                if status in (MessageStatus.COMPLETED, MessageStatus.SKIPPED):
                    self._log.log(f"[消息 #{message.id}] 自动打断静默完成，跳过响应发送")
                    return True

            if response:
                # 判断是否为外部消息（task/reminder）
                if message.is_external:
                    # 外部消息：直接标记为完成（不需要 Discord Bot 发送）
                    self.message_queue.update_status(
                        message.id,
                        MessageStatus.COMPLETED,
                        response=response
                    )
                    self._log.log(f"[消息 #{message.id}] 处理成功（外部消息，已完成）")
                else:
                    # 正常消息：保持 PROCESSING 状态，等待 Discord Bot 发送
                    self.message_queue.update_status(
                        message.id,
                        MessageStatus.PROCESSING,  # 保持 PROCESSING 状态，等待 Discord Bot 发送
                        response=response
                    )
                    self._log.log(f"[消息 #{message.id}] 处理成功")
                return True
            else:
                # 响应为空
                self.message_queue.update_status(
                    message.id,
                    MessageStatus.COMPLETED,
                    response="(Claude 没有返回响应)"
                )
                self._log.log(f"[消息 #{message.id}] 处理完成（无响应）")
                return True

        except Exception as e:
            error_msg = f"处理失败: {str(e)}"
            self._log.log(f"❌ [消息 #{message.id}] {error_msg}")

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
        attachments: list = None,
        channel_type: str = 'discord',
        pending_history: str = None
    ) -> Optional[str]:
        """
        调用 Claude Code CLI（从 ClaudeBridge 迁移）

        这个方法实现了和 ClaudeBridge.call_claude_cli 相同的逻辑，
        但在 SessionWorker 中运行，实现不同 session 的并发处理。
        """
        import json

        retries = 0
        max_attempts = self.config.max_attempts

        # 使用传入的 working_dir
        cwd = working_dir or self.config.working_directory

        # 构建提示词
        if message_tag == MessageTag.TASK.value:
            prompt = self._build_task_prompt(prompt, username, user_id, is_dm, channel_id, channel_type)
        elif message_tag == MessageTag.REMINDER.value:
            prompt = self._build_reminder_prompt(prompt, username, user_id, is_dm, channel_id, channel_type)
        else:
            sender_info = self._build_sender_info(username, user_id, is_dm, channel_id, attachments, channel_type)

            # retain 模式：未@历史补发块拼在最前面（sender_info 之前），与当前消息间以空行分隔
            history_prefix = f"{pending_history}\n\n" if pending_history else ""
            if self.config.auto_load_enabled and not session_created:
                prompt = f"{self.config.auto_load_prompt_text}{history_prefix}{sender_info}{prompt}"
            else:
                prompt = f"{history_prefix}{sender_info}{prompt}"

        while retries < max_attempts:
            try:
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
                else:
                    if session_id:
                        cmd_args.extend(['--session-id', session_id])
                    else:
                        self._log.log(f"⚠️  警告：session_id 为空，将使用 Claude 默认会话")

                cmd_args.append(prompt)

                # 使用 claude 命令进行非交互式调用
                # Windows 下使用 CREATE_NO_WINDOW 防止弹出窗口
                if sys.platform == 'win32':
                    process = await asyncio.create_subprocess_exec(
                        self.config.claude_executable,
                        *cmd_args,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=cwd,
                        creationflags=subprocess.CREATE_NO_WINDOW
                    )
                else:
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
                sequence_index = 0
                aborted = False
                pending_ask_signal = None  # AskUserQuestion 拦截信号

                try:
                    # 按块读取
                    buffer = b''
                    chunk_size = 4096

                    def process_json_object(data: dict):
                        """处理流式输出的单个 JSON 对象（内层函数，直接捕获外层变量）"""
                        nonlocal ai_started_notified, response_lines, last_update_time, sequence_index, pending_ask_signal

                        if not ai_started_notified and data.get('type') == 'system' and data.get('subtype') == 'init':
                            self._log.log(f"🚀 [消息 #{message_id}] AI 开始工作")
                            if message_id:
                                self.message_queue.update_status(message_id, MessageStatus.AI_STARTED)
                            if not session_created and session_key:
                                self.message_queue.mark_session_created(session_key)
                            ai_started_notified = True

                        elif data.get('type') == 'user' and message_id:
                            message_data = data.get('message', {})
                            if message_data.get('content'):
                                for content_item in message_data['content']:
                                    if content_item.get('type') == 'tool_result':
                                        tool_use_id = content_item.get('tool_use_id', '')
                                        is_error = content_item.get('is_error', False)
                                        tool_uses = self.message_queue.get_tool_uses(message_id)
                                        tool_use_index = None
                                        for i, tool_use in enumerate(tool_uses):
                                            if tool_use.get('id') == tool_use_id:
                                                tool_use_index = i
                                                break
                                        if tool_use_index is not None:
                                            self.message_queue.save_tool_use_result(message_id, tool_use_index, not is_error)

                        elif data.get('type') == 'assistant' and data.get('message'):
                            message_data = data.get('message', {})
                            if message_data.get('content'):
                                content_blocks = message_data['content']
                                sequence_index = self.message_queue.get_max_sequence_index(message_id) + 1
                                # 查询 per-channel 设置，没有则 fallback 到全局配置默认值
                                if channel_type == 'weixin':
                                    need_split = self.message_queue.get_channel_message_splitting(
                                        channel_id, default=self.config.weixin_message_splitting_enabled
                                    )
                                else:
                                    need_split = self.message_queue.get_channel_message_splitting(
                                        channel_id, default=self.config.enable_message_splitting
                                    )

                                for block_index, content_item in enumerate(content_blocks):
                                    block_type = content_item.get('type')

                                    if block_type == 'text':
                                        text = content_item.get('text', '')
                                        self.message_queue.add_content_block(message_id, block_index, 'text', {'text': text})
                                        if need_split:
                                            text_parts = text.split('\n\n')
                                            for part in text_parts:
                                                if part.strip():
                                                    segments = self._parse_sticker_segments(part.strip())
                                                    for segment in segments:
                                                        if segment["type"] == "text" and segment["content"].strip():
                                                            self.message_queue.add_message_sequence(
                                                                message_id, sequence_index, block_index, 'text',
                                                                {'text': segment["content"].strip()}
                                                            )
                                                            sequence_index += 1
                                                        elif segment["type"] == "sticker":
                                                            self.message_queue.add_message_sequence(
                                                                message_id, sequence_index, block_index, 'sticker',
                                                                {'file_path': segment["file_path"]}
                                                            )
                                                            sequence_index += 1
                                        else:
                                            if text.strip():
                                                segments = self._parse_sticker_segments(text.strip())
                                                for segment in segments:
                                                    if segment["type"] == "text" and segment["content"].strip():
                                                        self.message_queue.add_message_sequence(
                                                            message_id, sequence_index, block_index, 'text',
                                                            {'text': segment["content"].strip()}
                                                        )
                                                        sequence_index += 1
                                                    elif segment["type"] == "sticker":
                                                        self.message_queue.add_message_sequence(
                                                            message_id, sequence_index, block_index, 'sticker',
                                                            {'file_path': segment["file_path"]}
                                                        )
                                                        sequence_index += 1
                                        response_lines.append(text)
                                        partial = '\n'.join(response_lines)
                                        current = time.time()
                                        if message_id and current - last_update_time > 0.1:
                                            self.message_queue.update_streaming_response(message_id, partial)
                                            last_update_time = current

                                    elif block_type == 'tool_use' and message_id:
                                        tool_name = content_item.get('name', '')
                                        tool_input = content_item.get('input', {})
                                        tool_id = content_item.get('id', '')
                                        self.message_queue.add_content_block(
                                            message_id, block_index, 'tool_use',
                                            {'name': tool_name, 'input': tool_input, 'id': tool_id}
                                        )
                                        tool_use_index = self.message_queue.add_tool_use(message_id, tool_name, tool_input, tool_id)
                                        self.message_queue.add_message_sequence(
                                            message_id, sequence_index, block_index, 'tool_use',
                                            {'name': tool_name, 'input': tool_input, 'id': tool_id},
                                            tool_use_index=tool_use_index
                                        )
                                        sequence_index += 1

                                        # 如果是文件发送工具，额外创建 file 类型的序列条目
                                        if tool_name in ('send_files', 'mcp__im-claude-bridge__send_files'):
                                            file_paths = tool_input.get('file_paths', [])
                                            valid_files = [fp for fp in file_paths if os.path.exists(fp)]
                                            if valid_files:
                                                self.message_queue.add_message_sequence(
                                                    message_id, sequence_index, block_index, 'file',
                                                    {'file_paths': valid_files}
                                                )
                                                sequence_index += 1

                                        # 微信主动通知工具：写入 outbox，由微信 Bot 轮询发送（username=None 由 Bot 端用默认配置补齐）
                                        if tool_name in ('notify_weixin', 'mcp__im-claude-bridge__notify_weixin'):
                                            notify_content = (tool_input.get('content') or '').strip()
                                            if notify_content:
                                                # 拼接来源前缀（半角">"引用块格式，微信端可渲染；拼在正文最前面）
                                                try:
                                                    if channel_type == 'weixin':
                                                        source_desc = "> 来自微信私聊的消息："
                                                    elif channel_type == 'discord' and is_dm:
                                                        source_desc = "> 来自 Discord 私聊的消息："
                                                    elif channel_type == 'discord':
                                                        channel_name = self.message_queue.get_channel_name(channel_id)
                                                        if channel_name:
                                                            source_desc = f"> 来自 Discord 频道`＃{channel_name}`的消息："
                                                        else:
                                                            source_desc = f"> 来自 Discord 频道`＃{channel_id}`的消息："
                                                    else:
                                                        source_desc = f"> 来自会话 {session_key} 的消息："
                                                except Exception:
                                                    source_desc = None
                                                # username 为空时由 Bot 端用默认配置（notify_weixin_username）补齐
                                                notify_username = (tool_input.get('username') or '').strip() or None
                                                self.message_queue.add_notify(username=notify_username, content=notify_content, source_desc=source_desc)
                                                self._log.log(
                                                    f"[NotifyWeixin] 已受理微信通知, message #{message_id}, "
                                                    f"session {session_key}"
                                                )

                                        # ====== AskUserQuestion 拦截（阶段一） ======
                                        # Claude Code 在 -p 模式下调用 AskUserQuestion 时会被内部立即注入"用户拒绝"的 tool_result，
                                        # 进程随即退出。这里拦截该工具调用，触发 kill，并保留 questions 数据用于后续 Discord 投票。
                                        if tool_name == 'AskUserQuestion' and pending_ask_signal is None:
                                            pending_ask_signal = {
                                                'tool_use_id': tool_id,  # 内部用，不展示给用户
                                                'questions': tool_input.get('questions', []),  # 原始 questions 数组
                                                'asked_at': datetime.now().isoformat(),
                                            }
                                            self._log.log(
                                                f"[AskUserQuestion] Intercepted for message #{message_id}, "
                                                f"session {session_key}, will kill process"
                                            )

                    # wait_for 轮询式读取：stdout 静默（工具长时间执行）时也能定期醒来检查中止信号，
                    # 避免 abort 标志被无限期阻塞的 read 卡住；超时取消 read 不会丢已到达的数据
                    abort_poll_interval = 0.5
                    claude_start_deadline = time.monotonic() + float(self.config.claude_timeout)

                    while True:
                        # 检查是否收到中止信号
                        if message_id and self.message_queue.is_aborting(message_id):
                            aborted = True
                            break

                        # 检查 AskUserQuestion 拦截信号（拦截后立即跳出读取循环，触发 kill）
                        if pending_ask_signal is not None:
                            break

                        try:
                            chunk = await asyncio.wait_for(
                                process.stdout.read(chunk_size),
                                timeout=abort_poll_interval
                            )
                        except asyncio.TimeoutError:
                            if not ai_started_notified and time.monotonic() > claude_start_deadline:
                                raise Exception(f"Claude Code 启动超时（超过 {self.config.claude_timeout} 秒）")
                            continue

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
                                process_json_object(data)
                            except json.JSONDecodeError:
                                pass

                    if buffer:
                        try:
                            line_str = buffer.decode('utf-8', errors='replace').strip()
                            if line_str:
                                data = json.loads(line_str)
                                process_json_object(data)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            pass

                    # ====== AskUserQuestion 拦截：kill 进程 + 保留 PROCESSING 状态 ======
                    if pending_ask_signal is not None:
                        # 1. kill 进程（复用 abort 的优雅终止路径）
                        process.terminate()
                        try:
                            returncode = await asyncio.wait_for(process.wait(), timeout=5.0)
                            self._log.log(
                                f"✅ [消息 #{message_id}] AskUserQuestion 拦截，进程已优雅终止 (退出码: {returncode})"
                            )
                        except asyncio.TimeoutError:
                            self._log.log(f"⚠️ [消息 #{message_id}] AskUserQuestion 拦截，进程未在 5 秒内退出，强制终止...")
                            process.kill()
                            await process.wait()
                            self._log.log(f"✅ [消息 #{message_id}] AskUserQuestion 拦截，进程已强制终止")

                        # 2. 暴露信号给 _process_message（实例属性，单 worker 串行处理无竞态）
                        self._pending_ask_payload = pending_ask_signal

                        # 3. 不写错误状态、不改 message.status（_process_message 会处理）
                        return None

                    if aborted:
                        process.terminate()
                        try:
                            returncode = await asyncio.wait_for(process.wait(), timeout=5.0)
                            self._log.log(f"✅ [消息 #{message_id}] 进程已优雅终止 (退出码: {returncode})")
                        except asyncio.TimeoutError:
                            self._log.log(f"⚠️ [消息 #{message_id}] 进程未在 5 秒内退出，强制终止...")
                            process.kill()
                            await process.wait()
                            self._log.log(f"✅ [消息 #{message_id}] 进程已强制终止")

                        if message_id:
                            abort_status = self.message_queue.get_message_status(message_id)
                            is_auto_abort = abort_status == MessageStatus.AUTO_ABORTING
                            partial_response = '\n'.join(response_lines).strip()
                            if is_auto_abort and not partial_response:
                                # 引导模式自动打断且无部分响应：不发送占位提示；
                                # 标记"内容未送达 AI"（error 字段标记，status 会被发送器
                                # 完成流程覆盖为 COMPLETED 不影响），待下一条消息出队时捞回合并
                                self.message_queue.update_status(
                                    message_id, MessageStatus.SKIPPED,
                                    error='auto_aborted_pending_merge'
                                )
                                # 空文本序列让发送器走完完成流程（停 typing、清理缓存）
                                self.message_queue.add_message_sequence(message_id, 0, 0, 'text', {'text': ''})
                                self._log.log(f"🤫 [消息 #{message_id}] 自动打断且无部分响应，内容待下一条消息捞回")
                                return None

                            abort_msg = partial_response if partial_response else "(响应被用户中止)"
                            self.message_queue.update_status(
                                message_id,
                                MessageStatus.COMPLETED,
                                response=abort_msg
                            )
                            # 打打断标记：被打断结束的消息不发送智谱额度提示
                            # （update_status 的 response 优先写入会跳过 error，需单独补）
                            self.message_queue.mark_message_aborted(message_id)

                            return partial_response if partial_response else "(响应被用户中止)"

                        return partial_response if partial_response else "(响应被用户中止)"

                    returncode = await process.wait()

                    # 无论成功失败都读取 stderr（避免进程因管道满而阻塞）
                    stderr_output = ""
                    try:
                        stderr_data = await process.stderr.read()
                        if stderr_data:
                            stderr_output = stderr_data.decode('utf-8', errors='replace').strip()
                    except Exception:
                        pass
                    # StreamReader 不需要手动关闭，会在进程结束时自动清理

                    if returncode == 0:
                        response = '\n'.join(response_lines).strip()

                        # 如果有 stderr 输出且非空，打印警告（调试用）
                        if stderr_output:
                            self._log.log(f"⚠️ Claude stderr: {stderr_output}")

                        if message_id and response:
                            self.message_queue.update_streaming_response(message_id, response)

                        self._log.log(f"✅ Claude 响应成功 (长度: {len(response) if response else 0} 字符)")
                        return response if response else "(Claude 没有返回文本响应)"
                    else:
                        error_msg = f"Claude Code 返回错误码 {returncode}"
                        if stderr_output:
                            error_msg += f": {stderr_output}"

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
                self._log.log(f"❌ {error_msg}")
                raise Exception(error_msg)

            except Exception as e:
                retries += 1
                self._log.log(f"❌ 调用失败 (尝试 {retries}/{max_attempts}): {e}")

                if retries >= max_attempts:
                    raise Exception(f"经过 {max_attempts} 次调用尝试后仍然失败: {str(e)}")

                wait_time = 2 ** retries
                self._log.log(f"⏳ {wait_time} 秒后重试...")
                await asyncio.sleep(wait_time)

        return None

    def _build_session_target(self, channel_type: str, is_dm: bool, channel_id: int, user_id: int, username: str) -> str:
        """构建"当前会话"描述（任务/提醒执行所处的位置）"""
        platform = "微信" if channel_type == 'weixin' else "Discord"
        if is_dm:
            return f"{platform} 私聊（{username}，{user_id}）"
        if channel_type == 'weixin':
            return f"{platform} 群聊（{channel_id}）"
        return f"{platform} 频道（{channel_id}）"

    def _build_creator_line(self, username: str, user_id: int) -> str:
        """构建创建人行（频道模式 user_id 为 0，只显示用户名）"""
        if user_id:
            return f"{username}（{user_id}）"
        return f"{username}"

    def _build_task_prompt(self, content: str, username: str, user_id: int, is_dm: bool, channel_id: int, channel_type: str = 'discord') -> str:
        """构建任务消息结构"""
        target = self._build_session_target(channel_type, is_dm, channel_id, user_id, username)
        creator = self._build_creator_line(username, user_id)
        return f"""🔔 定时任务已触发

当前会话：{target}
创建人：{creator}
内容：{content}"""

    def _build_reminder_prompt(self, content: str, username: str, user_id: int, is_dm: bool, channel_id: int, channel_type: str = 'discord') -> str:
        """构建提醒消息结构"""
        target = self._build_session_target(channel_type, is_dm, channel_id, user_id, username)
        creator = self._build_creator_line(username, user_id)
        return f"""🔔 定时提醒已触发

当前会话：{target}
创建人：{creator}
内容：{content}"""

    def _build_sender_info(self, username: str, user_id: int, is_dm: bool, channel_id: int, attachments: list = None, channel_type: str = 'discord') -> str:
        """构建发送者信息"""
        sender_base = f"{username}（{user_id}）"

        # 根据 channel_type 确定频道名称
        if channel_type == 'discord':
            channel_prefix = "在 Discord "
            channel_text = "频道"
        else:  # weixin
            channel_prefix = "在微信"
            channel_text = "群聊"

        if attachments:
            filenames_str = '、'.join([a.filename for a in attachments])
            file_path = self.config.default_download_directory
            attachment_info = f"引用了文件名为 {filenames_str} 的已下载附件，文件位于 {file_path} 中"
            if is_dm:
                sender_info = f"{sender_base}{channel_prefix}私聊中{attachment_info}，并说："
            elif channel_id:
                sender_info = f"{sender_base}{channel_prefix}{channel_text}（{channel_id}）中{attachment_info}，并说："
            else:
                sender_info = f"{sender_base}{attachment_info}，并说："
        else:
            if is_dm:
                sender_info = f"{sender_base}{channel_prefix}私聊中说："
            elif channel_id:
                sender_info = f"{sender_base}{channel_prefix}{channel_text}（{channel_id}）中说："
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

        # 如果正在处理消息，不算空闲（防止长时间运行的任务被误清理）
        if self.current_message_id is not None:
            return False

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

    def _parse_sticker_segments(self, text: str) -> list:
        """解析文本中的表情包标记，按位置拆分为有序段列表

        例如输入 "文本1<:开心-森贝儿贵宾犬起飞.gif>文本2"
        返回 [
            {"type": "text", "content": "文本1"},
            {"type": "sticker", "file_path": "D:\\...\\stickers\\开心-森贝儿贵宾犬起飞.gif"},
            {"type": "text", "content": "文本2"}
        ]

        实现要点：
        - 使用正则 <:([^>]+\\.(png|jpg|jpeg|gif))> 匹配表情包标记
        - 跳过代码块（``` 包裹的内容），避免误处理示例代码中的标记
        - 先精确匹配文件，再模糊匹配（含义-*.*）
        - 匹配失败时保留原始标记文本作为普通文本
        - 连续的文本段合并为一个 text 段
        """
        sticker_dir = Path(self.config.stickers_path)
        sticker_pattern = re.compile(r'<:([^>]+\.(png|jpg|jpeg|gif))>')

        # 将文本按代码块分割，标记哪些部分是代码块
        parts = []
        code_block_pattern = re.compile(r'(```.*?```)', re.DOTALL)
        last_end = 0

        for match in code_block_pattern.finditer(text):
            # 代码块之前的普通文本
            if match.start() > last_end:
                parts.append((text[last_end:match.start()], False))
            # 代码块本身
            parts.append((match.group(), True))
            last_end = match.end()

        # 尾部剩余文本
        if last_end < len(text):
            parts.append((text[last_end:], False))

        segments = []

        for part_text, is_code_block in parts:
            if is_code_block:
                # 代码块整体作为文本段
                segments.append({"type": "text", "content": part_text})
            else:
                # 在非代码块中解析表情包标记
                last_pos = 0
                for match in sticker_pattern.finditer(part_text):
                    marker = match.group()
                    filename = match.group(1)

                    # 标记之前的文本
                    if match.start() > last_pos:
                        segments.append({"type": "text", "content": part_text[last_pos:match.start()]})

                    # 尝试精确匹配
                    exact_path = sticker_dir / filename
                    if exact_path.exists():
                        segments.append({"type": "sticker", "file_path": str(exact_path)})
                        self._log.log(f"🖼️ 表情包精确匹配: {filename}")
                    else:
                        # 模糊匹配：如果文件名包含 '-'，取 '-' 前的部分作为含义
                        matched = False
                        if '-' in filename:
                            meaning = filename.split('-', 1)[0]
                            # 用 glob 匹配 "含义-*.*"（保留原始扩展名选项）
                            pattern = f"{meaning}-*.*"
                            matches = list(sticker_dir.glob(pattern))
                            if matches:
                                segments.append({"type": "sticker", "file_path": str(matches[0])})
                                self._log.log(f"🖼️ 表情包模糊匹配: {filename} -> {matches[0].name}")
                                matched = True

                        if not matched:
                            # 匹配失败，保留原始标记文本
                            self._log.log(f"⚠️ 表情包未找到: {filename}，保留原始标记")
                            segments.append({"type": "text", "content": marker})

                    last_pos = match.end()

                # 剩余文本
                if last_pos < len(part_text):
                    segments.append({"type": "text", "content": part_text[last_pos:]})

        # 合并连续的文本段
        merged = []
        for seg in segments:
            if merged and seg["type"] == "text" and merged[-1]["type"] == "text":
                merged[-1]["content"] += seg["content"]
            else:
                merged.append(seg)

        if not merged:
            merged = [{"type": "text", "content": text}]

        return merged
