"""
Discord Bot 主程序
接收 Discord 消息并转发给 Claude Code
支持斜杠命令（Slash Commands）
"""
import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import sys
from pathlib import Path
from typing import List

# 添加 shared 目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.config import Config
from shared.message_queue import MessageQueue, Message, MessageDirection, MessageStatus, MessageTag, AttachmentInfo


class DiscordBot(commands.Bot):
    """Discord Bot 类"""

    def __init__(self, config: Config):
        """初始化 Bot"""
        intents = discord.Intents.default()
        intents.message_content = True  # 需要在 Discord Developer Portal 启用
        intents.messages = True

        super().__init__(
            command_prefix=config.command_prefix,
            intents=intents,
            help_command=None
        )

        self.config = config
        self.message_queue = MessageQueue(config.database_path)
        self.response_check_task = None
        self.file_request_check_task = None
        self.file_download_check_task = None
        self.message_request_check_task = None  # 新增：消息发送请求检查任务
        self.pending_messages = {}  # 追踪待处理的消息 {message_id: {"channel": channel, "user_msg": message, "start_time": time}}
        self.stop_requests = {}  # 追踪停止请求 {user_id: {"timestamp": time}}

    async def setup_hook(self):
        """Bot 启动后的钩子"""
        print(f"Bot 已启动，登录为 {self.user}")

        # 清理上次崩溃时卡住的消息
        await self.cleanup_stuck_messages()

        # 注册斜杠命令
        await self.add_commands()

        # 同步命令到 Discord
        try:
            print("🔄 正在同步斜杠命令到 Discord...")

            # 检查是否配置了特定服务器 ID
            if self.config.sync_guild_id:
                # 同步到特定服务器（立即生效）
                guild = discord.Object(id=int(self.config.sync_guild_id))
                synced = await self.tree.sync(guild=guild)
                print(f"✅ 已同步 {len(synced)} 个斜杠命令到服务器 {self.config.sync_guild_id}")
                print(f"⚡ 服务器命令立即生效！")
            else:
                # 全局同步（需要等待几分钟）
                synced = await self.tree.sync()
                print(f"✅ 已同步 {len(synced)} 个斜杠命令（全局）")
                print(f"⏱️  注意：全局命令可能需要 1-5 分钟才能生效")
                print(f"💡 提示：在 config.yaml 中配置 sync_guild_id 可以立即生效")

        except Exception as e:
            print(f"⚠️ 命令同步失败: {e}")
            print(f"📋 请确认：")
            print(f"   1. Bot Token 是否正确")
            print(f"   2. 是否已在 Discord Developer Portal 启用 'applications.commands' scope")
            print(f"   3. 如果配置了 sync_guild_id，确认服务器 ID 是否正确")

        # 启动响应检查任务
        self.response_check_task = asyncio.create_task(self.check_responses())

        # 启动文件请求检查任务
        self.file_request_check_task = asyncio.create_task(self.check_file_requests())

        # 启动文件下载检查任务
        self.file_download_check_task = asyncio.create_task(self.check_file_downloads())

        # 启动消息发送请求检查任务
        self.message_request_check_task = asyncio.create_task(self.check_message_requests())

        # 🔥 启动流式响应检查任务
        self.stream_check_task = asyncio.create_task(self.check_streaming_responses())

    async def cleanup_stuck_messages(self):
        """清理上次崩溃时卡住的消息"""
        import sqlite3
        try:
            conn = sqlite3.connect(self.config.database_path)
            cursor = conn.cursor()

            # 1. 清理 PROCESSING 状态的消息
            cursor.execute("SELECT COUNT(*) FROM messages WHERE status = 'processing'")
            stuck_count = cursor.fetchone()[0]

            if stuck_count > 0:
                print(f"🧹 发现 {stuck_count} 条卡住的消息（PROCESSING），正在清理...")

                cursor.execute("""
                    UPDATE messages
                    SET status = 'completed',
                        updated_at = CURRENT_TIMESTAMP,
                        error = 'Bot 重置：消息被标记为已完成'
                    WHERE status = 'processing'
                """)

                affected = cursor.rowcount
                conn.commit()
                print(f"✅ 已清理 {affected} 条卡住的消息")
            else:
                print("✓ 没有发现 PROCESSING 状态的消息")

            # 2. 清理 PENDING 状态的消息（避免重启后重复处理）
            cursor.execute("SELECT COUNT(*) FROM messages WHERE status = 'pending'")
            pending_count = cursor.fetchone()[0]

            if pending_count > 0:
                print(f"🧹 发现 {pending_count} 条待处理的消息（PENDING），正在跳过...")

                cursor.execute("""
                    UPDATE messages
                    SET status = 'skipped',
                        updated_at = CURRENT_TIMESTAMP,
                        error = 'Bot 重启：消息被跳过，避免重复处理'
                    WHERE status = 'pending'
                """)

                affected = cursor.rowcount
                conn.commit()
                print(f"✅ 已跳过 {affected} 条旧消息")
            else:
                print("✓ 没有发现 PENDING 状态的消息")

            conn.close()

        except Exception as e:
            print(f"⚠️ 清理卡住消息时出错: {e}")

    async def send_startup_notification(self):
        """发送启动通知"""
        notification_channel_id = self.config.startup_notification_channel
        notification_user_id = self.config.startup_notification_user

        # 如果都没有配置，跳过通知
        if not notification_channel_id and not notification_user_id:
            print("ℹ️  未配置启动通知，跳过")
            return

        # 获取当前会话信息
        session_key, session_id, session_created, _ = self.message_queue.get_or_create_session(
            self.config.working_directory
        )

        # 创建启动成功消息
        embed = discord.Embed(
            title="🚀 Discord Claude Bridge 启动成功",
            description="桥接系统已就绪，可以开始使用！",
            color=discord.Color.green()
        )

        # 显示会话信息
        session_info = f"**Session ID**: `{session_id[:8]}...`" if session_id else "`未生成`"
        session_info += f"\n**状态**: {'已创建 ✅' if session_created else '未创建 ⏳'}"
        embed.add_field(name="📋 当前会话", value=session_info, inline=False)

        embed.add_field(name="📂 工作目录", value=f"`{self.config.working_directory}`", inline=False)
        embed.add_field(name="🔧 可用命令", value="`/new` - 新会话\n`/status` - 查看状态\n`/upload` - 上传文件\n`/restart` - 重启服务\n`/stop` - 停止服务", inline=False)

        embed.set_footer(text=f"Bot: {self.user.name}")

        # 发送到频道
        if notification_channel_id:
            try:
                channel = self.get_channel(int(notification_channel_id))
                if not channel:
                    print(f"⚠️  找不到启动通知频道: {notification_channel_id}")
                else:
                    await channel.send(embed=embed)
                    print(f"✅ 已向频道 #{channel.name} 发送启动通知")
            except ValueError:
                print(f"⚠️  启动通知频道 ID 格式错误: {notification_channel_id}")
            except Exception as e:
                print(f"❌ 发送到频道失败: {e}")

        # 发送到用户私聊
        if notification_user_id:
            try:
                user = self.get_user(int(notification_user_id))
                if not user:
                    try:
                        user = await self.fetch_user(int(notification_user_id))
                    except discord.NotFound:
                        print(f"⚠️  找不到启动通知用户: {notification_user_id}")
                        return
                    except discord.HTTPException as e:
                        print(f"⚠️  获取用户失败: {e}")
                        return

                # 创建或获取 DM 频道
                dm_channel = await user.create_dm()
                await dm_channel.send(embed=embed)
                print(f"✅ 已向用户 {user.display_name} 发送启动通知（私聊）")

            except ValueError:
                print(f"⚠️  启动通知用户 ID 格式错误: {notification_user_id}")
            except Exception as e:
                print(f"❌ 发送到用户私聊失败: {e}")

    async def add_commands(self):
        """注册斜杠命令"""

        @self.tree.command(name="new", description="开始新的对话上下文（重置全局会话）")
        async def reset_command(interaction: discord.Interaction):
            """重置全局 Claude 会话"""
            # 检查用户权限
            if self.config.allowed_users:
                if interaction.user.id not in self.config.allowed_users:
                    await interaction.response.send_message(
                        f"❌ {interaction.user.mention}，您没有权限执行此操作。",
                        ephemeral=True
                    )
                    return

            # 获取全局会话的工作目录
            session_key, old_session_id, _, working_dir = self.message_queue.get_or_create_session(
                self.config.working_directory
            )

            # 删除会话（包括数据库记录和 Claude Code 会话文件）
            deleted = self.message_queue.delete_session(session_key, working_dir)

            # 验证重置：重新获取会话，应该生成新的 session_id
            session_key, new_session_id, session_created, _ = self.message_queue.get_or_create_session(
                self.config.working_directory
            )

            if deleted:
                await interaction.response.send_message(
                    f"✅ {interaction.user.mention}，全局会话已重置！\n"
                    f"**旧的 Session ID**: `{old_session_id[:8]}...` (已删除)\n"
                    f"**新的 Session ID**: `{new_session_id[:8]}...`\n"
                    f"下次对话将使用新的会话 ID 创建全新上下文。"
                )
                print(f"[会话重置] 用户 {interaction.user.display_name} 重置了全局会话")
                print(f"[会话重置] 旧 Session ID: {old_session_id} -> 新 Session ID: {new_session_id}")
                print(f"[会话重置] 已删除 Claude Code 会话文件: {working_dir}")
            else:
                await interaction.response.send_message(
                    f"⚠️ {interaction.user.mention}，没有找到活跃的会话。\n"
                    f"**当前 Session ID**: `{new_session_id[:8]}...`"
                )

        @self.tree.command(name="status", description="查看当前会话和系统状态")
        async def status_command(interaction: discord.Interaction):
            """查看当前会话状态"""
            # 获取全局会话信息（包括 session_id）
            session_key, session_id, session_created, _ = self.message_queue.get_or_create_session(
                self.config.working_directory
            )

            embed = discord.Embed(
                title="📊 Claude Bridge 状态",
                color=discord.Color.blue()
            )

            # 显示 session ID 和状态（不显示 Key）
            session_info = f"**Session ID**: `{session_id[:8]}...`" if session_id else "`未生成`"
            session_info += f"\n**状态**: {'已创建 ✅' if session_created else '未创建 ⏳'}"
            embed.add_field(name="当前会话", value=session_info, inline=False)

            embed.add_field(name="工作目录", value=f"`{self.config.working_directory}`", inline=False)

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

                    await interaction.response.send_message(
                        f"🛑 {interaction.user.mention}，正在停止 Discord Bridge 服务...\n"
                        f"服务将在几秒钟后停止。"
                    )
                    print(f"[停止命令] 用户 {interaction.user.display_name} 确认停止服务")

                    # 执行停止脚本（通过 manager）
                    import subprocess
                    import os

                    try:
                        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                        manager_script = os.path.join(script_dir, 'manager.py')

                        if os.path.exists(manager_script):
                            # 在后台执行 manager stop
                            subprocess.Popen(
                                ["python", manager_script, "stop"],
                                cwd=script_dir,
                                creationflags=subprocess.CREATE_NEW_CONSOLE
                            )
                            print(f"✅ 停止命令已执行: python manager.py stop")
                        else:
                            await interaction.followup.send(f"❌ 找不到 manager.py")
                            print(f"⚠️  manager.py 不存在: {manager_script}")

                    except Exception as e:
                        await interaction.followup.send(f"❌ 停止失败: {str(e)}")
                        print(f"❌ 执行停止命令时出错: {e}")
                        import traceback
                        traceback.print_exc()

                    return

            # 第一次使用 /stop，记录请求
            self.stop_requests[user_id] = {"timestamp": current_time}

            await interaction.response.send_message(
                f"⚠️ {interaction.user.mention}，确定要停止 Discord Bridge 服务吗？\n"
                f"此操作将停止 Bot 和 Bridge，服务将不再响应消息。\n\n"
                f"**如需确认，请在 60 秒内再次使用 `/stop` 命令**"
            )

            print(f"[停止命令] 用户 {interaction.user.display_name} 请求停止服务，等待再次确认...")

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
            await interaction.response.send_message(
                f"🔄 {interaction.user.mention}，正在重启 Discord Bridge 服务...\n"
                f"请稍候，服务将在几秒钟后重新启动。"
            )
            print(f"[重启命令] 用户 {interaction.user.display_name} 触发了服务重启")

            # 执行重启脚本（通过 manager）
            import subprocess
            import os

            try:
                # 获取项目根目录
                script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                manager_script = os.path.join(script_dir, 'manager.py')

                if os.path.exists(manager_script):
                    # 在后台执行 manager restart
                    subprocess.Popen(
                        ["python", manager_script, "restart"],
                        cwd=script_dir,
                        creationflags=subprocess.CREATE_NEW_CONSOLE
                    )
                    print(f"✅ 重启命令已执行: python manager.py restart")
                else:
                    await interaction.followup.send(f"❌ 找不到 manager.py")
                    print(f"⚠️  manager.py 不存在: {manager_script}")

            except Exception as e:
                await interaction.followup.send(f"❌ 重启失败: {str(e)}")
                print(f"❌ 执行重启命令时出错: {e}")
                import traceback
                traceback.print_exc()

        @self.tree.command(name="upload", description="批量上传文件到工作区（最多25个）")
        @app_commands.describe(
            file1="文件 1（必填）",
            file2="文件 2（可选）",
            file3="文件 3（可选）",
            file4="文件 4（可选）",
            file5="文件 5（可选）",
            file6="文件 6（可选）",
            file7="文件 7（可选）",
            file8="文件 8（可选）",
            file9="文件 9（可选）",
            file10="文件 10（可选）",
            file11="文件 11（可选）",
            file12="文件 12（可选）",
            file13="文件 13（可选）",
            file14="文件 14（可选）",
            file15="文件 15（可选）",
            file16="文件 16（可选）",
            file17="文件 17（可选）",
            file18="文件 18（可选）",
            file19="文件 19（可选）",
            file20="文件 20（可选）",
            file21="文件 21（可选）",
            file22="文件 22（可选）",
            file23="文件 23（可选）",
            file24="文件 24（可选）",
            file25="文件 25（可选）"
        )
        async def upload_command(
            interaction: discord.Interaction,
            file1: discord.Attachment,
            file2: discord.Attachment = None,
            file3: discord.Attachment = None,
            file4: discord.Attachment = None,
            file5: discord.Attachment = None,
            file6: discord.Attachment = None,
            file7: discord.Attachment = None,
            file8: discord.Attachment = None,
            file9: discord.Attachment = None,
            file10: discord.Attachment = None,
            file11: discord.Attachment = None,
            file12: discord.Attachment = None,
            file13: discord.Attachment = None,
            file14: discord.Attachment = None,
            file15: discord.Attachment = None,
            file16: discord.Attachment = None,
            file17: discord.Attachment = None,
            file18: discord.Attachment = None,
            file19: discord.Attachment = None,
            file20: discord.Attachment = None,
            file21: discord.Attachment = None,
            file22: discord.Attachment = None,
            file23: discord.Attachment = None,
            file24: discord.Attachment = None,
            file25: discord.Attachment = None
        ):
            """处理文件上传命令（批量上传）"""
            import aiohttp
            from pathlib import Path

            # 收集所有非空文件
            files_list = [f for f in [file1, file2, file3, file4, file5, file6, file7, file8, file9, file10,
                                      file11, file12, file13, file14, file15, file16, file17, file18, file19, file20,
                                      file21, file22, file23, file24, file25] if f is not None]

            # 使用配置的默认下载目录
            save_dir = Path(self.config.default_download_directory)

            # 确保目录存在
            try:
                save_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                await interaction.response.send_message(
                    f"❌ {interaction.user.mention}，无法创建下载目录: {e}",
                    ephemeral=True
                )
                print(f"[文件上传] 创建目录失败: {e}")
                return

            # 先响应，告知用户正在处理
            await interaction.response.send_message(
                f"📤 {interaction.user.mention}，正在上传 {len(files_list)} 个文件到 `{save_dir}`..."
            )
            # 获取原始消息以便后续编辑
            status_message = await interaction.original_response()

            downloaded_files = []
            failed_files = []

            # 下载所有文件
            async with aiohttp.ClientSession() as session:
                for file in files_list:
                    try:
                        # 处理文件名冲突
                        local_path = save_dir / file.filename
                        counter = 1
                        original_stem = Path(file.filename).stem
                        original_suffix = Path(file.filename).suffix

                        # 检查文件是否存在，如存在则添加后缀
                        while local_path.exists():
                            local_path = save_dir / f"{original_stem}_{counter}{original_suffix}"
                            counter += 1

                        # 下载文件
                        async with session.get(file.url) as resp:
                            if resp.status == 200:
                                content = await resp.read()
                                with open(local_path, 'wb') as f:
                                    f.write(content)

                                downloaded_files.append({
                                    "filename": file.filename,
                                    "local_path": str(local_path),
                                    "size": len(content)
                                })
                                print(f"[文件上传] ✓ 已下载: {file.filename} -> {local_path}")
                            else:
                                raise ValueError(f"HTTP {resp.status}")

                    except Exception as e:
                        failed_files.append({
                            "filename": file.filename,
                            "error": str(e)
                        })
                        print(f"[文件上传] ✗ 下载失败: {file.filename} - {e}")

            # 获取目标频道（命令执行的频道/私聊）
            target_channel = interaction.channel

            # 构建响应消息
            response_lines = [
                f"✅ {interaction.user.mention}，文件上传完成！",
                f"📁 保存目录: `{save_dir}`",
                ""
            ]

            if downloaded_files:
                response_lines.append(f"**成功上传 {len(downloaded_files)} 个文件:**")
                for f in downloaded_files:
                    size_kb = f['size'] / 1024
                    response_lines.append(f"  • **{f['filename']}** ({size_kb:.1f} KB)")
                    response_lines.append(f"    `{f['local_path']}`")

            if failed_files:
                response_lines.append("")
                response_lines.append(f"**失败 {len(failed_files)} 个文件:**")
                for f in failed_files:
                    response_lines.append(f"  • **{f['filename']}**: {f['error']}")

            # 编辑原消息发送最终结果（文本）
            followup_msg = "\n".join(response_lines)
            await status_message.edit(content=followup_msg)

            # 将下载的文件发送回原频道（每个文件单独发送）
            if downloaded_files:
                for f in downloaded_files:
                    try:
                        discord_file = discord.File(f['local_path'], filename=f['filename'])
                        await target_channel.send(file=discord_file)
                        print(f"[文件上传] ✓ 已发送: {f['filename']}")
                    except Exception as e:
                        print(f"[文件上传] ✗ 发送文件失败 {f['filename']}: {e}")
                        await target_channel.send(f"❌ 发送文件失败: {f['filename']} - {e}")

            print(f"[文件上传] 用户 {interaction.user.display_name} 上传了 {len(downloaded_files)}/{len(files_list)} 个文件")

    async def on_ready(self):
        """Bot 准备就绪"""
        print(f"✓ Bot 已准备就绪!")
        print(f"✓ 在 {len(self.guilds)} 个服务器中")
        print(f"✓ 斜杠命令: /new, /status, /stop, /restart, /upload")

        # 发送启动通知
        await self.send_startup_notification()

    async def on_message(self, message: discord.Message):
        """处理接收到的消息"""
        # 忽略自己的消息
        if message.author == self.user:
            return

        # 检查是否被提及
        if self.user not in message.mentions:
            return

        # 检查频道权限（仅对频道消息生效，私聊不受限）
        if not isinstance(message.channel, discord.DMChannel):
            if self.config.allowed_channels:
                if message.channel.id not in self.config.allowed_channels:
                    return

        # 检查用户权限
        if self.config.allowed_users:
            if message.author.id not in self.config.allowed_users:
                await message.channel.send(
                    f"❌ {message.author.mention}，您没有权限使用此功能。"
                )
                return

        # 检查是否为转发/回复消息（带文件下载指令）
        if message.reference:
            await self.handle_file_download_command(message)
        else:
            # 处理普通消息
            await self.handle_user_message(message)

    async def handle_user_message(self, message: discord.Message):
        """处理用户消息"""
        try:
            # 移除 bot 提及，提取实际内容
            content = message.content
            for mention in message.mentions:
                if mention == self.user:
                    content = content.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
                    break

            content = content.strip()

            if not content:
                await message.channel.send("❌ 请提供消息内容。")
                return

            # 检测是否为私聊消息
            is_dm = isinstance(message.channel, discord.DMChannel)

            # 获取会话信息，检查是否为首次对话
            session_key, session_id, session_created, _ = self.message_queue.get_or_create_session(
                self.config.working_directory
            )

            # 创建消息对象（默认标签）
            msg = Message(
                id=None,
                direction=MessageDirection.TO_CLAUDE.value,
                content=content,
                status=MessageStatus.PENDING.value,
                discord_channel_id=message.channel.id,
                discord_message_id=message.id,
                discord_user_id=message.author.id,
                username=message.author.display_name,
                is_dm=is_dm,
                tag=MessageTag.DEFAULT.value
            )

            # 添加到消息队列（状态为 PENDING，等待 Claude Bridge 接收）
            message_id = self.message_queue.add_message(msg)

            print(f"[消息 #{message_id}] 收到来自 {message.author.display_name} 的消息: {content[:50]}... ({'私聊' if is_dm else '频道'})")

            # 根据配置决定是否发送确认消息
            if self.config.direct_reply_enabled:
                # 直接回复模式：不发送确认消息，直接启动 typing indicator
                from bot.streaming_queue import StreamingMessageQueue

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
                    "direct_reply": True,  # 标记为直接回复模式
                    "typing_task": typing_task,
                    "typing_active": True,
                    "streaming_queue": StreamingMessageQueue(
                        message.channel,
                        self.config.direct_reply_streaming_min_interval
                    ),
                    "last_streaming_content": "",
                    "sent_blocks": [],
                }
                print(f"[消息 #{message_id}] 直接回复模式：已启用，不发送确认消息")
            else:
                # Embed 模式：发送确认消息（原有逻辑）
                # 显示"正在输入"状态
                async with message.channel.typing():
                    pass

                confirmation_msg = await message.reply(
                    f"✅ 消息已接收！正在等待 Claude Bridge 接收...\n"
                    f"消息 ID: {message_id}"
                )

                self.pending_messages[message_id] = {
                    "channel": message.channel,
                    "user_message": message,
                    "confirmation_msg": confirmation_msg,
                    "start_time": asyncio.get_event_loop().time(),
                    "content": content[:50],
                    "notified_processing": False,
                    "direct_reply": False,  # 标记为 Embed 模式
                }

        except Exception as e:
            print(f"❌ 处理消息时出错: {e}")
            import traceback
            traceback.print_exc()
            await message.channel.send(f"❌ 处理消息时出错: {str(e)}")

    async def handle_file_download_command(self, message: discord.Message):
        """处理附件引用消息（转发/回复消息）"""
        try:
            # 获取原始消息的 ID 和频道 ID
            original_message_id = message.reference.message_id
            original_channel_id = message.reference.channel_id

            print(f"[附件引用] 用户 {message.author.display_name} 引用了消息 {original_message_id}")

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
                attachment_infos.append(AttachmentInfo(
                    filename=attachment.filename,
                    size=attachment.size,
                    url=attachment.url,
                    description=attachment.description
                ))

            print(f"[附件引用] 检测到 {len(attachment_infos)} 个附件")
            for idx, att in enumerate(attachment_infos, 1):
                print(f"  附件 {idx}: {att.filename} ({att.size} 字节)")

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
                self.config.working_directory
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
                    attachments=attachment_infos  # 附件信息作为独立参数
                )

                # 添加到消息队列
                message_id = self.message_queue.add_message(msg)

                print(f"[消息 #{message_id}] 收到来自 {message.author.display_name} 的附件引用消息 ({'私聊' if is_dm else '频道'})")

                # 根据配置决定是否发送确认消息
                if self.config.direct_reply_enabled:
                    # 直接回复模式：不发送确认消息，直接启动 typing indicator
                    from bot.streaming_queue import StreamingMessageQueue

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
                        "direct_reply": True,  # 标记为直接回复模式
                        "typing_task": typing_task,
                        "typing_active": True,
                        "streaming_queue": StreamingMessageQueue(
                            message.channel,
                            self.config.direct_reply_streaming_min_interval
                        ),
                        "last_streaming_content": "",
                        "sent_blocks": [],
                    }
                    print(f"[消息 #{message_id}] 直接回复模式：已启用，不发送确认消息")
                else:
                    # Embed 模式：发送确认消息（原有逻辑）
                    confirmation_msg = await message.reply(
                        f"✅ 消息已接收！检测到 {len(original_message.attachments)} 个附件\n"
                        f"消息 ID: {message_id}"
                    )

                    self.pending_messages[message_id] = {
                        "channel": message.channel,
                        "user_message": message,
                        "confirmation_msg": confirmation_msg,
                        "start_time": asyncio.get_event_loop().time(),
                        "content": content[:50] if content else "(空消息)",
                        "notified_processing": False,
                        "direct_reply": False,  # 标记为 Embed 模式
                    }

        except Exception as e:
            print(f"❌ 处理附件引用消息时出错: {e}")
            import traceback
            traceback.print_exc()
            await message.channel.send(f"❌ 处理消息时出错: {str(e)}")

    async def monitor_download_progress(self, request_id: int, channel, confirmation_msg):
        """监控文件下载进度（轮询方式）"""
        import json
        import sqlite3
        from shared.message_queue import FileDownloadRequestStatus

        try:
            max_wait_time = 120  # 最大等待 120 秒
            check_interval = 2   # 每 2 秒检查一次
            elapsed = 0
            last_progress_update = 0

            print(f"[文件下载 #{request_id}] 开始监控下载进度")

            while elapsed < max_wait_time:
                # 直接查询数据库状态
                conn = sqlite3.connect(self.config.database_path)
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT status, downloaded_files, save_directory, error
                    FROM file_download_requests
                    WHERE id = ?
                """, (request_id,))
                db_result = cursor.fetchone()
                conn.close()

                if db_result:
                    status, files_json, save_dir, error = db_result

                    if status == FileDownloadRequestStatus.COMPLETED.value:
                        # 下载完成
                        print(f"[文件下载 #{request_id}] 下载完成")

                        downloaded_files = []
                        if files_json:
                            try:
                                result_data = json.loads(files_json)
                                downloaded_files = result_data.get("downloaded_files", [])
                            except json.JSONDecodeError as e:
                                print(f"[文件下载 #{request_id}] 解析文件列表失败: {e}")

                        if downloaded_files:
                            files_info = "\n".join([
                                f"  • {f['filename']} ({f['size']} 字节)"
                                for f in downloaded_files
                            ])
                            await confirmation_msg.edit(
                                content=f"✅ 文件下载完成！请求 #{request_id}\n"
                                        f"保存目录: `{save_dir}`\n"
                                        f"已下载 {len(downloaded_files)} 个文件:\n"
                                        f"{files_info}"
                            )
                        else:
                            await confirmation_msg.edit(
                                content=f"⚠️ 文件下载完成，但没有找到文件。请求 #{request_id}"
                            )
                        return

                    elif status == FileDownloadRequestStatus.FAILED.value:
                        # 下载失败
                        print(f"[文件下载 #{request_id}] 下载失败: {error}")
                        error_msg = error or "未知错误"
                        await confirmation_msg.edit(
                            content=f"❌ 文件下载失败！请求 #{request_id}\n"
                                    f"错误: {error_msg}"
                        )
                        return

                    elif status == FileDownloadRequestStatus.PROCESSING.value:
                        # 正在处理中
                        print(f"[文件下载 #{request_id}] 正在处理中... ({elapsed}s)")

                        # 每 30 秒更新一次进度提示
                        if elapsed - last_progress_update >= 30:
                            await confirmation_msg.edit(
                                content=f"⏳ 正在下载中... ({elapsed}/{max_wait_time}秒)\n"
                                        f"请求 ID: {request_id}"
                            )
                            last_progress_update = elapsed

                # 等待下一次检查
                await asyncio.sleep(check_interval)
                elapsed += check_interval

            # 超时 - 最后检查一次
            print(f"[文件下载 #{request_id}] 监控超时 ({elapsed}秒)，最后检查一次")
            conn = sqlite3.connect(self.config.database_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT status, downloaded_files, save_directory, error
                FROM file_download_requests
                WHERE id = ?
            """, (request_id,))
            db_result = cursor.fetchone()
            conn.close()

            if db_result and db_result[0] == FileDownloadRequestStatus.COMPLETED.value:
                # 实际上已经完成
                print(f"[文件下载 #{request_id}] 超时检查时发现已完成")
                downloaded_files = []
                if db_result[1]:
                    try:
                        result_data = json.loads(db_result[1])
                        downloaded_files = result_data.get("downloaded_files", [])
                    except json.JSONDecodeError:
                        pass

                if downloaded_files:
                    files_info = "\n".join([
                        f"  • {f['filename']} ({f['size']} 字节)"
                        for f in downloaded_files
                    ])
                    await confirmation_msg.edit(
                        content=f"✅ 文件下载完成！请求 #{request_id}\n"
                                f"保存目录: `{db_result[2]}`\n"
                                f"已下载 {len(downloaded_files)} 个文件:\n"
                                f"{files_info}"
                    )
                else:
                    await confirmation_msg.edit(
                        content=f"⚠️ 文件下载完成，但没有找到文件。请求 #{request_id}"
                    )
            else:
                # 真的超时了
                print(f"[文件下载 #{request_id}] 真的超时")
                await confirmation_msg.edit(
                    content=f"⏱️ 文件下载请求 #{request_id} 超时（{max_wait_time}秒）\n"
                            f"可能原因：Bot 未运行或消息不存在。"
                )

        except Exception as e:
            print(f"❌ 监控下载进度时出错: {e}")
            import traceback
            traceback.print_exc()

    async def check_responses(self):
        """定期检查 Claude 的响应和消息状态"""
        await self.wait_until_ready()

        while not self.is_closed():
            try:
                current_time = asyncio.get_event_loop().time()

                # 扫描外部插入的消息（is_external=True）
                # 查询 pending 和 processing 状态，并过滤已追踪的消息
                import sqlite3
                conn = sqlite3.connect(self.config.database_path)
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, discord_user_id, discord_channel_id, username, content, is_dm
                    FROM messages
                    WHERE status IN (?, ?) AND direction = ? AND is_external = 1
                    ORDER BY created_at ASC
                """, (MessageStatus.PENDING.value, MessageStatus.PROCESSING.value, MessageDirection.TO_CLAUDE.value))
                external_messages = cursor.fetchall()
                conn.close()

                for msg_info in external_messages:
                    msg_id, user_id, channel_id, username, content, is_dm = msg_info
                    # 跳过已追踪的消息（防止重复处理）
                    if msg_id in self.pending_messages:
                        continue

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
                                    print(f"⚠️  外部消息 #{msg_id}: 找不到频道 {channel_id}")
                                    continue

                            # 根据配置决定是否发送确认消息
                            if self.config.direct_reply_enabled:
                                # 直接回复模式：不发送确认消息，直接启动 typing indicator
                                from bot.streaming_queue import StreamingMessageQueue

                                typing_task = asyncio.create_task(
                                    self._maintain_typing_indicator(channel)
                                )

                                self.pending_messages[msg_id] = {
                                    "channel": channel,
                                    "user_message": None,
                                    "confirmation_msg": None,  # 无确认消息
                                    "initial_message": None,
                                    "start_time": asyncio.get_event_loop().time(),
                                    "content": content[:50],
                                    "notified_processing": False,
                                    "direct_reply": True,  # 标记为直接回复模式
                                    "typing_task": typing_task,
                                    "typing_active": True,
                                    "streaming_queue": StreamingMessageQueue(
                                        channel,
                                        self.config.direct_reply_streaming_min_interval
                                    ),
                                    "last_streaming_content": "",
                                    "sent_blocks": [],
                                }
                                print(f"📨 [消息 #{msg_id}] 已加载外部消息: {username} (直接回复模式)")
                            else:
                                # Embed 模式：发送确认消息（原有逻辑）
                                confirmation_msg = await channel.send(
                                    f"✅ 消息已接收！正在等待 Claude Bridge 接收...\n"
                                    f"消息 ID: {msg_id}"
                                )

                                # 🔥 加入 pending_messages 追踪（保存 initial_message 引用）
                                self.pending_messages[msg_id] = {
                                    "channel": channel,
                                    "user_message": None,
                                    "confirmation_msg": confirmation_msg,
                                    "initial_message": confirmation_msg,  # 🔥 用于流式编辑
                                    "start_time": asyncio.get_event_loop().time(),
                                    "content": content[:50],
                                    "notified_processing": False,
                                    "direct_reply": False,  # 标记为 Embed 模式
                                }
                                print(f"📨 [消息 #{msg_id}] 已加载外部消息: {username}")

                        except Exception as e:
                            print(f"⚠️  外部消息 #{msg_id} 加载失败: {e}")

                # 检查待处理消息的状态
                messages_to_remove = []
                for msg_id, tracking_info in list(self.pending_messages.items()):
                    elapsed_time = current_time - tracking_info["start_time"]

                    # 查询数据库中消息的最新状态
                    import sqlite3
                    conn = sqlite3.connect(self.config.database_path)
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT status, response, error FROM messages WHERE id = ?
                    """, (msg_id,))
                    result = cursor.fetchone()
                    conn.close()

                    if not result:
                        # 消息不存在，从追踪中移除
                        messages_to_remove.append(msg_id)
                        continue

                    status, response, error = result

                    # 状态 1: PENDING - 等待 Claude Bridge 接收
                    if status == MessageStatus.PENDING.value:
                        # 只有在未进入 AI_STARTED 状态时才检查超时
                        is_direct_reply = tracking_info.get("direct_reply", False)
                        if (not tracking_info.get("notified_ai_started") and
                            not tracking_info.get("notified_pending_timeout") and
                            elapsed_time > 30):
                            # 超过 30 秒仍未被接收
                            try:
                                if not is_direct_reply:
                                    # Embed 模式：编辑确认消息
                                    await tracking_info["confirmation_msg"].edit(
                                        content=f"⏱️ 消息 #{msg_id} 等待时间过长（{int(elapsed_time)}秒）\n"
                                                f"Claude Bridge 可能未运行，或当前有尚未响应完成的消息。\n"
                                                f"建议：检查服务状态，或等待当前消息响应完成。"
                                    )
                                else:
                                    # 直接回复模式：发送超时消息
                                    await tracking_info["channel"].send(
                                        f"⏱️ 消息 #{msg_id} 等待时间过长（{int(elapsed_time)}秒）\n"
                                        f"Claude Bridge 可能未运行，或当前有尚未响应完成的消息。\n"
                                        f"建议：检查服务状态，或等待当前消息响应完成。"
                                    )
                                    # 停止 typing indicator
                                    tracking_info["typing_active"] = False
                                    typing_task = tracking_info.get("typing_task")
                                    if typing_task and not typing_task.done():
                                        typing_task.cancel()
                                tracking_info["notified_pending_timeout"] = True
                            except Exception as e:
                                print(f"⚠️ 无法发送超时消息: {e}")
                            print(f"⚠️ [消息 #{msg_id}] PENDING 超时（{int(elapsed_time)}秒）")

                    # 状态 2: PROCESSING 且无 response - Claude Bridge已接收，正在调用CLI
                    elif status == MessageStatus.PROCESSING.value and not response:
                        is_direct_reply = tracking_info.get("direct_reply", False)
                        if not tracking_info.get("notified_bridge_received"):
                            # Claude Bridge成功接收消息
                            try:
                                if not is_direct_reply:
                                    # Embed 模式：编辑确认消息
                                    await tracking_info["confirmation_msg"].edit(
                                        content=f"⏳ 消息 #{msg_id} 处理中\n"
                                                f"Claude Bridge 已接收消息，正在调用 Claude Code CLI..."
                                    )
                                # 直接回复模式：不发送任何消息（typing indicator 依然显示）
                                tracking_info["notified_bridge_received"] = True
                                print(f"📥 [消息 #{msg_id}] Claude Bridge 已接收消息 ({'直接回复模式' if is_direct_reply else 'Embed模式'})")
                            except Exception as e:
                                print(f"⚠️ 无法发送处理中消息: {e}")

                    # 状态 2.5: AI_STARTED - AI 开始工作！
                    elif status == MessageStatus.AI_STARTED.value:
                        if not tracking_info.get("notified_ai_started"):
                            # 检查是否为直接回复模式
                            is_direct_reply = tracking_info.get("direct_reply", False)

                            if not is_direct_reply:
                                # Embed 模式：发送 Embed 卡片（原有逻辑）
                                try:
                                    # 🔥 立即发送一个初始 Embed
                                    embed = discord.Embed(
                                        title="🤖 Claude Code 处理中",
                                        description=f"消息 #{msg_id} 已接收，AI 正在思考，请稍候……",
                                        color=discord.Color.gold()
                                    )
                                    embed.set_footer(text=f"消息 ID: {msg_id}")

                                    # 发送初始 Embed
                                    initial_embed_msg = await tracking_info["channel"].send(embed=embed)

                                    # 🔥 保存 Embed 引用，供后续流式编辑使用
                                    tracking_info["discord_message"] = initial_embed_msg

                                    # 编辑旧的确认消息
                                    await tracking_info["confirmation_msg"].edit(
                                        content=f"🔄 消息 #{msg_id} 已接收，AI 正在工作，请稍候……"
                                    )

                                    tracking_info["notified_ai_started"] = True
                                    print(f"🤖 [消息 #{msg_id}] AI 开始工作，已发送初始 Embed (Embed模式)")
                                except Exception as e:
                                    print(f"⚠️ 无法发送 Embed: {e}")
                                    import traceback
                                    traceback.print_exc()
                            else:
                                # 直接回复模式：不发送任何消息，只标记状态
                                # typing indicator 依旧由 _maintain_typing_indicator 维持
                                tracking_info["notified_ai_started"] = True
                                print(f"🤖 [消息 #{msg_id}] AI 开始工作 (直接回复模式)")

                    # 状态 3: PROCESSING 且有 response - AI 响应完成，发送响应
                    elif status == MessageStatus.PROCESSING.value and response:
                        # 检查是否有流式响应
                        import sqlite3
                        conn = sqlite3.connect(self.config.database_path)
                        cursor = conn.cursor()
                        cursor.execute("""
                            SELECT streaming_response FROM messages WHERE id = ?
                        """, (msg_id,))
                        streaming_result = cursor.fetchone()
                        conn.close()

                        # 如果有流式响应，说明已经通过流式输出了
                        if streaming_result and streaming_result[0]:
                            is_direct_reply = tracking_info.get("direct_reply", False)

                            if not is_direct_reply:
                                # Embed 模式：编辑 Embed 为最终状态（原有逻辑）
                                print(f"✅ [消息 #{msg_id}] 响应已完成 (Embed模式)")

                                # 🔥 更新状态为已完成
                                self.message_queue.update_status(msg_id, MessageStatus.COMPLETED)

                                # 🔥 编辑 Embed 为成功状态（支持多 Embed 显示完整内容）
                                try:
                                    discord_msg = tracking_info.get('discord_message')
                                    if discord_msg:
                                        # 获取最终的流式响应内容
                                        final_response = streaming_result[0]

                                        # Discord Embed 限制：
                                        # - Description: 最多 4096 字符
                                        # - Embed 总大小: 最多 6000 字符
                                        max_desc_length = 3800

                                        # 计算可用于实际内容的空间（减去消息 ID 前缀）
                                        header_text = f"**消息 ID: {msg_id}**\n\n"
                                        available_space = max_desc_length - len(header_text)

                                        if len(final_response) <= available_space:
                                            # 短响应：直接编辑 Embed
                                            display_text = final_response
                                            footer_text = f"消息 ID: {msg_id} • 响应已完成 ({len(final_response)} 字符)"

                                            embed = discord.Embed(
                                                title="✨ Claude Code 的回复",
                                                description=header_text + display_text,
                                                color=discord.Color.green()
                                            )
                                            embed.set_footer(text=footer_text)
                                            await discord_msg.edit(embed=embed)
                                        else:
                                            # 长响应：删除旧 Embed，发送多个新 Embed 显示完整内容
                                            await discord_msg.delete()

                                            # Discord Embed 限制（保守值）
                                            # Description 最多 4096，总大小最多 6000
                                            max_desc_first = 3780  # 第一个 Embed：需要为 header 留空间
                                            max_desc_other = 4000  # 后续 Embed：不需要 header，可以放更多

                                            # 计算需要多少个 Embed
                                            total_length = len(final_response)
                                            parts = []
                                            current_pos = 0

                                            part_num = 1
                                            while current_pos < total_length:
                                                remaining = total_length - current_pos

                                                # 根据是否为第一个 Embed，使用不同的限制
                                                if part_num == 1:
                                                    chunk_size = min(max_desc_first, remaining)
                                                    chunk = final_response[current_pos:current_pos + chunk_size]
                                                    desc = header_text + chunk
                                                else:
                                                    chunk_size = min(max_desc_other, remaining)
                                                    chunk = final_response[current_pos:current_pos + chunk_size]
                                                    desc = chunk

                                                parts.append((desc, chunk_size))
                                                current_pos += chunk_size
                                                part_num += 1

                                            # 发送所有 Embed
                                            for i, (part_desc, chunk_size) in enumerate(parts, 1):
                                                if i == 1:
                                                    # 第一个 Embed
                                                    embed = discord.Embed(
                                                        title="✨ Claude Code 的回复",
                                                        description=part_desc,
                                                        color=discord.Color.green()
                                                    )
                                                    embed.set_footer(text=f"消息 ID: {msg_id} • 第 {i}/{len(parts)} 部分 • 共 {total_length} 字符")
                                                    await discord_msg.channel.send(embed=embed)
                                                else:
                                                    # 后续 Embed
                                                    embed = discord.Embed(
                                                        title=f"✨ Claude Code 的回复 (续 {i}/{len(parts)})",
                                                        description=part_desc,
                                                        color=discord.Color.green()
                                                    )
                                                    embed.set_footer(text=f"消息 ID: {msg_id} • 第 {i}/{len(parts)} 部分")
                                                    await discord_msg.channel.send(embed=embed)

                                            print(f"[消息 #{msg_id}] 已发送 {len(parts)} 个 Embed (共 {total_length} 字符)")

                                    # 🔥 同时更新确认消息为完成状态
                                    confirmation_msg = tracking_info.get('confirmation_msg')
                                    if confirmation_msg:
                                        await confirmation_msg.edit(
                                            content=f"✅ 消息 #{msg_id} 响应已完成！"
                                        )
                                except Exception as e:
                                    print(f"❌ 发送响应时出错: {e}")
                                    import traceback
                                    traceback.print_exc()
                            else:
                                # 直接回复模式：等待所有 block 发送完成
                                print(f"✅ [消息 #{msg_id}] 响应已完成 (直接回复模式)")

                                streaming_queue = tracking_info.get("streaming_queue")
                                if streaming_queue:
                                    # 等待队列为空（所有消息已发送）
                                    while not streaming_queue.is_empty():
                                        await asyncio.sleep(0.1)

                                # 停止 typing indicator
                                tracking_info["typing_active"] = False
                                typing_task = tracking_info.get("typing_task")
                                if typing_task and not typing_task.done():
                                    typing_task.cancel()

                                # 🔥 更新状态为已完成
                                self.message_queue.update_status(msg_id, MessageStatus.COMPLETED)

                                print(f"✅ [消息 #{msg_id}] 直接回复模式：所有 block 已发送")

                            messages_to_remove.append(msg_id)
                            continue

                        # AI_STARTED 状态已经提前触发了"Claude Code 处理中"提示
                        # 这里直接发送响应即可
                        try:
                            # 获取完整消息信息
                            conn = sqlite3.connect(self.config.database_path)
                            cursor = conn.cursor()
                            cursor.execute("""
                                SELECT discord_channel_id, discord_message_id, username,
                                       content, is_dm, discord_user_id
                                FROM messages WHERE id = ?
                            """, (msg_id,))
                            msg_info = cursor.fetchone()
                            conn.close()

                            if msg_info:
                                channel_id, original_msg_id, username, content, is_dm, user_id = msg_info

                                # 区分私聊和频道消息
                                if is_dm:
                                    user = self.get_user(user_id)
                                    if not user:
                                        try:
                                            user = await self.fetch_user(user_id)
                                        except discord.NotFound:
                                            print(f"⚠️  找不到用户 {user_id}")
                                            messages_to_remove.append(msg_id)
                                            continue
                                        except discord.HTTPException as e:
                                            print(f"⚠️  获取用户 {user_id} 失败: {e}")
                                            messages_to_remove.append(msg_id)
                                            continue
                                    channel = await user.create_dm()
                                else:
                                    channel = self.get_channel(channel_id)
                                    if not channel:
                                        print(f"⚠️  找不到频道 {channel_id}")
                                        messages_to_remove.append(msg_id)
                                        continue

                                # Discord Embed 限制：
                                # - Description: 最多 4096 字符
                                # - Embed 总大小: 最多 6000 字符（包括 title、description、footer）
                                max_desc_length = 3800

                                # 计算可用于实际内容的空间（减去消息 ID 前缀）
                                header_text = f"**消息 ID: {msg_id}**\n\n"
                                available_space = max_desc_length - len(header_text)

                                if len(response) <= available_space:
                                    # 短响应：直接发送完整内容
                                    display_text = response
                                    footer_text = f"消息 ID: {msg_id} • 响应已完成 ({len(response)} 字符)"

                                    # 创建 Embed
                                    embed = discord.Embed(
                                        title="✨ Claude Code 的回复",
                                        description=header_text + display_text,
                                        color=discord.Color.green()
                                    )
                                    embed.set_footer(text=footer_text)
                                    await channel.send(embed=embed)
                                else:
                                    # 长响应：发送多个 Embed 显示完整内容
                                    total_length = len(response)

                                    # Discord Embed 限制（保守值）
                                    max_desc_first = 3780  # 第一个 Embed：需要为 header 留空间
                                    max_desc_other = 4000  # 后续 Embed：不需要 header，可以放更多

                                    parts = []
                                    current_pos = 0

                                    part_num = 1
                                    while current_pos < total_length:
                                        remaining = total_length - current_pos

                                        # 根据是否为第一个 Embed，使用不同的限制
                                        if part_num == 1:
                                            chunk_size = min(max_desc_first, remaining)
                                            chunk = response[current_pos:current_pos + chunk_size]
                                            desc = header_text + chunk
                                        else:
                                            chunk_size = min(max_desc_other, remaining)
                                            chunk = response[current_pos:current_pos + chunk_size]
                                            desc = chunk

                                        parts.append((desc, chunk_size))
                                        current_pos += chunk_size
                                        part_num += 1

                                    # 发送所有 Embed
                                    for i, (part_desc, chunk_size) in enumerate(parts, 1):
                                        if i == 1:
                                            # 第一个 Embed
                                            embed = discord.Embed(
                                                title="✨ Claude Code 的回复",
                                                description=part_desc,
                                                color=discord.Color.green()
                                            )
                                            embed.set_footer(text=f"消息 ID: {msg_id} • 第 {i}/{len(parts)} 部分 • 共 {total_length} 字符")
                                            await channel.send(embed=embed)
                                        else:
                                            # 后续 Embed
                                            embed = discord.Embed(
                                                title=f"✨ Claude Code 的回复 (续 {i}/{len(parts)})",
                                                description=part_desc,
                                                color=discord.Color.green()
                                            )
                                            embed.set_footer(text=f"消息 ID: {msg_id} • 第 {i}/{len(parts)} 部分")
                                            await channel.send(embed=embed)

                                    print(f"[消息 #{msg_id}] 已发送 {len(parts)} 个 Embed (共 {total_length} 字符)")

                                # 更新状态为已完成
                                self.message_queue.update_status(msg_id, MessageStatus.COMPLETED)
                                print(f"[消息 #{msg_id}] 已发送响应到 Discord")

                                # 发送响应成功提示
                                try:
                                    await tracking_info["confirmation_msg"].edit(
                                        content=f"✅ 消息 #{msg_id} 响应成功！"
                                    )
                                except Exception as e:
                                    print(f"⚠️ 无法编辑确认消息: {e}")

                                messages_to_remove.append(msg_id)

                        except Exception as e:
                            print(f"❌ 发送响应时出错: {e}")
                            import traceback
                            traceback.print_exc()
                            self.message_queue.update_status(msg_id, MessageStatus.FAILED, error=str(e))
                            messages_to_remove.append(msg_id)

                    # 状态 4: FAILED - 处理失败
                    elif status == MessageStatus.FAILED.value:
                        is_direct_reply = tracking_info.get("direct_reply", False)

                        if not is_direct_reply:
                            # Embed 模式：编辑确认消息（原有逻辑）
                            try:
                                error_msg = error or "未知错误"
                                await tracking_info["confirmation_msg"].edit(
                                    content=f"❌ 消息 #{msg_id} 处理失败\n"
                                            f"错误: {error_msg}"
                                )
                            except Exception as e:
                                print(f"⚠️ 无法编辑确认消息: {e}")
                        else:
                            # 直接回复模式：发送错误消息
                            try:
                                error_msg = error or "未知错误"
                                await tracking_info["channel"].send(
                                    f"❌ 消息处理失败\n错误: {error_msg}"
                                )

                                # 停止 typing indicator
                                tracking_info["typing_active"] = False
                                typing_task = tracking_info.get("typing_task")
                                if typing_task and not typing_task.done():
                                    typing_task.cancel()
                            except Exception as e:
                                print(f"⚠️ 无法发送错误消息: {e}")

                        messages_to_remove.append(msg_id)
                        print(f"❌ [消息 #{msg_id}] 处理失败: {error}")

                # 清理已处理的消息
                for msg_id in messages_to_remove:
                    if msg_id in self.pending_messages:
                        del self.pending_messages[msg_id]

                # 等待一段时间再检查
                await asyncio.sleep(self.config.poll_interval / 1000)

            except Exception as e:
                print(f"❌ 检查响应时出错: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(5)

    async def check_streaming_responses(self):
        """定期检查流式响应更新并实时发送 Discord 消息"""
        await self.wait_until_ready()

        print("🌊 流式响应检查任务已启动")

        while not self.is_closed():
            try:
                import sqlite3

                # 查询有 streaming_response 的消息（ai_started 和 processing 状态）
                conn = sqlite3.connect(self.config.database_path)
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, discord_channel_id, streaming_response
                    FROM messages
                    WHERE status IN ('ai_started', 'processing')
                      AND streaming_response IS NOT NULL
                      AND streaming_response != ''
                    ORDER BY last_stream_update DESC
                """)
                rows = cursor.fetchall()
                conn.close()

                for msg_id, channel_id, streaming_response in rows:
                    # 如果消息在 pending_messages 中，处理它
                    if msg_id in self.pending_messages:
                        pending = self.pending_messages[msg_id]
                        is_direct_reply = pending.get("direct_reply", False)

                        if is_direct_reply:
                            # 直接回复模式：检测并发送新的 block
                            last_content = pending.get("last_streaming_content", "")
                            new_blocks = self._detect_new_blocks(last_content, streaming_response)

                            if new_blocks:
                                # 合并短 block（可选）
                                if self.config.direct_reply_merge_short_blocks:
                                    new_blocks = self._merge_short_blocks(new_blocks)

                                # 将新 block 添加到发送队列
                                streaming_queue = pending.get("streaming_queue")
                                if streaming_queue:
                                    for block in new_blocks:
                                        await streaming_queue.add_block(block)
                                        pending["sent_blocks"].append(block)

                                # 更新上次的内容
                                pending["last_streaming_content"] = streaming_response

                                # 如果配置为在第一个 block 后停止 typing indicator
                                if (self.config.direct_reply_stop_typing_after_first_block and
                                    pending.get("typing_active")):
                                    pending["typing_active"] = False
                                    typing_task = pending.get("typing_task")
                                    if typing_task and not typing_task.done():
                                        typing_task.cancel()
                        else:
                            # Embed 模式：编辑 Embed 卡片（原有逻辑）
                            discord_msg = pending.get('discord_message')
                            if discord_msg:
                                try:
                                    # 🔥 实时编辑 Embed（流式更新）- 简化版本，避免 Embed 过大
                                    if streaming_response:
                                        # Discord Embed 限制：
                                        # - Description: 最多 4096 字符
                                        # - Embed 总大小: 最多 6000 字符（包括 title、description、footer）
                                        # 流式更新时只显示前 3800 字符，为 footer 和消息 ID 前缀留空间
                                        max_desc_length = 3800

                                        # 计算可用于实际内容的空间（减去消息 ID 前缀）
                                        header_text = f"**消息 ID: {msg_id}**\n\n"
                                        available_space = max_desc_length - len(header_text)

                                        if len(streaming_response) <= available_space:
                                            # 短响应：直接编辑原 Embed
                                            display_text = streaming_response
                                        else:
                                            # 长响应：截断并添加提示
                                            display_text = streaming_response[:available_space]
                                            display_text += f"\n\n...(实时预览已显示 {available_space}/{len(streaming_response)} 字符，完整内容将在 AI 完成后发送)"

                                        # 编辑 Embed
                                        embed = discord.Embed(
                                            title="🤖 Claude Code 响应",
                                            description=header_text + display_text,
                                            color=discord.Color.blue()
                                        )
                                        embed.set_footer(text=f"消息 ID: {msg_id} • 实时更新中... ({len(streaming_response)} 字符)")
                                        await discord_msg.edit(embed=embed)

                                except discord.NotFound:
                                    # 消息已删除，从 pending 移除
                                    print(f"⚠️  消息 #{msg_id} Discord Embed 已删除")
                                    del self.pending_messages[msg_id]
                                except Exception as e:
                                    print(f"❌ 编辑 Embed #{msg_id} 失败: {e}")
                                    import traceback
                                    traceback.print_exc()

                # 等待一段时间再检查（0.5 秒，比 check_responses 更快）
                await asyncio.sleep(0.5)

            except Exception as e:
                print(f"❌ 检查流式响应时出错: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(5)

    async def check_file_requests(self):
        """定期检查并处理文件发送请求"""
        await self.wait_until_ready()

        print("📁 文件发送检查任务已启动")

        while not self.is_closed():
            try:
                # 获取下一个待处理的文件请求
                from shared.message_queue import FileRequestStatus
                file_request = self.message_queue.get_next_file_request()

                if file_request:
                    print(f"📁 处理文件请求 #{file_request.id}")
                    # 标记为处理中
                    self.message_queue.update_file_request_status(
                        file_request.id,
                        FileRequestStatus.PROCESSING
                    )

                    try:
                        import os
                        import json

                        # 准备文件列表
                        valid_files = []
                        for file_path in file_request.file_paths:
                            if os.path.exists(file_path):
                                valid_files.append(discord.File(file_path))

                        if not valid_files:
                            raise FileNotFoundError("没有有效的文件")

                        # 确定发送目标
                        if file_request.user_id:
                            # 发送到用户私聊
                            user = self.get_user(file_request.user_id)
                            if not user:
                                user = await self.fetch_user(file_request.user_id)
                            target_channel = await user.create_dm()
                            target_info = f"用户 {user.display_name}"
                        elif file_request.channel_id:
                            # 发送到频道
                            target_channel = self.get_channel(file_request.channel_id)
                            if not target_channel:
                                raise ValueError(f"找不到频道: {file_request.channel_id}")
                            target_info = f"频道 {target_channel.name}"
                        else:
                            raise ValueError("必须指定 user_id 或 channel_id")

                        # 发送文件
                        sent_msg = await target_channel.send(
                            files=valid_files if len(valid_files) > 1 else valid_files
                        )

                        # 标记为完成
                        result = json.dumps({
                            "success": True,
                            "message": f"成功发送 {len(valid_files)} 个文件到 {target_info}",
                            "message_id": str(sent_msg.id)
                        }, ensure_ascii=False)
                        self.message_queue.update_file_request_status(
                            file_request.id,
                            FileRequestStatus.COMPLETED,
                            result=result
                        )
                        print(f"✅ 文件请求 #{file_request.id} 处理完成")

                    except Exception as e:
                        # 标记为失败
                        error_msg = json.dumps({
                            "success": False,
                            "error": str(e)
                        }, ensure_ascii=False)
                        self.message_queue.update_file_request_status(
                            file_request.id,
                            FileRequestStatus.FAILED,
                            error=error_msg
                        )
                        print(f"❌ 文件请求 #{file_request.id} 处理失败: {e}")

                # 等待一段时间再检查
                await asyncio.sleep(self.config.poll_interval / 1000)

            except Exception as e:
                print(f"❌ 检查文件请求时出错: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(5)

    async def check_file_downloads(self):
        """定期检查并处理文件下载请求（支持私聊和频道）"""
        await self.wait_until_ready()

        print("📥 文件下载检查任务已启动")

        while not self.is_closed():
            try:
                # 获取下一个待处理的下载请求
                from shared.message_queue import FileDownloadRequestStatus
                download_request = self.message_queue.get_next_file_download_request()

                if download_request:
                    print(f"📥 处理文件下载请求 #{download_request.id}")
                    # 标记为处理中
                    self.message_queue.update_file_download_request_status(
                        download_request.id,
                        FileDownloadRequestStatus.PROCESSING
                    )

                    try:
                        import os
                        import json
                        import aiohttp
                        from pathlib import Path

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
                                # 处理文件名冲突
                                local_path = save_dir / attachment.filename
                                counter = 1
                                while local_path.exists():
                                    stem = Path(attachment.filename).stem
                                    suffix = Path(attachment.filename).suffix
                                    local_path = save_dir / f"{stem}_{counter}{suffix}"
                                    counter += 1

                                # 下载文件
                                async with session.get(attachment.url) as resp:
                                    if resp.status == 200:
                                        # 写入文件
                                        with open(local_path, 'wb') as f:
                                            f.write(await resp.read())

                                        downloaded_files.append({
                                            "filename": attachment.filename,
                                            "local_path": str(local_path),
                                            "size": attachment.size
                                        })
                                        print(f"  ✓ 已下载: {attachment.filename} -> {local_path}")
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
                        print(f"✅ 文件下载请求 #{download_request.id} 处理完成")

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
                        print(f"❌ 文件下载请求 #{download_request.id} 处理失败: {e}")
                        import traceback
                        traceback.print_exc()

                # 等待一段时间再检查
                await asyncio.sleep(self.config.poll_interval / 1000)

            except Exception as e:
                print(f"❌ 检查文件下载请求时出错: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(5)

    async def check_message_requests(self):
        """定期检查并处理消息发送请求"""
        await self.wait_until_ready()

        print("💬 消息发送检查任务已启动")

        while not self.is_closed():
            try:
                # 获取下一个待处理的消息请求
                from shared.message_queue import MessageRequestStatus
                message_request = self.message_queue.get_next_message_request()

                if message_request:
                    print(f"💬 处理消息请求 #{message_request.id}")
                    # 标记为处理中
                    self.message_queue.update_message_request_status(
                        message_request.id,
                        MessageRequestStatus.PROCESSING
                    )

                    try:
                        import json

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
                            target_channel = target_channel
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
                        else:
                            # 发送纯文本
                            sent_msg = await target_channel.send(content=message_request.content)

                        # 标记为完成
                        result = json.dumps({
                            "success": True,
                            "message": f"成功发送消息到 {target_info}",
                            "message_id": str(sent_msg.id)
                        }, ensure_ascii=False)
                        self.message_queue.update_message_request_status(
                            message_request.id,
                            MessageRequestStatus.COMPLETED,
                            result=result
                        )
                        print(f"✅ 消息请求 #{message_request.id} 处理完成")

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
                        print(f"❌ 消息请求 #{message_request.id} 处理失败: {e}")

                # 等待一段时间再检查
                await asyncio.sleep(self.config.poll_interval / 1000)

            except Exception as e:
                print(f"❌ 检查消息请求时出错: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(5)

    async def on_close(self):
        """Bot 关闭时的清理"""
        if self.response_check_task:
            self.response_check_task.cancel()
        if self.file_request_check_task:
            self.file_request_check_task.cancel()
        if self.file_download_check_task:
            self.file_download_check_task.cancel()
        if self.message_request_check_task:
            self.message_request_check_task.cancel()

    async def _maintain_typing_indicator(self, channel):
        """
        维持 typing indicator（仅用于直接回复模式）

        Args:
            channel: Discord 频道对象
        """
        try:
            while not self.is_closed():
                async with channel.typing():
                    # Discord typing indicator 默认持续 10 秒
                    # 每 9 秒刷新一次，避免中断
                    for _ in range(9):
                        await asyncio.sleep(1)
                        # 每秒检查一次是否应该停止（通过检查 pending_messages）
                        # 这里我们简单地持续运行，停止信号由外部 cancel 控制
        except asyncio.CancelledError:
            # 任务被取消，正常退出
            pass
        except Exception as e:
            print(f"⚠️ 维持 typing indicator 时出错: {e}")

    def _detect_new_blocks(self, previous_content: str, new_content: str) -> list:
        """
        检测新增的 block（内容块）

        Args:
            previous_content: 上次的内容
            new_content: 新的完整内容

        Returns:
            list: 新增的 block 列表
        """
        if not new_content:
            return []

        # 如果是首次内容，整个作为一个 block
        if not previous_content:
            return [new_content]

        # 计算新增的文本部分
        new_text = new_content[len(previous_content):]

        if not new_text:
            return []

        # 检测新增文本中的 block 边界
        blocks = []
        current_block = []
        in_code_block = False
        empty_line_count = 0

        lines = new_text.split('\n')
        for line in lines:
            # 检测代码块开始/结束
            if line.strip().startswith('```'):
                if not in_code_block:
                    # 代码块开始
                    if current_block:
                        blocks.append('\n'.join(current_block))
                        current_block = []
                    in_code_block = True
                    current_block.append(line)
                else:
                    # 代码块结束
                    current_block.append(line)
                    blocks.append('\n'.join(current_block))
                    current_block = []
                    in_code_block = False
                    empty_line_count = 0
                continue

            # 如果在代码块内，所有内容都视为同一 block
            if in_code_block:
                current_block.append(line)
                continue

            # 检测段落分隔（两个或以上连续换行）
            if not line.strip():
                empty_line_count += 1
                if empty_line_count >= 2 and current_block:
                    # 空行，结束当前 block
                    blocks.append('\n'.join(current_block))
                    current_block = []
                    empty_line_count = 0
            else:
                empty_line_count = 0
                current_block.append(line)

        # 处理最后一个 block
        if current_block:
            blocks.append('\n'.join(current_block))

        return blocks

    def _merge_short_blocks(self, blocks: list) -> list:
        """
        合并短 block（减少消息数量）

        Args:
            blocks: 要合并的 block 列表

        Returns:
            list: 合并后的 block 列表
        """
        if not blocks:
            return []

        max_length = self.config.direct_reply_short_block_max_length
        merged = []
        current_merged = []
        current_length = 0

        for block in blocks:
            # 如果 block 包含代码块，不合并
            if '```' in block:
                # 先添加当前已合并的内容
                if current_merged:
                    merged.append('\n\n'.join(current_merged))
                    current_merged = []
                    current_length = 0
                # 添加代码块
                merged.append(block)
            elif len(block) < max_length and current_length + len(block) < max_length * 2:
                # 短 block，合并
                current_merged.append(block)
                current_length += len(block)
            else:
                # 长 block 或合并后过长
                if current_merged:
                    merged.append('\n\n'.join(current_merged))
                    current_merged = []
                    current_length = 0
                merged.append(block)

        # 处理剩余内容
        if current_merged:
            merged.append('\n'.join(current_merged))

        return merged


def main():
    """主函数"""
    try:
        # 加载配置
        config = Config()

        # 创建并启动 Bot
        bot = DiscordBot(config)
        bot.run(config.discord_token)

    except FileNotFoundError as e:
        print(f"❌ 配置错误: {e}")
        sys.exit(1)
    except ValueError as e:
        print(f"❌ 配置错误: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 启动失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
