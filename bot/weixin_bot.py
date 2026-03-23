"""
微信 Bot 主程序
接收微信消息并转发给 Claude Code
"""
import asyncio
import sys
from pathlib import Path
from typing import Dict, List
import logging
import zlib

# 添加 shared 目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.config import Config
from shared.message_queue import MessageQueue, Message, MessageDirection, MessageStatus, MessageTag, ChannelType
from shared.message_queue import MessageTag as MessageTagEnum
from bot.weixin_client import WeixinClient, WeixinAccount
from bot.weixin_qr_login import WeixinAccountManager

logger = logging.getLogger(__name__)


class WeixinBot:
    """微信 Bot 类"""

    def __init__(self, config: Config, message_queue: MessageQueue):
        """初始化 Bot"""
        self.config = config
        self.message_queue = message_queue
        self.running = False
        self.accounts: List[WeixinAccount] = []
        self.clients: Dict[str, WeixinClient] = {}
        self.polling_tasks = []
        self.send_check_task = None

        # 账号管理
        self.account_manager = WeixinAccountManager(config.weixin_accounts_file)
        self._load_accounts()
        print(f"✅ 微信 Bot 初始化完成，共 {len(self.accounts)} 个账号")

        # Context Token 缓存（用户 -> 最新 context_token）
        # 这里的键已经是解析后的纯净用户名（如 "鸵鸟居士"）
        self.context_tokens: Dict[str, str] = {}

    def _load_accounts(self):
        """加载已保存的账号"""
        self.accounts = self.account_manager.load_accounts()
        logger.info(f"Loaded {len(self.accounts)} accounts")

    async def run(self):
        """启动 Bot"""
        if not self.accounts:
            print("⚠️  未配置微信账号，请先使用 scripts/login_weixin.py 扫码登录")
            return

        self.running = True
        print(f"🚀 微信 Bot 启动中...")

        # 为每个账号启动长轮询任务
        for account in self.accounts:
            task = asyncio.create_task(self._polling_loop(account))
            self.polling_tasks.append(task)

        # 启动发送消息检查任务
        self.send_check_task = asyncio.create_task(self._check_send_messages())

        print(f"✅ 微信 Bot 已启动，{len(self.accounts)} 个账号正在监听")

        # 等待所有任务完成
        await asyncio.gather(*self.polling_tasks, self.send_check_task)

    async def stop(self):
        """停止 Bot"""
        print("🛑 微信 Bot 正在停止...")
        self.running = False

        # 取消所有任务
        for task in self.polling_tasks:
            task.cancel()
        if self.send_check_task:
            self.send_check_task.cancel()

        # 等待任务取消完成
        await asyncio.gather(*self.polling_tasks, self.send_check_task, return_exceptions=True)
        print("✅ 微信 Bot 已停止")

    async def _polling_loop(self, account: WeixinAccount):
        """长轮询循环"""
        print(f"🔄 账号 {account.bot_id} 开始长轮询")

        # 创建客户端
        async with WeixinClient(account) as client:
            self.clients[account.bot_id] = client

            # 测试连接
            try:
                if not await client.test_connection():
                    print(f"❌ 账号 {account.bot_id} 连接测试失败")
                    return
            except Exception as e:
                print(f"❌ 账号 {account.bot_id} 连接测试失败: {e}")
                return

            print(f"✅ 账号 {account.bot_id} 连接成功")

            while self.running:
                try:
                    # 长轮询获取消息
                    data = await client.get_updates(timeout_ms=35000)

                    # 处理消息
                    msgs = data.get("msgs", [])
                    if msgs:
                        for msg in msgs:
                            await self._handle_message(msg, account.bot_id)

                except asyncio.TimeoutError:
                    # 长轮询超时是正常的
                    continue
                except Exception as e:
                    print(f"❌ 账号 {account.bot_id} 轮询错误: {e}")
                    await asyncio.sleep(5)

    async def _check_send_messages(self):
        """检查并发送消息到微信"""
        print("📤 消息发送检查任务已启动")

        while self.running:
            try:
                # 获取正在处理的消息（只获取微信频道的消息）
                messages = self.message_queue.get_processing_messages(channel_type=ChannelType.WEIXIN.value)

                for msg in messages:
                    # 只发送有回复内容的消息
                    if not msg.response:
                        continue

                    try:
                        # 选择一个账号（轮询）
                        if not self.accounts:
                            raise Exception("没有可用的微信账号")

                        account_index = msg.discord_channel_id % len(self.accounts)
                        account = self.accounts[account_index]
                        client = self.clients.get(account.bot_id)

                        if not client:
                            raise Exception(f"账号 {account.bot_id} 的客户端未初始化")

                        # 发送消息
                        await self._send_to_weixin(client, msg)

                        # 标记消息为已完成
                        self.message_queue.update_status(
                            msg.id,
                            MessageStatus.COMPLETED
                        )

                    except Exception as e:
                        print(f"❌ 发送消息失败: {e}")
                        self.message_queue.update_status(
                            msg.id,
                            MessageStatus.FAILED,
                            error=str(e)
                        )

                # 等待一段时间再检查
                await asyncio.sleep(self.config.queue_send_interval)

            except Exception as e:
                print(f"❌ 检查发送消息错误: {e}")
                await asyncio.sleep(1)

    async def _send_to_weixin(self, client: WeixinClient, msg: Message):
        """发送消息到微信"""
        response_text = msg.response or msg.content
        context_token = self.context_tokens.get(msg.username, msg.context_token or "")

        if not context_token:
            raise Exception(f"context_token is required but missing for user {msg.username}")

        # 注意：这里的 msg.username 已经是 "鸵鸟居士" 了
        # 直接传给 client，由 client 底层自动还原为微信 ID
        result = await client.send_message(
            to_user_id=msg.username,
            text=response_text,
            context_token=context_token
        )

        print(f"✅ 已发送: {len(response_text)} 字符")
        return result

    async def _handle_message(self, msg: dict, account_id: str):
        """处理单条消息"""
        try:
            # 解析消息
            # 注意：由于 WeixinClient 底层已经做了拦截替换，
            # 这里的 from_user_id 直接就是 "鸵鸟居士"（或者是没配置的原始 wxid）
            from_user_id = msg.get("from_user_id")
            to_user_id = msg.get("to_user_id")
            message_type = msg.get("message_type")  # 1 = USER, 2 = BOT
            context_token = msg.get("context_token")

            # 只处理用户消息 (message_type = 1)
            if message_type != 1:
                return

            # 更新 context_token 缓存（保存最新的 token）
            if context_token:
                self.context_tokens[from_user_id] = context_token

            # 解析消息内容
            content = await self._parse_message_content(msg)

            if not content:
                return

            print(f"📨 收到来自 [{from_user_id}] 的消息: {content[:30]}...")

            # 构造消息队列消息
            # 使用hash()函数将字符串转换为正整数
            def weixin_id_to_int(weixin_id: str) -> int:
                """将用户ID转换为固定的整数ID（不受程序重启影响）"""
                return zlib.crc32(weixin_id.encode('utf-8')) % (10 ** 10)  # 限制在10位数字内

            queue_msg = Message(
                id=None,
                direction=MessageDirection.TO_CLAUDE.value,
                content=content,
                status=MessageStatus.PENDING.value,
                discord_channel_id=weixin_id_to_int(from_user_id),  # 用发送者 ID 作为频道 ID
                discord_message_id=int(msg.get("message_id", 0)),
                discord_user_id=weixin_id_to_int(from_user_id),
                username=from_user_id,  # 这里直接存 "鸵鸟居士"
                is_dm=True,  # 微信都是私聊
                is_external=False,
                tag=MessageTag.DEFAULT.value,
                channel_type=ChannelType.WEIXIN.value,  # 微信频道
                context_token=context_token  # 保存 context_token 用于回复
            )

            # 写入消息队列
            message_id = self.message_queue.add_message(queue_msg)
            queue_msg.id = message_id

        except Exception as e:
            print(f"❌ 处理消息失败: {e}")

    async def _parse_message_content(self, msg: dict) -> str | None:
        """解析消息内容"""
        try:
            item_list = msg.get("item_list", [])
            if not item_list:
                return None

            content_parts = []

            for item in item_list:
                item_type = item.get("type")

                # 文本消息
                if item_type == 1:
                    text_item = item.get("text_item", {})
                    text = text_item.get("text", "")
                    content_parts.append(text)

                # 图片消息
                elif item_type == 2:
                    image_item = item.get("image_item", {})
                    # TODO: 下载图片并保存
                    content_parts.append("[图片]")

                # 语音消息
                elif item_type == 3:
                    content_parts.append("[语音]")

                # 文件消息
                elif item_type == 4:
                    file_item = item.get("file_item", {})
                    filename = file_item.get("filename", "未知文件")
                    content_parts.append(f"[文件: {filename}]")

                # 视频消息
                elif item_type == 5:
                    content_parts.append("[视频]")

            return "\n".join(content_parts)

        except Exception as e:
            print(f"❌ 解析消息内容失败: {e}")
            return None


async def main():
    """主函数（用于测试）"""
    config = Config()
    message_queue = MessageQueue(config.database_path)
    bot = WeixinBot(config, message_queue)

    try:
        await bot.run()
    except KeyboardInterrupt:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())