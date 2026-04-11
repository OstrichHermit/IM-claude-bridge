"""
微信 Bot 消息序列发送模块
处理消息序列的发送
"""
import asyncio
import os
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.message_queue import MessageStatus
from shared.logger import get_logger

log = get_logger("WeixinBot", "weixin")


class WeixinSequenceSenderMixin:
    """消息序列发送 Mixin"""

    async def check_message_sequences(self):
        """检查并发送消息序列（统一的发送任务）"""
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

                    # 根据用户名选择正确的账号（提前解析，和 Discord bot 一致：发现消息就启动 typing）
                    target_account = None
                    to_user_id = username  # 默认使用原始 username

                    if self.accounts and username in self.username_to_wxid:
                        target_wxid = self.username_to_wxid.get(username)
                        for account in self.accounts:
                            if account.wxid == target_wxid:
                                target_account = account
                                break
                        to_user_id = username
                    elif self.accounts and len(self.accounts) == 1:
                        target_account = self.accounts[0]
                        to_user_id = username
                    elif self.accounts and self.context_tokens.get(username) is not None:
                        target_account = self.accounts[0]
                        to_user_id = username
                    elif self.accounts and user_id and user_id in self.userid_to_user:
                        user_info = self.userid_to_user[user_id]
                        resolved_username = user_info['username']
                        target_wxid = user_info['wxid']
                        for account in self.accounts:
                            if account.wxid == target_wxid:
                                target_account = account
                                to_user_id = resolved_username
                                username = resolved_username  # 修正 username，确保后续 context_token 等查找正确
                                break

                    # 发现外部消息：立即占位 + 尝试启动 typing indicator（和 Discord bot 一致的逻辑）
                    if message_id not in self.pending_messages:
                        log.log(f"📨 [消息 #{message_id}] 已加载外部消息: {username}")

                        # 尝试立即启动 typing indicator
                        typing_task = None
                        typing_stop_event = None
                        typing_active = False
                        account_bot_id = None

                        if target_account:
                            client = self.clients.get(target_account.bot_id)
                            wxid = self.username_to_wxid.get(username, username)
                            typing_ticket = self.typing_tickets.get(username)
                            # 优先从持久化缓存取 context_token（和发送序列时的逻辑一致）
                            effective_context_token = self.context_tokens.get(username) or msg_context_token or ""
                            if not typing_ticket and client and effective_context_token:
                                # 尝试通过 get_config 获取 typing_ticket
                                try:
                                    config_result = await client.get_config(
                                        ilink_user_id=wxid,
                                        context_token=effective_context_token
                                    )
                                    typing_ticket = config_result.get("typing_ticket", "")
                                    if typing_ticket:
                                        self.typing_tickets[username] = typing_ticket
                                except Exception:
                                    pass
                            if typing_ticket and client:
                                typing_stop_event = asyncio.Event()
                                typing_task = asyncio.create_task(
                                    self._maintain_typing_indicator(client, wxid, typing_ticket, typing_stop_event)
                                )
                                typing_active = True
                                account_bot_id = target_account.bot_id

                        self.pending_messages[message_id] = {
                            "typing_task": typing_task,
                            "typing_stop_event": typing_stop_event,
                            "typing_active": typing_active,
                            "from_user_id": username,
                            "account_bot_id": account_bot_id
                        }

                    # 初始化消息状态
                    if message_id not in message_states:
                        message_states[message_id] = {"pending": []}

                    # 获取待发送的序列项（每次只取一条，确保严格按顺序发送）
                    pending_sequences = self.message_queue.get_pending_message_sequences(message_id, limit=1)

                    if not pending_sequences:
                        # 没有待发送的序列，检查是否完成
                        stats = self.message_queue.get_message_sequences_stats(message_id)

                        # 检查 AI 响应是否已完成，且所有序列都已发送
                        # 使用和 Discord bot 相同的逻辑：pending == 0 且 AI 响应完成
                        # 但需要额外检查是否还有未处理的工具结果
                        pending_tool_results = self.message_queue.get_pending_tool_use_results()
                        pending_for_this_msg = [r for r in pending_tool_results if r["message_id"] == message_id]
                        if pending_for_this_msg:
                            await asyncio.sleep(0.1)
                            continue

                        if stats["total"] > 0 and stats["pending"] == 0 and self.message_queue.is_ai_response_complete(message_id):
                            log.log(f"✅ [消息 #{message_id}] 所有序列已发送，AI 响应已完成")
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
                            #还未完成，等待下一轮
                            await asyncio.sleep(0.1)
                        continue

                    # 确保有可用的账号（提前解析可能失败时的兜底逻辑）
                    if not target_account:
                        if not self.accounts:
                            continue
                        if username in self.username_to_wxid:
                            target_wxid = self.username_to_wxid.get(username)
                            for account in self.accounts:
                                if account.wxid == target_wxid:
                                    target_account = account
                                    break
                            if target_account:
                                to_user_id = username
                        if not target_account and len(self.accounts) == 1:
                            target_account = self.accounts[0]
                            to_user_id = username
                        if not target_account and self.context_tokens.get(username) is not None:
                            target_account = self.accounts[0]
                            to_user_id = username
                        if not target_account and user_id and user_id in self.userid_to_user:
                            user_info = self.userid_to_user[user_id]
                            resolved_username = user_info['username']
                            target_wxid = user_info['wxid']
                            for account in self.accounts:
                                if account.wxid == target_wxid:
                                    target_account = account
                                    to_user_id = resolved_username
                                    username = resolved_username  # 修正 username，确保后续 context_token 等查找正确
                                    break
                        if not target_account:
                            log.log(f"⚠️ [消息 #{message_id}] 无法解析目标账号: username={username}, user_id={user_id}，消息序列已清理")
                            self.message_queue.cleanup_message_sequences(message_id)
                            continue

                    if not target_account:
                        continue

                    client = self.clients.get(target_account.bot_id)
                    if not client:
                        continue

                    # 获取 context_token
                    # 优先从缓存获取，如果没有则使用消息保存的 context_token
                    context_token = self.context_tokens.get(username) or msg_context_token or ""

                    if not context_token:
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
                                    except Exception as e:
                                        pass

                                # 确保 typing indicator 已启动（后备：如果前面因为缺少 typing_ticket 没启动）
                                pending_info = self.pending_messages.get(message_id)
                                if pending_info and not pending_info.get("typing_active"):
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

                                # 调试日志

                                try:
                                    # 直接发送文本
                                    await client.send_message(
                                        to_user_id=to_user_id,
                                        text=text.strip(),
                                        context_token=context_token
                                    )
                                    log.log(f"✅ [消息 #{message_id}] 已发送: {text[:30]}...")
                                except Exception as send_error:
                                    # 发送失败
                                    log.log(f"❌ [消息 #{message_id}] 发送失败: {send_error}")
                                    # 标记序列为已发送，避免无限重试
                                    self.message_queue.mark_sequence_sent(seq_id)
                                    # 继续下一条消息
                                    continue

                        elif item_type == "sticker":
                            # 表情包：从 item_data 获取文件路径，发送为图片
                            sticker_path = item_data.get("file_path", "") if item_data else ""
                            if sticker_path:
                                try:
                                    await self._send_sticker_image(client, to_user_id, sticker_path, context_token)
                                    log.log(f"✅ [消息 #{message_id}] 已发送表情包: {os.path.basename(sticker_path)}")
                                except Exception as e:
                                    log.log(f"❌ [消息 #{message_id}] 表情包发送失败: {sticker_path} - {e}")
                            else:
                                log.log(f"⚠️ [消息 #{message_id}] 表情包文件路径为空")

                        elif item_type == "tool_use":
                            # 对于工具调用，由于微信不支持编辑消息
                            # 我们需要等待工具执行完成后再发送通知
                            # 这里先标记序列为已发送，但实际通知在 check_tool_use_results 中发送
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

                        elif item_type == "file":
                            # 文件发送：从 item_data 获取文件路径列表
                            file_paths = item_data.get("file_paths", []) if item_data else []
                            sent_count = 0
                            for fp in file_paths:
                                if fp and os.path.exists(fp):
                                    try:
                                        await self._send_file_to_weixin(client, to_user_id, fp, context_token, user_id)
                                        sent_count += 1
                                        log.log(f"✅ [消息 #{message_id}] 已发送文件: {os.path.basename(fp)}")
                                    except Exception as e:
                                        log.log(f"❌ [消息 #{message_id}] 文件发送失败: {fp} - {e}")
                            if sent_count == 0:
                                log.log(f"⚠️ [消息 #{message_id}] 没有有效的文件可发送")

                        # 标记为已发送
                        self.message_queue.mark_sequence_sent(seq_id)

                        # 控制发送速率
                        await asyncio.sleep(self.config.queue_send_interval)

                    except Exception as e:
                        log.log(f"❌ 发送序列项失败: 消息#{message_id}, 序列#{seq_index}, 错误: {e}")
                        # 标记为已发送，避免无限重试
                        self.message_queue.mark_sequence_sent(seq_id)

                except Exception as e:
                    log.log(f"❌ 处理消息序列失败: 消息#{message_id}, 错误: {e}")

                # 极小延迟，避免无消息时CPU空转
                await asyncio.sleep(0.01)

            except Exception as e:
                log.log(f"❌ 检查消息序列时出错: {e}")
                await asyncio.sleep(5)

    async def _send_sticker_image(self, client, to_user_id: str, file_path: str, context_token: str):
        """发送表情包图片到微信（轻量版，专用于表情包场景）

        Args:
            client: WeixinClient 实例
            to_user_id: 接收者的 wxid 或 username
            file_path: 表情包图片的本地路径
            context_token: 上下文 token
        """
        import hashlib
        import base64
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad

        # 将 username 转换为 wxid（get_upload_url 需要 wxid）
        target_wxid = self.username_to_wxid.get(to_user_id, to_user_id)

        # 检查文件是否存在
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")

        # 读取文件并计算 MD5
        with open(file_path, 'rb') as f:
            plaintext = f.read()
        rawfilemd5 = hashlib.md5(plaintext).hexdigest()

        # 生成随机 AES key 和 filekey
        aeskey = os.urandom(16)
        filekey = os.urandom(16).hex()

        # AES-128-ECB 加密文件
        cipher = AES.new(aeskey, AES.MODE_ECB)
        ciphertext = cipher.encrypt(pad(plaintext, AES.block_size))
        filesize = len(ciphertext)

        # 获取上传 URL（media_type=1 表示图片）
        upload_resp = await client.get_upload_url(
            filekey=filekey,
            media_type=1,
            to_user_id=target_wxid,
            rawsize=len(plaintext),
            rawfilemd5=rawfilemd5,
            filesize=filesize,
            aeskey=aeskey.hex(),
            no_need_thumb=True
        )

        upload_param = upload_resp.get("upload_param")
        if not upload_param:
            raise Exception("获取上传参数失败")

        # 上传加密文件到 CDN
        download_param = await client.upload_to_cdn(
            file_path=file_path,
            upload_param=upload_param,
            filekey=filekey,
            aeskey=aeskey,
            filesize=filesize
        )

        # 构造媒体信息
        media_info = {
            "encrypt_query_param": download_param,
            "aes_key": base64.b64encode(aeskey.hex().encode('utf-8')).decode('utf-8'),
            "filesize_ciphertext": filesize
        }

        # 发送图片消息
        await client.send_media_message(
            to_user_id=target_wxid,
            media_type="image",
            media_info=media_info,
            context_token=context_token
        )

    async def _send_file_to_weixin(self, client: "WeixinClient", to_user_id: str, file_path: str, context_token: str, user_id: int):
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
            log.log(f"❌ 读取账号配置失败: {e}")

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
            "aes_key": base64.b64encode(aeskey.hex().encode('utf-8')).decode('utf-8'),
            "filesize_ciphertext": filesize
        }

        # 发送媒体消息
        log.log(f"📤 [文件发送] to_user={target_wxid}, type={message_type}, file={file_name}, size={file_size}")
        result = await client.send_media_message(
            to_user_id=target_wxid,
            media_type=message_type,
            media_info=media_info,
            context_token=context_token,
            file_name=file_name,
            filesize=file_size
        )
        log.log(f"✅ [文件发送] 成功: {file_name}")