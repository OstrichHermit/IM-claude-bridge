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

# 添加 shared 目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.config import Config
from shared.message_queue import MessageQueue, Message, MessageDirection, MessageStatus, MessageTag


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

    async def cleanup_stuck_messages(self):
        """清理上次崩溃时卡住的消息（将 processing 状态改为 completed）"""
        import sqlite3
        try:
            conn = sqlite3.connect(self.config.database_path)
            cursor = conn.cursor()

            # 查询卡住的消息数量
            cursor.execute("SELECT COUNT(*) FROM messages WHERE status = 'processing'")
            stuck_count = cursor.fetchone()[0]

            if stuck_count > 0:
                print(f"🧹 发现 {stuck_count} 条卡住的消息，正在清理...")

                # 将 processing 状态的消息标记为 completed（避免重复处理）
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
                print("✓ 没有发现卡住的消息")

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
        embed.add_field(name="🔧 可用命令", value="`/new` - 新会话\n`/status` - 查看状态\n`/restart` - 重启服务\n`/stop` - 停止服务", inline=False)

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

    async def on_ready(self):
        """Bot 准备就绪"""
        print(f"✓ Bot 已准备就绪!")
        print(f"✓ 在 {len(self.guilds)} 个服务器中")
        print(f"✓ 斜杠命令: /new, /status, /stop, /restart")

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

            # 显示"正在输入"状态
            async with message.channel.typing():
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

                # 发送确认消息
                confirmation_msg = await message.reply(
                    f"✅ 消息已接收！正在等待 Claude Bridge 接收...\n"
                    f"消息 ID: {message_id}"
                )

                # 记录到待处理列表（用于追踪接收状态和超时）
                self.pending_messages[message_id] = {
                    "channel": message.channel,
                    "user_message": message,
                    "confirmation_msg": confirmation_msg,
                    "start_time": asyncio.get_event_loop().time(),
                    "content": content[:50],
                    "notified_processing": False  # 是否已发送"正在处理中"通知
                }

        except Exception as e:
            print(f"❌ 处理消息时出错: {e}")
            import traceback
            traceback.print_exc()
            await message.channel.send(f"❌ 处理消息时出错: {str(e)}")

    async def handle_file_download_command(self, message: discord.Message):
        """处理文件下载命令（转发/回复消息）"""
        try:
            from shared.message_queue import FileDownloadRequest, FileDownloadRequestStatus
            import re
            from pathlib import Path

            # 移除 bot 提及，提取实际内容
            content = message.content
            for mention in message.mentions:
                if mention == self.user:
                    content = content.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
                    break

            content = content.strip()

            # 解析保存目录（支持多种格式）
            save_directory = None

            # 格式 1: "下载到 D:/Downloads"
            match = re.search(r'下载到\s+([^\s]+)', content)
            if match:
                save_directory = match.group(1)

            # 格式 2: "save D:/Downloads"
            if not save_directory:
                match = re.search(r'save\s+([^\s]+)', content)
                if match:
                    save_directory = match.group(1)

            # 格式 3: 直接给出路径（最后一个参数）
            if not save_directory:
                parts = content.split()
                if parts:
                    # 尝试最后一个参数作为路径
                    potential_path = parts[-1]
                    # 检查是否像路径（包含 / 或 \ 或 :）
                    if any(c in potential_path for c in ['/', '\\', ':']):
                        save_directory = potential_path

            # 如果没有指定目录，使用配置文件中的默认目录
            if not save_directory:
                save_directory = self.config.default_download_directory
                print(f"[文件下载] 使用配置的默认下载目录: {save_directory}")

            # 验证路径安全性
            save_directory = Path(save_directory).resolve()
            try:
                # 尝试创建目录以验证路径
                save_directory.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                await message.channel.send(
                    f"❌ {message.author.mention}，无效的保存目录: `{save_directory}`\n错误: {e}"
                )
                return

            # 获取原始消息的 ID 和频道 ID
            original_message_id = message.reference.message_id
            original_channel_id = message.reference.channel_id

            print(f"[文件下载命令] 用户 {message.author.display_name} 请求下载消息 {original_message_id}")

            # 创建文件下载请求
            download_request = FileDownloadRequest(
                id=None,
                discord_message_id=original_message_id,
                discord_channel_id=original_channel_id,
                save_directory=str(save_directory),
                status=FileDownloadRequestStatus.PENDING.value
            )

            # 添加到队列
            request_id = self.message_queue.add_file_download_request(download_request)

            print(f"[文件下载 #{request_id}] 已创建下载请求")
            print(f"[文件下载 #{request_id}] 消息 ID: {original_message_id}, 频道 ID: {original_channel_id}")
            print(f"[文件下载 #{request_id}] 保存目录: {save_directory}")

            # 发送确认消息
            confirmation_msg = await message.reply(
                f"✅ 文件下载请求已接收！\n"
                f"请求 ID: {request_id}\n"
                f"正在下载消息中的附件到 `{save_directory}`..."
            )

            # 启动后台任务监控下载状态
            asyncio.create_task(
                self.monitor_download_progress(
                    request_id,
                    message.channel,
                    confirmation_msg
                )
            )

        except Exception as e:
            print(f"❌ 处理文件下载命令时出错: {e}")
            import traceback
            traceback.print_exc()
            await message.channel.send(f"❌ 处理文件下载命令时出错: {str(e)}")

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

                            # 发送确认消息
                            confirmation_msg = await channel.send(
                                f"✅ 消息已接收！正在等待 Claude Bridge 接收...\n"
                                f"消息 ID: {msg_id}"
                            )

                            # 加入 pending_messages 追踪
                            self.pending_messages[msg_id] = {
                                "channel": channel,
                                "user_message": None,
                                "confirmation_msg": confirmation_msg,
                                "start_time": asyncio.get_event_loop().time(),
                                "content": content[:50],
                                "notified_processing": False
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
                        if not tracking_info.get("notified_ai_started") and not tracking_info.get("notified_pending_timeout") and elapsed_time > 30:
                            # 超过 30 秒仍未被接收
                            try:
                                await tracking_info["confirmation_msg"].edit(
                                    content=f"⏱️ 消息 #{msg_id} 等待时间过长（{int(elapsed_time)}秒）\n"
                                            f"Claude Bridge 可能未运行。\n"
                                            f"建议：检查服务状态或重新发送消息。"
                                )
                                tracking_info["notified_pending_timeout"] = True
                            except Exception as e:
                                print(f"⚠️ 无法编辑确认消息: {e}")
                            print(f"⚠️ [消息 #{msg_id}] PENDING 超时（{int(elapsed_time)}秒）")

                    # 状态 2: PROCESSING 且无 response - Claude Bridge已接收，正在调用CLI
                    elif status == MessageStatus.PROCESSING.value and not response:
                        if not tracking_info.get("notified_bridge_received"):
                            # Claude Bridge成功接收消息
                            try:
                                await tracking_info["confirmation_msg"].edit(
                                    content=f"⏳ 消息 #{msg_id} 处理中\n"
                                            f"Claude Bridge 已接收消息，正在调用 Claude Code CLI..."
                                )
                                tracking_info["notified_bridge_received"] = True
                                print(f"📥 [消息 #{msg_id}] Claude Bridge 已接收消息")
                            except Exception as e:
                                print(f"⚠️ 无法编辑确认消息: {e}")

                    # 状态 2.5: AI_STARTED - AI 开始工作！
                    elif status == MessageStatus.AI_STARTED.value:
                        if not tracking_info.get("notified_ai_started"):
                            try:
                                await tracking_info["confirmation_msg"].edit(
                                    content=f"🔄 Claude Code 处理中\n"
                                            f"消息 #{msg_id} 已接收，AI 正在思考，请稍候。"
                                )
                                tracking_info["notified_ai_started"] = True
                                print(f"🤖 [消息 #{msg_id}] AI 开始工作（实时检测）")
                            except Exception as e:
                                print(f"⚠️ 无法编辑确认消息: {e}")

                    # 状态 3: PROCESSING 且有 response - AI 响应完成，发送响应
                    elif status == MessageStatus.PROCESSING.value and response:
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

                                # Discord Embed 字段值长度限制为 1024 字符
                                # 描述长度限制为 4096 字符
                                max_desc_length = 4000
                                max_field_length = 1000

                                # 创建 Embed
                                embed = discord.Embed(
                                    title=f"✨ Claude Code 的回复",
                                    description=f"消息 ID: {msg_id}",
                                    color=discord.Color.green()
                                )

                                # 分割长响应
                                if len(response) <= max_desc_length:
                                    embed.description = f"**消息 ID: {msg_id}**\n\n{response}"
                                    await channel.send(embed=embed)
                                else:
                                    chunks = []
                                    current_chunk = ""
                                    lines = response.split('\n')

                                    for line in lines:
                                        if len(current_chunk) + len(line) + 1 <= max_field_length:
                                            current_chunk += line + '\n'
                                        else:
                                            if current_chunk:
                                                chunks.append(current_chunk)
                                            current_chunk = line + '\n'

                                    if current_chunk:
                                        chunks.append(current_chunk)

                                    if chunks:
                                        embed.description = f"**消息 ID: {msg_id}**\n\n{chunks[0]}"
                                        chunks.pop(0)

                                    for i, chunk in enumerate(chunks[:25], 1):
                                        embed.add_field(
                                            name=f"续 ({i}/{len(chunks)})" if len(chunks) > 1 else "续",
                                            value=chunk,
                                            inline=False
                                        )

                                    await channel.send(embed=embed)

                                    if len(chunks) > 25:
                                        remaining_chunks = chunks[25:]
                                        for extra_idx in range(0, len(remaining_chunks), 25):
                                            extra_embed = discord.Embed(
                                                title=f"✨ Claude Code 的回复 (续)",
                                                color=discord.Color.green()
                                            )
                                            batch = remaining_chunks[extra_idx:extra_idx+25]
                                            for i, chunk in enumerate(batch, 1):
                                                extra_embed.add_field(
                                                    name=f"部分 {extra_idx + i}",
                                                    value=chunk,
                                                    inline=False
                                                )
                                            await channel.send(embed=extra_embed)
                                            print(f"[消息 #{msg_id}] 发送额外 Embed {extra_idx//25 + 1}")

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
                        try:
                            error_msg = error or "未知错误"
                            await tracking_info["confirmation_msg"].edit(
                                content=f"❌ 消息 #{msg_id} 处理失败\n"
                                        f"错误: {error_msg}"
                            )
                        except Exception as e:
                            print(f"⚠️ 无法编辑确认消息: {e}")
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
                        if file_request.use_embed:
                            embed = discord.Embed(
                                title=f"📎 文件发送",
                                description=file_request.message or f"文件: {len(valid_files)} 个",
                                color=discord.Color.green()
                            )
                            sent_msg = await target_channel.send(
                                embed=embed,
                                files=valid_files if len(valid_files) > 1 else valid_files
                            )
                        else:
                            content = file_request.message if file_request.message else f"📎 发送 {len(valid_files)} 个文件"
                            sent_msg = await target_channel.send(
                                content=content,
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
