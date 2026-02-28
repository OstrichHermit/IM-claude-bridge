"""
流式消息发送队列
控制消息发送速率，避免触发 Discord 速率限制
"""
import asyncio
import time
import discord


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
        self.queue = []
        self.last_send_time = 0
        self.sending = False
        self.send_lock = asyncio.Lock()

    async def add_block(self, block: str):
        """
        添加一个 block 到队列

        Args:
            block: 要发送的内容块
        """
        if not block or not block.strip():
            return

        self.queue.append(block)

        # 如果没有正在发送的任务，启动发送循环
        if not self.sending:
            asyncio.create_task(self._send_loop())

    async def _send_loop(self):
        """发送队列中的 block（控制速率）"""
        async with self.send_lock:
            if self.sending:
                return

            self.sending = True

            try:
                while self.queue:
                    block = self.queue.pop(0)

                    # 计算需要等待的时间
                    current_time = time.time()
                    elapsed = current_time - self.last_send_time
                    if elapsed < self.min_interval:
                        await asyncio.sleep(self.min_interval - elapsed)

                    # 发送消息
                    await self._send_with_retry(block)
                    self.last_send_time = time.time()

            finally:
                self.sending = False

    async def _send_with_retry(self, content: str, max_retries: int = 3):
        """
        发送消息（支持重试和速率限制处理）

        Args:
            content: 要发送的内容
            max_retries: 最大重试次数
        """
        for attempt in range(max_retries):
            try:
                await self.channel.send(content)
                return

            except discord.HTTPException as e:
                if e.status == 429:  # 速率限制
                    retry_after = e.retry_after
                    print(f"⚠️ 触发 Discord 速率限制，等待 {retry_after:.2f} 秒")
                    await asyncio.sleep(retry_after)
                    # 重试
                    continue
                else:
                    print(f"❌ 发送消息失败: {e}")
                    raise

            except Exception as e:
                print(f"❌ 发送消息时出错: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                else:
                    raise

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
