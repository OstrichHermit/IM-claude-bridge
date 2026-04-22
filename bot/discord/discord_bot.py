"""
Discord Bot 主程序
接收 Discord 消息并转发给 Claude Code
支持斜杠命令（Slash Commands）
"""
import discord
from discord import app_commands
import asyncio
import sys
import sqlite3
from pathlib import Path

# 添加 shared 目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.config import Config
from shared.logger import get_logger
from shared.message_queue import MessageQueue, Message, MessageDirection, MessageStatus, MessageTag, ChannelType, AttachmentInfo
from shared.file_mapping import FileMapping
from shared.cron_scheduler import BotCronScheduler
from bot.discord.discord_commands import DiscordCommandsMixin
from bot.discord.discord_message_handlers import DiscordMessageHandlersMixin
from bot.discord.discord_pollers import DiscordPollersMixin
from bot.discord.discord_sequence_sender import DiscordSequenceSenderMixin

log = get_logger("DiscordBot", "discord")


class DiscordBot(
    discord.Client,
    DiscordPollersMixin,
    DiscordSequenceSenderMixin,
    DiscordMessageHandlersMixin,
    DiscordCommandsMixin,
):
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
            tasks_file = Path(__file__).parent.parent.parent / "shared" / "cron_jobs.json"
            self.cron_scheduler = BotCronScheduler(str(tasks_file))
            await self.cron_scheduler.start()

            # 启动任务文件扫描任务
            self.cron_scan_task = asyncio.create_task(self.cron_scheduler.scan_loop())
        except Exception as e:
            log.log(f"⚠️  定时任务调度器启动失败: {e}")
            self.cron_scheduler = None

    async def cleanup_stuck_messages(self):
        """清理上次崩溃时卡住的消息"""
        try:
            conn = sqlite3.connect(self.config.database_path)
            try:
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
                                       error = 'Bot 重置：AI 响应被标记为已完成（避免重复发送工具调用通知）'
                                   WHERE status = 'ai_started' AND channel_type = ?
                                   """, (ChannelType.DISCORD.value,))

                    affected = cursor.rowcount
                    conn.commit()
                    log.log(f"✅ 已标记 {affected} 条 AI_STARTED 消息为已完成")
                else:
                    log.log("✓ 没有发现 AI_STARTED 状态的消息")
            finally:
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

        embed.add_field(name="🔧 可用命令", value="`/new` - 新会话\n`/status` - 查看状态\n`/abort` - 中止输出\n`/mention` - 切换是否需要 @\n`/split` - 切换换行分割\n`/restart` - 重启服务\n`/stop` - 停止服务\n`下载附件` - 右键消息下载附件", inline=False)

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
                log.log(f"⚠️ 启动通知频道 ID 格式错误: {notification_channel_id}")
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
                        log.log(f"⚠️ 找不到启动通知用户: {notification_user_id}")
                        return
                    except discord.HTTPException as e:
                        log.log(f"⚠️ 获取用户失败: {e}")
                        return

                # 创建或获取 DM 频道
                dm_channel = await user.create_dm()
                await dm_channel.send(embed=embed)
                log.log(f"✅ 已向用户 {user.display_name} 发送启动通知（私聊）")

            except ValueError:
                log.log(f"⚠️ 启动通知用户 ID 格式错误: {notification_user_id}")
            except Exception as e:
                log.log(f"❌ 发送到用户私聊失败: {e}")

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
