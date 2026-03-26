"""
流式消息发送队列
控制消息发送速率，避免触发 Discord 速率限制

支持统一队列模式：文本消息、工具调用卡片、文件都通过同一个队列发送
"""
import asyncio
import time
import discord
from enum import Enum
from typing import Union, List
from datetime import datetime
from pathlib import Path


class MessageType(Enum):
    """消息类型枚举"""
    TEXT = "text"  # 纯文本消息
    EMBED = "embed"  # 嵌入式卡片（工具调用通知）
    FILES = "files"  # 文件


class StreamingMessageQueue:
    """流式消息发送队列"""

    def __init__(self, channel: discord.abc.Messageable, min_interval: float = 1.5):
        """
        初始化队列

        Args:
            channel: Discord 频道对象
            min_interval: 每条消息的最小间隔（秒）
        """
        self.channel = channel
        self.min_interval = min_interval
        self.queue = []  # 队列项格式: {"type": MessageType, "data": ..., "content_block_index": int, "item_index": int}
        self.last_send_time = 0
        self.sending = False
        self.send_lock = asyncio.Lock()

        # Content block 顺序追踪
        self.content_block_sequence = []  # 记录 content block 顺序，例如：[("text", 0), ("tool", 0), ("tool", 1)]
        self.current_content_block_index = 0  # 当前发送到第几个 content block
        self.content_block_senders = {}  # 记录每个 content block 的发送状态：{index: {"pending": int, "sent": int}}
        self.content_block_lock = asyncio.Lock()  # Content block 顺序锁

        # 日志文件设置
        self.log_file = Path(__file__).parent.parent / "logs" / "discord_bot.log"
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

    def log(self, message):
        """同时输出到控制台和日志文件"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] {message}\n"
        print(log_line.strip())
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception as e:
            print(f"⚠️  写入日志失败: {e}")

    async def add_block(self, block: str):
        """
        添加一个文本 block 到队列（保持向后兼容）

        Args:
            block: 要发送的内容块
        """
        await self.add_message(MessageType.TEXT, block)

    async def add_message(self, msg_type: MessageType, data: Union[str, discord.Embed, List[discord.File]], return_future: bool = False, content_block_index: int = None, item_index: int = None):
        """
        添加一条消息到队列

        Args:
            msg_type: 消息类型（TEXT/EMBED/FILES）
            data: 消息数据
                - TEXT: str 文本内容
                - EMBED: discord.Embed 嵌入式卡片
                - FILES: List[discord.File] 文件列表
            return_future: 是否返回 Future（用于获取发送后的消息 ID）
            content_block_index: Content block 索引（用于保证顺序）
            item_index: 该 content block 中的第几个 item

        Returns:
            如果 return_future=True，返回 asyncio.Future，否则返回 None
        """
        # 验证数据
        if msg_type == MessageType.TEXT:
            if not data or not data.strip():
                return None
        elif msg_type == MessageType.EMBED:
            if not data:
                return None
        elif msg_type == MessageType.FILES:
            if not data or len(data) == 0:
                return None

        future = None
        if return_future:
            future = asyncio.Future()

        queue_item = {"type": msg_type, "data": data, "future": future}

        # 添加 content block 索引（如果提供）
        if content_block_index is not None:
            queue_item["content_block_index"] = content_block_index
            if item_index is not None:
                queue_item["item_index"] = item_index

        self.queue.append(queue_item)

        # 如果没有正在发送的任务，启动发送循环
        if not self.sending:
            asyncio.create_task(self._send_loop())

        return future

    async def _send_loop(self):
        """发送队列中的消息（控制速率，按 content block 顺序）"""
        async with self.send_lock:
            if self.sending:
                return

            self.sending = True

            try:
                while self.queue:
                    # 检查是否有 content block 顺序限制
                    if self.queue[0].get("content_block_index") is not None:
                        target_block_index = self.queue[0]["content_block_index"]

                        # 如果当前 content block 还没轮到，等待
                        if target_block_index > self.current_content_block_index:
                            # 检查前面的 content block 是否已完成
                            all_previous_done = True
                            for i in range(target_block_index):
                                if i in self.content_block_senders:
                                    sender_info = self.content_block_senders[i]
                                    if sender_info.get("pending", 0) > sender_info.get("sent", 0):
                                        all_previous_done = False
                                        break

                            # 如果前面的 content block 没完成，等待一下再检查
                            if not all_previous_done:
                                await asyncio.sleep(0.1)
                                continue

                        # 更新 content block 发送状态
                        if target_block_index not in self.content_block_senders:
                            self.content_block_senders[target_block_index] = {"pending": 0, "sent": 0}

                    msg_item = self.queue.pop(0)
                    msg_type = msg_item["type"]
                    data = msg_item["data"]
                    future = msg_item.get("future")
                    content_block_index = msg_item.get("content_block_index")

                    # 更新 pending 计数
                    if content_block_index is not None:
                        self.content_block_senders[content_block_index]["pending"] += 1

                    # 计算需要等待的时间
                    current_time = time.time()
                    elapsed = current_time - self.last_send_time
                    if elapsed < self.min_interval:
                        await asyncio.sleep(self.min_interval - elapsed)

                    # 根据类型发送消息
                    sent_message = await self._send_with_retry(msg_type, data)

                    # 更新 sent 计数
                    if content_block_index is not None:
                        self.content_block_senders[content_block_index]["sent"] += 1

                        # 检查当前 content block 是否完成
                        sender_info = self.content_block_senders[content_block_index]
                        if sender_info["pending"] == sender_info["sent"]:
                            # 当前 content block 完成，移动到下一个
                            self.current_content_block_index = content_block_index + 1

                    # 如果有 Future，设置结果
                    if future and not future.done():
                        if sent_message:
                            future.set_result(sent_message)
                        else:
                            future.set_result(None)

                    self.last_send_time = time.time()

            finally:
                self.sending = False

    async def _send_with_retry(self, msg_type: MessageType, data: Union[str, discord.Embed, List[discord.File]], max_retries: int = 3):
        """
        发送消息（支持重试和速率限制处理）

        Args:
            msg_type: 消息类型
            data: 消息数据
            max_retries: 最大重试次数

        Returns:
            发送的消息对象（仅 EMBED 类型返回，其他类型返回 None）
        """
        for attempt in range(max_retries):
            try:
                if msg_type == MessageType.TEXT:
                    await self.channel.send(data)
                    return None
                elif msg_type == MessageType.EMBED:
                    sent_message = await self.channel.send(embed=data)
                    return sent_message  # 返回消息对象，用于后续更新
                elif msg_type == MessageType.FILES:
                    await self.channel.send(files=data)
                    return None

            except discord.HTTPException as e:
                if e.status == 429:  # 速率限制
                    retry_after = e.retry_after
                    self.log(f"⚠️ 触发 Discord 速率限制，等待 {retry_after:.2f} 秒")
                    await asyncio.sleep(retry_after)
                    # 重试
                    continue
                else:
                    self.log(f"❌ 发送消息失败: {e}")
                    raise

            except Exception as e:
                self.log(f"❌ 发送消息时出错: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                else:
                    raise

        return None

    async def flush(self):
        """立即发送队列中的所有消息（用于强制刷新）"""
        while self.queue:
            block = self.queue.pop(0)
            await self._send_with_retry(block)

    def is_empty(self) -> bool:
        """检查队列是否为空"""
        return len(self.queue) == 0

    def get_queue_length(self) -> int:
        """获取队列长度"""
        return len(self.queue)
