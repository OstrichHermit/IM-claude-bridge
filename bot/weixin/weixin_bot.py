"""
微信 Bot 主程序
接收微信消息并转发给 Claude Code
"""
import asyncio
import sys
from pathlib import Path
from typing import Dict, List, Any

# 添加 shared 目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.config import Config
from shared.logger import get_logger
from shared.message_queue import MessageQueue, Message, ChannelType
from shared.context_token_storage import ContextTokenStorage
from bot.weixin.weixin_client import WeixinClient, WeixinAccount
from bot.weixin.weixin_qr_login import WeixinAccountManager
from bot.weixin.weixin_media import WeixinMediaHandler, WeixinFileMapping
from bot.weixin.weixin_message_handlers import WeixinMessageHandlersMixin
from bot.weixin.weixin_commands import WeixinCommandsMixin
from bot.weixin.weixin_sequence_sender import WeixinSequenceSenderMixin
from bot.weixin.weixin_pollers import WeixinPollersMixin

log = get_logger("WeixinBot", "weixin")


class WeixinBot(
    WeixinMessageHandlersMixin,
    WeixinCommandsMixin,
    WeixinSequenceSenderMixin,
    WeixinPollersMixin,
):
    """微信 Bot 类"""

    def __init__(self, config: Config, message_queue: MessageQueue):
        """初始化 Bot"""
        self.config = config
        self.message_queue = message_queue
        self.running = False
        self.accounts: List[WeixinAccount] = []
        self.clients: Dict[str, WeixinClient] = {}
        self.polling_tasks = []

        # Context Token 持久化存储（用户 -> 最新 context_token）
        self.context_tokens = ContextTokenStorage(config.weixin_accounts_file)

        # 整数 ID 到用户名的映射（用于文件发送）
        self.id_to_username: Dict[int, str] = {}

        # 整数 ID 到用户信息的映射（用于文件发送）
        self.userid_to_user: Dict[int, Dict[str, Any]] = {}

        # 用户名到原始微信 ID 的反向映射（用于 API 调用）
        self.username_to_wxid: Dict[str, str] = {}

        # 用户名到整数 ID 的映射（用于消息处理）
        self.username_to_userid: Dict[str, int] = {}

        # wxid 到用户信息的映射（用于消息处理）
        self.wxid_to_user: Dict[str, Dict[str, Any]] = {}

        # 停止命令确认缓存（用户_id -> 第一次请求的时间戳）
        self.stop_requests: Dict[str, float] = {}

        # Typing indicator 追踪（消息ID -> typing_task）
        self.pending_messages: Dict[int, Dict[str, Any]] = {}

        # Typing ticket 缓存（用户 -> typing_ticket）
        self.typing_tickets: Dict[str, str] = {}

        # 账号管理
        self.account_manager = WeixinAccountManager(config.weixin_accounts_file)
        self._load_accounts()
        log.log(f"微信 Bot 初始化完成，共 {len(self.accounts)} 个账号")

        # 加载用户信息（从账号配置中）
        self._load_users()

        # 文件下载和处理（使用 config 中的文件下载路径）
        self.media_handler = WeixinMediaHandler(config.default_download_directory)

        # 文件映射表（使用微信专用的映射表路径，不与 Discord 共享）
        self.file_mapping = WeixinFileMapping(config.weixin_file_mapping_path)

    def _load_accounts(self):
        """加载已保存的账号"""
        self.accounts = self.account_manager.load_accounts()
        log.log(f"Loaded {len(self.accounts)} accounts")

    def _load_users(self):
        """从账号配置中加载用户信息"""
        for account in self.accounts:
            # wxid -> 用户信息
            self.wxid_to_user[account.wxid] = {
                "wxid": account.wxid,
                "username": account.username,
                "user_id": account.user_id
            }

            # user_id -> 用户信息
            self.userid_to_user[account.user_id] = {
                "wxid": account.wxid,
                "username": account.username,
                "user_id": account.user_id
            }

            # username -> wxid（反向映射，用于API调用）
            self.username_to_wxid[account.username] = account.wxid

            # username -> user_id（用于消息处理）
            self.username_to_userid[account.username] = account.user_id

        log.log(f"已加载 {len(self.accounts)} 个用户信息")

    async def run(self):
        """启动 Bot"""
        if not self.accounts:
            log.log("⚠️  未配置微信账号，请先使用 scripts/login_weixin.py 扫码登录")
            return

        self.running = True
        log.log("🚀 微信 Bot 启动中...")

        # 清理数据库中的旧消息序列（避免重复处理）
        log.log("🧹 清理旧的消息序列和工具调用结果...")
        import sqlite3
        try:
            conn = sqlite3.connect(self.config.database_path)
            cursor = conn.cursor()

            # 清理微信频道的旧消息序列
            cursor.execute("""
                           DELETE FROM message_sequence
                           WHERE id IN (
                               SELECT ms.id
                               FROM message_sequence ms
                               INNER JOIN messages m ON ms.message_id = m.id
                               WHERE m.channel_type = ?
                                 AND ms.status = 'pending'
                           )
                           """, (ChannelType.WEIXIN.value,))

            deleted_count = cursor.rowcount

            # 清理微信频道的旧工具调用结果（超过 10 分钟的）
            cursor.execute("""
                           DELETE FROM tool_use_results
                           WHERE id IN (
                               SELECT r.id
                               FROM tool_use_results r
                               INNER JOIN messages m ON r.message_id = m.id
                               WHERE m.channel_type = ?
                                 AND r.processed = 0
                                 AND datetime(r.created_at) <= datetime('now', '-10 minutes')
                           )
                           """, (ChannelType.WEIXIN.value,))

            deleted_tools_count = cursor.rowcount

            # 清理微信频道的旧 PROCESSING 状态消息（超过 1 小时的）
            cursor.execute("""
                           UPDATE messages
                           SET status = 'failed',
                               error = 'Bot 重启，消息已取消'
                           WHERE channel_type = ?
                             AND status = 'processing'
                             AND datetime(updated_at) <= datetime('now', '-1 hour')
                           """, (ChannelType.WEIXIN.value,))

            updated_messages_count = cursor.rowcount

            conn.commit()
            conn.close()

            if deleted_count > 0:
                log.log(f"✅ 已清理 {deleted_count} 条旧的消息序列")
            if deleted_tools_count > 0:
                log.log(f"✅ 已清理 {deleted_tools_count} 条旧的工具调用结果")
            if updated_messages_count > 0:
                log.log(f"✅ 已取消 {updated_messages_count} 条旧的处理中消息")
            if deleted_count == 0 and deleted_tools_count == 0 and updated_messages_count == 0:
                log.log("✓ 没有需要清理的旧数据")
        except Exception as e:
            log.log(f"❌ 清理旧数据时出错: {e}")

        # 为每个账号启动长轮询任务
        for account in self.accounts:
            task = asyncio.create_task(self._polling_loop(account))
            self.polling_tasks.append(task)

        # 启动消息序列检查任务
        self.sequence_check_task = asyncio.create_task(self.check_message_sequences())

        # 启动工具执行结果检查任务
        self.tool_result_check_task = asyncio.create_task(self.check_tool_use_results())

        log.log(f"✅ 微信 Bot 已启动，{len(self.accounts)} 个账号正在监听")

        # 等待所有任务完成
        await asyncio.gather(
            *self.polling_tasks,
            self.sequence_check_task,
            self.tool_result_check_task
        )
        log.log("✓ 微信 Bot 已停止")

    async def stop(self):
        """停止 Bot"""
        log.log("🛑 微信 Bot 正在停止...")
        self.running = False

        # 停止所有 typing indicator
        for message_id in list(self.pending_messages.keys()):
            await self.stop_typing_indicator(message_id)

        # 取消所有轮询任务
        for task in self.polling_tasks:
            task.cancel()

        # 取消序列检查和工具结果检查任务
        if hasattr(self, 'sequence_check_task') and self.sequence_check_task:
            self.sequence_check_task.cancel()
        if hasattr(self, 'tool_result_check_task') and self.tool_result_check_task:
            self.tool_result_check_task.cancel()

        # 等待任务取消完成
        await asyncio.gather(
            *self.polling_tasks,
            self.sequence_check_task if hasattr(self, 'sequence_check_task') else None,
            self.tool_result_check_task if hasattr(self, 'tool_result_check_task') else None,
            return_exceptions=True
        )
        log.log("微信 Bot 已停止")

    async def _polling_loop(self, account: WeixinAccount):
        """长轮询循环"""
        log.log(f"🔄 账号 {account.bot_id} 开始长轮询")

        # 创建客户端
        async with WeixinClient(account) as client:
            self.clients[account.bot_id] = client

            # 测试连接
            try:
                if not await client.test_connection():
                    log.log(f"❌ 账号 {account.bot_id} 连接测试失败")
                    return
            except Exception as e:
                log.log(f"❌ 账号 {account.bot_id} 连接测试失败: {e}")
                return

            log.log(f"✅ 账号 {account.bot_id} 连接成功")

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
                    log.log(f"❌ 账号 {account.bot_id} 轮询错误: {e}")
                    await asyncio.sleep(5)

    async def _send_to_weixin(self, client: WeixinClient, msg: Message):
        """发送消息到微信"""
        response_text = msg.response or msg.content
        context_token = self.context_tokens.get(msg.username) or msg.context_token or ""

        if not context_token:
            raise Exception(f"context_token is required but missing for user {msg.username}")

        # 注意：这里的 msg.username 已经是 "用户名" 了
        # 直接传给 client，由 client 底层自动还原为微信 ID
        result = await client.send_message(
            to_user_id=msg.username,
            text=response_text,
            context_token=context_token
        )

        return result

    async def _send_text_to_weixin(self, client: WeixinClient, msg: Message, text: str):
        """发送文本内容到微信（用于流式输出）"""
        context_token = self.context_tokens.get(msg.username) or msg.context_token or ""

        if not context_token:
            raise Exception(f"context_token is required but missing for user {msg.username}")

        result = await client.send_message(
            to_user_id=msg.username,
            text=text,
            context_token=context_token
        )

        return result


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
