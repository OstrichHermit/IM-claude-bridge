"""
微信 Bot 主程序
接收微信消息并转发给 Claude Code
"""
import asyncio
import sys
from pathlib import Path
from typing import Dict, List, Optional, Any
import logging
import zlib

# 添加 shared 目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.config import Config
from shared.message_queue import MessageQueue, Message, MessageDirection, MessageStatus, MessageTag, ChannelType
from shared.message_queue import MessageTag as MessageTagEnum
from shared.context_token_storage import ContextTokenStorage
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
        self.sequence_check_task = None

        # Context Token 持久化存储（用户 -> 最新 context_token）
        # 这里的键已经是解析后的纯净用户名（如 "鸵鸟居士"）
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
        print(f"✅ 微信 Bot 初始化完成，共 {len(self.accounts)} 个账号")

        # 加载用户信息（从账号配置中）
        self._load_users()

    def _load_accounts(self):
        """加载已保存的账号"""
        self.accounts = self.account_manager.load_accounts()
        logger.info(f"Loaded {len(self.accounts)} accounts")

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

        print(f"✅ 加载了 {len(self.accounts)} 个用户信息")
        logger.info(f"Loaded {len(self.accounts)} users")

    async def run(self):
        """启动 Bot"""
        if not self.accounts:
            print("⚠️  未配置微信账号，请先使用 scripts/login_weixin.py 扫码登录")
            return

        self.running = True
        print(f"🚀 微信 Bot 启动中...")

        # 清理数据库中的旧消息序列（避免重复处理）
        print("🧹 清理旧的消息序列和工具调用结果...")
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
            # 这些消息是上次会话遗留的，context_token 已经过期
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
                print(f"✅ 已清理 {deleted_count} 条旧的消息序列")
            else:
                print("✓ 没有需要清理的消息序列")

            if deleted_tools_count > 0:
                print(f"✅ 已清理 {deleted_tools_count} 条旧的工具调用结果")
            else:
                print("✓ 没有需要清理的工具调用结果")

            if updated_messages_count > 0:
                print(f"✅ 已取消 {updated_messages_count} 条旧的处理中消息")
            else:
                print("✓ 没有需要取消的旧消息")
        except Exception as e:
            print(f"⚠️  清理旧数据时出错: {e}")

        # 为每个账号启动长轮询任务
        for account in self.accounts:
            task = asyncio.create_task(self._polling_loop(account))
            self.polling_tasks.append(task)

        # 启动文件请求检查任务
        self.file_check_task = asyncio.create_task(self._check_file_requests())

        # 启动消息序列检查任务
        self.sequence_check_task = asyncio.create_task(self.check_message_sequences())

        # 启动工具执行结果检查任务
        self.tool_result_check_task = asyncio.create_task(self.check_tool_use_results())

        print(f"✅ 微信 Bot 已启动，{len(self.accounts)} 个账号正在监听")

        # 等待所有任务完成
        await asyncio.gather(
            *self.polling_tasks,
            self.file_check_task,
            self.sequence_check_task,
            self.tool_result_check_task
        )

    async def stop(self):
        """停止 Bot"""
        print("🛑 微信 Bot 正在停止...")
        self.running = False

        # 停止所有 typing indicator
        for message_id in list(self.pending_messages.keys()):
            await self.stop_typing_indicator(message_id)

        # 取消所有任务
        for task in self.polling_tasks:
            task.cancel()
        if hasattr(self, 'file_check_task') and self.file_check_task:
            self.file_check_task.cancel()
        if hasattr(self, 'sequence_check_task') and self.sequence_check_task:
            self.sequence_check_task.cancel()
        if hasattr(self, 'tool_result_check_task') and self.tool_result_check_task:
            self.tool_result_check_task.cancel()

        # 等待任务取消完成
        await asyncio.gather(
            *self.polling_tasks,
            self.file_check_task if hasattr(self, 'file_check_task') else None,
            self.sequence_check_task if hasattr(self, 'sequence_check_task') else None,
            self.tool_result_check_task if hasattr(self, 'tool_result_check_task') else None,
            return_exceptions=True
        )
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

    async def _send_to_weixin(self, client: WeixinClient, msg: Message):
        """发送消息到微信"""
        response_text = msg.response or msg.content
        context_token = self.context_tokens.get(msg.username) or msg.context_token or ""

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

        print(f"📤 流式发送: {len(text)} 字符")
        return result

    async def _check_file_requests(self):
        """检查并发送文件请求到微信"""
        from shared.message_queue import FileRequestStatus

        while self.running:
            try:

                # 获取下一个待处理的微信文件请求
                file_request = self.message_queue.get_next_file_request(channel_type="weixin")

                if not file_request:
                    await asyncio.sleep(0.5)
                    continue

                file_paths = file_request.file_paths

                if file_request.user_id:
                    user_id_int = file_request.user_id

                try:
                        if file_request.user_id:
                            # 优先从配置中查找
                            target_user = None

                            # 从 userid_to_user 中查找
                            user_info = self.userid_to_user.get(user_id_int)
                            if user_info:
                                target_user = user_info["username"]
                            else:
                                # 从运行时映射中查找
                                target_user = self.id_to_username.get(user_id_int)

                            # 如果还没找到，尝试从消息队列中查找
                            if not target_user:
                                from shared.message_queue import Message, MessageDirection
                                import sqlite3

                                conn = sqlite3.connect(self.message_queue.db_path)
                                cursor = conn.cursor()
                                cursor.execute(
                                    """SELECT username FROM messages
                                       WHERE discord_user_id = ?
                                       AND direction = ?
                                       ORDER BY id DESC LIMIT 1""",
                                    (user_id_int, MessageDirection.TO_CLAUDE.value)
                                )
                                recent_messages = cursor.fetchone()
                                conn.close()

                                if recent_messages:
                                    target_user = recent_messages[0]
                                    self.id_to_username[user_id_int] = target_user
                                else:
                                    self.message_queue.update_file_request_status(
                                        file_request.id,
                                        FileRequestStatus.FAILED,
                                        error=f"无法找到用户 ID {user_id_int} 对应的用户名"
                                    )
                                    await asyncio.sleep(self.config.queue_send_interval)
                                    continue

                        elif file_request.channel_id:
                            target_user = str(file_request.channel_id)
                        else:
                            raise Exception("未指定目标用户")

                        # 获取 context_token
                        context_token = self.context_tokens.get(target_user) or ""
                        if not context_token:
                            self.message_queue.update_file_request_status(
                                file_request.id,
                                FileRequestStatus.FAILED,
                                error=f"用户 {target_user} 没有有效的 context_token"
                            )
                            await asyncio.sleep(self.config.queue_send_interval)
                            continue

                        # 根据用户 wxid 选择对应的账号
                        user_info = self.userid_to_user.get(user_id_int)
                        if not user_info:
                            raise Exception(f"未找到用户 ID {user_id_int} 对应的信息")

                        target_wxid = user_info.get("wxid")
                        if not target_wxid:
                            raise Exception(f"用户 {user_id_int} 没有 wxid 信息")

                        # 找到 wxid 对应的账号
                        account = None
                        for acc in self.accounts:
                            if acc.wxid == target_wxid:
                                account = acc
                                break

                        if not account:
                            raise Exception(f"未找到 wxid={target_wxid} 对应的账号")

                        client = self.clients.get(account.bot_id)
                        print(f"📤 [文件请求] target_user={target_user}, user_id={user_id_int}, wxid={target_wxid}, 选择账号={account.username}({account.bot_id})")

                        if not client:
                            raise Exception(f"账号 {account.bot_id} 的客户端未初始化")

                        # 发送每个文件
                        sent_count = 0
                        failed_count = 0
                        for file_path in file_paths:
                            try:
                                await self._send_file_to_weixin(client, target_user, file_path, context_token, user_id_int)
                                sent_count += 1
                            except Exception as e:
                                print(f"❌ 发送文件 {file_path} 失败: {e}")
                                failed_count += 1

                        # 只有至少有一个文件成功发送才标记为完成
                        if sent_count > 0:
                            self.message_queue.update_file_request_status(file_request.id, FileRequestStatus.COMPLETED)
                        else:
                            self.message_queue.update_file_request_status(
                                file_request.id,
                                FileRequestStatus.FAILED,
                                error=f"所有 {len(file_paths)} 个文件发送失败"
                            )
                            print(f"❌ 文件请求 {file_request.id} 失败: 所有文件发送失败")

                except Exception as e:
                        print(f"❌ 处理文件请求失败: {e}")
                        self.message_queue.update_file_request_status(
                            file_request.id,
                            FileRequestStatus.FAILED,
                            error=str(e)
                        )

                # 等待一段时间再检查
                await asyncio.sleep(self.config.queue_send_interval)

            except Exception as e:
                print(f"❌ 检查文件请求错误: {e}")
                await asyncio.sleep(1)

    async def check_message_sequences(self):
        """检查并发送消息序列（统一的发送任务）"""
        print("🌊 消息序列检查任务已启动")

        from datetime import datetime

        # 追踪每个消息的发送状态
        message_states = {}

        while self.running:
            try:
                # 获取有待发送序列的消息
                messages = self.message_queue.get_messages_with_pending_sequences('weixin', limit=1)

                if not messages:
                    # 没有待发送的序列，检查 pending_messages 中的消息是否完成
                    for message_id in list(self.pending_messages.keys()):
                        stats = self.message_queue.get_message_sequences_stats(message_id)

                        # 检查 AI 响应是否已完成，且所有序列都已发送（和 Discord bot 完全一样的逻辑）
                        if stats["total"] > 0 and stats["pending"] == 0 and self.message_queue.is_ai_response_complete(message_id):
                            print(f"✅ [消息 #{message_id}] 所有序列已发送，AI 响应已完成")
                            # 1. 停止正在输入状态
                            await self.stop_typing_indicator(message_id)
                            # 2. 清理数据库相关序列
                            self.message_queue.cleanup_message_sequences(message_id)
                            # 3. 更新消息状态为 COMPLETED
                            self.message_queue.update_status(message_id, MessageStatus.COMPLETED)
                            # 4. 清理内存缓存
                            if message_id in message_states:
                                del message_states[message_id]
                            if message_id in self.pending_messages:
                                del self.pending_messages[message_id]

                    await asyncio.sleep(0.5)
                    continue

                message_info = messages[0]
                message_id = message_info['id']
                channel_id = message_info['discord_channel_id']
                user_id = message_info['discord_user_id']
                is_dm = message_info['is_dm']
                channel_type = message_info['channel_type']
                username = message_info['username']
                msg_context_token = message_info['context_token']

                try:

                    # 检查是否需要启动 typing indicator（AI started 但点 A 失败的情况）
                    if message_id not in self.pending_messages:
                        msg_status = self.message_queue.get_message_status(message_id)
                        if msg_status == MessageStatus.AI_STARTED:
                            # AI started 但 typing indicator 还没启动，立即启动
                            print(f"⚡ [消息 #{message_id}] AI 已开始工作但 typing indicator 未启动，尝试启动...")
                            # 查找对应的账号
                            for account in self.accounts:
                                if account.bot_id in self.clients:
                                    client = self.clients.get(account.bot_id)
                                    if client and username in self.typing_tickets:
                                        wxid = self.username_to_wxid.get(username, username)
                                        typing_ticket = self.typing_tickets.get(username)
                                        if typing_ticket:
                                            stop_event = asyncio.Event()
                                            typing_task = asyncio.create_task(
                                                self._maintain_typing_indicator(client, wxid, typing_ticket, stop_event)
                                            )
                                            self.pending_messages[message_id] = {
                                                "typing_task": typing_task,
                                                "typing_stop_event": stop_event,
                                                "typing_active": True,
                                                "from_user_id": username,
                                                "account_bot_id": account.bot_id
                                            }
                                            print(f"✅ [消息 #{message_id}] AI started 时补启动 typing indicator 成功")
                                            break

                    # 初始化消息状态
                    if message_id not in message_states:
                        message_states[message_id] = {"pending": []}

                    # 获取待发送的序列项（每次只取一条，确保严格按顺序发送）
                    pending_sequences = self.message_queue.get_pending_message_sequences(message_id, limit=1)

                    if not pending_sequences:
                        # 没有待发送的序列，检查是否完成
                        stats = self.message_queue.get_message_sequences_stats(message_id)
                        print(f"🔍 [消息 #{message_id}] 序列统计: total={stats['total']}, pending={stats['pending']}, sent={stats['sent']}")

                        # 检查 AI 响应是否已完成，且所有序列都已发送
                        # 使用和 Discord bot 相同的逻辑：pending == 0 且 AI 响应完成
                        # 但需要额外检查是否还有未处理的工具结果
                        pending_tool_results = self.message_queue.get_pending_tool_use_results()
                        pending_for_this_msg = [r for r in pending_tool_results if r["message_id"] == message_id]
                        if pending_for_this_msg:
                            print(f"🔍 [消息 #{message_id}] 还有 {len(pending_for_this_msg)} 个工具结果待处理，等待...")
                            await asyncio.sleep(0.1)
                            continue

                        if stats["total"] > 0 and stats["pending"] == 0 and self.message_queue.is_ai_response_complete(message_id):
                            print(f"✅ [消息 #{message_id}] 所有序列已发送，AI 响应已完成")
                            # 1. 停止正在输入状态
                            await self.stop_typing_indicator(message_id)
                            # 2. 清理数据库相关序列
                            self.message_queue.cleanup_message_sequences(message_id)
                            # 3. 更新消息状态为 COMPLETED
                            self.message_queue.update_status(message_id, MessageStatus.COMPLETED)
                            # 4. 清理内存缓存
                            if message_id in message_states:
                                del message_states[message_id]
                            # 5. 清理 pending_messages
                            if message_id in self.pending_messages:
                                del self.pending_messages[message_id]
                        else:
                            # 还未完成，等待下一轮
                            await asyncio.sleep(0.1)
                        continue

                    # 根据用户名选择正确的账号
                    if not self.accounts:
                        print(f"⚠️  没有可用的微信账号")
                        continue

                    # 检查 username 是配置的用户名还是原始 wxid
                    target_account = None
                    to_user_id = username  # 默认使用原始 username

                    if username in self.username_to_wxid:
                        # username 是配置的用户名（如 "鸵鸟居士"）
                        # 从用户名获取对应的 wxid
                        target_wxid = self.username_to_wxid.get(username)
                        # 找到包含该 wxid 的账号
                        for account in self.accounts:
                            if account.wxid == target_wxid:
                                target_account = account
                                break
                        # 使用配置的用户名（send_message 会自动转换为 wxid）
                        to_user_id = username
                    elif len(self.accounts) == 1:
                        # 只有一个账号，直接使用它
                        target_account = self.accounts[0]
                        # username 是原始 wxid（外部联系人），直接使用
                        to_user_id = username
                        print(f"ℹ️  使用默认账号 [{target_account.bot_id}] 发送给 [{username}]")
                    else:
                        # 多个账号，无法确定使用哪个
                        # 检查是否有 context_token，如果有则使用第一个有 token 的账号
                        if self.context_tokens.get(username) is not None:
                            # 有 context_token，使用第一个账号
                            target_account = self.accounts[0]
                            to_user_id = username
                            print(f"ℹ️  使用第一个账号 [{target_account.bot_id}] 发送给 [{username}]")
                        else:
                            # 没有 context_token，跳过这条消息（可能是旧消息）
                            # 清理这些消息序列，避免重复处理
                            self.message_queue.cleanup_message_sequences(message_id)
                            print(f"⏭️  跳过未配置用户 [{username}] 的消息序列")
                            continue

                    if not target_account:
                        print(f"⚠️  未找到用户 [{username}] 对应的账号")
                        continue

                    client = self.clients.get(target_account.bot_id)
                    if not client:
                        print(f"⚠️  账号 {target_account.bot_id} 的客户端未初始化")
                        continue

                    # 获取 context_token
                    # 优先从缓存获取，如果没有则使用消息保存的 context_token
                    context_token = self.context_tokens.get(username) or msg_context_token or ""

                    if not context_token:
                        print(f"⚠️  用户 {username} 没有有效的 context_token")
                        continue

                    # 发送序列项（只有一条）
                    seq = pending_sequences[0]
                    seq_id = seq["id"]
                    seq_index = seq["sequence_index"]
                    item_type = seq["item_type"]
                    item_data = seq["item_data"]
                    tool_use_index = seq.get("tool_use_index")  # 获取工具调用索引

                    try:
                        if item_type == "text":
                            # 发送文本消息
                            text = item_data.get("text", "")
                            if text and text.strip():
                                # 确保 typing_ticket 存在（如果不存在，自动获取）
                                if username not in self.typing_tickets:
                                    try:
                                        wxid = self.username_to_wxid.get(username, username)
                                        config_result = await client.get_config(
                                            ilink_user_id=wxid,
                                            context_token=context_token or ""
                                        )
                                        typing_ticket = config_result.get("typing_ticket", "")
                                        if typing_ticket:
                                            self.typing_tickets[username] = typing_ticket
                                            print(f"✅ 自动获取用户 {username} 的 typing_ticket 成功")
                                    except Exception as e:
                                        print(f"⚠️ 自动获取 typing_ticket 失败: {e}")

                                # 确保 typing indicator 已启动（AI started 时触发）
                                if message_id not in self.pending_messages:
                                    typing_ticket = self.typing_tickets.get(username)
                                    if typing_ticket:
                                        wxid = self.username_to_wxid.get(username, username)
                                        stop_event = asyncio.Event()
                                        typing_task = asyncio.create_task(
                                            self._maintain_typing_indicator(client, wxid, typing_ticket, stop_event)
                                        )
                                        self.pending_messages[message_id] = {
                                            "typing_task": typing_task,
                                            "typing_stop_event": stop_event,
                                            "typing_active": True,
                                            "from_user_id": username,
                                            "account_bot_id": target_account.bot_id
                                        }
                                        print(f"✅ [消息 #{message_id}] AI started，启动 typing indicator")

                                # 调试日志
                                print(f"🔍 [消息 #{message_id}] 准备发送: to_user_id={to_user_id}, context_token长度={len(context_token)}, text长度={len(text.strip())}")

                                try:
                                    # 直接发送文本
                                    await client.send_message(
                                        to_user_id=to_user_id,
                                        text=text.strip(),
                                        context_token=context_token
                                    )
                                    print(f"✅ [消息 #{message_id}] 已发送文本: {text[:30]}...")
                                except Exception as send_error:
                                    # 发送失败
                                    print(f"❌ [消息 #{message_id}] 发送失败: {send_error}")
                                    # 标记序列为已发送，避免无限重试
                                    self.message_queue.mark_sequence_sent(seq_id)
                                    # 继续下一条消息
                                    continue

                        elif item_type == "tool_use":
                            # 对于工具调用，由于微信不支持编辑消息
                            # 我们需要等待工具执行完成后再发送通知
                            # 这里先标记序列为已发送，但实际通知在 check_tool_use_results 中发送
                            tool_name = item_data.get("name", "")
                            print(f"🔧 [消息 #{message_id}] 工具调用: {tool_name} (等待执行完成)")
                            # 不发送任何消息，等待工具执行完成

                            # 保存工具调用引用（用于后续查询工具执行结果）
                            # 微信没有真实的消息 ID，使用 0 作为占位符
                            if tool_use_index is not None:
                                self.message_queue.save_tool_use_message_ref(
                                    message_id,
                                    tool_use_index,
                                    0,  # 微信没有真实的消息 ID，使用 0 作为占位符
                                    channel_id,
                                    is_dm,
                                    'weixin'
                                )

                        # 标记为已发送
                        self.message_queue.mark_sequence_sent(seq_id)

                        # 控制发送速率
                        await asyncio.sleep(self.config.queue_send_interval)

                    except Exception as e:
                        print(f"❌ 发送序列项失败: 消息#{message_id}, 序列#{seq_index}, 错误: {e}")
                        # 标记为已发送，避免无限重试
                        self.message_queue.mark_sequence_sent(seq_id)
                        import traceback
                        traceback.print_exc()

                except Exception as e:
                    print(f"❌ 处理消息序列失败: 消息#{message_id}, 错误: {e}")
                    import traceback
                    traceback.print_exc()

                # 极小延迟，避免无消息时CPU空转
                await asyncio.sleep(0.01)

            except Exception as e:
                print(f"❌ 检查消息序列时出错: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(5)

    async def check_tool_use_results(self):
        """定期检查工具执行结果并发送工具调用通知"""
        print("🔧 工具执行结果检查任务已启动")

        while self.running:
            try:
                # 检查是否启用微信工具调用通知
                if not self.config.weixin_tool_use_notification_enabled:
                    # 禁用时跳过处理，但继续运行循环以便配置更改后能生效
                    await asyncio.sleep(5)
                    continue

                # 获取待处理的工具执行结果（只处理微信频道的）
                pending_results = self.message_queue.get_pending_tool_use_results(channel_type='weixin')

                if pending_results:
                    print(f"🔍 找到 {len(pending_results)} 个待处理的工具执行结果")

                for result in pending_results:
                    message_id = result['message_id']
                    tool_use_index = result['tool_use_index']
                    success = result['success']

                    print(f"🔧 处理工具执行结果: 消息#{message_id}, 索引#{tool_use_index}, 成功={success}")

                    try:
                        # 从数据库获取消息信息
                        import sqlite3
                        conn = sqlite3.connect(self.config.database_path)
                        cursor = conn.cursor()
                        cursor.execute("""
                                       SELECT username, discord_channel_id, context_token
                                       FROM messages
                                       WHERE id = ?
                                       """, (message_id,))
                        row = cursor.fetchone()
                        conn.close()

                        if not row:
                            # 找不到消息信息，标记为已处理
                            self.message_queue.mark_tool_use_result_processed(message_id, tool_use_index)
                            continue

                        username, channel_id, msg_context_token = row

                        # 根据用户名选择正确的账号
                        if not self.accounts:
                            print(f"⚠️  没有可用的微信账号")
                            continue

                        # 检查 username 是配置的用户名还是原始 wxid
                        target_account = None
                        to_user_id = username  # 默认使用原始 username

                        if username in self.username_to_wxid:
                            # username 是配置的用户名（如 "鸵鸟居士"）
                            # 从用户名获取对应的 wxid
                            target_wxid = self.username_to_wxid.get(username)
                            # 找到包含该 wxid 的账号
                            for account in self.accounts:
                                if account.wxid == target_wxid:
                                    target_account = account
                                    break
                            # 使用配置的用户名（send_message 会自动转换为 wxid）
                            to_user_id = username
                        elif len(self.accounts) == 1:
                            # 只有一个账号，直接使用它
                            target_account = self.accounts[0]
                            # username 是原始 wxid（外部联系人），直接使用
                            to_user_id = username
                            print(f"ℹ️  使用默认账号 [{target_account.bot_id}] 发送给 [{username}]")
                        else:
                            # 多个账号，无法确定使用哪个
                            # 检查是否有 context_token，如果有则使用第一个有 token 的账号
                            if self.context_tokens.get(username) is not None:
                                # 有 context_token，使用第一个账号
                                target_account = self.accounts[0]
                                to_user_id = username
                                print(f"ℹ️  使用第一个账号 [{target_account.bot_id}] 发送给 [{username}]")
                            else:
                                # 没有 context_token，跳过这条消息
                                # 标记为已处理，避免重复处理
                                self.message_queue.mark_tool_use_result_processed(message_id, tool_use_index)
                                print(f"⏭️  跳过未配置用户 [{username}] 的工具调用通知")
                                continue

                        if not target_account:
                            print(f"⚠️  未找到用户 [{username}] 对应的账号，跳过工具调用通知")
                            # 标记为已处理
                            self.message_queue.mark_tool_use_result_processed(message_id, tool_use_index)
                            continue

                        client = self.clients.get(target_account.bot_id)
                        if not client:
                            print(f"⚠️  账号 {target_account.bot_id} 的客户端未初始化，跳过工具调用通知")
                            # 标记为已处理
                            self.message_queue.mark_tool_use_result_processed(message_id, tool_use_index)
                            continue

                        # 获取 context_token
                        # 优先从缓存获取，如果没有则使用消息保存的 context_token
                        context_token = self.context_tokens.get(username) or msg_context_token or ""

                        if not context_token:
                            print(f"⚠️  用户 {username} 没有有效的 context_token，跳过工具调用通知")
                            # 标记为已处理，避免重复处理
                            self.message_queue.mark_tool_use_result_processed(message_id, tool_use_index)
                            continue

                        # 获取工具调用信息
                        tool_uses = self.message_queue.get_tool_uses(message_id)
                        if tool_use_index >= len(tool_uses):
                            print(f"⚠️  工具调用索引 {tool_use_index} 超出范围，跳过")
                            # 标记为已处理
                            self.message_queue.mark_tool_use_result_processed(message_id, tool_use_index)
                            continue

                        tool_use = tool_uses[tool_use_index]
                        tool_name = tool_use.get('name', '')
                        tool_input = tool_use.get('input', {})

                        # 构建工具调用通知文本
                        status_emoji = "✅" if success else "❌"

                        # 从配置文件读取工具 emoji 映射
                        TOOL_EMOJIS = self.config.tool_emoji_mapping

                        # 检查是否是 MCP 工具
                        is_mcp = tool_name.startswith('mcp__')

                        if is_mcp:
                            # MCP 工具：提取服务器名和工具名
                            parts = tool_name.split('__')
                            if len(parts) >= 3:
                                mcp_server = parts[1]
                                mcp_tool = parts[2]

                                emoji = TOOL_EMOJIS.get(tool_name)
                                if emoji is None:
                                    emoji = TOOL_EMOJIS.get(mcp_server, "🔧")

                                tool_title = f"{emoji} MCP {mcp_server}"
                                tool_desc = mcp_tool
                            else:
                                emoji = TOOL_EMOJIS.get(tool_name, "🔧")
                                tool_title = f"{emoji} {tool_name}"
                                tool_desc = "无参数"
                        else:
                            emoji = TOOL_EMOJIS.get(tool_name, "🔧")
                            tool_title = f"{emoji} {tool_name}"

                            # 智能显示参数（为每个工具定制显示内容）
                            tool_desc = None

                            if tool_name == 'Read':
                                tool_desc = tool_input.get('file_path', '无路径')
                            elif tool_name == 'Write':
                                tool_desc = tool_input.get('file_path', '无路径')
                            elif tool_name == 'Edit':
                                tool_desc = tool_input.get('file_path', '无路径')
                            elif tool_name == 'Glob':
                                pattern = tool_input.get('pattern', '无 pattern')
                                path = tool_input.get('path', '')
                                if path:
                                    tool_desc = f"{path}: {pattern}"
                                else:
                                    tool_desc = pattern
                            elif tool_name == 'Grep':
                                tool_desc = tool_input.get('pattern', '无 pattern')
                            elif tool_name == 'Bash':
                                cmd = tool_input.get('command', '')
                                if len(cmd) > 100:
                                    cmd = cmd[:97] + "..."
                                tool_desc = cmd
                            elif tool_name == 'WebSearch':
                                tool_desc = tool_input.get('query', '无 query')
                            elif tool_name == 'Skill':
                                tool_desc = tool_input.get('skill', '无 skill')
                            elif tool_name == 'Agent':
                                desc = tool_input.get('description', '')
                                subagent = tool_input.get('subagent_type', 'general-purpose')
                                tool_desc = f"{subagent}: {desc}"
                            elif tool_name == 'EnterPlanMode':
                                tool_desc = "进入计划模式"
                            elif tool_name == 'ExitPlanMode':
                                tool_desc = "退出计划模式"
                            elif 'prompt' in tool_input:
                                tool_desc = tool_input['prompt']
                                if len(tool_desc) > 50:
                                    tool_desc = tool_desc[:47] + "..."
                            else:
                                tool_desc = "无参数"

                        # 构建通知文本（只显示状态 emoji，不显示"成功"/"失败"文字）
                        notification_text = f"{status_emoji} {tool_title}"

                        if tool_desc and tool_desc != "无参数":
                            notification_text += f"\n{tool_desc}"

                        # 确保 typing_ticket 存在（如果不存在，自动获取）
                        if username not in self.typing_tickets:
                            try:
                                wxid = self.username_to_wxid.get(username, username)
                                config_result = await client.get_config(
                                    ilink_user_id=wxid,
                                    context_token=context_token or ""
                                )
                                typing_ticket = config_result.get("typing_ticket", "")
                                if typing_ticket:
                                    self.typing_tickets[username] = typing_ticket
                                    print(f"✅ 自动获取用户 {username} 的 typing_ticket 成功")
                            except Exception as e:
                                print(f"⚠️ 自动获取 typing_ticket 失败: {e}")

                        print(f"🔍 [消息 #{message_id}] 准备发送工具调用通知:")
                        print(f"   to_user_id={to_user_id}")
                        print(f"   context_token长度={len(context_token)}")
                        print(f"   通知文本={notification_text[:50]}...")

                        # 发送通知到微信
                        try:
                            await client.send_message(
                                to_user_id=to_user_id,
                                text=notification_text,
                                context_token=context_token
                            )
                            print(f"✅ [消息 #{message_id}] 已发送工具调用通知: {tool_name} - {'成功' if success else '失败'}")
                        except Exception as send_error:
                            print(f"❌ [消息 #{message_id}] 发送工具调用通知失败: {send_error}")
                            # 标记为已处理，避免无限重试
                            self.message_queue.mark_tool_use_result_processed(message_id, tool_use_index)
                            continue

                    except Exception as e:
                        print(f"❌ 发送工具调用通知失败: 消息#{message_id}, 工具#{tool_use_index}, 错误: {e}")
                        import traceback
                        traceback.print_exc()

                    # 标记为已处理
                    self.message_queue.mark_tool_use_result_processed(message_id, tool_use_index)

                # 等待一段时间再检查（1秒）
                await asyncio.sleep(1)

            except Exception as e:
                print(f"❌ 检查工具执行结果时出错: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(5)

    async def _send_file_to_weixin(self, client: WeixinClient, to_user_id: str, file_path: str, context_token: str, user_id: int):
        """发送文件到微信

        Args:
            client: 微信客户端
            to_user_id: 接收者用户名
            file_path: 文件路径
            context_token: 上下文 token
            user_id: 用户整数 ID（用于查找 wxid）
        """
        import mimetypes
        import hashlib
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad
        import os

        # 检查文件是否存在
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")

        # 获取文件信息
        file_size = os.path.getsize(file_path)
        file_name = os.path.basename(file_path)

        # 判断 MIME 类型
        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type is None:
            mime_type = "application/octet-stream"

        # 将 user_id（整数）转换为原始微信 wxid
        import json
        target_wxid = None

        try:
            accounts_file = self.config.weixin_accounts_file
            if os.path.exists(accounts_file):
                with open(accounts_file, 'r', encoding='utf-8') as f:
                    accounts = json.load(f)
                for acc in accounts:
                    if acc.get("user_id") == user_id:
                        target_wxid = acc.get("wxid")
                        break
        except Exception as e:
            print(f"⚠️ 读取账号配置失败: {e}")

        if not target_wxid:
            raise Exception(f"未找到用户 wxid: user_id={user_id}")

        # 读取文件并计算 MD5
        with open(file_path, 'rb') as f:
            plaintext = f.read()
        rawfilemd5 = hashlib.md5(plaintext).hexdigest()

        # 生成 AES 密钥和 filekey
        aeskey = os.urandom(16)
        filekey = os.urandom(16).hex()

        # 计算加密后大小
        cipher = AES.new(aeskey, AES.MODE_ECB)
        ciphertext = cipher.encrypt(pad(plaintext, AES.block_size))
        filesize = len(ciphertext)

        # 确定媒体类型
        if mime_type.startswith("video/"):
            media_type = 2
            message_type = "video"
        elif mime_type.startswith("image/"):
            media_type = 1
            message_type = "image"
        else:
            media_type = 3
            message_type = "file"

        # 获取上传 URL
        upload_resp = await client.get_upload_url(
            filekey=filekey,
            media_type=media_type,
            to_user_id=target_wxid,
            rawsize=file_size,
            rawfilemd5=rawfilemd5,
            filesize=filesize,
            aeskey=aeskey.hex(),
            no_need_thumb=True
        )

        upload_param = upload_resp.get("upload_param")
        if not upload_param:
            raise Exception("获取上传参数失败")

        # 上传到 CDN
        download_param = await client.upload_to_cdn(
            file_path=file_path,
            upload_param=upload_param,
            filekey=filekey,
            aeskey=aeskey,
            filesize=filesize
        )

        # 构造媒体信息
        import base64
        media_info = {
            "encrypt_query_param": download_param,
            "aes_key": base64.b64encode(aeskey).decode('utf-8'),
            "filesize_ciphertext": filesize
        }

        # 发送媒体消息
        print(f"📤 [文件发送] to_user_id={target_wxid}, media_type={message_type}, file={file_name}")
        print(f"📤 [文件发送] context_token长度={len(context_token) if context_token else 0}")
        result = await client.send_media_message(
            to_user_id=target_wxid,
            media_type=message_type,
            media_info=media_info,
            context_token=context_token,
            file_name=file_name,
            filesize=file_size
        )
        print(f"✅ [文件发送] 成功发送媒体消息: {result}")

    async def _handle_message(self, msg: dict, account_id: str):
        """处理单条消息"""
        try:
            # 解析消息
            from_user_id = msg.get("from_user_id")
            message_type = msg.get("message_type")  # 1 = USER, 2 = BOT
            context_token = msg.get("context_token")

            # 只处理用户消息 (message_type = 1)
            if message_type != 1:
                return

            # 更新 context_token 缓存（自动持久化到磁盘）
            if context_token:
                self.context_tokens.set(from_user_id, context_token)

            # 从配置中获取 user_id
            user_id_int = self.username_to_userid.get(from_user_id)
            if user_id_int is None:
                return

            self.id_to_username[user_id_int] = from_user_id

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
            # 从配置中获取 user_id（不再动态计算）

            queue_msg = Message(
                id=None,
                direction=MessageDirection.TO_CLAUDE.value,
                content=content,
                status=MessageStatus.PENDING.value,
                discord_channel_id=user_id_int,  # 用发送者 ID 作为频道 ID
                discord_message_id=int(msg.get("message_id", 0)),
                discord_user_id=user_id_int,
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

            # 获取 typing ticket（如果还没有的话）
            if from_user_id not in self.typing_tickets:
                client = self.clients.get(account_id)
                if client:
                    try:
                        # 获取用户的原始 wxid
                        wxid = self.username_to_wxid.get(from_user_id, from_user_id)
                        config_result = await client.get_config(
                            ilink_user_id=wxid,
                            context_token=context_token or ""
                        )
                        typing_ticket = config_result.get("typing_ticket", "")
                        if typing_ticket:
                            self.typing_tickets[from_user_id] = typing_ticket
                            print(f"✅ 获取到用户 {from_user_id} 的 typing ticket")
                    except Exception as e:
                        print(f"⚠️ 获取 typing ticket 失败: {e}")

            # 启动 typing indicator（确保 typing_ticket 存在）
            if from_user_id not in self.typing_tickets:
                print(f"⚠️ [消息 #{message_id}] 点 A: 等待获取 typing_ticket...")
                client = self.clients.get(account_id)
                if client:
                    try:
                        wxid = self.username_to_wxid.get(from_user_id, from_user_id)
                        config_result = await client.get_config(
                            ilink_user_id=wxid,
                            context_token=context_token or ""
                        )
                        typing_ticket = config_result.get("typing_ticket", "")
                        if typing_ticket:
                            self.typing_tickets[from_user_id] = typing_ticket
                            print(f"✅ [消息 #{message_id}] 点 A: 自动获取 typing_ticket 成功")
                    except Exception as e:
                        print(f"⚠️ [消息 #{message_id}] 点 A: 获取 typing_ticket 失败: {e}")

            # 尝试启动 typing indicator
            if from_user_id in self.typing_tickets:
                self.start_typing_indicator(message_id, from_user_id, account_id)
            else:
                print(f"⚠️ [消息 #{message_id}] 点 A: 未能启动 typing indicator (typing_ticket 不存在)")

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
        # 从配置中获取 user_id
        user_id_int = self.username_to_userid.get(from_user_id)
        if user_id_int is None:
            await self._send_to_weixin(self.clients[account_bot_id], f"⚠️  未找到用户 [{from_user_id}] 的配置", from_user_id, "")
            return

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
        # 从配置中获取 user_id
        user_id_int = self.username_to_userid.get(from_user_id)
        if user_id_int is None:
            await self._send_to_weixin(self.clients[account_bot_id], f"⚠️  未找到用户 [{from_user_id}] 的配置", from_user_id, "")
            return

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
            # 停止正在输入状态
            await self.stop_typing_indicator(message_to_abort.id)

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

        context_token = self.context_tokens.get(to_user_id) or ""
        if not context_token:
            raise Exception(f"context_token is required but missing for user {to_user_id}")

        await client.send_message(
            to_user_id=to_user_id,
            text=text,
            context_token=context_token
        )

    async def _maintain_typing_indicator(self, client: WeixinClient, ilink_user_id: str, typing_ticket: str, stop_event: asyncio.Event):
        """
        维持 typing indicator（正在输入状态）

        使用持续刷新模式，每 8 秒刷新一次（微信 typing ticket 默认持续 10 秒）

        Args:
            client: 微信客户端
            ilink_user_id: 用户 ID（原始 wxid）
            typing_ticket: typing 票据
            stop_event: 停止事件
        """
        retry_count = 0
        max_retries = 3
        retry_delay = 5

        try:
            while self.running and not stop_event.is_set():
                try:
                    # 微信 typing indicator 默认持续 10 秒
                    # 我们每 8 秒刷新一次，确保有足够余量避免中断
                    await client.send_typing(
                        ilink_user_id=ilink_user_id,
                        typing_ticket=typing_ticket,
                        status=1  # 1 = 正在输入
                    )
                    # 使用 wait_for 来响应停止事件，最多等待 8 秒
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=8)
                        # stop_event 被设置，退出循环
                        break
                    except asyncio.TimeoutError:
                        # 8 秒超时，继续下一轮循环
                        pass

                    # 成功完成一次循环，重置重试计数
                    retry_count = 0

                except asyncio.CancelledError:
                    # 任务被取消，正常退出
                    break
                except Exception as e:
                    retry_count += 1
                    print(f"⚠️ 维持 typing indicator 时出错 (第{retry_count}次): {e}")

                    if retry_count >= max_retries:
                        print(f"❌ 维持 typing indicator 失败，已达最大重试次数 ({max_retries})，停止尝试")
                        break

                    print(f"🔄 {retry_delay}秒后重试...")
                    # 使用 wait_for 来响应停止事件
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=retry_delay)
                        break
                    except asyncio.TimeoutError:
                        pass

        except asyncio.CancelledError:
            # 任务被取消，正常退出
            pass
        except Exception as e:
            print(f"❌ 维持 typing indicator 时发生未预期错误: {e}")

    def start_typing_indicator(self, message_id: int, from_user_id: str, account_bot_id: str):
        """
        启动指定消息对应的 typing indicator 任务

        Args:
            message_id: 消息记录在数据库中的唯一 ID
            from_user_id: 用户名（如"鸵鸟居士"）
            account_bot_id: 微信账号 bot_id
        """
        client = self.clients.get(account_bot_id)
        if not client:
            print(f"⚠️ 账号 {account_bot_id} 的客户端未初始化，无法启动 typing indicator")
            return

        # 获取用户的原始 wxid
        wxid = self.username_to_wxid.get(from_user_id)
        if not wxid:
            print(f"⚠️ 用户 {from_user_id} 的 wxid 未找到，无法启动 typing indicator")
            return

        # 检查是否已经有 typing ticket
        typing_ticket = self.typing_tickets.get(from_user_id)
        if not typing_ticket:
            print(f"⚠️ 用户 {from_user_id} 的 typing ticket 未找到，无法启动 typing indicator")
            return

        # 创建停止事件
        stop_event = asyncio.Event()

        # 创建 typing indicator 任务
        typing_task = asyncio.create_task(
            self._maintain_typing_indicator(client, wxid, typing_ticket, stop_event)
        )

        # 保存到 pending_messages
        self.pending_messages[message_id] = {
            "typing_task": typing_task,
            "typing_stop_event": stop_event,
            "typing_active": True,
            "from_user_id": from_user_id,
            "account_bot_id": account_bot_id
        }

        print(f"✅ [消息 #{message_id}] 已启动 typing indicator")

    async def stop_typing_indicator(self, message_id: int):
        """
        停止指定消息对应的 typing indicator 任务

        Args:
            message_id: 消息记录在数据库中的唯一 ID
        """
        # 从 pending_messages 字典中找到这个消息记录并取消它的 typing_task
        if message_id in self.pending_messages:
            msg_info = self.pending_messages[message_id]
            task = msg_info.get("typing_task")
            stop_event = msg_info.get("typing_stop_event")
            from_user_id = msg_info.get("from_user_id")
            account_bot_id = msg_info.get("account_bot_id")

            # 检查是否已经在停止状态
            if not msg_info.get("typing_active", False):
                # 已经停止，静默返回
                return

            # 首先设置停止事件，这会立即停止 _maintain_typing_indicator 循环
            if stop_event:
                stop_event.set()
                print(f"🛑 [消息 #{message_id}] 已设置停止事件")

            # 然后发送取消状态给微信 API
            if from_user_id and account_bot_id:
                client = self.clients.get(account_bot_id)
                typing_ticket = self.typing_tickets.get(from_user_id)
                if client and typing_ticket:
                    try:
                        wxid = self.username_to_wxid.get(from_user_id)
                        if wxid:
                            await client.send_typing(
                                ilink_user_id=wxid,
                                typing_ticket=typing_ticket,
                                status=2  # 2 = 取消输入
                            )
                            print(f"🛑 [消息 #{message_id}] 已发送取消输入状态")
                    except Exception as e:
                        print(f"⚠️ 发送取消输入状态失败: {e}")

            # 取消任务（如果还在运行）
            if task and not task.done():
                task.cancel()  # 这会触发 _maintain_typing_indicator 中的 CancelledError
                try:
                    await task  # 等待任务完全停止
                except asyncio.CancelledError:
                    pass
                print(f"🛑 [消息 #{message_id}] 已停止 typing indicator 任务")

            # 更新状态为已停止
            msg_info["typing_active"] = False
            msg_info["typing_task"] = None
        else:
            # 消息不在缓存中，可能已经被清理，静默返回
            pass



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