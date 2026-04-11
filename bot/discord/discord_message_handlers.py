"""
Discord Bot - 消息处理模块
处理用户消息和附件引用消息
"""
import discord
import asyncio
import aiohttp
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.message_queue import Message, MessageDirection, MessageStatus, MessageTag, ChannelType, AttachmentInfo
from shared.logger import get_logger

log = get_logger("DiscordBot", "discord")


class DiscordMessageHandlersMixin:
    """消息处理 Mixin"""

    async def handle_user_message(self, message: discord.Message):
        """处理用户消息"""
        try:
            import aiohttp
            from pathlib import Path

            # 移除 bot 提及，提取实际内容
            content = message.content
            for mention in message.mentions:
                if mention == self.user:
                    content = content.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
                    break

            content = content.strip()

            # 检查并处理附件
            attachment_infos = None
            if message.attachments:
                log.log(f"[附件检测] 用户 {message.author.display_name} 发送了 {len(message.attachments)} 个附件")

                # 使用配置的默认下载目录
                save_dir = Path(self.config.default_download_directory)
                save_dir.mkdir(parents=True, exist_ok=True)

                downloaded_files = []

                # 下载所有附件
                async with aiohttp.ClientSession() as session:
                    for attachment in message.attachments:
                        try:
                            # 检查映射表中是否已有该附件的本地文件名
                            mapped_filename = self.file_mapping.get_local_filename(attachment.id)
                            if mapped_filename:
                                # 使用映射表中的文件名
                                local_path = save_dir / mapped_filename
                                log.log(f"[附件下载] 使用已映射文件名: {mapped_filename}")
                            else:
                                # 处理文件名冲突
                                local_path = save_dir / attachment.filename
                                counter = 1
                                original_stem = Path(attachment.filename).stem
                                original_suffix = Path(attachment.filename).suffix

                                # 检查文件是否存在，如存在则添加后缀
                                while local_path.exists():
                                    local_path = save_dir / f"{original_stem}_{counter}{original_suffix}"
                                    counter += 1

                                # 记录映射关系
                                self.file_mapping.set_local_filename(attachment.id, local_path.name)

                            # 下载文件
                            async with session.get(attachment.url) as resp:
                                if resp.status == 200:
                                    file_content = await resp.read()
                                    with open(local_path, 'wb') as f:
                                        f.write(file_content)

                                    downloaded_files.append({
                                        "id": attachment.id,
                                        "filename": attachment.filename,
                                        "local_filename": local_path.name,
                                        "local_path": str(local_path),
                                        "size": len(file_content)
                                    })
                                    log.log(f"[附件下载] ✓ 已下载: {attachment.filename} -> {local_path}")
                                else:
                                    raise ValueError(f"HTTP {resp.status}")

                        except Exception as e:
                            log.log(f"[附件下载] ✗ 下载失败: {attachment.filename} - {e}")

                # 构建附件信息对象列表
                if downloaded_files:
                    attachment_infos = []
                    for f in downloaded_files:
                        attachment_infos.append(AttachmentInfo(
                            id=f['id'],
                            filename=f['local_filename'],  # 使用本地文件名
                            local_filename=f['local_filename'],
                            size=f['size'],
                            url=f"file://{f['local_path']}",  # 使用本地文件路径
                            description=None
                        ))
                    log.log(f"[附件处理] 成功处理 {len(attachment_infos)} 个附件")

            # 如果没有内容也没有附件，返回错误
            if not content and not attachment_infos:
                await message.channel.send("❌ 请提供消息内容或附件。")
                return

            # 检测是否为私聊消息
            is_dm = isinstance(message.channel, discord.DMChannel)

            # 获取会话信息，检查是否为首次对话
            session_key, session_id, session_created, _ = self.message_queue.get_or_create_session(
                self.config.working_directory,
                channel_id=message.channel.id if not is_dm else None,
                user_id=message.author.id if is_dm else None,
                is_dm=is_dm,
                use_temp_session=False,
                temp_session_key=None
            )

            # 创建消息对象（默认标签）
            msg = Message(
                id=None,
                direction=MessageDirection.TO_CLAUDE.value,
                content=content if content else "",  # 允许空内容，当只有附件时
                status=MessageStatus.PENDING.value,
                discord_channel_id=message.channel.id,
                discord_message_id=message.id,
                discord_user_id=message.author.id,
                username=message.author.display_name,
                is_dm=is_dm,
                tag=MessageTag.DEFAULT.value,
                channel_type=ChannelType.DISCORD.value,  # Discord 频道
                attachments=attachment_infos  # 传入附件信息
            )

            # 添加到消息队列（状态为 PENDING，等待 Claude Bridge 接收）
            message_id = self.message_queue.add_message(msg)

            # 打印日志，包含附件信息
            attach_info = f" (+{len(attachment_infos)}个附件)" if attachment_infos else ""
            log.log(f"[消息 #{message_id}] 收到来自 {message.author.display_name} 的消息: {content[:50] if content else '(仅附件)'}...{attach_info} ({'私聊' if is_dm else '频道'})")

            # 不发送确认消息，直接启动 typing indicator
            typing_task = asyncio.create_task(
                self._maintain_typing_indicator(message.channel)
            )

            self.pending_messages[message_id] = {
                "channel": message.channel,
                "user_message": message,
                "confirmation_msg": None,  # 无确认消息
                "start_time": asyncio.get_event_loop().time(),
                "content": content[:50],
                "notified_processing": False,
                "typing_task": typing_task,
                "typing_active": True,
            }
            log.log(f"[消息 #{message_id}] 已启动 typing indicator")

        except Exception as e:
            log.log(f"❌ 处理消息时出错: {e}")
            import traceback
            traceback.print_exc()
            await message.channel.send(f"❌ 处理消息时出错: {str(e)}")

    async def handle_file_download_command(self, message: discord.Message):
        """处理附件引用消息（转发/回复消息）"""
        try:
            # 获取原始消息的 ID 和频道 ID
            original_message_id = message.reference.message_id
            original_channel_id = message.reference.channel_id

            log.log(f"[附件引用] 用户 {message.author.display_name} 引用了消息 {original_message_id}")

            # 获取原始消息
            channel = self.get_channel(original_channel_id)
            if not channel:
                # 可能是私聊频道，尝试获取
                try:
                    channel = await self.fetch_channel(original_channel_id)
                except discord.NotFound:
                    await message.channel.send(f"❌ 找不到原始消息")
                    return
                except discord.Forbidden:
                    await message.channel.send(f"❌ 没有权限访问原始消息")
                    return

            try:
                original_message = await channel.fetch_message(original_message_id)
            except discord.NotFound:
                await message.channel.send(f"❌ 找不到原始消息")
                return
            except discord.Forbidden:
                await message.channel.send(f"❌ 没有权限访问原始消息")
                return

            # 检查消息是否有附件
            if not original_message.attachments:
                await message.channel.send(f"❌ 原始消息没有附件")
                return

            # 构建附件信息对象列表
            attachment_infos = []
            for attachment in original_message.attachments:
                # 查询映射表获取本地文件名
                local_filename = self.file_mapping.get_local_filename(attachment.id)

                # 如果文件已下载，使用本地文件名和本地路径
                if local_filename:
                    from pathlib import Path
                    save_dir = Path(self.config.default_download_directory)
                    local_path = save_dir / local_filename
                    display_filename = local_filename
                    file_url = f"file://{local_path}"
                else:
                    # 文件未下载，使用 Discord 信息
                    display_filename = attachment.filename
                    file_url = attachment.url

                attachment_infos.append(AttachmentInfo(
                    id=attachment.id,
                    filename=display_filename,  # 优先使用本地文件名
                    local_filename=local_filename,
                    size=attachment.size,
                    url=file_url,  # 优先使用本地路径
                    description=attachment.description
                ))

            log.log(f"[附件引用] 检测到 {len(attachment_infos)} 个附件")
            for idx, att in enumerate(attachment_infos, 1):
                log.log(f"  附件 {idx}: {att.filename} ({att.size} 字节)")

            # 移除 bot 提及，提取用户输入的内容
            content = message.content
            for mention in message.mentions:
                if mention == self.user:
                    content = content.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
                    break
            content = content.strip()

            # 检查是否为空消息
            if not content:
                await message.channel.send("❌ 请提供消息内容。")
                return

            # 检测是否为私聊消息
            is_dm = isinstance(message.channel, discord.DMChannel)

            # 获取会话信息
            session_key, session_id, session_created, _ = self.message_queue.get_or_create_session(
                self.config.working_directory,
                channel_id=message.channel.id if not is_dm else None,
                user_id=message.author.id if is_dm else None,
                is_dm=is_dm,
                use_temp_session=False,
                temp_session_key=None
            )

            # 显示"正在输入"状态
            async with message.channel.typing():
                # 创建消息对象（附件信息作为独立参数传递）
                msg = Message(
                    id=None,
                    direction=MessageDirection.TO_CLAUDE.value,
                    content=content,  # 只包含用户输入，不包含附件信息
                    status=MessageStatus.PENDING.value,
                    discord_channel_id=message.channel.id,
                    discord_message_id=message.id,
                    discord_user_id=message.author.id,
                    username=message.author.display_name,
                    is_dm=is_dm,
                    tag=MessageTag.DEFAULT.value,
                    channel_type=ChannelType.DISCORD.value,  # Discord 频道
                    attachments=attachment_infos  # 附件信息作为独立参数
                )

                # 添加到消息队列
                message_id = self.message_queue.add_message(msg)

                log.log(f"[消息 #{message_id}] 收到来自 {message.author.display_name} 的附件引用消息 ({'私聊' if is_dm else '频道'})")

                # 直接回复模式（固定启用）：不发送确认消息，直接启动 typing indicator
                typing_task = asyncio.create_task(
                    self._maintain_typing_indicator(message.channel)
                )

                self.pending_messages[message_id] = {
                    "channel": message.channel,
                    "user_message": message,
                    "confirmation_msg": None,  # 无确认消息
                    "start_time": asyncio.get_event_loop().time(),
                    "content": content[:50] if content else "(空消息)",
                    "notified_processing": False,
                    "typing_task": typing_task,
                    "typing_active": True,
                }
                log.log(f"[消息 #{message_id}] 已启动 typing indicator")

        except Exception as e:
            log.log(f"❌ 处理附件引用消息时出错: {e}")
            import traceback
            traceback.print_exc()
            await message.channel.send(f"❌ 处理消息时出错: {str(e)}")
