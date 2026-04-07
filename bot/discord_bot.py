"""
Discord Bot 主程序
接收 Discord 消息并转发给 Claude Code
支持斜杠命令（Slash Commands）
"""
import discord
from discord import app_commands
import asyncio
import sys
import json
from pathlib import Path

# 添加 shared 目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.config import Config
from shared.logger import get_logger
from shared.message_queue import MessageQueue, Message, MessageDirection, MessageStatus, MessageTag, ChannelType, AttachmentInfo
from shared.file_mapping import FileMapping
from bot.cron_scheduler import BotCronScheduler

log = get_logger("DiscordBot", "discord")


class DiscordBot(discord.Client):
    """Discord Bot 类"""

    def __init__(self, config: Config):
        """初始化 Bot"""
        intents = discord.Intents.default()
        intents.message_content = True  # 需要在 Discord Developer Portal 启用
        intents.messages = True

        super().__init__(
            intents=intents
        )

        # 手动创建命令树（discord.Client 需要，commands.Bot 自带）
        self.tree = app_commands.CommandTree(self)

        self.config = config
        self.message_queue = MessageQueue(config.database_path)
        self.file_mapping = FileMapping()  # 文件映射表管理器
        self.response_check_task = None
        self.file_request_check_task = None
        self.file_download_check_task = None
        self.message_request_check_task = None  # 新增：消息发送请求检查任务
        self.pending_messages = {}  # 追踪待处理的消息 {message_id: {"channel": channel, "user_msg": message, "start_time": time}}
        self.stop_requests = {}  # 追踪停止请求 {user_id: {"timestamp": time}}

        # 消息队列管理器
        self.unified_queues = {}  # {channel_id: StreamingMessageQueue}

        # ⏰ 定时任务调度器
        self.cron_scheduler = None
        self.cron_scan_task = None

    def _get_unified_queue(self, channel: discord.abc.Messageable):
        """
        获取或创建频道的消息队列

        Args:
            channel: Discord 频道对象

        Returns:
            StreamingMessageQueue: 消息队列对象
        """
        from bot.streaming_queue import StreamingMessageQueue

        channel_id = channel.id
        if channel_id not in self.unified_queues:
            self.unified_queues[channel_id] = StreamingMessageQueue(
                channel,
                self.config.queue_send_interval
            )
        return self.unified_queues[channel_id]

    async def setup_hook(self):
        """Bot 启动后的钩子"""
        log.log(f"Bot 已启动，登录为 {self.user}")

        # 清理上次崩溃时卡住的消息
        await self.cleanup_stuck_messages()
        
        # 注册斜杠命令
        await self.add_commands()

        # 同步命令到 Discord（全局同步）
        try:
            log.log("🔄 正在同步斜杠命令到 Discord（全局）...")
            synced = await self.tree.sync()
            log.log(f"✅ 已同步 {len(synced)} 个斜杠命令")
            log.log(f"⏱️  注意：全局命令可能需要 1-5 分钟才能生效")

        except Exception as e:
            log.log(f"⚠️ 命令同步失败: {e}")
            log.log(f"📋 请确认：")
            log.log(f"   1. Bot Token 是否正确")
            log.log(f"   2. 是否已在 Discord Developer Portal 启用 'applications.commands' scope")

        # 启动响应检查任务
        self.response_check_task = asyncio.create_task(self.check_responses())

        # 启动文件请求检查任务
        self.file_request_check_task = asyncio.create_task(self.check_file_requests())

        # 启动文件下载检查任务
        self.file_download_check_task = asyncio.create_task(self.check_file_downloads())

        # 启动消息发送请求检查任务
        self.message_request_check_task = asyncio.create_task(self.check_message_requests())

        # 🔥 启动统一消息序列检查任务（替代旧的流式响应和工具调用检查）
        self.sequence_check_task = asyncio.create_task(self.check_message_sequences())

        # 🔥 启动工具执行结果检查任务
        self.tool_result_check_task = asyncio.create_task(self.check_tool_use_results())

        # ⏰ 启动定时任务调度器
        try:
            tasks_file = Path(__file__).parent.parent / "shared" / "cron_jobs.json"
            self.cron_scheduler = BotCronScheduler(str(tasks_file))
            await self.cron_scheduler.start()

            # 启动任务文件扫描任务
            self.cron_scan_task = asyncio.create_task(self.cron_scheduler.scan_loop())
        except Exception as e:
            log.log(f"⚠️  定时任务调度器启动失败: {e}")
            self.cron_scheduler = None

    async def cleanup_stuck_messages(self):
        """清理上次崩溃时卡住的消息"""
        import sqlite3
        try:
            conn = sqlite3.connect(self.config.database_path)
            cursor = conn.cursor()

            # 1. 清理 PROCESSING 状态的消息（只清理 Discord 频道的）
            cursor.execute("SELECT COUNT(*) FROM messages WHERE status = 'processing' AND channel_type = ?", (ChannelType.DISCORD.value,))
            stuck_count = cursor.fetchone()[0]

            if stuck_count > 0:
                log.log(f"🧹 发现 {stuck_count} 条卡住的消息（PROCESSING），正在清理...")

                cursor.execute("""
                               UPDATE messages
                               SET status = 'completed',
                                   updated_at = CURRENT_TIMESTAMP,
                                   error = 'Bot 重置：消息被标记为已完成'
                               WHERE status = 'processing' AND channel_type = ?
                               """, (ChannelType.DISCORD.value,))

                affected = cursor.rowcount
                conn.commit()
                log.log(f"✅ 已清理 {affected} 条卡住的消息")
            else:
                log.log("✓ 没有发现 PROCESSING 状态的消息")

            # 2. 清理 PENDING 状态的消息（避免重启后重复处理，只清理 Discord 频道的）
            cursor.execute("SELECT COUNT(*) FROM messages WHERE status = 'pending' AND channel_type = ?", (ChannelType.DISCORD.value,))
            pending_count = cursor.fetchone()[0]

            if pending_count > 0:
                log.log(f"🧹 发现 {pending_count} 条待处理的消息（PENDING），正在跳过...")

                cursor.execute("""
                               UPDATE messages
                               SET status = 'skipped',
                                   updated_at = CURRENT_TIMESTAMP,
                                   error = 'Bot 重启：消息被跳过，避免重复处理'
                               WHERE status = 'pending' AND channel_type = ?
                               """, (ChannelType.DISCORD.value,))

                affected = cursor.rowcount
                conn.commit()
                log.log(f"✅ 已跳过 {affected} 条旧消息")
            else:
                log.log("✓ 没有发现 PENDING 状态的消息")

            # 3. 清理 AI_STARTED 状态的消息（避免重启后重复发送工具调用通知，只清理 Discord 频道的）
            cursor.execute("SELECT COUNT(*) FROM messages WHERE status = 'ai_started' AND channel_type = ?", (ChannelType.DISCORD.value,))
            ai_started_count = cursor.fetchone()[0]

            if ai_started_count > 0:
                log.log(f"🧹 发现 {ai_started_count} 条 AI 正在处理的消息（AI_STARTED），正在标记为已完成...")

                cursor.execute("""
                               UPDATE messages
                               SET status = 'completed',
                                   updated_at = CURRENT_TIMESTAMP,
                                   error = 'Bot 重启：AI 响应被标记为已完成（避免重复发送工具调用通知）'
                               WHERE status = 'ai_started' AND channel_type = ?
                               """, (ChannelType.DISCORD.value,))

                affected = cursor.rowcount
                conn.commit()
                log.log(f"✅ 已标记 {affected} 条 AI_STARTED 消息为已完成")
            else:
                log.log("✓ 没有发现 AI_STARTED 状态的消息")

            conn.close()

        except Exception as e:
            log.log(f"⚠️ 清理卡住消息时出错: {e}")

    async def _send_long_message(self, channel, content: str):
        """发送长消息，自动分割超过 2000 字符的消息"""
        if not content or not content.strip():
            return

        content = content.strip()

        # Discord 消息长度限制（设置为 1000 以提前分割）
        MAX_LENGTH = 1000

        if len(content) <= MAX_LENGTH:
            await channel.send(content)
        else:
            # 分割长消息
            parts = []
            current_part = ""

            # 按行分割，保留代码块结构
            lines = content.split('\n')
            in_code_block = False
            code_block_lang = ""

            for line in lines:
                # 检测代码块开始/结束
                if line.strip().startswith('```'):
                    if not in_code_block:
                        in_code_block = True
                        code_block_lang = line.strip()[3:]  # 获取语言标记
                        current_part += line + '\n'
                        continue
                    else:
                        in_code_block = False
                        current_part += line + '\n'
                        # 代码块结束，检查是否需要分割
                        if len(current_part) >= MAX_LENGTH * 0.9:
                            parts.append(current_part)
                            current_part = ""
                        continue

                current_part += line + '\n'

                # 如果不在代码块中，且当前部分接近限制，就分割
                if not in_code_block and len(current_part) >= MAX_LENGTH * 0.9:
                    parts.append(current_part.rstrip())
                    current_part = ""

                # 在代码块中，强制分割
                if in_code_block and len(current_part) >= MAX_LENGTH * 0.95:
                    # 关闭当前代码块
                    current_part = current_part.rstrip()
                    if current_part.endswith('```'):
                        pass
                    else:
                        current_part += '\n```'
                    parts.append(current_part)
                    # 重新打开代码块
                    current_part = f'```{code_block_lang}\n'

            # 添加剩余内容
            if current_part.strip():
                parts.append(current_part.strip())

            # 发送所有部分
            for i, part in enumerate(parts, 1):
                if part:
                    try:
                        await channel.send(part[:MAX_LENGTH])
                        # 如果不是最后一部分，稍微延迟一下
                        if i < len(parts):
                            await asyncio.sleep(0.5)
                    except discord.errors.HTTPException as e:
                        log.log(f"❌ 发送消息部分 {i}/{len(parts)} 失败: {e}")
                        # 尝试再次强制分割
                        if len(part) > MAX_LENGTH:
                            for j in range(0, len(part), MAX_LENGTH):
                                try:
                                    await channel.send(part[j:j+MAX_LENGTH])
                                except:
                                    pass

    async def send_startup_notification(self):
        """发送启动通知"""
        notification_channel_id = self.config.startup_notification_channel
        notification_user_id = self.config.startup_notification_user

        # 如果都没有配置，跳过通知
        if not notification_channel_id and not notification_user_id:
            log.log("ℹ️  未配置启动通知，跳过")
            return

        # 创建启动成功消息
        embed = discord.Embed(
            title="🚀 IM Claude Bridge 启动成功",
            description="桥接系统已就绪，可以开始使用！",
            color=discord.Color.green()
        )

        # 显示会话模式信息（固定使用 Session 模式）
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
                    log.log(f"⚠️  找不到启动通知频道: {notification_channel_id}")
                else:
                    await channel.send(embed=embed)
                    log.log(f"✅ 已向频道 #{channel.name} 发送启动通知")
            except ValueError:
                log.log(f"⚠️  启动通知频道 ID 格式错误: {notification_channel_id}")
            except Exception as e:
                log.log(f"❌ 发送到频道失败: {e}")

        # 发送到用户私聊
        if notification_user_id:
            try:
                user = self.get_user(int(notification_user_id))
                if not user:
                    try:
                        user = await self.fetch_user(int(notification_user_id))
                    except discord.NotFound:
                        log.log(f"⚠️  找不到启动通知用户: {notification_user_id}")
                        return
                    except discord.HTTPException as e:
                        log.log(f"⚠️  获取用户失败: {e}")
                        return

                # 创建或获取 DM 频道
                dm_channel = await user.create_dm()
                await dm_channel.send(embed=embed)
                log.log(f"✅ 已向用户 {user.display_name} 发送启动通知（私聊）")

            except ValueError:
                log.log(f"⚠️  启动通知用户 ID 格式错误: {notification_user_id}")
            except Exception as e:
                log.log(f"❌ 发送到用户私聊失败: {e}")

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


    async def on_ready(self):
        """Bot 准备就绪"""
        log.log(f"✓ Bot 已准备就绪!")
        log.log(f"✓ 在 {len(self.guilds)} 个服务器中")
        log.log(f"✓ 斜杠命令: /new, /status, /stop, /restart, /abort")
        log.log(f"✓ 上下文菜单: 下载附件")

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
                "typing_task": typing_task,
                "typing_active": True,
                "streaming_queue": StreamingMessageQueue(
                    message.channel,
                    self.config.queue_send_interval
                ),
                "last_streaming_content": "",
                "sent_blocks": [],
                # Content block 顺序追踪
                "content_blocks": [],  # 从数据库加载的 content block 顺序
                "current_text_block_index": 0,  # 当前文本块索引（在所有 text 类型的 content block 中）
                "text_block_item_indices": {},  # {text_block_index: item_count}
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
                    "typing_task": typing_task,
                    "typing_active": True,
                    "streaming_queue": StreamingMessageQueue(
                        message.channel,
                        self.config.queue_send_interval
                    ),
                    "last_streaming_content": "",
                    "sent_blocks": [],
                }
                log.log(f"[消息 #{message_id}] 已启动 typing indicator")

        except Exception as e:
            log.log(f"❌ 处理附件引用消息时出错: {e}")
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

            log.log(f"[文件下载 #{request_id}] 开始监控下载进度")

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
                        log.log(f"[文件下载 #{request_id}] 下载完成")

                        downloaded_files = []
                        if files_json:
                            try:
                                result_data = json.loads(files_json)
                                downloaded_files = result_data.get("downloaded_files", [])
                            except json.JSONDecodeError as e:
                                log.log(f"[文件下载 #{request_id}] 解析文件列表失败: {e}")

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
                        log.log(f"[文件下载 #{request_id}] 下载失败: {error}")
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
                        log.log(f"[文件下载 #{request_id}] 正在处理中... ({elapsed}s)")

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
            log.log(f"[文件下载 #{request_id}] 监控超时 ({elapsed}秒)，最后检查一次")
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
                log.log(f"[文件下载 #{request_id}] 超时检查时发现已完成")
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
                log.log(f"[文件下载 #{request_id}] 真的超时")
                embed = discord.Embed(
                    title="⏱️ 下载请求超时",
                    description=f"请求 #{request_id}\n\n文件下载请求超时（{max_wait_time}秒）\n可能原因：Bot 未运行或消息不存在。",
                    color=discord.Color.orange()
                )
                embed.set_footer(text=f"请求 ID: {request_id}")
                await confirmation_msg.edit(content="", embed=embed)

        except Exception as e:
            log.log(f"❌ 监控下载进度时出错: {e}")
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

                # 查询同一用户的私聊消息是否有正在处理的（只检查 Discord 频道）
                cursor.execute("""
                               SELECT COUNT(*) FROM messages
                               WHERE id != ? AND discord_user_id = ? AND is_dm = 1
                    AND status IN ('processing', 'ai_started') AND channel_type = ?
                               """, (current_message_id, user_id, ChannelType.DISCORD.value))
            else:
                # 频道：检查同一频道的其他消息是否正在处理
                channel_id = channel.id

                # 查询同一频道的消息是否有正在处理的（只检查 Discord 频道）
                cursor.execute("""
                               SELECT COUNT(*) FROM messages
                               WHERE id != ? AND discord_channel_id = ? AND is_dm = 0
                    AND status IN ('processing', 'ai_started') AND channel_type = ?
                               """, (current_message_id, channel_id, ChannelType.DISCORD.value))

            count = cursor.fetchone()[0]
            conn.close()

            # 如果有其他消息正在处理，说明 Worker 忙碌
            return count > 0

        except Exception as e:
            log.log(f"⚠️ 检查 session 状态时出错: {e}")
            return False

    async def check_responses(self):
        """定期检查 Claude 的响应和消息状态"""
        await self.wait_until_ready()

        while not self.is_closed():
            try:
                current_time = asyncio.get_event_loop().time()

                # 扫描外部插入的消息（is_external=True）
                # 查询 pending 和 processing 状态，并过滤已追踪的消息（只获取 Discord 频道）
                import sqlite3
                conn = sqlite3.connect(self.config.database_path)
                cursor = conn.cursor()
                cursor.execute("""
                               SELECT id, discord_user_id, discord_channel_id, username, content, is_dm
                               FROM messages
                               WHERE status IN (?, ?) AND direction = ? AND is_external = 1 AND channel_type = ?
                               ORDER BY created_at ASC
                               """, (MessageStatus.PENDING.value, MessageStatus.PROCESSING.value, MessageDirection.TO_CLAUDE.value, ChannelType.DISCORD.value))
                external_messages = cursor.fetchall()
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
                            from bot.streaming_queue import StreamingMessageQueue

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
                                "streaming_queue": StreamingMessageQueue(
                                    channel,
                                    self.config.queue_send_interval
                                ),
                                "last_streaming_content": "",
                                "sent_blocks": [],
                            }
                            log.log(f"📨 [消息 #{msg_id}] 已加载外部消息: {username}")

                        except Exception as e:
                            log.log(f"⚠️  外部消息 #{msg_id} 加载失败: {e}")

                # 等待一段时间再检查
                await asyncio.sleep(self.config.poll_interval / 1000)

            except Exception as e:
                log.log(f"❌ 检查响应时出错: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(5)

    async def check_streaming_responses(self):
        """定期检查流式响应更新并实时发送 Discord 消息"""
        await self.wait_until_ready()

        log.log("🌊 流式响应检查任务已启动")

        while not self.is_closed():
            try:
                # 批量获取有待发送流式响应的 Discord 消息
                messages = self.message_queue.get_streaming_messages(channel_type="discord")

                for msg_info in messages:
                    msg_id = msg_info["id"]
                    channel_id = msg_info["discord_channel_id"]
                    streaming_response = msg_info["streaming_response"]
                    # 如果消息在 pending_messages 中，处理它
                    if msg_id in self.pending_messages:
                        pending = self.pending_messages[msg_id]

                        # 检测并发送新的 block
                        last_content = pending.get("last_streaming_content", "")
                        new_blocks = self._detect_new_blocks(last_content, streaming_response)

                        if new_blocks:
                            # 将新 block 添加到发送队列
                            streaming_queue = pending.get("streaming_queue")
                            if streaming_queue:
                                # 导入 MessageType
                                from bot.streaming_queue import MessageType

                                # 获取 content block 顺序（如果还没有加载，先加载）
                                content_blocks = pending.get("content_blocks", [])
                                if not content_blocks:
                                    content_blocks = self.message_queue.get_content_blocks(msg_id)
                                    pending["content_blocks"] = content_blocks

                                # 找到当前文本对应的 content block 索引
                                # 当前文本块索引
                                current_text_block_index = pending.get("current_text_block_index", 0)

                                # 找到对应的 content block 索引
                                content_block_index = 0  # 默认为第一个 content block
                                text_block_count = 0
                                for cb in content_blocks:
                                    if cb.get("type") == "text":
                                        if text_block_count == current_text_block_index:
                                            content_block_index = cb.get("index", 0)
                                            break
                                        text_block_count += 1

                                # 获取当前文本块的 item 索引
                                text_block_item_indices = pending.get("text_block_item_indices", {})
                                current_item_index = text_block_item_indices.get(current_text_block_index, 0)

                                # 实际发送的 block 计数（用于 item_index）
                                sent_count = 0

                                for i, block in enumerate(new_blocks):
                                    # 处理表情包标记 <:文件名.扩展名>
                                    channel = pending.get("channel")
                                    processed_block, stickers, additional_sent = await self._process_sticker_mentions(
                                        block,
                                        channel,
                                        streaming_queue,
                                        content_block_index,
                                        current_item_index + sent_count
                                    )

                                    # 发送表情包（如果找到）
                                    for sticker_path in stickers:
                                        try:
                                            # 直接使用文件路径创建 discord.File
                                            # discord.File 会自动处理文件打开和关闭
                                            file = discord.File(sticker_path)
                                            await streaming_queue.add_message(
                                                MessageType.FILES,
                                                [file],
                                                content_block_index=content_block_index,
                                                item_index=current_item_index + sent_count
                                            )
                                            sent_count += 1
                                        except Exception as e:
                                            log.log(f"[表情包] 发送失败: {sticker_path} - {e}")

                                    # 只有当处理后文本不为空时才发送
                                    if processed_block and processed_block.strip():
                                        # 使用实际的 content block 索引
                                        await streaming_queue.add_message(
                                            MessageType.TEXT,
                                            processed_block,
                                            content_block_index=content_block_index,
                                            item_index=current_item_index + sent_count + additional_sent
                                        )
                                        pending["sent_blocks"].append(block)
                                        sent_count += 1

                                    # 加上表情包前文本发送的数量
                                    sent_count += additional_sent

                                # 更新 item 索引（使用实际发送的数量）
                                text_block_item_indices[current_text_block_index] = current_item_index + sent_count
                                pending["text_block_item_indices"] = text_block_item_indices

                                # 检查是否需要移动到下一个文本 content block
                                # 检查 streaming_response 中是否已经包含了下一个 content block 的标记（工具调用）
                                # 如果包含工具调用标记，说明当前文本 block 已完成
                                if content_blocks:
                                    # 找到当前文本 block 在 content_blocks 中的位置
                                    current_text_block_position = -1
                                    text_block_count = 0
                                    for i, cb in enumerate(content_blocks):
                                        if cb.get("type") == "text":
                                            if text_block_count == current_text_block_index:
                                                current_text_block_position = i
                                                break
                                            text_block_count += 1

                                    # 检查下一个 block 是否是 tool_use
                                    if current_text_block_position >= 0 and current_text_block_position + 1 < len(content_blocks):
                                        next_block = content_blocks[current_text_block_position + 1]
                                        if next_block.get("type") == "tool_use":
                                            # 下一个 block 是工具调用，说明当前文本 block 已完成
                                            pending["current_text_block_index"] = current_text_block_index + 1

                            # 更新上次的内容
                            pending["last_streaming_content"] = streaming_response

            except Exception as e:
                log.log(f"❌ 检查流式响应时出错: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(5)

    async def check_file_requests(self):
        """定期检查并处理文件发送请求"""
        await self.wait_until_ready()

        log.log("📁 文件发送检查任务已启动")

        while not self.is_closed():
            try:
                # 获取下一个待处理的 Discord 文件请求
                from shared.message_queue import FileRequestStatus
                file_request = self.message_queue.get_next_file_request(channel_type="discord")

                if file_request:
                    log.log(f"📁 处理文件请求 #{file_request.id}")
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

                        # 发送文件（直接发送，不使用统一队列）
                        sent_msg = await target_channel.send(
                            files=valid_files if len(valid_files) > 1 else valid_files
                        )

                        # 标记为完成
                        result = json.dumps({
                            "success": True,
                            "message": f"成功发送 {len(valid_files)} 个文件到 {target_info}",
                            "message_id": str(sent_msg.id) if sent_msg else None
                        }, ensure_ascii=False)
                        self.message_queue.update_file_request_status(
                            file_request.id,
                            FileRequestStatus.COMPLETED,
                            result=result
                        )
                        log.log(f"✅ 文件请求 #{file_request.id} 处理完成")

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
                        log.log(f"❌ 文件请求 #{file_request.id} 处理失败: {e}")

                # 等待一段时间再检查
                await asyncio.sleep(self.config.poll_interval / 1000)

            except Exception as e:
                log.log(f"❌ 检查文件请求时出错: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(5)

    async def check_file_downloads(self):
        """定期检查并处理文件下载请求（支持私聊和频道）"""
        await self.wait_until_ready()

        log.log("📥 文件下载检查任务已启动")

        while not self.is_closed():
            try:
                # 获取下一个待处理的下载请求
                from shared.message_queue import FileDownloadRequestStatus
                download_request = self.message_queue.get_next_file_download_request()

                if download_request:
                    log.log(f"📥 处理文件下载请求 #{download_request.id}")
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
                        import traceback
                        traceback.print_exc()

                # 等待一段时间再检查
                await asyncio.sleep(self.config.poll_interval / 1000)

            except Exception as e:
                log.log(f"❌ 检查文件下载请求时出错: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(5)

    async def check_message_requests(self):
        """定期检查并处理消息发送请求"""
        await self.wait_until_ready()

        log.log("💬 消息发送检查任务已启动")

        while not self.is_closed():
            try:
                # 获取下一个待处理的消息请求
                from shared.message_queue import MessageRequestStatus
                message_request = self.message_queue.get_next_message_request()

                if message_request:
                    log.log(f"💬 处理消息请求 #{message_request.id}")
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

        # 如果禁用了消息分割功能，直接返回整个内容
        if not self.config.enable_message_splitting:
            if not previous_content:
                return [new_content]
            else:
                new_text = new_content[len(previous_content):]
                return [new_text] if new_text else []

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

    async def _process_sticker_mentions(self, text: str, channel: discord.abc.Messageable, streaming_queue=None, content_block_index=None, base_item_index=0):
        """
        处理表情包标记 <:文件名.扩展名>

        注意：会跳过代码块（```）中的内容，避免误处理示例代码
        如果表情包在文本中间，会将前面的文本直接发送到队列

        Args:
            text: 要处理的文本
            channel: Discord 频道对象
            streaming_queue: 流式队列对象（可选）
            content_block_index: content block 索引（可选）
            base_item_index: 基础 item 索引（可选）

        Returns:
            tuple: (处理后的文本, 表情包路径列表, 发送的文本块数量)
        """
        import re
        from pathlib import Path
        from bot.streaming_queue import MessageType

        # 表情包目录（从配置读取）
        sticker_dir = Path(self.config.stickers_path)

        # 检测表情包标记（但不在代码块中）
        sticker_pattern = re.compile(r'<:([^>]+\.(png|jpg|jpeg|gif))>')

        # 分离代码块和非代码块
        result_text = text
        sticker_paths = []
        sent_blocks_count = 0

        # 检查是否在代码块中
        lines = text.split('\n')
        in_code_block = False
        processed_lines = []

        for line in lines:
            # 检测代码块开始/结束
            if line.strip().startswith('```'):
                in_code_block = not in_code_block
                processed_lines.append(line)
                continue

            # 如果在代码块中，直接添加，不处理表情包
            if in_code_block:
                processed_lines.append(line)
                continue

            # 不在代码块中，检测表情包
            matches = sticker_pattern.findall(line)
            if matches:
                # 检查表情包是否在行中间（前后都有文本）
                line_parts = sticker_pattern.split(line)

                # 如果表情包在文本中间（split 后有多部分）
                if len(line_parts) > 1:
                    # 使用循环处理每个表情包
                    current_text = line
                    for match_index in range(len(matches)):
                        # 在当前文本中搜索第一个表情包
                        match_obj = sticker_pattern.search(current_text)
                        if not match_obj:
                            break

                        # 分割文本：表情包之前的部分
                        prefix_text = current_text[:match_obj.start()].rstrip()

                        # 分割文本：表情包之后的部分
                        suffix_text = current_text[match_obj.end():].lstrip()

                        # 如果前缀不为空，发送到队列
                        if prefix_text and streaming_queue:
                            await streaming_queue.add_message(
                                MessageType.TEXT,
                                prefix_text,
                                content_block_index=content_block_index,
                                item_index=base_item_index + sent_blocks_count
                            )
                            sent_blocks_count += 1
                        elif prefix_text:
                            # 没有 streaming_queue，添加到 processed_lines
                            processed_lines.append(prefix_text)

                        # 查找并添加当前表情包文件
                        # 从匹配中提取文件名（去掉扩展名）
                        filename_match = match_obj.group(1)  # xxx
                        ext_match = match_obj.group(2)  # gif
                        filename = f"{filename_match}.{ext_match}"

                        sticker_path = sticker_dir / filename

                        if sticker_path.exists():
                            sticker_paths.append(sticker_path)
                        else:
                            # 模糊匹配
                            if '-' in filename:
                                meaning = filename.split('-')[0]
                                matching_files = list(sticker_dir.glob(f"{meaning}-*.*"))
                                if matching_files:
                                    sticker_paths.append(matching_files[0])
                                else:
                                    log.log(f"[表情包] 文件不存在: {filename}")
                            else:
                                log.log(f"[表情包] 文件不存在: {filename}")

                        # 更新当前文本为后缀，继续处理下一个表情包
                        current_text = suffix_text

                    # 添加最后剩余的文本
                    if current_text and current_text.strip():
                        processed_lines.append(current_text)
                else:
                    # 表情包在行首或行尾，正常处理
                    # 查找表情包文件
                    for filename, _ in matches:
                        sticker_path = sticker_dir / filename

                        if sticker_path.exists():
                            # 精确匹配
                            sticker_paths.append(sticker_path)
                        else:
                            # 模糊匹配（只匹配含义部分）
                            if '-' in filename:
                                meaning = filename.split('-')[0]
                                matching_files = list(sticker_dir.glob(f"{meaning}-*.*"))
                                if matching_files:
                                    sticker_paths.append(matching_files[0])
                                else:
                                    log.log(f"[表情包] 文件不存在: {filename}")
                            else:
                                log.log(f"[表情包] 文件不存在: {filename}")

                    # 移除表情包标记
                    line = sticker_pattern.sub('', line)
                    processed_lines.append(line)
            else:
                processed_lines.append(line)

        # 重新组合文本
        processed_text = '\n'.join(processed_lines).strip()

        return processed_text, sticker_paths, sent_blocks_count

    async def _process_sticker_mentions_for_queue(self, text: str, channel: discord.abc.Messageable):
        """
        处理表情包标记 <:文件名.扩展名>（用于队列模式）

        注意：会跳过代码块（```）中的内容，避免误处理示例代码

        Args:
            text: 要处理的文本
            channel: Discord 频道对象（未使用，保持接口一致性）

        Returns:
            tuple: (处理后的文本, 表情包路径列表)
        """
        import re
        from pathlib import Path

        # 表情包目录（从配置读取）
        sticker_dir = Path(self.config.stickers_path)

        # 检测表情包标记（但不在代码块中）
        sticker_pattern = re.compile(r'<:([^>]+\.(png|jpg|jpeg|gif))>')

        # 分离代码块和非代码块
        result_text = text
        sticker_paths = []

        # 检查是否在代码块中
        lines = text.split('\n')
        in_code_block = False
        processed_lines = []

        for line in lines:
            # 检测代码块开始/结束
            if line.strip().startswith('```'):
                in_code_block = not in_code_block
                processed_lines.append(line)
                continue

            # 如果在代码块中，直接添加，不处理表情包
            if in_code_block:
                processed_lines.append(line)
                continue

            # 不在代码块中，检测表情包
            matches = sticker_pattern.findall(line)
            if matches:
                # 查找表情包文件
                for filename, _ in matches:
                    sticker_path = sticker_dir / filename

                    if sticker_path.exists():
                        # 精确匹配
                        sticker_paths.append(sticker_path)
                    else:
                        # 模糊匹配（只匹配含义部分）
                        if '-' in filename:
                            meaning = filename.split('-')[0]
                            matching_files = list(sticker_dir.glob(f"{meaning}-*.*"))
                            if matching_files:
                                sticker_paths.append(matching_files[0])
                            else:
                                log.log(f"[表情包] 文件不存在: {filename}")
                        else:
                            log.log(f"[表情包] 文件不存在: {filename}")

                # 移除表情包标记
                line = sticker_pattern.sub('', line)
                processed_lines.append(line)
            else:
                processed_lines.append(line)

        # 重新组合文本
        processed_text = '\n'.join(processed_lines).strip()

        return processed_text, sticker_paths

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
                               SELECT id, discord_channel_id, discord_user_id, tool_uses, is_dm
                               FROM messages
                               WHERE status IN ('ai_started', 'processing')
                                 AND tool_uses IS NOT NULL
                                 AND tool_uses != ''
                               ORDER BY id ASC
                               """)
                rows = cursor.fetchall()
                conn.close()

                for msg_id, channel_id, user_id, tool_uses_json, is_dm in rows:
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
                                    msg_id,
                                    i,
                                    tool_use['name'],
                                    tool_use['input'],
                                    channel_id,
                                    user_id,
                                    is_dm
                                )

                            # 更新已处理的数量
                            processed_tool_uses[msg_id] = list(range(len(tool_uses)))

                    except (json.JSONDecodeError, KeyError) as e:
                        log.log(f"❌ [Bot] 解析工具调用失败: {e}")
                        pass

                # 清理已完成消息的记录
                conn = sqlite3.connect(self.config.database_path)
                cursor = conn.cursor()
                cursor.execute("""
                               SELECT id FROM messages
                               WHERE status IN ('completed', 'failed', 'skipped') AND channel_type = ?
                               """, (ChannelType.DISCORD.value,))
                completed_ids = [row[0] for row in cursor.fetchall()]
                conn.close()

                for completed_id in completed_ids:
                    if completed_id in processed_tool_uses:
                        del processed_tool_uses[completed_id]

                # 等待一段时间再检查（0.1 秒，快速响应工具调用）
                await asyncio.sleep(0.1)

            except Exception as e:
                log.log(f"❌ 检查工具调用时出错: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(5)

    async def check_message_sequences(self):
        """检查并发送消息序列（统一的发送任务）"""
        await self.wait_until_ready()

        log.log("🌊 消息序列检查任务已启动")

        from datetime import datetime

        # 追踪每个消息的发送状态
        message_states = {}

        while not self.is_closed():
            try:
                # 获取有待发送序列的消息
                messages = self.message_queue.get_messages_with_pending_sequences('discord', limit=1)

                if not messages:
                    # 没有待发送的序列，检查 pending_messages 中的消息是否完成
                    for message_id in list(self.pending_messages.keys()):
                        stats = self.message_queue.get_message_sequences_stats(message_id)

                        # 检查 AI 响应是否已完成，且所有序列都已发送
                        if stats["total"] > 0 and stats["total"] == stats["sent"] and self.message_queue.is_ai_response_complete(message_id):
                            # 1. 停止正在输入状态
                            self.stop_typing_indicator(message_id)
                            # 2. 所有序列都已发送，清理数据库相关序列
                            self.message_queue.cleanup_message_sequences(message_id)
                            # 3. 更新消息状态为 COMPLETED（防止重复加载）
                            self.message_queue.update_status(message_id, MessageStatus.COMPLETED)
                            # 4. 清理内存缓存，防止内存泄漏
                            if message_id in message_states:
                                del message_states[message_id]
                            if message_id in self.pending_messages:
                                del self.pending_messages[message_id]

                    # 等待一会儿再检查
                    await asyncio.sleep(0.5)
                    continue

                message_info = messages[0]
                message_id = message_info['id']
                channel_id = message_info['discord_channel_id']
                user_id = message_info['discord_user_id']
                is_dm = message_info['is_dm']
                channel_type = message_info['channel_type']

                try:

                    # 初始化消息状态
                    if message_id not in message_states:
                        message_states[message_id] = {"pending": []}

                    # 获取待发送的序列项（每次只取一条，确保严格按顺序发送）
                    pending_sequences = self.message_queue.get_pending_message_sequences(message_id, limit=1)

                    if not pending_sequences:
                        # 没有待发送的序列，检查是否完成
                        stats = self.message_queue.get_message_sequences_stats(message_id)
                        log.log(f"🔍 [消息 #{message_id}] 序列统计: total={stats['total']}, pending={stats['pending']}, sent={stats['sent']}")

                        # 检查 AI 响应是否已完成，且所有序列都已发送
                        if stats["total"] > 0 and stats["pending"] == 0 and self.message_queue.is_ai_response_complete(message_id):
                            log.log(f"✅ [消息 #{message_id}] 所有序列已发送，停止 typing indicator")
                            # 1. 停止正在输入状态
                            self.stop_typing_indicator(message_id)
                            # 2. 所有序列都已发送，清理数据库相关序列
                            self.message_queue.cleanup_message_sequences(message_id)
                            # 3. 更新消息状态为 COMPLETED（防止重复加载）
                            self.message_queue.update_status(message_id, MessageStatus.COMPLETED)
                            # 4. 清理内存缓存，防止内存泄漏
                            if message_id in message_states:
                                del message_states[message_id]
                            if message_id in self.pending_messages:
                                del self.pending_messages[message_id]
                        else:
                            # 还未完成，等待下一轮
                            await asyncio.sleep(0.1)
                        continue

                    # 获取频道
                    if is_dm:
                        user = self.get_user(user_id)
                        if not user:
                            try:
                                user = await self.fetch_user(user_id)
                            except discord.NotFound:
                                # 用户不存在，标记消息为失败并清理
                                log.log(f"❌ 消息 #{message_id} 发送失败: 用户不存在 (user_id={user_id})")
                                self.message_queue.cleanup_message_sequences(message_id)
                                self.message_queue.update_status(message_id, MessageStatus.FAILED, error=f"用户不存在: {user_id}")
                                continue
                            except Exception as e:
                                log.log(f"⚠️  获取用户失败: {user_id}, 错误: {e}")
                                continue
                        if not user:
                            continue
                        try:
                            channel = await user.create_dm()
                        except discord.NotFound:
                            # 无法创建 DM（用户不存在），标记消息为失败
                            log.log(f"❌ 消息 #{message_id} 发送失败: 无法创建私聊频道 (user_id={user_id})")
                            self.message_queue.cleanup_message_sequences(message_id)
                            self.message_queue.update_status(message_id, MessageStatus.FAILED, error=f"无法创建私聊频道: {user_id}")
                            continue
                        except discord.Forbidden:
                            # 没有权限创建 DM
                            log.log(f"❌ 消息 #{message_id} 发送失败: 没有权限创建私聊频道 (user_id={user_id})")
                            self.message_queue.cleanup_message_sequences(message_id)
                            self.message_queue.update_status(message_id, MessageStatus.FAILED, error=f"没有权限创建私聊频道: {user_id}")
                            continue
                    else:
                        channel = self.get_channel(channel_id)
                        if not channel:
                            # 频道不存在，标记消息为失败并清理
                            log.log(f"❌ 消息 #{message_id} 发送失败: 频道不存在 (channel_id={channel_id})")
                            self.message_queue.cleanup_message_sequences(message_id)
                            self.message_queue.update_status(message_id, MessageStatus.FAILED, error=f"频道不存在: {channel_id}")
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
                                # 处理表情包标记 <:文件名.扩展名>
                                processed_text, stickers = await self._process_sticker_mentions_for_queue(text, channel)

                                # 发送表情包（如果找到）
                                for sticker_path in stickers:
                                    try:
                                        file = discord.File(sticker_path)
                                        await channel.send(file=file)
                                    except Exception as e:
                                        log.log(f"[表情包] 发送失败: {sticker_path} - {e}")

                                # 发送处理后的文本（如果不为空）
                                if processed_text and processed_text.strip():
                                    await self._send_long_message(channel, processed_text.strip())

                        elif item_type == "tool_use":
                            # 发送工具调用通知（直接发送，不使用队列）
                            tool_name = item_data.get("name", "")
                            tool_input = item_data.get("input", {})
                            tool_id = item_data.get("id", "")

                            # 过滤管理命令的工具调用通知（避免噪音）
                            should_skip = False
                            if tool_name == "Bash" and tool_input.get("command"):
                                command = tool_input["command"]
                                management_keywords = ["restart.bat", "im_claude_bridge_manager.py", "restart", "shutdown", "stop"]
                                if any(keyword in command.lower() for keyword in management_keywords):
                                    should_skip = True

                            if not should_skip:
                                # 构建工具调用Embed
                                TOOL_EMOJIS = self.config.tool_emoji_mapping
                                is_mcp = tool_name.startswith('mcp__')

                                if is_mcp:
                                    parts = tool_name.split('__')
                                    if len(parts) >= 3:
                                        mcp_server = parts[1]
                                        mcp_tool = parts[2]
                                        display_title = f"MCP {mcp_server}"
                                        emoji = TOOL_EMOJIS.get(tool_name)
                                        if emoji is None:
                                            emoji = TOOL_EMOJIS.get(mcp_server, "🔧")

                                        embed = discord.Embed(
                                            title=f"🔄 {emoji} {display_title}",
                                            color=discord.Color.blue()
                                        )
                                        embed.description = mcp_tool
                                    else:
                                        emoji = TOOL_EMOJIS.get(tool_name, "🔧")
                                        embed = discord.Embed(
                                            title=f"🔄 {emoji} {tool_name}",
                                            color=discord.Color.blue()
                                        )
                                        embed.description = "无参数"
                                else:
                                    emoji = TOOL_EMOJIS.get(tool_name, "🔧")
                                    embed = discord.Embed(
                                        title=f"🔄 {emoji} {tool_name}",
                                        color=discord.Color.blue()
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
                                        if len(cmd) > 100:
                                            cmd = cmd[:97] + "..."
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
                                        # TodoWrite: 显示所有任务（不截断）
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

                                            # 构建任务列表（按状态分组：已完成 → 进行中 → 待办中）
                                            todo_lines = []

                                            # 1. 显示已完成任务
                                            completed_tasks = [t for t in todos if t.get('status') == 'completed']
                                            if completed_tasks:
                                                todo_lines.append("✅ 已完成:")
                                                for todo in completed_tasks:
                                                    content = todo.get('content', '')
                                                    todo_lines.append(f"  • {content}")
                                                todo_lines.append("")

                                            # 2. 显示进行中任务
                                            in_progress_tasks = [t for t in todos if t.get('status') == 'in_progress']
                                            if in_progress_tasks:
                                                todo_lines.append("🔄 进行中:")
                                                for todo in in_progress_tasks:
                                                    active_form = todo.get('activeForm', todo.get('content', ''))
                                                    todo_lines.append(f"  • {active_form}")
                                                todo_lines.append("")

                                            # 3. 显示待办中任务
                                            pending_tasks = [t for t in todos if t.get('status') == 'pending']
                                            if pending_tasks:
                                                todo_lines.append("📋 待办中:")
                                                for todo in pending_tasks:
                                                    content = todo.get('content', '')
                                                    todo_lines.append(f"  • {content}")
                                                todo_lines.append("")

                                            # 添加进度统计
                                            total = len(todos)
                                            completed = status_counts['completed']
                                            if completed > 0:
                                                todo_lines.append(f"进度: {completed}/{total} ({completed*100//total}%)")
                                            else:
                                                todo_lines.append(f"总任务: {total} 个")

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

                                # 直接发送Embed（不使用队列）
                                sent_message = await channel.send(embed=embed)

                                # 保存消息引用（用于后续更新）
                                if sent_message:
                                    # 使用正确的tool_use_index（而不是sequence_index）
                                    ref_tool_use_index = tool_use_index if tool_use_index is not None else seq_index
                                    self.message_queue.save_tool_use_message_ref(
                                        message_id,
                                        ref_tool_use_index,
                                        sent_message.id,
                                        channel_id,
                                        is_dm,
                                        channel_type='discord'
                                    )

                        # 标记为已发送
                        self.message_queue.mark_sequence_sent(seq_id)

                        # 控制发送速率，避免触发Discord速率限制
                        await asyncio.sleep(self.config.queue_send_interval)

                    except discord.NotFound as e:
                        # 频道/用户不存在，标记消息为失败并清理
                        log.log(f"❌ 消息 #{message_id} 发送失败: 资源不存在 - {e}")
                        self.message_queue.cleanup_message_sequences(message_id)
                        self.message_queue.update_status(message_id, MessageStatus.FAILED, error=f"资源不存在: {e}")
                        import traceback
                        traceback.print_exc()
                    except discord.Forbidden as e:
                        # 没有权限，标记消息为失败并清理
                        log.log(f"❌ 消息 #{message_id} 发送失败: 没有权限 - {e}")
                        self.message_queue.cleanup_message_sequences(message_id)
                        self.message_queue.update_status(message_id, MessageStatus.FAILED, error=f"没有权限: {e}")
                        import traceback
                        traceback.print_exc()
                    except Exception as e:
                        log.log(f"❌ 发送序列项失败: 消息#{message_id}, 序列#{seq_index}, 错误: {e}")
                        import traceback
                        traceback.print_exc()

                except Exception as e:
                    log.log(f"❌ 处理消息序列失败: 消息#{message_id}, 错误: {e}")
                    import traceback
                    traceback.print_exc()

                # 极小延迟，避免无消息时CPU空转
                await asyncio.sleep(0.01)

            except Exception as e:
                log.log(f"❌ 检查消息序列时出错: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(5)

    async def _send_tool_use_notification(self, message_id: int, tool_use_index: int, tool_name: str, tool_input: dict, channel_id: int, user_id: int, is_dm: bool):
        """发送工具调用通知

        Args:
            message_id: 消息 ID
            tool_use_index: 工具调用索引
            tool_name: 工具名称
            tool_input: 工具参数
            channel_id: Discord 频道/私聊 ID
            user_id: Discord 用户 ID
            is_dm: 是否为私聊
        """
        # 过滤管理命令的工具调用通知（避免噪音）
        if tool_name == "Bash" and tool_input.get("command"):
            command = tool_input["command"]
            # 检查是否是管理相关的命令
            management_keywords = ["restart.bat", "im_claude_bridge_manager.py", "restart", "shutdown", "stop"]
            if any(keyword in command.lower() for keyword in management_keywords):
                # 这是管理命令，不发送通知
                return

        # 从配置文件读取工具 emoji 映射
        TOOL_EMOJIS = self.config.tool_emoji_mapping

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

                # 优先检查完整的 MCP 工具名（mcp__服务器名__工具名）
                emoji = TOOL_EMOJIS.get(tool_name)
                # 如果没有找到完整工具名的映射，检查服务器名
                if emoji is None:
                    emoji = TOOL_EMOJIS.get(mcp_server, "🔧")

                # MCP 工具显示工具名
                embed = discord.Embed(
                    title=f"🔄 {emoji} {display_title}",
                    color=discord.Color.blue()
                )
                embed.description = mcp_tool
            else:
                # 格式异常，按普通工具处理
                emoji = TOOL_EMOJIS.get(tool_name, "🔧")
                embed = discord.Embed(
                    title=f"🔄 {emoji} {tool_name}",
                    color=discord.Color.blue()
                )
                embed.description = "无参数"
        else:
            # 非 MCP 工具：正常处理
            emoji = TOOL_EMOJIS.get(tool_name, "🔧")

            # 构建 Embed
            embed = discord.Embed(
                title=f"🔄 {emoji} {tool_name}",
                color=discord.Color.blue()
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
                if len(cmd) > 100:
                    cmd = cmd[:97] + "..."
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
                # TodoWrite: 显示所有任务（不截断）
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

                    # 构建任务列表（按状态分组：已完成 → 进行中 → 待办中）
                    todo_lines = []

                    # 1. 显示已完成任务
                    completed_tasks = [t for t in todos if t.get('status') == 'completed']
                    if completed_tasks:
                        todo_lines.append("✅ 已完成:")
                        for todo in completed_tasks:
                            content = todo.get('content', '')
                            todo_lines.append(f"  • {content}")
                        todo_lines.append("")

                    # 2. 显示进行中任务
                    in_progress_tasks = [t for t in todos if t.get('status') == 'in_progress']
                    if in_progress_tasks:
                        todo_lines.append("🔄 进行中:")
                        for todo in in_progress_tasks:
                            active_form = todo.get('activeForm', todo.get('content', ''))
                            todo_lines.append(f"  • {active_form}")
                        todo_lines.append("")

                    # 3. 显示待办中任务
                    pending_tasks = [t for t in todos if t.get('status') == 'pending']
                    if pending_tasks:
                        todo_lines.append("📋 待办中:")
                        for todo in pending_tasks:
                            content = todo.get('content', '')
                            todo_lines.append(f"  • {content}")
                        todo_lines.append("")

                    # 添加进度统计
                    total = len(todos)
                    completed = status_counts['completed']
                    if completed > 0:
                        todo_lines.append(f"进度: {completed}/{total} ({completed*100//total}%)")
                    else:
                        todo_lines.append(f"总任务: {total} 个")

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

        # 发送到 Discord（统一队列模式）
        try:
            from bot.streaming_queue import MessageType

            # 获取 content block 顺序，找到工具调用对应的 content block 索引
            content_blocks = self.message_queue.get_content_blocks(message_id)

            # 找到对应的 content block 索引
            content_block_index = tool_use_index  # 默认使用 tool_use_index

            # 按 tool_use_index 的顺序查找对应的 content block
            tool_use_count = 0
            for cb in content_blocks:
                if cb.get("type") == "tool_use":
                    if tool_use_count == tool_use_index:
                        content_block_index = cb.get("index", tool_use_index)
                        break
                    tool_use_count += 1

            sent_message = None
            if is_dm:
                # 私聊：通过 user_id 获取用户并创建/获取 DM 频道
                user = self.get_user(user_id)
                if not user:
                    user = await self.fetch_user(user_id)
                if user:
                    dm_channel = await user.create_dm()
                    queue = self._get_unified_queue(dm_channel)
                    # 使用 return_future=True 获取 Future，并设置 content block 索引
                    future = await queue.add_message(
                        MessageType.EMBED,
                        embed,
                        return_future=True,
                        content_block_index=content_block_index
                    )
                    if future:
                        # 等待消息发送完成，获取消息对象
                        sent_message = await future
            else:
                # 频道：直接通过 channel_id 获取
                channel = self.get_channel(channel_id)
                if channel:
                    queue = self._get_unified_queue(channel)
                    # 使用 return_future=True 获取 Future，并设置 content block 索引
                    future = await queue.add_message(
                        MessageType.EMBED,
                        embed,
                        return_future=True,
                        content_block_index=content_block_index
                    )
                    if future:
                        # 等待消息发送完成，获取消息对象
                        sent_message = await future

            # 保存 Discord 消息引用（统一队列模式和原有模式都需要）
            if sent_message:
                self.message_queue.save_tool_use_message_ref(
                    message_id,
                    tool_use_index,
                    sent_message.id,
                    channel_id,
                    is_dm,
                    channel_type='discord'
                )
            else:
                log.log(f"⚠️ [Bot] 发送卡片失败: 消息 #{message_id}, 工具 #{tool_use_index}, sent_message 为 None")
        except Exception as e:
            log.log(f"❌ [Bot] 发送卡片异常: 消息 #{message_id}, 工具 #{tool_use_index}, 错误: {e}")
            pass  # 静默失败，避免刷屏

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

            # 更新标题：将 🔄 替换为 ✅ 或 ❌
            old_title = embed.title
            if old_title:
                new_title = old_title.replace('🔄', '✅' if success else '❌', 1)

                # 更新 embed
                embed.title = new_title

                # 更新颜色
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
                import traceback
                traceback.print_exc()
                await asyncio.sleep(5)


def main():
    """主函数"""
    try:
        # 加载配置
        config = Config()

        # 创建并启动 Bot
        bot = DiscordBot(config)
        bot.run(config.discord_token)

    except FileNotFoundError as e:
        log.log(f"❌ 配置错误: {e}")
        sys.exit(1)
    except ValueError as e:
        log.log(f"❌ 配置错误: {e}")
        sys.exit(1)
    except Exception as e:
        log.log(f"❌ 启动失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
