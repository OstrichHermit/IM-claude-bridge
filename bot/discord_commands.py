"""
Discord Bot - 斜杠命令模块
包含所有斜杠命令和右键菜单的注册与处理
"""
import discord
from discord import app_commands
import os
import sys
import time
import subprocess
import asyncio
import aiohttp
from pathlib import Path

# 添加 shared 目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.message_queue import Message, MessageDirection, MessageStatus, MessageTag, ChannelType, AttachmentInfo
from shared.logger import get_logger

log = get_logger("DiscordBot", "discord")


class DiscordCommandsMixin:
    """斜杠命令 Mixin"""

    async def add_commands(self):
        """注册斜杠命令"""

        @self.tree.command(name="new", description="开始新的对话上下文（重置当前频道/私聊的会话）")
        async def reset_command(interaction: discord.Interaction):
            """重置当前频道/私聊的 Claude 会话"""
            # 检查用户权限
            if self.config.allowed_users:
                if interaction.user.id not in self.config.allowed_users:
                    await interaction.response.send_message(
                        f"❌ {interaction.user.mention}，您没有权限执行此操作。",
                        ephemeral=True
                    )
                    return

            # 判断当前是频道还是私聊
            is_dm = isinstance(interaction.channel, discord.DMChannel)

            # 获取当前频道/私聊的会话工作目录
            session_key, old_session_id, _, working_dir = self.message_queue.get_or_create_session(
                self.config.working_directory,
                channel_id=interaction.channel.id if not is_dm else None,
                user_id=interaction.user.id if is_dm else None,
                is_dm=is_dm,
                use_temp_session=False,
                temp_session_key=None
            )

            # 删除会话（包括数据库记录和 Claude Code 会话文件）
            deleted = self.message_queue.delete_session(session_key, working_dir)

            # 验证重置：重新获取会话，应该生成新的 session_id
            session_key, new_session_id, session_created, _ = self.message_queue.get_or_create_session(
                self.config.working_directory,
                channel_id=interaction.channel.id if not is_dm else None,
                user_id=interaction.user.id if is_dm else None,
                is_dm=is_dm,
                use_temp_session=False,
                temp_session_key=None
            )

            if deleted:
                # 判断会话类型用于显示
                session_type = "私聊会话" if is_dm else f"频道 #{interaction.channel.name} 的会话"
                embed = discord.Embed(
                    title="✅ 会话已重置",
                    description=f"{interaction.user.mention}，{session_type}已成功重置！",
                    color=discord.Color.green()
                )
                embed.add_field(name="旧的 Session ID", value=f"`{old_session_id[:8]}...` (已删除)", inline=False)
                embed.add_field(name="新的 Session ID", value=f"`{new_session_id[:8]}...`", inline=False)
                embed.add_field(name="说明", value="下次对话将使用新的会话 ID 创建全新上下文。", inline=False)
                await interaction.response.send_message(embed=embed)
                log.log(f"[会话重置] 用户 {interaction.user.display_name} 重置了 {session_type}")
                log.log(f"[会话重置] Session Key: {session_key}")
                log.log(f"[会话重置] 旧 Session ID: {old_session_id} -> 新 Session ID: {new_session_id}")
                log.log(f"[会话重置] 已删除 Claude Code 会话文件: {working_dir}")
            else:
                embed = discord.Embed(
                    title="⚠️ 没有活跃会话",
                    description=f"{interaction.user.mention}，当前没有找到活跃的会话。",
                    color=discord.Color.orange()
                )
                embed.add_field(name="当前 Session ID", value=f"`{new_session_id[:8]}...`", inline=False)
                await interaction.response.send_message(embed=embed)

            # /new 后自动触发对话
            if self.config.auto_trigger_after_new_enabled:
                preset_msg = self.config.auto_trigger_after_new_message
                if preset_msg:
                    auto_msg = Message(
                        id=None,
                        direction=MessageDirection.TO_CLAUDE.value,
                        content=preset_msg,
                        status=MessageStatus.PENDING.value,
                        discord_channel_id=interaction.channel.id if not is_dm else 0,
                        discord_message_id=0,
                        discord_user_id=interaction.user.id,
                        username=interaction.user.display_name,
                        is_dm=is_dm,
                        tag=MessageTag.DEFAULT.value,
                        channel_type=ChannelType.DISCORD.value,
                        attachments=[]
                    )
                    auto_message_id = self.message_queue.add_message(auto_msg)
                    log.log(f"[自动触发] 已发送预设消息 #{auto_message_id} 到新会话: {preset_msg[:50]}...")

        @self.tree.command(name="status", description="查看当前会话和系统状态")
        async def status_command(interaction: discord.Interaction):
            """查看当前会话状态"""
            # 判断当前是频道还是私聊
            is_dm = isinstance(interaction.channel, discord.DMChannel)

            # 获取当前频道/私聊的会话信息
            session_key, session_id, session_created, working_dir = self.message_queue.get_or_create_session(
                self.config.working_directory,
                channel_id=interaction.channel.id if not is_dm else None,
                user_id=interaction.user.id if is_dm else None,
                is_dm=is_dm,
                use_temp_session=False,
                temp_session_key=None
            )

            embed = discord.Embed(
                title="📊 Claude Bridge 状态",
                color=discord.Color.blue()
            )

            # 显示会话类型
            session_type = "私聊会话" if is_dm else f"频道 #{interaction.channel.name}"
            embed.add_field(name="会话类型", value=session_type, inline=False)

            # 显示 session ID 和状态（不显示 Key）
            session_info = f"**Session ID**: `{session_id[:8]}...`" if session_id else "`未生成`"
            session_info += f"\n**状态**: {'已创建 ✅' if session_created else '未创建 ⏳'}"
            embed.add_field(name="当前会话", value=session_info, inline=False)

            embed.add_field(name="工作目录", value=f"`{working_dir}`", inline=False)

            if is_dm:
                mention_status = "不需要 @（私聊）"
            else:
                mention_required = self.message_queue.get_channel_mention_required(
                    interaction.channel.id,
                    default=self.config.mention_required
                )
                mention_status = "需要 @" if mention_required else "不需要 @"
            embed.add_field(name="对话模式", value=mention_status, inline=False)

            await interaction.response.send_message(embed=embed)

        @self.tree.command(name="stop", description="停止 Discord Bridge 服务")
        async def stop_command(interaction: discord.Interaction):
            """停止 Discord Bridge 服务（需要 60 秒内再次使用 /stop 确认）"""
            # 检查用户权限
            if self.config.allowed_users:
                if interaction.user.id not in self.config.allowed_users:
                    await interaction.response.send_message(
                        f"❌ {interaction.user.mention}，您没有权限执行此操作。",
                        ephemeral=True
                    )
                    return

            import time
            user_id = interaction.user.id
            current_time = time.time()

            # 检查是否有未过期的停止请求
            if user_id in self.stop_requests:
                request_time = self.stop_requests[user_id]["timestamp"]
                time_diff = current_time - request_time

                if time_diff <= 60:  # 60 秒内再次使用 /stop
                    # 确认停止
                    del self.stop_requests[user_id]  # 清除记录

                    embed = discord.Embed(
                        title="🛑 正在停止服务",
                        description=f"{interaction.user.mention}，正在停止 Discord Bridge 服务...",
                        color=discord.Color.orange()
                    )
                    embed.add_field(name="说明", value="服务将在几秒钟后停止。", inline=False)
                    await interaction.response.send_message(embed=embed)
                    log.log(f"[停止命令] 用户 {interaction.user.display_name} 确认停止服务")

                    # 执行停止脚本（通过 manager）
                    import subprocess
                    import os

                    try:
                        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                        stop_script = os.path.join(script_dir, 'stop.bat')

                        if os.path.exists(stop_script):
                            subprocess.Popen(
                                ["cmd", "/c", stop_script],
                                cwd=script_dir,
                                creationflags=subprocess.CREATE_NO_WINDOW
                            )
                            log.log(f"✅ 停止命令已执行: stop.bat")
                        else:
                            embed = discord.Embed(
                                title="❌ 文件未找到",
                                description="找不到 stop.bat 文件",
                                color=discord.Color.red()
                            )
                            await interaction.followup.send(embed=embed)
                            log.log(f"⚠️  stop.bat 不存在: {stop_script}")

                    except Exception as e:
                        embed = discord.Embed(
                            title="❌ 停止失败",
                            description=f"错误: {str(e)}",
                            color=discord.Color.red()
                        )
                        await interaction.followup.send(embed=embed)
                        log.log(f"❌ 执行停止命令时出错: {e}")
                        import traceback
                        traceback.print_exc()

                    return

            # 第一次使用 /stop，记录请求
            self.stop_requests[user_id] = {"timestamp": current_time}

            embed = discord.Embed(
                title="⚠️ 确认停止服务",
                description=f"{interaction.user.mention}，确定要停止 Discord Bridge 服务吗？",
                color=discord.Color.orange()
            )
            embed.add_field(name="警告", value="此操作将停止 Bot 和 Bridge，服务将不再响应消息。", inline=False)
            embed.add_field(name="确认方式", value="如需确认，请在 60 秒内再次使用 `/stop` 命令", inline=False)
            await interaction.response.send_message(embed=embed)

            log.log(f"[停止命令] 用户 {interaction.user.display_name} 请求停止服务，等待再次确认...")

        @self.tree.command(name="restart", description="重启 Discord Bridge 服务")
        async def restart_command(interaction: discord.Interaction):
            """重启 Discord Bridge 服务"""
            # 检查用户权限
            if self.config.allowed_users:
                if interaction.user.id not in self.config.allowed_users:
                    await interaction.response.send_message(
                        f"❌ {interaction.user.mention}，您没有权限执行此操作。",
                        ephemeral=True
                    )
                    return

            # 发送确认消息
            embed = discord.Embed(
                title="🔄 正在重启服务",
                description=f"{interaction.user.mention}，正在重启 Discord Bridge 服务...",
                color=discord.Color.blue()
            )
            embed.add_field(name="说明", value="请稍候，服务将在几秒钟后重新启动。", inline=False)
            await interaction.response.send_message(embed=embed)
            log.log(f"[重启命令] 用户 {interaction.user.display_name} 触发了服务重启")

            # 执行重启脚本（直接调用 restart.bat，与 Web 界面行为一致）
            import subprocess
            import os

            try:
                # 获取项目根目录
                script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                restart_script = os.path.join(script_dir, 'restart.bat')

                if os.path.exists(restart_script):
                    # 在后台无窗口执行 restart.bat
                    subprocess.Popen(
                        ["cmd", "/c", restart_script],
                        cwd=script_dir,
                        creationflags=subprocess.CREATE_NO_WINDOW
                    )
                    log.log(f"✅ 重启命令已执行: restart.bat")
                else:
                    embed = discord.Embed(
                        title="❌ 文件未找到",
                        description="找不到 restart.bat 文件",
                        color=discord.Color.red()
                    )
                    await interaction.followup.send(embed=embed)
                    log.log(f"⚠️  restart.bat 不存在: {restart_script}")

            except Exception as e:
                embed = discord.Embed(
                    title="❌ 重启失败",
                    description=f"错误: {str(e)}",
                    color=discord.Color.red()
                )
                await interaction.followup.send(embed=embed)
                log.log(f"❌ 执行重启命令时出错: {e}")
                import traceback
                traceback.print_exc()

        @self.tree.command(name="abort", description="中止当前正在处理的 Claude 响应")
        async def abort_command(interaction: discord.Interaction):
            """中止当前正在处理的 Claude 响应"""
            # 检查用户权限
            if self.config.allowed_users:
                if interaction.user.id not in self.config.allowed_users:
                    await interaction.response.send_message("❌ 您没有权限执行此操作", ephemeral=True)
                    return

            # 查找正在处理的消息（匹配发送命令的频道或私聊）
            if interaction.channel.type == discord.ChannelType.private:
                processing_messages = self.message_queue.get_processing_messages(
                    channel_type=ChannelType.DISCORD.value,
                    user_id=interaction.user.id
                )
            else:
                processing_messages = self.message_queue.get_processing_messages(
                    channel_type=ChannelType.DISCORD.value,
                    channel_id=interaction.channel.id
                )

            if not processing_messages:
                embed = discord.Embed(
                    title="⚠️ 没有正在处理的响应",
                    description="当前没有正在处理的 Claude 响应。",
                    color=discord.Color.orange()
                )
                await interaction.response.send_message(embed=embed)
                return

            # 请求中止第一个处理中的消息
            message_to_abort = processing_messages[0]
            success = self.message_queue.request_abort(message_to_abort.id)

            if success:
                embed = discord.Embed(
                    title="🛑 已请求中止",
                    description=f"已请求中止消息 #{message_to_abort.id} 的处理",
                    color=discord.Color.orange()
                )
                embed.add_field(name="说明", value="Claude 响应将在几秒内停止...", inline=False)
                await interaction.response.send_message(embed=embed)
                log.log(f"[中止命令] 用户 {interaction.user.display_name} 请求中止消息 #{message_to_abort.id}")

                # 停止正在输入状态
                self.stop_typing_indicator(message_to_abort.id)
            else:
                embed = discord.Embed(
                    title="❌ 中止请求失败",
                    description="中止请求失败，请稍后重试。",
                    color=discord.Color.red()
                )
                await interaction.response.send_message(embed=embed)

        @self.tree.command(name="mention", description="切换当前频道是否需要 @")
        async def mention_command(interaction: discord.Interaction):
            """切换当前频道的 mention_required 设置"""
            # 检查用户权限
            if self.config.allowed_users:
                if interaction.user.id not in self.config.allowed_users:
                    await interaction.response.send_message(
                        f"❌ {interaction.user.display_name}，您没有权限执行此操作。",
                        ephemeral=True
                    )
                    return

            # 私聊中不可用
            if isinstance(interaction.channel, discord.DMChannel):
                await interaction.response.send_message(
                    "❌ 私聊中无需切换，私聊始终不需要 @",
                    ephemeral=True
                )
                return

            # 切换当前频道的设置
            channel_id = interaction.channel.id
            current = self.message_queue.get_channel_mention_required(
                channel_id,
                default=self.config.mention_required
            )
            new_value = not current
            self.message_queue.set_channel_mention_required(channel_id, new_value)

            # 构建响应
            status_text = "需要 @" if new_value else "不需要 @"
            target = f"频道 #{interaction.channel.name}"

            desc = f"{target} 的对话模式已切换为：**{status_text}**"
            if new_value:
                note = "现在需要 @机器人 才能触发对话"
            else:
                note = "现在不需要 @机器人，任何消息都会触发对话"

            embed = discord.Embed(
                title="💬 对话模式",
                description=desc,
                color=discord.Color.green()
            )
            embed.add_field(name="说明", value=note, inline=False)

            await interaction.response.send_message(embed=embed)
            log.log(f"[Mention命令] 用户 {interaction.user.display_name} 在{target}({channel_id}) 切换 mention_required 为 {new_value}")

        @self.tree.context_menu(name="下载附件")
        async def download_context_menu(interaction: discord.Interaction, message: discord.Message):
            """右键消息下载附件（上下文菜单）"""
            import aiohttp
            from pathlib import Path

            log.log(f"[下载命令] 用户 {interaction.user.display_name} 右键点击消息 {message.id}")

            # 检查消息是否有附件
            if not message.attachments:
                await interaction.response.send_message(
                    f"❌ {interaction.user.mention}，这条消息没有附件。",
                    ephemeral=True
                )
                return

            # 使用配置的默认下载目录
            save_dir = Path(self.config.default_download_directory)
            save_dir.mkdir(parents=True, exist_ok=True)

            downloaded_files = []
            failed_files = []

            # 先响应，告知用户正在处理
            await interaction.response.send_message(
                f"📥 {interaction.user.mention}，正在下载 {len(message.attachments)} 个附件到 `{save_dir}`..."
            )
            # 获取原始消息以便后续编辑
            status_message = await interaction.original_response()

            # 下载所有附件
            async with aiohttp.ClientSession() as session:
                for attachment in message.attachments:
                    try:
                        # 检查映射表中是否已有该附件的本地文件名
                        mapped_filename = self.file_mapping.get_local_filename(attachment.id)
                        if mapped_filename:
                            # 使用映射表中的文件名
                            local_path = save_dir / mapped_filename
                            log.log(f"[下载命令] 使用已映射文件名: {mapped_filename}")
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
                                log.log(f"[下载命令] ✓ 已下载: {attachment.filename} -> {local_path}")
                            else:
                                raise ValueError(f"HTTP {resp.status}")

                    except Exception as e:
                        failed_files.append({
                            "filename": attachment.filename,
                            "error": str(e)
                        })
                        log.log(f"[下载命令] ✗ 下载失败: {attachment.filename} - {e}")

            # 构建响应消息
            response_lines = [
                f"✅ {interaction.user.mention}，附件下载完成！",
                f"📁 保存目录: `{save_dir}`",
                ""
            ]

            if downloaded_files:
                response_lines.append(f"**成功下载 {len(downloaded_files)} 个文件:**")
                for f in downloaded_files:
                    size_kb = f['size'] / 1024
                    response_lines.append(f"  • **{f['filename']}** ({size_kb:.1f} KB)")
                    response_lines.append(f"    `{f['local_path']}`")

            if failed_files:
                response_lines.append("")
                response_lines.append(f"**失败 {len(failed_files)} 个文件:**")
                for f in failed_files:
                    response_lines.append(f"  • **{f['filename']}**: {f['error']}")

            # 编辑原消息发送最终结果
            followup_msg = "\n".join(response_lines)
            await status_message.edit(content=followup_msg)

            log.log(f"[下载命令] 用户 {interaction.user.display_name} 下载了 {len(downloaded_files)}/{len(message.attachments)} 个文件")
