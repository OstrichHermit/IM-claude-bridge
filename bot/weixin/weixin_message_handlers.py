"""
微信 Bot 消息处理模块
处理用户消息和附件引用消息
"""
import os
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.message_queue import Message, MessageDirection, MessageStatus, MessageTag, ChannelType, AttachmentInfo
from shared.logger import get_logger
from bot.weixin.weixin_media import MediaType, WeixinMediaHandler, WeixinFileMapping

log = get_logger("WeixinBot", "weixin")


class WeixinMessageHandlersMixin:
    """消息处理 Mixin"""

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

            # 解析消息内容（获取文本和引用的文件）
            content, ref_files = await self._parse_message_content(msg)

            # 如果没有内容（只有文件消息），不发送给 AI
            if not content:
                return

            log.log(f"📨 [{from_user_id}] 收到消息: {content[:50]}...")
            if ref_files:
                log.log(f"📎 引用了 {len(ref_files)} 个文件")

            # 检查是否是命令
            content_stripped = content.strip()
            if content_stripped.startswith("/"):
                await self._handle_command(from_user_id, content_stripped, account_id)
                return

            # 构造消息队列消息
            # 将引用的文件信息转换为 AttachmentInfo 对象
            attachments = None
            if ref_files:
                attachments = []
                for f in ref_files:
                    # 获取文件大小
                    file_size = 0
                    try:
                        file_size = os.path.getsize(f["file_path"])
                    except Exception:
                        pass

                    attachments.append(AttachmentInfo(
                        id=int(f.get("message_id", 0)),  # 使用 message_id 作为 ID
                        filename=f["filename"],  # 文件名
                        size=file_size,  # 文件大小
                        url=f"file://{f['file_path']}",  # 本地文件路径作为 URL
                        local_filename=f["filename"],  # 本地文件名
                        description=None  # 无描述
                    ))

            queue_msg = Message(
                id=None,
                direction=MessageDirection.TO_CLAUDE.value,
                content=content,
                status=MessageStatus.PENDING.value,
                discord_channel_id=user_id_int,  # 用发送者 ID 作为频道 ID
                discord_message_id=int(msg.get("message_id", 0)),
                discord_user_id=user_id_int,
                username=from_user_id,  # 这里直接存 "用户名"
                is_dm=True,  # 微信都是私聊
                is_external=False,
                tag=MessageTag.DEFAULT.value,
                channel_type=ChannelType.WEIXIN.value,  # 微信频道
                context_token=context_token,  # 保存 context_token 用于回复
                attachments=attachments  # 添加附件信息
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
                    except Exception as e:
                        pass

            # 启动 typing indicator（确保 typing_ticket 存在）
            if from_user_id not in self.typing_tickets:
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
                    except Exception as e:
                        pass

            # 尝试启动 typing indicator
            if from_user_id in self.typing_tickets:
                self.start_typing_indicator(message_id, from_user_id, account_id)

        except Exception as e:
            log.log(f"❌ 处理消息失败: {e}")

    async def _parse_message_content(self, msg: dict) -> tuple[str | None, list[dict]]:
        """解析消息内容

        Returns:
            (文本内容, 引用的文件列表)
        """
        try:
            # 获取消息 ID，用于文件命名
            message_id = msg.get("message_id", "")
            item_list = msg.get("item_list", [])
            if not item_list:
                return None, []

            content_parts = []
            ref_files = []

            for item in item_list:
                item_type = item.get("type")

                # 文本消息
                if item_type == MediaType.TEXT:
                    text_item = item.get("text_item", {})
                    text = text_item.get("text", "")

                    # 检查是否有引用消息
                    ref_msg = item.get("ref_msg")
                    if ref_msg:
                        _, found_files = self._parse_ref_message_and_lookup(ref_msg, msg.get("from_user_id", ""))
                        ref_files.extend(found_files)

                    content_parts.append(text)

                # 图片消息（只下载保存，不加入返回列表）
                elif item_type == MediaType.IMAGE:
                    filepath = await self.media_handler.download_media_item(
                        item,
                        label=f"inbound_{message_id}"
                    )
                    if filepath:
                        filename = Path(filepath).name
                        # 从文件获取实际大小
                        import os
                        file_size = os.path.getsize(filepath)
                        # 保存文件映射：file_size → filename
                        self.file_mapping.add_file(filename, file_size)
                        log.log(f"📎 图片已下载: {filename} ({file_size} bytes)")
                    # 图片消息不返回内容，不发送给 AI

                # 语音消息（不处理）
                elif item_type == MediaType.VOICE:
                    pass  # 语音不返回内容

                # 文件消息（只下载保存，不加入返回列表）
                elif item_type == MediaType.FILE:
                    filepath = await self.media_handler.download_media_item(
                        item,
                        label=f"inbound_{message_id}"
                    )
                    if filepath:
                        filename = Path(filepath).name
                        # 从文件获取实际大小
                        import os
                        file_size = os.path.getsize(filepath)
                        # 保存文件映射：file_size → filename
                        self.file_mapping.add_file(filename, file_size)
                        log.log(f"📎 文件已下载: {filename} ({file_size} bytes)")
                    # 文件消息不返回内容，不发送给 AI

                # 视频消息（只下载保存，不加入返回列表）
                elif item_type == MediaType.VIDEO:
                    filepath = await self.media_handler.download_media_item(
                        item,
                        label=f"inbound_{message_id}"
                    )
                    if filepath:
                        filename = Path(filepath).name
                        # 从文件获取实际大小
                        import os
                        file_size = os.path.getsize(filepath)
                        # 保存文件映射：file_size → filename
                        self.file_mapping.add_file(filename, file_size)
                        log.log(f"📎 视频已下载: {filename} ({file_size} bytes)")
                    # 视频消息不返回内容，不发送给 AI

            # 如果只有媒体文件没有文字，返回 None（不发送给 AI）
            if not content_parts or all(not part.strip() for part in content_parts):
                return None, []

            return "\n".join(content_parts), ref_files

        except Exception as e:
            log.log(f"❌ 解析消息内容失败: {e}")
            return None, []

    def _parse_ref_message_and_lookup(self, ref_msg: dict, from_user_id: str) -> tuple[str, list[dict]]:
        """解析引用消息并从映射表中查找文件

        Returns:
            (引用文本, 找到的文件列表)
        """
        parts = []
        found_files = []

        # 添加标题
        title = ref_msg.get("title")
        if title:
            parts.append(title)

        # 添加引用的消息内容
        ref_item = ref_msg.get("message_item")
        if ref_item:
            ref_type = ref_item.get("type")

            if ref_type == MediaType.TEXT:  # 文本
                text = ref_item.get("text_item", {}).get("text", "")
                if text:
                    parts.append(text)

            elif ref_type == MediaType.FILE:  # 文件
                file_item = ref_item.get("file_item", {})
                filename = file_item.get("filename", "文件")
                parts.append(f"[文件: {filename}]")

                # 使用文件大小匹配文件
                file_size = file_item.get("filesize")
                if file_size:
                    local_filename = self.file_mapping.get_filename_by_size(file_size)
                    if local_filename:
                        # 构造完整文件路径
                        file_path = str(self.media_handler.save_dir / local_filename)
                        found_files.append({
                            "message_id": str(file_size),
                            "file_path": file_path,
                            "filename": local_filename,
                        })

            elif ref_type == MediaType.IMAGE:  # 图片
                parts.append("[图片]")
                # 使用文件大小匹配文件
                image_item = ref_item.get("image_item", {})
                file_size = image_item.get("mid_size")
                if file_size:
                    local_filename = self.file_mapping.get_filename_by_size(file_size)
                    if local_filename:
                        # 构造完整文件路径
                        file_path = str(self.media_handler.save_dir / local_filename)
                        found_files.append({
                            "message_id": str(file_size),
                            "file_path": file_path,
                            "filename": local_filename,
                        })

            elif ref_type == MediaType.VIDEO:  # 视频
                parts.append("[视频]")
                # 使用文件大小匹配文件
                video_item = ref_item.get("video_item", {})
                file_size = video_item.get("video_size")
                if file_size:
                    local_filename = self.file_mapping.get_filename_by_size(file_size)
                    if local_filename:
                        # 构造完整文件路径
                        file_path = str(self.media_handler.save_dir / local_filename)
                        found_files.append({
                            "message_id": str(file_size),
                            "file_path": file_path,
                            "filename": local_filename,
                        })
        else:
            pass

        return " | ".join(parts), found_files