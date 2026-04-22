"""
Discord Bot - 后台轮询任务模块
包含所有后台轮询任务和 typing indicator 管理
"""
import discord
import asyncio
import json
import sqlite3
import os
import traceback
import aiohttp
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.logger import get_logger
from shared.message_queue import (
    MessageStatus, MessageDirection, ChannelType,
    FileDownloadRequestStatus, MessageRequestStatus
)

log = get_logger("DiscordBot", "discord")


class DiscordPollersMixin:
    """后台轮询任务 Mixin"""

    async def check_responses(self):
        """定期检查 Claude 的响应和消息状态"""
        await self.wait_until_ready()

        while not self.is_closed():
            try:
                # 扫描外部插入的消息（is_external=True）
                # 查询 pending 和 processing 状态，并过滤已追踪的消息（只获取 Discord 频道）
                conn = sqlite3.connect(self.config.database_path)
                try:
                    cursor = conn.cursor()
                    cursor.execute("""
                                   SELECT id, discord_user_id, discord_channel_id, username, content, is_dm
                                   FROM messages
                                   WHERE status IN (?, ?) AND direction = ? AND is_external = 1 AND channel_type = ?
                                   ORDER BY created_at ASC
                                   """, (MessageStatus.PENDING.value, MessageStatus.PROCESSING.value, MessageDirection.TO_CLAUDE.value, ChannelType.DISCORD.value))
                    external_messages = cursor.fetchall()
                finally:
                    conn.close()

                for msg_info in external_messages:
                    msg_id, user_id, channel_id, username, content, is_dm = msg_info
                    # 跳过已追踪的消息
                    if msg_id not in self.pending_messages:
                        try:
                            if is_dm:
                                user = self.get_user(user_id)
                                if not user:
                                    user = await self.fetch_user(user_id)
                                channel = await user.create_dm()
                            else:
                                channel = self.get_channel(channel_id)
                                if not channel:
                                    log.log(f"⚠️  外部消息 #{msg_id}: 找不到频道 {channel_id}")
                                    continue

                            # 直接回复模式（固定启用）：不发送确认消息，直接启动 typing indicator
                            typing_task = asyncio.create_task(
                                self._maintain_typing_indicator(channel)
                            )

                            self.pending_messages[msg_id] = {
                                "channel": channel,
                                "user_message": None,
                                "confirmation_msg": None,  # 无确认消息
                                "start_time": asyncio.get_event_loop().time(),
                                "content": content[:50],
                                "notified_processing": False,
                                "typing_task": typing_task,
                                "typing_active": True,
                            }
                            log.log(f"📨 [消息 #{msg_id}] 已加载外部消息: {username}")

                        except Exception as e:
                            log.log(f"⚠️  外部消息 #{msg_id} 加载失败: {e}")

                # 等待一段时间再检查
                await asyncio.sleep(self.config.poll_interval / 1000)

            except Exception as e:
                log.log(f"❌ 检查响应时出错: {e}")
                traceback.print_exc()
                await asyncio.sleep(5)

    async def check_file_downloads(self):
        """定期检查并处理文件下载请求（支持私聊和频道）"""
        await self.wait_until_ready()

        log.log("📥 文件下载检查任务已启动")

        while not self.is_closed():
            try:
                # 获取下一个待处理的下载请求
                download_request = self.message_queue.get_next_file_download_request()

                if download_request:
                    log.log(f"📥 处理文件下载请求 #{download_request.id}")
                    # 标记为处理中
                    self.message_queue.update_file_download_request_status(
                        download_request.id,
                        FileDownloadRequestStatus.PROCESSING
                    )

                    try:
                        # 获取 Discord 频道/私聊
                        channel = self.get_channel(download_request.discord_channel_id)

                        # 如果获取不到，尝试从用户获取（私聊情况）
                        if not channel:
                            # 可能是私聊频道，需要通过消息获取用户
                            try:
                                # 尝试获取消息来获取用户信息
                                channel = await self.fetch_channel(download_request.discord_channel_id)
                            except discord.NotFound:
                                raise ValueError(f"找不到频道: {download_request.discord_channel_id}")
                            except discord.Forbidden:
                                raise ValueError(f"没有权限访问频道: {download_request.discord_channel_id}")

                        # 获取消息
                        try:
                            message = await channel.fetch_message(download_request.discord_message_id)
                        except discord.NotFound:
                            raise ValueError(f"找不到消息: {download_request.discord_message_id}")
                        except discord.Forbidden:
                            raise ValueError(f"没有权限访问消息: {download_request.discord_message_id}")

                        # 检查消息是否有附件
                        if not message.attachments:
                            raise ValueError("该消息没有附件")

                        # 创建保存目录
                        save_dir = Path(download_request.save_directory)
                        try:
                            save_dir.mkdir(parents=True, exist_ok=True)
                        except Exception as e:
                            raise ValueError(f"无法创建保存目录 {save_dir}: {e}")

                        # 下载所有附件
                        downloaded_files = []
                        async with aiohttp.ClientSession() as session:
                            for attachment in message.attachments:
                                # 检查映射表中是否已有该附件的本地文件名
                                mapped_filename = self.file_mapping.get_local_filename(attachment.id)
                                if mapped_filename:
                                    # 使用映射表中的文件名
                                    local_path = save_dir / mapped_filename
                                    log.log(f"  [文件下载] 使用已映射文件名: {mapped_filename}")
                                else:
                                    # 处理文件名冲突
                                    local_path = save_dir / attachment.filename
                                    counter = 1
                                    while local_path.exists():
                                        stem = Path(attachment.filename).stem
                                        suffix = Path(attachment.filename).suffix
                                        local_path = save_dir / f"{stem}_{counter}{suffix}"
                                        counter += 1

                                    # 记录映射关系
                                    self.file_mapping.set_local_filename(attachment.id, local_path.name)

                                # 下载文件
                                async with session.get(attachment.url) as resp:
                                    if resp.status == 200:
                                        # 写入文件
                                        with open(local_path, 'wb') as f:
                                            f.write(await resp.read())

                                        downloaded_files.append({
                                            "id": attachment.id,
                                            "filename": attachment.filename,
                                            "local_filename": local_path.name,
                                            "local_path": str(local_path),
                                            "size": attachment.size
                                        })
                                        log.log(f"  ✓ 已下载: {attachment.filename} -> {local_path}")
                                    else:
                                        raise ValueError(f"下载文件失败: {attachment.filename} (HTTP {resp.status})")

                        # 标记为完成
                        result = json.dumps({
                            "success": True,
                            "message": f"成功下载 {len(downloaded_files)} 个文件",
                            "downloaded_files": downloaded_files
                        }, ensure_ascii=False)

                        self.message_queue.update_file_download_request_status(
                            download_request.id,
                            FileDownloadRequestStatus.COMPLETED,
                            downloaded_files=result
                        )
                        log.log(f"✅ 文件下载请求 #{download_request.id} 处理完成")

                    except Exception as e:
                        # 标记为失败
                        error_msg = json.dumps({
                            "success": False,
                            "error": str(e)
                        }, ensure_ascii=False)
                        self.message_queue.update_file_download_request_status(
                            download_request.id,
                            FileDownloadRequestStatus.FAILED,
                            error=error_msg
                        )
                        log.log(f"❌ 文件下载请求 #{download_request.id} 处理失败: {e}")
                        traceback.print_exc()

                # 等待一段时间再检查
                await asyncio.sleep(self.config.poll_interval / 1000)

            except Exception as e:
                log.log(f"❌ 检查文件下载请求时出错: {e}")
                traceback.print_exc()
                await asyncio.sleep(5)

    async def check_message_requests(self):
        """定期检查并处理消息发送请求"""
        await self.wait_until_ready()

        log.log("💬 消息发送检查任务已启动")

        while not self.is_closed():
            try:
                # 获取下一个待处理的消息请求
                message_request = self.message_queue.get_next_message_request()

                if message_request:
                    log.log(f"💬 处理消息请求 #{message_request.id}")
                    # 标记为处理中
                    self.message_queue.update_message_request_status(
                        message_request.id,
                        MessageRequestStatus.PROCESSING
                    )

                    try:
                        # 确定发送目标
                        if message_request.user_id:
                            # 发送到用户私聊
                            user = self.get_user(message_request.user_id)
                            if not user:
                                user = await self.fetch_user(message_request.user_id)
                            target_channel = await user.create_dm()
                            target_info = f"用户 {user.display_name}"
                        elif message_request.channel_id:
                            # 发送到频道
                            target_channel = self.get_channel(message_request.channel_id)
                            if not target_channel:
                                raise ValueError(f"找不到频道: {message_request.channel_id}")
                            target_info = f"频道 {target_channel.name}"
                        else:
                            raise ValueError("必须指定 user_id 或 channel_id")

                        # 发送消息
                        if message_request.use_embed:
                            # 使用 Embed 格式
                            embed = discord.Embed(
                                title=message_request.embed_title,
                                description=message_request.content,
                                color=discord.Color(message_request.embed_color) if message_request.embed_color else discord.Color.blue()
                            )
                            sent_msg = await target_channel.send(embed=embed)
                            message_id = str(sent_msg.id)
                        else:
                            # 发送纯文本（支持长消息分割）
                            await self._send_long_message(target_channel, message_request.content)
                            message_id = None  # 分割消息不返回单个 message_id

                        # 标记为完成
                        result = json.dumps({
                            "success": True,
                            "message": f"成功发送消息到 {target_info}",
                            "message_id": message_id
                        }, ensure_ascii=False)
                        self.message_queue.update_message_request_status(
                            message_request.id,
                            MessageRequestStatus.COMPLETED,
                            result=result
                        )
                        log.log(f"✅ 消息请求 #{message_request.id} 处理完成")

                    except Exception as e:
                        # 标记为失败
                        error_msg = json.dumps({
                            "success": False,
                            "error": str(e)
                        }, ensure_ascii=False)
                        self.message_queue.update_message_request_status(
                            message_request.id,
                            MessageRequestStatus.FAILED,
                            error=error_msg
                        )
                        log.log(f"❌ 消息请求 #{message_request.id} 处理失败: {e}")

                # 等待一段时间再检查
                await asyncio.sleep(self.config.poll_interval / 1000)

            except Exception as e:
                log.log(f"❌ 检查消息请求时出错: {e}")
                traceback.print_exc()
                await asyncio.sleep(5)

    async def _maintain_typing_indicator(self, channel):
        """
        维持 typing indicator（仅用于直接回复模式）
        带重试机制，网络波动时会自动恢复
        使用持续刷新模式，避免 typing indicator 中断闪烁

        Args:
            channel: Discord 频道对象
        """
        retry_count = 0
        max_retries = self.config.typing_indicator_max_retries  # 最大连续重试次数
        retry_delay = self.config.typing_indicator_retry_delay  # 重试等待时间（秒）

        try:
            while not self.is_closed():
                try:
                    # Discord typing indicator 默认持续 10 秒
                    # 我们每 8 秒刷新一次，确保有足够余量避免中断
                    async with channel.typing():
                        await asyncio.sleep(8)

                    # 成功完成一次循环，重置重试计数
                    retry_count = 0

                except asyncio.CancelledError:
                    # 任务被取消，正常退出
                    break
                except Exception as e:
                    retry_count += 1
                    log.log(f"⚠️ 维持 typing indicator 时出错 (第{retry_count}次): {e}")

                    if retry_count >= max_retries:
                        log.log(f"❌ 维持 typing indicator 失败，已达最大重试次数 ({max_retries})，停止尝试")
                        break

                    log.log(f"🔄 {retry_delay}秒后重试...")
                    await asyncio.sleep(retry_delay)

        except asyncio.CancelledError:
            # 任务被取消，正常退出
            pass
        except Exception as e:
            log.log(f"❌ 维持 typing indicator 时发生未预期错误: {e}")

    def stop_typing_indicator(self, message_id):
        """
        停止指定消息对应的 typing indicator 任务

        Args:
            message_id: 消息记录在数据库中的唯一 ID
        """
        # 从 pending_messages 字典中找到这根消息记录并取消它的 typing_task
        if message_id in self.pending_messages:
            msg_info = self.pending_messages[message_id]
            task = msg_info.get("typing_task")

            # 检查是否已经在停止状态
            if not msg_info.get("typing_active", False):
                # 已经停止，静默返回
                return

            if task and not task.done():
                task.cancel()  # 这会触发 _maintain_typing_indicator 中的 CancelledError
                log.log(f"🛑 [消息 #{message_id}] 已停止 typing indicator")

            # 更新状态为已停止
            msg_info["typing_active"] = False
            msg_info["typing_task"] = None
        else:
            # 消息不在缓存中，可能已经被清理，静默返回
            pass

    async def _update_tool_use_card(self, message_id: int, tool_use_index: int, success: bool):
        """更新工具调用卡片的状态

        Args:
            message_id: 消息 ID
            tool_use_index: 工具调用索引
            success: 工具执行是否成功
        """
        # 获取保存的消息引用，带重试机制（最多10次，每次间隔1秒）
        max_retries = 10
        ref = None

        for retry in range(max_retries):
            ref = self.message_queue.get_tool_use_message_ref(message_id, tool_use_index)
            if ref:
                break

            if retry < max_retries - 1:  # 不是最后一次重试
                await asyncio.sleep(1)  # 等待1秒后重试
            else:
                log.log(f"❌ [Bot] 未找到卡片引用: 消息 #{message_id}, 工具 #{tool_use_index}，已达最大重试次数")
                return  # 达到最大重试次数，放弃

        try:
            # 获取原消息
            if ref['is_dm']:
                user = self.get_user(ref['channel_id'])
                if not user:
                    user = await self.fetch_user(ref['channel_id'])
                if not user:
                    return
                dm_channel = await user.create_dm()
                message = await dm_channel.fetch_message(ref['discord_message_id'])
            else:
                channel = self.get_channel(ref['channel_id'])
                if not channel:
                    return
                message = await channel.fetch_message(ref['discord_message_id'])

            if not message or not message.embeds:
                return

            # 获取原 embed
            embed = message.embeds[0]

            # 更新颜色（成功=绿色，失败=红色）
            embed.color = discord.Color.green() if success else discord.Color.red()

            # 编辑消息
            await message.edit(embed=embed)

        except Exception as e:
            pass  # 静默失败，避免刷屏

    async def check_tool_use_results(self):
        """定期检查工具执行结果并更新卡片"""
        await self.wait_until_ready()

        while not self.is_closed():
            try:
                # 获取待处理的工具执行结果（只处理 discord 频道的）
                pending_results = self.message_queue.get_pending_tool_use_results(channel_type='discord')

                for result in pending_results:
                    message_id = result['message_id']
                    tool_use_index = result['tool_use_index']
                    success = result['success']

                    # 更新工具调用卡片
                    await self._update_tool_use_card(message_id, tool_use_index, success)

                    # 标记为已处理
                    self.message_queue.mark_tool_use_result_processed(message_id, tool_use_index)

                # 等待一段时间再检查（1秒）
                await asyncio.sleep(1)

            except Exception as e:
                log.log(f"❌ 检查工具执行结果时出错: {e}")
                traceback.print_exc()
                await asyncio.sleep(5)
