"""
Discord Bot 主程序
接收 Discord 消息并转发给 Claude Code
支持斜杠命令（Slash Commands）
"""
import discord
from discord import app_commands
import asyncio
import os
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

        # ⏰ 定时任务调度器
        self.cron_scheduler = None
        self.cron_scan_task = None

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
                                except Exception:
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

        mention_default = "需要 @" if self.config.mention_required else "不需要 @"
        embed.add_field(
            name=f"💬 对话模式：{mention_default}（默认）",
            value="每个频道和每个用户的私聊都使用独立的对话模式\n在具体频道或私聊中使用 `/mention` 进行对话模式设置",
            inline=False
        )

        embed.add_field(name="📂 工作目录", value=f"`{self.config.working_directory}`", inline=False)

        embed.add_field(name="🔧 可用命令", value="`/new` - 新会话\n`/status` - 查看状态\n`/abort` - 中止输出\n`/mention` - 切换是否需要 @\n`/restart` - 重启服务\n`/stop` - 停止服务\n`下载附件` - 右键消息下载附件", inline=False)

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


    async def on_ready(self):
        """Bot 准备就绪"""
        log.log(f"✓ Bot 已准备就绪!")
        log.log(f"✓ 在 {len(self.guilds)} 个服务器中")
        log.log(f"✓ 斜杠命令: /new, /status, /stop, /restart, /abort, /mention")
        log.log(f"✓ 上下文菜单: 下载附件")

        # 发送启动通知
        await self.send_startup_notification()

    async def on_message(self, message: discord.Message):
        """处理接收到的消息"""
        # 忽略自己的消息
        if message.author == self.user:
            return

        # 检查是否需要 @提及（频道级别独立管理）
        if not isinstance(message.channel, discord.DMChannel):
            mention_required = self.message_queue.get_channel_mention_required(
                message.channel.id,
                default=self.config.mention_required
            )
            if mention_required:
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

    async def check_responses(self):
        """定期检查 Claude 的响应和消息状态"""
        await self.wait_until_ready()

        while not self.is_closed():
            try:
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
                        sent_msg = await target_channel.send(files=valid_files)

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
        if hasattr(self, 'sequence_check_task') and self.sequence_check_task:
            self.sequence_check_task.cancel()
        if hasattr(self, 'tool_result_check_task') and self.tool_result_check_task:
            self.tool_result_check_task.cancel()

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

    async def check_message_sequences(self):
        """检查并发送消息序列（统一的发送任务）"""
        await self.wait_until_ready()

        log.log("🌊 消息序列检查任务已启动")

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
                                await self._send_long_message(channel, text.strip())

                        elif item_type == "sticker":
                            # 表情包：从 item_data 获取文件路径，发送为图片
                            sticker_path = item_data.get("file_path", "") if item_data else ""
                            if sticker_path and os.path.exists(sticker_path):
                                try:
                                    file = discord.File(sticker_path)
                                    await channel.send(file=file)
                                    log.log(f"✅ [消息 #{message_id}] 已发送表情包: {os.path.basename(sticker_path)}")
                                except Exception as e:
                                    log.log(f"❌ [消息 #{message_id}] 表情包发送失败: {sticker_path} - {e}")
                            else:
                                log.log(f"⚠️ [消息 #{message_id}] 表情包文件不存在: {sticker_path}")

                        elif item_type == "tool_use":
                            # 发送工具调用通知（直接发送，不使用队列）
                            tool_name = item_data.get("name", "")
                            tool_input = item_data.get("input", {})

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
