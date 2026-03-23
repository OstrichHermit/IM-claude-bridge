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

        # 停止命令确认缓存（用户_id -> 第一次请求的时间戳）
        self.stop_requests: Dict[str, float] = {}

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

            # 检查是否是命令
            content_stripped = content.strip()
            if content_stripped.startswith("/"):
                await self._handle_command(from_user_id, content_stripped, account_id)
                return

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

    async def _handle_command(self, from_user_id: str, command: str, account_bot_id: str):
        """处理命令消息

        Args:
            from_user_id: 发送者用户 ID
            command: 命令文本（如 "/new", "/status" 等）
            account_bot_id: 微信账号 bot_id（用于获取 client）
        """
        import time

        # 获取客户端
        client = self.clients.get(account_bot_id)
        if not client:
            await self._send_direct_message(from_user_id, account_bot_id, "❌ 客户端未初始化，请稍后重试")
            return

        # 解析命令和参数
        parts = command.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        # /new - 重置当前用户的会话
        if cmd == "/new":
            await self._cmd_new(from_user_id, account_bot_id)

        # /status - 查看当前会话状态
        elif cmd == "/status":
            await self._cmd_status(from_user_id, account_bot_id)

        # /stop - 停止服务（需要 60 秒内再次确认）
        elif cmd == "/stop":
            await self._cmd_stop(from_user_id, account_bot_id)

        # /restart - 重启服务
        elif cmd == "/restart":
            await self._cmd_restart(from_user_id, account_bot_id)

        # /abort - 中止当前正在处理的响应
        elif cmd == "/abort":
            await self._cmd_abort(from_user_id, account_bot_id)

        # 未知命令
        else:
            help_text = (
                "📋 可用命令:\n"
                "/new - 重置当前会话\n"
                "/status - 查看会话状态\n"
                "/abort - 中止当前响应\n"
                "/stop - 停止服务（需确认）\n"
                "/restart - 重启服务"
            )
            await self._send_direct_message(from_user_id, account_bot_id, help_text)

    async def _cmd_new(self, from_user_id: str, account_bot_id: str):
        """重置当前用户的会话"""
        # 将用户 ID 转换为整数（与消息队列保持一致）
        def weixin_id_to_int(weixin_id: str) -> int:
            return zlib.crc32(weixin_id.encode('utf-8')) % (10 ** 10)

        user_id_int = weixin_id_to_int(from_user_id)

        # 获取当前会话
        session_key, old_session_id, _, working_dir = self.message_queue.get_or_create_session(
            self.config.working_directory,
            channel_id=None,
            user_id=user_id_int,
            is_dm=True,
            use_temp_session=False,
            temp_session_key=None
        )

        # 删除会话
        deleted = self.message_queue.delete_session(session_key, working_dir)

        # 重新获取会话（应该生成新的 session_id）
        session_key, new_session_id, session_created, _ = self.message_queue.get_or_create_session(
            self.config.working_directory,
            channel_id=None,
            user_id=user_id_int,
            is_dm=True,
            use_temp_session=False,
            temp_session_key=None
        )

        if deleted:
            msg = (
                f"✅ 会话已重置\n\n"
                f"旧 Session ID: {old_session_id[:8]}... (已删除)\n"
                f"新 Session ID: {new_session_id[:8]}...\n\n"
                f"下次对话将使用新的会话 ID 创建全新上下文。"
            )
            print(f"[会话重置] 用户 {from_user_id} 重置了私聊会话")
            print(f"[会话重置] Session Key: {session_key}")
            print(f"[会话重置] 旧 Session ID: {old_session_id} -> 新 Session ID: {new_session_id}")
        else:
            msg = (
                f"⚠️ 没有活跃会话\n\n"
                f"当前 Session ID: {new_session_id[:8]}..."
            )

        await self._send_direct_message(from_user_id, account_bot_id, msg)

    async def _cmd_status(self, from_user_id: str, account_bot_id: str):
        """查看当前会话状态"""
        # 将用户 ID 转换为整数
        def weixin_id_to_int(weixin_id: str) -> int:
            return zlib.crc32(weixin_id.encode('utf-8')) % (10 ** 10)

        user_id_int = weixin_id_to_int(from_user_id)

        # 获取会话信息
        session_key, session_id, session_created, working_dir = self.message_queue.get_or_create_session(
            self.config.working_directory,
            channel_id=None,
            user_id=user_id_int,
            is_dm=True,
            use_temp_session=False,
            temp_session_key=None
        )

        msg = (
            f"📊 Claude Bridge 状态\n\n"
            f"会话类型: 私聊会话\n"
            f"Session ID: {session_id[:8] if session_id else '未生成'}...\n"
            f"状态: {'已创建 ✅' if session_created else '未创建 ⏳'}\n"
            f"工作目录: {working_dir}"
        )

        await self._send_direct_message(from_user_id, account_bot_id, msg)

    async def _cmd_stop(self, from_user_id: str, account_bot_id: str):
        """停止服务（需要 60 秒内再次确认）"""
        import time
        import subprocess
        import os

        current_time = time.time()

        # 检查是否有未过期的停止请求
        if from_user_id in self.stop_requests:
            request_time = self.stop_requests[from_user_id]
            time_diff = current_time - request_time

            if time_diff <= 60:  # 60 秒内再次使用 /stop
                # 确认停止
                del self.stop_requests[from_user_id]

                msg = "🛑 正在停止服务\n\n服务将在几秒钟后停止。"
                await self._send_direct_message(from_user_id, account_bot_id, msg)
                print(f"[停止命令] 用户 {from_user_id} 确认停止服务")

                # 执行停止脚本
                try:
                    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    manager_script = os.path.join(script_dir, 'manager.py')

                    if os.path.exists(manager_script):
                        subprocess.Popen(
                            ["python", manager_script, "stop"],
                            cwd=script_dir,
                            creationflags=subprocess.CREATE_NEW_CONSOLE
                        )
                        print(f"✅ 停止命令已执行: python manager.py stop")
                    else:
                        msg = f"❌ 文件未找到\n\n找不到 manager.py 文件"
                        await self._send_direct_message(from_user_id, account_bot_id, msg)
                        print(f"⚠️  manager.py 不存在: {manager_script}")

                except Exception as e:
                    msg = f"❌ 停止失败\n\n错误: {str(e)}"
                    await self._send_direct_message(from_user_id, account_bot_id, msg)
                    print(f"❌ 执行停止命令时出错: {e}")

                return

        # 第一次使用 /stop，记录请求
        self.stop_requests[from_user_id] = current_time

        msg = (
            "⚠️ 确认停止服务\n\n"
            "此操作将停止 Bot 和 Bridge，服务将不再响应消息。\n"
            "如需确认，请在 60 秒内再次使用 /stop 命令"
        )
        await self._send_direct_message(from_user_id, account_bot_id, msg)
        print(f"[停止命令] 用户 {from_user_id} 请求停止服务，等待再次确认...")

    async def _cmd_restart(self, from_user_id: str, account_bot_id: str):
        """重启服务"""
        import subprocess
        import os

        msg = "🔄 正在重启服务\n\n请稍候，服务将在几秒钟后重新启动。"
        await self._send_direct_message(from_user_id, account_bot_id, msg)
        print(f"[重启命令] 用户 {from_user_id} 触发了服务重启")

        try:
            script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            manager_script = os.path.join(script_dir, 'manager.py')

            if os.path.exists(manager_script):
                subprocess.Popen(
                    ["python", manager_script, "restart"],
                    cwd=script_dir,
                    creationflags=subprocess.CREATE_NEW_CONSOLE
                )
                print(f"✅ 重启命令已执行: python manager.py restart")
            else:
                msg = "❌ 文件未找到\n\n找不到 manager.py 文件"
                await self._send_direct_message(from_user_id, account_bot_id, msg)
                print(f"⚠️  manager.py 不存在: {manager_script}")

        except Exception as e:
            msg = f"❌ 重启失败\n\n错误: {str(e)}"
            await self._send_direct_message(from_user_id, account_bot_id, msg)
            print(f"❌ 执行重启命令时出错: {e}")

    async def _cmd_abort(self, from_user_id: str, account_bot_id: str):
        """中止当前正在处理的响应"""
        # 查找正在处理的消息（只获取微信频道的消息）
        processing_messages = self.message_queue.get_processing_messages(channel_type=ChannelType.WEIXIN.value)

        if not processing_messages:
            msg = "⚠️ 没有正在处理的响应\n\n当前没有正在处理的 Claude 响应。"
            await self._send_direct_message(from_user_id, account_bot_id, msg)
            return

        # 请求中止第一个处理中的消息
        message_to_abort = processing_messages[0]
        success = self.message_queue.request_abort(message_to_abort.id)

        if success:
            msg = (
                f"🛑 已请求中止\n\n"
                f"已请求中止消息 #{message_to_abort.id} 的处理\n"
                f"Claude 响应将在几秒内停止..."
            )
            await self._send_direct_message(from_user_id, account_bot_id, msg)
            print(f"[中止命令] 用户 {from_user_id} 请求中止消息 #{message_to_abort.id}")
        else:
            msg = "❌ 中止请求失败\n\n中止请求失败，请稍后重试。"
            await self._send_direct_message(from_user_id, account_bot_id, msg)

    async def _send_direct_message(self, to_user_id: str, account_bot_id: str, text: str):
        """直接发送消息到微信（绕过消息队列）

        Args:
            to_user_id: 接收者用户 ID
            account_bot_id: 微信账号 bot_id
            text: 消息文本
        """
        client = self.clients.get(account_bot_id)
        if not client:
            raise Exception(f"账号 {account_bot_id} 的客户端未初始化")

        context_token = self.context_tokens.get(to_user_id, "")
        if not context_token:
            raise Exception(f"context_token is required but missing for user {to_user_id}")

        await client.send_message(
            to_user_id=to_user_id,
            text=text,
            context_token=context_token
        )


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