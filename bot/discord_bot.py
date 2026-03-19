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
import json
from pathlib import Path
from typing import List

# 添加 shared 目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.config import Config
from shared.message_queue import MessageQueue, Message, MessageDirection, MessageStatus, MessageTag, AttachmentInfo
from bot.cron_scheduler import BotCronScheduler


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

        # ⏰ 定时任务调度器
        self.cron_scheduler = None
        self.cron_scan_task = None

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

        # 🔥 启动工具调用通知检查任务
        self.tool_use_check_task = asyncio.create_task(self.check_tool_uses())

        # ⏰ 启动定时任务调度器
        try:
            tasks_file = Path(__file__).parent.parent / "shared" / "cron_jobs.json"
            self.cron_scheduler = BotCronScheduler(str(tasks_file))
            await self.cron_scheduler.start()

            # 启动任务文件扫描任务
            self.cron_scan_task = asyncio.create_task(self.cron_scheduler.scan_loop())
        except Exception as e:
            print(f"⚠️  定时任务调度器启动失败: {e}")
            self.cron_scheduler = None

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

        # 创建启动成功消息
        embed = discord.Embed(
            title="🚀 Discord Claude Bridge 启动成功",
            description="桥接系统已就绪，可以开始使用！",
            color=discord.Color.green()
        )

        # 根据会话模式显示不同的信息
        if self.config.session_mode == "global":
            # global 模式：显示全局会话信息
            session_key, session_id, session_created, _ = self.message_queue.get_or_create_session(
                self.config.working_directory,
                session_mode="global"
            )
            session_info = f"**Session ID**: `{session_id[:8]}...`" if session_id else "`未生成`"
            session_info += f"\n**状态**: {'已创建 ✅' if session_created else '未创建 ⏳'}"
            embed.add_field(
                name="📋 会话模式: Global（全局共享）",
                value=f"所有频道和私聊共享一个全局会话\n{session_info}",
                inline=False
            )
        else:
            # session 模式：说明独立会话架构
            embed.add_field(
                name="📋 会话模式: Session（独立会话）",
                value="每个频道和每个用户的私聊都使用独立的 Session ID\n在具体频道或私聊中使用 `/status` 查看该会话信息",
                inline=False
            )

        embed.add_field(name="📂 工作目录", value=f"`{self.config.working_directory}`", inline=False)
        embed.add_field(name="🔧 可用命令", value="`/new` - 新会话\n`/status` - 查看状态\n`/abort` - 中止输出\n`/restart` - 重启服务\n`/stop` - 停止服务\n`下载附件` - 右键消息下载附件", inline=False)

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
                temp_session_key=None,
                session_mode=self.config.session_mode
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
                temp_session_key=None,
                session_mode=self.config.session_mode
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
                print(f"[会话重置] 用户 {interaction.user.display_name} 重置了 {session_type}")
                print(f"[会话重置] Session Key: {session_key}")
                print(f"[会话重置] 旧 Session ID: {old_session_id} -> 新 Session ID: {new_session_id}")
                print(f"[会话重置] 已删除 Claude Code 会话文件: {working_dir}")
            else:
                embed = discord.Embed(
                    title="⚠️ 没有活跃会话",
                    description=f"{interaction.user.mention}，当前没有找到活跃的会话。",
                    color=discord.Color.orange()
                )
                embed.add_field(name="当前 Session ID", value=f"`{new_session_id[:8]}...`", inline=False)
                await interaction.response.send_message(embed=embed)

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
                temp_session_key=None,
                session_mode=self.config.session_mode
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
                            embed = discord.Embed(
                                title="❌ 文件未找到",
                                description="找不到 manager.py 文件",
                                color=discord.Color.red()
                            )
                            await interaction.followup.send(embed=embed)
                            print(f"⚠️  manager.py 不存在: {manager_script}")

                    except Exception as e:
                        embed = discord.Embed(
                            title="❌ 停止失败",
                            description=f"错误: {str(e)}",
                            color=discord.Color.red()
                        )
                        await interaction.followup.send(embed=embed)
                        print(f"❌ 执行停止命令时出错: {e}")
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
            embed = discord.Embed(
                title="🔄 正在重启服务",
                description=f"{interaction.user.mention}，正在重启 Discord Bridge 服务...",
                color=discord.Color.blue()
            )
            embed.add_field(name="说明", value="请稍候，服务将在几秒钟后重新启动。", inline=False)
            await interaction.response.send_message(embed=embed)
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
                    embed = discord.Embed(
                        title="❌ 文件未找到",
                        description="找不到 manager.py 文件",
                        color=discord.Color.red()
                    )
                    await interaction.followup.send(embed=embed)
                    print(f"⚠️  manager.py 不存在: {manager_script}")

            except Exception as e:
                embed = discord.Embed(
                    title="❌ 重启失败",
                    description=f"错误: {str(e)}",
                    color=discord.Color.red()
                )
                await interaction.followup.send(embed=embed)
                print(f"❌ 执行重启命令时出错: {e}")
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

            # 查找正在处理的消息
            processing_messages = self.message_queue.get_processing_messages()

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
                print(f"[中止命令] 用户 {interaction.user.display_name} 请求中止消息 #{message_to_abort.id}")
            else:
                embed = discord.Embed(
                    title="❌ 中止请求失败",
                    description="中止请求失败，请稍后重试。",
                    color=discord.Color.red()
                )
                await interaction.response.send_message(embed=embed)

        @self.tree.context_menu(name="下载附件")
        async def download_context_menu(interaction: discord.Interaction, message: discord.Message):
            """右键消息下载附件（上下文菜单）"""
            import aiohttp
            from pathlib import Path

            print(f"[下载命令] 用户 {interaction.user.display_name} 右键点击消息 {message.id}")

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
                        # 处理文件名冲突
                        local_path = save_dir / attachment.filename
                        counter = 1
                        original_stem = Path(attachment.filename).stem
                        original_suffix = Path(attachment.filename).suffix

                        # 检查文件是否存在，如存在则添加后缀
                        while local_path.exists():
                            local_path = save_dir / f"{original_stem}_{counter}{original_suffix}"
                            counter += 1

                        # 下载文件
                        async with session.get(attachment.url) as resp:
                            if resp.status == 200:
                                file_content = await resp.read()
                                with open(local_path, 'wb') as f:
                                    f.write(file_content)

                                downloaded_files.append({
                                    "filename": attachment.filename,
                                    "local_path": str(local_path),
                                    "size": len(file_content)
                                })
                                print(f"[下载命令] ✓ 已下载: {attachment.filename} -> {local_path}")
                            else:
                                raise ValueError(f"HTTP {resp.status}")

                    except Exception as e:
                        failed_files.append({
                            "filename": attachment.filename,
                            "error": str(e)
                        })
                        print(f"[下载命令] ✗ 下载失败: {attachment.filename} - {e}")

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

            print(f"[下载命令] 用户 {interaction.user.display_name} 下载了 {len(downloaded_files)}/{len(message.attachments)} 个文件")


    async def on_ready(self):
        """Bot 准备就绪"""
        print(f"✓ Bot 已准备就绪!")
        print(f"✓ 在 {len(self.guilds)} 个服务器中")
        print(f"✓ 斜杠命令: /new, /status, /stop, /restart, /abort")
        print(f"✓ 上下文菜单: 下载附件")

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
                print(f"[附件检测] 用户 {message.author.display_name} 发送了 {len(message.attachments)} 个附件")

                # 使用配置的默认下载目录
                save_dir = Path(self.config.default_download_directory)
                save_dir.mkdir(parents=True, exist_ok=True)

                downloaded_files = []

                # 下载所有附件
                async with aiohttp.ClientSession() as session:
                    for attachment in message.attachments:
                        try:
                            # 处理文件名冲突
                            local_path = save_dir / attachment.filename
                            counter = 1
                            original_stem = Path(attachment.filename).stem
                            original_suffix = Path(attachment.filename).suffix

                            # 检查文件是否存在，如存在则添加后缀
                            while local_path.exists():
                                local_path = save_dir / f"{original_stem}_{counter}{original_suffix}"
                                counter += 1

                            # 下载文件
                            async with session.get(attachment.url) as resp:
                                if resp.status == 200:
                                    file_content = await resp.read()
                                    with open(local_path, 'wb') as f:
                                        f.write(file_content)

                                    downloaded_files.append({
                                        "filename": attachment.filename,
                                        "local_path": str(local_path),
                                        "size": len(file_content)
                                    })
                                    print(f"[附件下载] ✓ 已下载: {attachment.filename} -> {local_path}")
                                else:
                                    raise ValueError(f"HTTP {resp.status}")

                        except Exception as e:
                            print(f"[附件下载] ✗ 下载失败: {attachment.filename} - {e}")

                # 构建附件信息对象列表
                if downloaded_files:
                    attachment_infos = []
                    for f in downloaded_files:
                        attachment_infos.append(AttachmentInfo(
                            filename=f['filename'],
                            size=f['size'],
                            url=f"file://{f['local_path']}",  # 使用本地文件路径
                            description=None
                        ))
                    print(f"[附件处理] 成功处理 {len(attachment_infos)} 个附件")

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
                temp_session_key=None,
                session_mode=self.config.session_mode
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
                attachments=attachment_infos  # 传入附件信息
            )

            # 添加到消息队列（状态为 PENDING，等待 Claude Bridge 接收）
            message_id = self.message_queue.add_message(msg)

            # 打印日志，包含附件信息
            attach_info = f" (+{len(attachment_infos)}个附件)" if attachment_infos else ""
            print(f"[消息 #{message_id}] 收到来自 {message.author.display_name} 的消息: {content[:50] if content else '(仅附件)'}...{attach_info} ({'私聊' if is_dm else '频道'})")

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

                # 发送确认消息（使用 Embed 卡片）
                embed = discord.Embed(
                    title="✅ 消息已接收",
                    description=f"正在等待 Claude Bridge 接收...\n\n消息 ID: {message_id}",
                    color=discord.Color.blue()
                )
                embed.set_footer(text=f"消息 ID: {message_id}")
                confirmation_msg = await message.reply(embed=embed)

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
                self.config.working_directory,
                channel_id=message.channel.id if not is_dm else None,
                user_id=message.author.id if is_dm else None,
                is_dm=is_dm,
                use_temp_session=False,
                temp_session_key=None,
                session_mode=self.config.session_mode
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
                    # Embed 模式：发送确认消息（使用 Embed 卡片）
                    embed = discord.Embed(
                        title="✅ 消息已接收",
                        description=f"检测到 {len(original_message.attachments)} 个附件\n\n消息 ID: {message_id}",
                        color=discord.Color.blue()
                    )
                    embed.set_footer(text=f"消息 ID: {message_id}")
                    confirmation_msg = await message.reply(embed=embed)

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
                            embed = discord.Embed(
                                title="✅ 文件下载完成",
                                description=f"请求 #{request_id}\n\n"
                                            f"保存目录: `{save_dir}`\n"
                                            f"已下载 {len(downloaded_files)} 个文件:\n"
                                            f"{files_info}",
                                color=discord.Color.green()
                            )
                            embed.set_footer(text=f"请求 ID: {request_id}")
                            await confirmation_msg.edit(content="", embed=embed)
                        else:
                            embed = discord.Embed(
                                title="⚠️ 下载完成但无文件",
                                description=f"请求 #{request_id}\n\n文件下载完成，但没有找到文件。",
                                color=discord.Color.orange()
                            )
                            embed.set_footer(text=f"请求 ID: {request_id}")
                            await confirmation_msg.edit(content="", embed=embed)
                        return

                    elif status == FileDownloadRequestStatus.FAILED.value:
                        # 下载失败
                        print(f"[文件下载 #{request_id}] 下载失败: {error}")
                        error_msg = error or "未知错误"
                        embed = discord.Embed(
                            title="❌ 文件下载失败",
                            description=f"请求 #{request_id}\n\n错误: {error_msg}",
                            color=discord.Color.red()
                        )
                        embed.set_footer(text=f"请求 ID: {request_id}")
                        await confirmation_msg.edit(content="", embed=embed)
                        return

                    elif status == FileDownloadRequestStatus.PROCESSING.value:
                        # 正在处理中
                        print(f"[文件下载 #{request_id}] 正在处理中... ({elapsed}s)")

                        # 每 30 秒更新一次进度提示
                        if elapsed - last_progress_update >= 30:
                            embed = discord.Embed(
                                title="⏳ 正在下载文件",
                                description=f"请求 #{request_id}\n\n进度: {elapsed}/{max_wait_time} 秒",
                                color=discord.Color.blue()
                            )
                            embed.set_footer(text=f"请求 ID: {request_id}")
                            await confirmation_msg.edit(content="", embed=embed)
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
                    embed = discord.Embed(
                        title="✅ 文件下载完成",
                        description=f"请求 #{request_id}\n\n"
                                    f"保存目录: `{db_result[2]}`\n"
                                    f"已下载 {len(downloaded_files)} 个文件:\n"
                                    f"{files_info}",
                        color=discord.Color.green()
                    )
                    embed.set_footer(text=f"请求 ID: {request_id}")
                    await confirmation_msg.edit(content="", embed=embed)
                else:
                    embed = discord.Embed(
                        title="⚠️ 下载完成但无文件",
                        description=f"请求 #{request_id}\n\n文件下载完成，但没有找到文件。",
                        color=discord.Color.orange()
                    )
                    embed.set_footer(text=f"请求 ID: {request_id}")
                    await confirmation_msg.edit(content="", embed=embed)
            else:
                # 真的超时了
                print(f"[文件下载 #{request_id}] 真的超时")
                embed = discord.Embed(
                    title="⏱️ 下载请求超时",
                    description=f"请求 #{request_id}\n\n文件下载请求超时（{max_wait_time}秒）\n可能原因：Bot 未运行或消息不存在。",
                    color=discord.Color.orange()
                )
                embed.set_footer(text=f"请求 ID: {request_id}")
                await confirmation_msg.edit(content="", embed=embed)

        except Exception as e:
            print(f"❌ 监控下载进度时出错: {e}")
            import traceback
            traceback.print_exc()

    def _check_session_busy(self, current_message_id: int, tracking_info: dict) -> bool:
        """
        检查同一 session 的 Worker 是否正在处理其他消息

        Args:
            current_message_id: 当前消息 ID
            tracking_info: 消息追踪信息

        Returns:
            True: Worker 忙碌（有其他消息正在处理）
            False: Worker 空闲（没有其他消息正在处理）
        """
        import sqlite3

        try:
            # 获取当前消息的 session 信息
            # 从 tracking_info 中获取 channel 信息
            channel = tracking_info.get("channel")
            if not channel:
                return False

            # 判断是否为私聊
            is_dm = isinstance(channel, discord.DMChannel)

            conn = sqlite3.connect(self.message_queue.db_path)
            cursor = conn.cursor()

            if is_dm:
                # 私聊：检查同一用户的其他消息是否正在处理
                # 获取 user_id（从数据库查询）
                cursor.execute("""
                    SELECT discord_user_id FROM messages WHERE id = ?
                """, (current_message_id,))
                row = cursor.fetchone()
                if not row:
                    conn.close()
                    return False

                user_id = row[0]

                # 查询同一用户的私聊消息是否有正在处理的
                cursor.execute("""
                    SELECT COUNT(*) FROM messages
                    WHERE id != ? AND discord_user_id = ? AND is_dm = 1
                    AND status IN ('processing', 'ai_started')
                """, (current_message_id, user_id))
            else:
                # 频道：检查同一频道的其他消息是否正在处理
                channel_id = channel.id

                # 查询同一频道的消息是否有正在处理的
                cursor.execute("""
                    SELECT COUNT(*) FROM messages
                    WHERE id != ? AND discord_channel_id = ? AND is_dm = 0
                    AND status IN ('processing', 'ai_started')
                """, (current_message_id, channel_id))

            count = cursor.fetchone()[0]
            conn.close()

            # 如果有其他消息正在处理，说明 Worker 忙碌
            return count > 0

        except Exception as e:
            print(f"⚠️ 检查 session 状态时出错: {e}")
            return False

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
                                # Embed 模式：发送确认消息（使用 Embed 卡片）
                                embed = discord.Embed(
                                    title="✅ 消息已接收",
                                    description=f"正在等待 Claude Bridge 接收...\n\n消息 ID: {msg_id}",
                                    color=discord.Color.blue()
                                )
                                embed.set_footer(text=f"消息 ID: {msg_id}")
                                confirmation_msg = await channel.send(embed=embed)

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
                            elapsed_time > self.config.pending_timeout):
                            # 超过配置的秒数仍未被接收
                            try:
                                if not is_direct_reply:
                                    # Embed 模式：编辑确认消息为 Embed 卡片
                                    embed = discord.Embed(
                                        title="⏱️ 消息等待超时",
                                        description=f"消息 #{msg_id} 等待时间过长（{int(elapsed_time)}秒）\n\n"
                                                    f"Claude Bridge 可能未运行，或当前有尚未响应完成的消息。\n"
                                                    f"建议：检查服务状态，或等待当前消息响应完成。",
                                        color=discord.Color.orange()
                                    )
                                    embed.set_footer(text=f"消息 ID: {msg_id}")
                                    await tracking_info["confirmation_msg"].edit(content="", embed=embed)
                                else:
                                    # 直接回复模式：发送超时消息（使用 Embed 卡片）
                                    embed = discord.Embed(
                                        title="⏱️ 消息等待超时",
                                        description=f"消息 #{msg_id} 等待时间过长（{int(elapsed_time)}秒）\n\n"
                                                    f"Claude Bridge 可能未运行，或当前有尚未响应完成的消息。\n"
                                                    f"建议：检查服务状态，或等待当前消息响应完成。",
                                        color=discord.Color.orange()
                                    )
                                    embed.set_footer(text=f"消息 ID: {msg_id}")
                                    await tracking_info["channel"].send(embed=embed)
                                    # 停止 typing indicator
                                    tracking_info["typing_active"] = False
                                    typing_task = tracking_info.get("typing_task")
                                    if typing_task and not typing_task.done():
                                        typing_task.cancel()
                                tracking_info["notified_pending_timeout"] = True
                            except Exception as e:
                                print(f"⚠️ 无法发送超时消息: {e}")
                            print(f"⚠️ [消息 #{msg_id}] PENDING 超时（{int(elapsed_time)}秒）")

                    # 状态 1.5: QUEUED - 消息已加入处理队列
                    elif status == MessageStatus.QUEUED.value:
                        is_direct_reply = tracking_info.get("direct_reply", False)
                        if not tracking_info.get("notified_queued"):
                            # 检查是否有同一 session 的其他消息正在处理（避免误报）
                            session_busy = self._check_session_busy(msg_id, tracking_info)

                            if session_busy:
                                # Worker 真的忙碌，记录队列状态，但暂不发送提示
                                # 等待后续确认真的排队了（>2秒）再发送
                                tracking_info["queue_start_time"] = current_time
                                print(f"📋 [消息 #{msg_id}] Worker 忙碌，消息已入队，等待确认是否真的排队...")
                            else:
                                # Worker 空闲，不发送提示（避免误报）
                                print(f"✓ [消息 #{msg_id}] Worker 空闲，跳过排队提示")

                            tracking_info["notified_queued"] = True

                    # 状态 2: PROCESSING 且无 response - Claude Bridge已接收，正在调用CLI
                    elif status == MessageStatus.PROCESSING.value and not response:
                        is_direct_reply = tracking_info.get("direct_reply", False)
                        if not tracking_info.get("notified_bridge_received"):
                            # Claude Bridge成功接收消息
                            try:
                                # 🔥 如果之前记录了队列状态，检查是否真的排队了
                                if tracking_info.get("notified_queued") and tracking_info.get("queue_start_time"):
                                    # 计算从加入队列到开始处理的时间
                                    time_since_queued = current_time - tracking_info["queue_start_time"]

                                    # 如果大于2秒，说明真的排队了，发送"排队后已被处理"的消息
                                    if time_since_queued >= 2.0:
                                        try:
                                            if not is_direct_reply:
                                                # Embed 模式：编辑确认消息为 Embed 卡片
                                                embed = discord.Embed(
                                                    title="📋 消息已加入队列",
                                                    description=f"消息 #{msg_id} 已加入处理队列，正在等待处理...",
                                                    color=discord.Color.blue()
                                                )
                                                embed.set_footer(text=f"消息 ID: {msg_id}")
                                                await tracking_info["confirmation_msg"].edit(content="", embed=embed)
                                                print(f"📋 [消息 #{msg_id}] 消息真的排队了 {time_since_queued:.1f} 秒，已显示排队提示 (Embed模式)")
                                            else:
                                                # 直接回复模式：发送 Embed 卡片
                                                embed = discord.Embed(
                                                    title="📋 消息已加入队列",
                                                    description=f"消息 #{msg_id} 已加入处理队列，正在等待处理...",
                                                    color=discord.Color.blue()
                                                )
                                                embed.set_footer(text=f"消息 ID: {msg_id}")
                                                queue_msg = await tracking_info["channel"].send(embed=embed)
                                                print(f"📋 [消息 #{msg_id}] 消息真的排队了 {time_since_queued:.1f} 秒，已显示排队提示 (直接回复模式)")
                                        except Exception as e:
                                            print(f"⚠️ 无法发送队列消息: {e}")
                                    else:
                                        # 小于2秒，说明是误报，不发送队列提示
                                        print(f"✓ [消息 #{msg_id}] 消息只等待了 {time_since_queued:.1f} 秒，跳过排队提示 ({'直接回复模式' if is_direct_reply else 'Embed模式'})")

                                if not is_direct_reply:
                                    # Embed 模式：编辑确认消息为 Embed 卡片
                                    embed = discord.Embed(
                                        title="⏳ 消息处理中",
                                        description=f"消息 #{msg_id}\n\nClaude Bridge 已接收消息，正在调用 Claude Code CLI...",
                                        color=discord.Color.blue()
                                    )
                                    embed.set_footer(text=f"消息 ID: {msg_id}")
                                    await tracking_info["confirmation_msg"].edit(content="", embed=embed)
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

                            # 🔥 如果之前记录了队列状态（但还没显示提示），检查是否真的排队了
                            if tracking_info.get("notified_queued") and tracking_info.get("queue_start_time") and not tracking_info.get("queue_notification_sent"):
                                # 计算从加入队列到开始处理的时间
                                time_since_queued = current_time - tracking_info["queue_start_time"]

                                # 如果大于2秒，说明真的排队了，发送"排队后已被处理"的消息
                                if time_since_queued >= 2.0:
                                    try:
                                        if not is_direct_reply:
                                            # Embed 模式：编辑确认消息为 Embed 卡片
                                            embed = discord.Embed(
                                                title="📋 消息已加入队列",
                                                description=f"消息 #{msg_id} 已加入处理队列，正在等待处理...",
                                                color=discord.Color.blue()
                                            )
                                            embed.set_footer(text=f"消息 ID: {msg_id}")
                                            await tracking_info["confirmation_msg"].edit(content="", embed=embed)
                                            print(f"📋 [消息 #{msg_id}] 消息真的排队了 {time_since_queued:.1f} 秒，已显示排队提示 (Embed模式)")
                                        else:
                                            # 直接回复模式：发送 Embed 卡片
                                            embed = discord.Embed(
                                                title="📋 消息已加入队列",
                                                description=f"消息 #{msg_id} 已加入处理队列，正在等待处理...",
                                                color=discord.Color.blue()
                                            )
                                            embed.set_footer(text=f"消息 ID: {msg_id}")
                                            queue_msg = await tracking_info["channel"].send(embed=embed)
                                            print(f"📋 [消息 #{msg_id}] 消息真的排队了 {time_since_queued:.1f} 秒，已显示排队提示 (直接回复模式)")
                                        # 标记已发送队列提示
                                        tracking_info["queue_notification_sent"] = True
                                    except Exception as e:
                                        print(f"⚠️ 无法发送队列消息: {e}")
                                else:
                                    # 小于2秒，说明是误报，不发送队列提示
                                    print(f"✓ [消息 #{msg_id}] 消息只等待了 {time_since_queued:.1f} 秒，跳过排队提示 ({'直接回复模式' if is_direct_reply else 'Embed模式'})")

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

                                    # 编辑旧的确认消息为 Embed 卡片
                                    embed_update = discord.Embed(
                                        title="🤖 AI 正在处理",
                                        description=f"消息 #{msg_id} 已接收，AI 正在工作，请稍候……",
                                        color=discord.Color.gold()
                                    )
                                    embed_update.set_footer(text=f"消息 ID: {msg_id}")
                                    await tracking_info["confirmation_msg"].edit(content="", embed=embed_update)

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

                                # 发送响应成功提示（使用 Embed 卡片）
                                try:
                                    embed = discord.Embed(
                                        title="✅ 响应成功",
                                        description=f"消息 #{msg_id} 已成功处理并回复！",
                                        color=discord.Color.green()
                                    )
                                    embed.set_footer(text=f"消息 ID: {msg_id}")
                                    await tracking_info["confirmation_msg"].edit(content="", embed=embed)
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
                            # Embed 模式：编辑确认消息为 Embed 卡片
                            try:
                                error_msg = error or "未知错误"
                                embed = discord.Embed(
                                    title="❌ 消息处理失败",
                                    description=f"消息 #{msg_id}\n\n错误: {error_msg}",
                                    color=discord.Color.red()
                                )
                                embed.set_footer(text=f"消息 ID: {msg_id}")
                                await tracking_info["confirmation_msg"].edit(content="", embed=embed)
                            except Exception as e:
                                print(f"⚠️ 无法编辑确认消息: {e}")
                        else:
                            # 直接回复模式：发送错误消息（使用 Embed 卡片）
                            try:
                                error_msg = error or "未知错误"
                                embed = discord.Embed(
                                    title="❌ 消息处理失败",
                                    description=f"错误: {error_msg}",
                                    color=discord.Color.red()
                                )
                                embed.set_footer(text=f"消息 ID: {msg_id}")
                                await tracking_info["channel"].send(embed=embed)

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
        if hasattr(self, 'tool_use_check_task') and self.tool_use_check_task:
            self.tool_use_check_task.cancel()

        # ⏰ 停止定时任务调度器
        if self.cron_scheduler:
            await self.cron_scheduler.stop()
        if self.cron_scan_task:
            self.cron_scan_task.cancel()

    async def _maintain_typing_indicator(self, channel):
        """
        维持 typing indicator（仅用于直接回复模式）
        带重试机制，网络波动时会自动恢复

        Args:
            channel: Discord 频道对象
        """
        retry_count = 0
        max_retries = self.config.typing_indicator_max_retries  # 最大连续重试次数
        retry_delay = self.config.typing_indicator_retry_delay  # 重试等待时间（秒）

        try:
            while not self.is_closed():
                try:
                    async with channel.typing():
                        # Discord typing indicator 默认持续 10 秒
                        # 每 9 秒刷新一次，避免中断
                        for _ in range(9):
                            await asyncio.sleep(1)

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
                    await asyncio.sleep(retry_delay)

        except asyncio.CancelledError:
            # 任务被取消，正常退出
            pass
        except Exception as e:
            print(f"❌ 维持 typing indicator 时发生未预期错误: {e}")

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

        # 如果是首次内容，检测内部是否有空行分隔
        if not previous_content:
            # 检测首次内容中的 block 边界（与增量检测相同的逻辑）
            blocks = []
            current_block = []
            in_code_block = False
            empty_line_count = 0

            lines = new_content.split('\n')
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

                # 检测段落分隔（一个空行，即两个连续换行）
                if not line.strip():
                    empty_line_count += 1
                    if empty_line_count >= 1 and current_block:
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

            return blocks if blocks else [new_content]

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

            # 检测段落分隔（一个空行，即两个连续换行）
            if not line.strip():
                empty_line_count += 1
                if empty_line_count >= 1 and current_block:
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

    async def check_tool_uses(self):
        """定期检查工具调用并发送通知"""
        await self.wait_until_ready()

        # 追踪已处理的工具调用 {message_id: [tool_use_indices]}
        processed_tool_uses = {}

        while not self.is_closed():
            try:
                if not self.config.tool_use_notification_enabled:
                    await asyncio.sleep(5)
                    continue

                import sqlite3
                conn = sqlite3.connect(self.config.database_path)
                cursor = conn.cursor()

                # 查询有 tool_uses 且状态为 ai_started 或 processing 的消息
                cursor.execute("""
                    SELECT id, discord_channel_id, tool_uses
                    FROM messages
                    WHERE status IN ('ai_started', 'processing')
                      AND tool_uses IS NOT NULL
                      AND tool_uses != ''
                    ORDER BY id DESC
                """)
                rows = cursor.fetchall()
                conn.close()

                for msg_id, channel_id, tool_uses_json in rows:
                    try:
                        tool_uses = json.loads(tool_uses_json)

                        # 检查是否有新的工具调用
                        if msg_id not in processed_tool_uses:
                            processed_tool_uses[msg_id] = []

                        last_processed_count = len(processed_tool_uses[msg_id])

                        # 如果有新的工具调用
                        if len(tool_uses) > last_processed_count:
                            # 发送新的工具调用通知
                            for i in range(last_processed_count, len(tool_uses)):
                                tool_use = tool_uses[i]
                                await self._send_tool_use_notification(
                                    tool_use['name'],
                                    tool_use['input'],
                                    channel_id
                                )

                            # 更新已处理的数量
                            processed_tool_uses[msg_id] = list(range(len(tool_uses)))

                    except (json.JSONDecodeError, KeyError):
                        pass

                # 清理已完成消息的记录
                conn = sqlite3.connect(self.config.database_path)
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id FROM messages
                    WHERE status IN ('completed', 'failed', 'skipped')
                """)
                completed_ids = [row[0] for row in cursor.fetchall()]
                conn.close()

                for completed_id in completed_ids:
                    if completed_id in processed_tool_uses:
                        del processed_tool_uses[completed_id]

                # 等待一段时间再检查（1秒）
                await asyncio.sleep(1)

            except Exception as e:
                print(f"❌ 检查工具调用时出错: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(5)

    async def _send_tool_use_notification(self, tool_name: str, tool_input: dict, channel_id: int):
        """发送工具调用通知

        Args:
            tool_name: 工具名称
            tool_input: 工具参数
            channel_id: Discord 频道 ID
        """
        # 过滤管理命令的工具调用通知（避免噪音）
        if tool_name == "Bash" and tool_input.get("command"):
            command = tool_input["command"]
            # 检查是否是管理相关的命令
            management_keywords = ["restart.bat", "manager.py", "restart", "shutdown", "stop"]
            if any(keyword in command.lower() for keyword in management_keywords):
                # 这是管理命令，不发送通知
                return

        # 工具 emoji 映射
        TOOL_EMOJIS = {
            # 文件操作
            "Read": "📄",
            "Write": "✍️",
            "Edit": "✏️",
            "Glob": "📁",
            "Grep": "🔍",

            # 命令执行
            "Bash": "⚡",

            # Web 相关
            "WebSearch": "🌐",

            # Skill
            "Skill": "📖",

            # Agent
            "Agent": "🤖",

            # 计划模式
            "EnterPlanMode": "📋",
            "ExitPlanMode": "✅",

            # 用户交互
            "AskUserQuestion": "❓",

            # 任务管理
            "TodoWrite": "✅",
            "CronCreate": "⏰",
            "CronDelete": "🗑️",
            "CronList": "📋",

            # 任务控制
            "TaskOutput": "📤",
            "TaskStop": "🛑",

            # Jupyter
            "NotebookEdit": "📓",

            # MCP 资源
            "ListMcpResourcesTool": "📚",
            "ReadMcpResourceTool": "📖",

            # Worktree
            "EnterWorktree": "🌳",
            "ExitWorktree": "🚪",

            # MCP 服务器 emoji 映射（基于服务器名）
            "jina-mcp-server": "🌐",
            "web-reader": "🌐",
            "brave-search": "🌐",
            "image": "🖼️",
            "discord-bridge": "💬",
            "memu": "🧠",
            "cat-litter-monitor": "🐱",
        }

        # 检查是否是 MCP 工具
        is_mcp = tool_name.startswith('mcp__')

        if is_mcp:
            # MCP 工具：提取服务器名和工具名
            # 格式：mcp__服务器名__工具名
            parts = tool_name.split('__')
            if len(parts) >= 3:
                mcp_server = parts[1]
                mcp_tool = parts[2]  # 获取工具名，如 save_memory

                display_title = f"MCP {mcp_server}"
                emoji = TOOL_EMOJIS.get(mcp_server, "🔧")

                # MCP 工具显示工具名
                embed = discord.Embed(
                    title=f"{emoji} {display_title}",
                    color=discord.Color.green()
                )
                embed.description = mcp_tool
            else:
                # 格式异常，按普通工具处理
                emoji = TOOL_EMOJIS.get(tool_name, "🔧")
                embed = discord.Embed(
                    title=f"{emoji} {tool_name}",
                    color=discord.Color.green()
                )
                embed.description = "无参数"
        else:
            # 非 MCP 工具：正常处理
            emoji = TOOL_EMOJIS.get(tool_name, "🔧")

            # 构建 Embed
            embed = discord.Embed(
                title=f"{emoji} {tool_name}",
                color=discord.Color.green()
            )

            # 智能显示参数（为每个工具定制显示内容）
            display_value = None

            if tool_name == 'Read':
                # Read: 显示文件路径
                display_value = tool_input.get('file_path', '无路径')
            elif tool_name == 'Write':
                # Write: 显示文件路径
                display_value = tool_input.get('file_path', '无路径')
            elif tool_name == 'Edit':
                # Edit: 显示文件路径
                display_value = tool_input.get('file_path', '无路径')
            elif tool_name == 'Glob':
                # Glob: 显示路径和 pattern
                pattern = tool_input.get('pattern', '无 pattern')
                path = tool_input.get('path', '')
                if path:
                    display_value = f"{path}: {pattern}"
                else:
                    display_value = pattern
            elif tool_name == 'Grep':
                # Grep: 显示 pattern
                display_value = tool_input.get('pattern', '无 pattern')
            elif tool_name == 'Bash':
                # Bash: 显示命令（截断）
                cmd = tool_input.get('command', '')
                if len(cmd) > 50:
                    cmd = cmd[:47] + "..."
                display_value = cmd
            elif tool_name == 'WebSearch':
                # WebSearch: 显示 query
                display_value = tool_input.get('query', '无 query')
            elif tool_name == 'Skill':
                # Skill: 显示 skill 名称
                display_value = tool_input.get('skill', '无 skill')
            elif tool_name == 'Agent':
                # Agent: 显示描述和 subagent_type
                desc = tool_input.get('description', '')
                subagent = tool_input.get('subagent_type', 'general-purpose')
                display_value = f"{subagent}: {desc}"
            elif tool_name == 'EnterPlanMode':
                # EnterPlanMode: 无需显示参数
                display_value = "进入计划模式"
            elif tool_name == 'ExitPlanMode':
                # ExitPlanMode: 无需显示参数
                display_value = "退出计划模式"
            elif tool_name == 'AskUserQuestion':
                # AskUserQuestion: 显示每个问题的内容和选项
                questions = tool_input.get('questions', [])
                question_lines = []
                for i, q in enumerate(questions, 1):
                    question_text = q.get('question', '无问题')
                    question_lines.append(f"Q{i}: {question_text}")

                    # 显示选项
                    options = q.get('options', [])
                    if options:
                        for opt in options:
                            label = opt.get('label', '无标签')
                            desc = opt.get('description', '')
                            if desc:
                                question_lines.append(f"  - {label}: {desc}")
                            else:
                                question_lines.append(f"  - {label}")

                display_value = '\n'.join(question_lines)
            elif tool_name == 'TodoWrite':
                # TodoWrite: 显示任务列表和进度
                todos = tool_input.get('todos', [])

                if not todos:
                    display_value = "无任务"
                else:
                    # 统计各状态任务数量
                    status_counts = {'pending': 0, 'in_progress': 0, 'completed': 0}
                    for todo in todos:
                        status = todo.get('status', 'pending')
                        if status in status_counts:
                            status_counts[status] += 1

                    # 构建任务列表
                    todo_lines = []

                    # 显示当前正在进行的任务（如果有）
                    in_progress_tasks = [t for t in todos if t.get('status') == 'in_progress']
                    if in_progress_tasks:
                        todo_lines.append("🔄 正在进行:")
                        for todo in in_progress_tasks:
                            active_form = todo.get('activeForm', todo.get('content', ''))
                            todo_lines.append(f"  • {active_form}")
                        todo_lines.append("")

                    # 显示待办任务
                    pending_tasks = [t for t in todos if t.get('status') == 'pending']
                    if pending_tasks:
                        todo_lines.append("📋 待办:")
                        for todo in pending_tasks[:3]:  # 最多显示3个
                            content = todo.get('content', '')
                            todo_lines.append(f"  • {content}")
                        if len(pending_tasks) > 3:
                            todo_lines.append(f"  ... 还有 {len(pending_tasks) - 3} 个任务")
                        todo_lines.append("")

                    # 显示已完成任务数量
                    if status_counts['completed'] > 0:
                        todo_lines.append(f"✅ 已完成: {status_counts['completed']} 个")

                    # 添加进度统计
                    total = len(todos)
                    completed = status_counts['completed']
                    if completed > 0:
                        todo_lines.append(f"\n进度: {completed}/{total} ({completed*100//total}%)")
                    else:
                        todo_lines.append(f"\n总任务: {total} 个")

                    display_value = '\n'.join(todo_lines)
            elif tool_name == 'CronCreate':
                # CronCreate: 显示 cron 表达式
                display_value = tool_input.get('cron', '无 cron')
            elif tool_name == 'CronDelete':
                # CronDelete: 显示任务 ID
                display_value = tool_input.get('id', '无 id')
            elif tool_name == 'CronList':
                # CronList: 无参数
                display_value = "列出定时任务"
            elif tool_name == 'TaskOutput':
                # TaskOutput: 显示任务 ID
                display_value = tool_input.get('task_id', '无 id')
            elif tool_name == 'TaskStop':
                # TaskStop: 显示任务 ID
                display_value = tool_input.get('task_id', '无 id')
            elif tool_name == 'NotebookEdit':
                # NotebookEdit: 显示 notebook 路径
                display_value = tool_input.get('notebook_path', '无路径')
            elif tool_name == 'ListMcpResourcesTool':
                # ListMcpResourcesTool: 显示服务器名
                display_value = tool_input.get('server', '全部服务器')
            elif tool_name == 'ReadMcpResourceTool':
                # ReadMcpResourceTool: 显示服务器和 URI
                server = tool_input.get('server', '')
                uri = tool_input.get('uri', '')
                display_value = f"{server}: {uri}"
            elif tool_name == 'EnterWorktree':
                # EnterWorktree: 显示名称
                display_value = tool_input.get('name', '默认名称')
            elif tool_name == 'ExitWorktree':
                # ExitWorktree: 显示操作
                action = tool_input.get('action', 'keep')
                display_value = f"操作: {action}"
            elif 'prompt' in tool_input:
                # 有 prompt 字段的工具（完整显示，不截断）
                display_value = tool_input['prompt']
            else:
                # 其他工具：显示第一个参数
                if tool_input:
                    first_key = list(tool_input.keys())[0]
                    first_value = str(tool_input[first_key])
                    if len(first_value) > 50:
                        first_value = first_value[:47] + "..."
                    display_value = f"{first_key}: {first_value}"
                else:
                    display_value = "无参数"

            if display_value:
                embed.description = display_value
            else:
                embed.description = "无参数"

        # 发送到 Discord
        try:
            channel = self.get_channel(channel_id)
            if channel:
                await channel.send(embed=embed)
        except Exception as e:
            pass  # 静默失败，避免刷屏


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
