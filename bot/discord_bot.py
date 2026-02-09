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
from shared.message_queue import MessageQueue, Message, MessageDirection, MessageStatus


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
        self.pending_messages = {}  # 追踪待处理的消息 {message_id: {"channel": channel, "user_msg": message, "start_time": time}}

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

        # 发送启动通知
        await self.send_startup_notification()

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

        # 创建启动成功消息
        embed = discord.Embed(
            title="🚀 Discord Claude Bridge 启动成功",
            description="桥接系统已就绪，可以开始使用！",
            color=discord.Color.green()
        )

        embed.add_field(name="📝 会话模式", value="`global` (全局共享)", inline=True)
        embed.add_field(name="📂 工作目录", value=f"`{self.config.working_directory}`", inline=True)
        embed.add_field(name="⏱️  超时时间", value=f"{self.config.claude_timeout} 秒", inline=True)

        embed.add_field(name="📋 可用命令", value="`/reset` - 重置会话\n`/status` - 查看状态\n`/restart` - 重启服务", inline=False)

        embed.set_footer(text=f"Bot: {self.user.name} | 启动时间: {discord.utils.format_dt(discord.utils.utcnow(), style='R')}")

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

        @self.tree.command(name="reset", description="重置全局会话，开始新的对话上下文")
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
            embed.add_field(name="会话模式", value="`global` - 全局共享会话", inline=False)

            # 显示 session key 和 session ID
            session_info = f"**Key**: `{session_key}`\n"
            if session_id:
                session_info += f"**ID**: `{session_id}`\n"
            session_info += f"**已创建**: {'是' if session_created else '否'}"
            embed.add_field(name="当前会话", value=session_info, inline=False)

            embed.add_field(name="工作目录", value=f"`{self.config.working_directory}`", inline=False)

            await interaction.response.send_message(embed=embed)

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

            # 执行重启脚本
            import subprocess
            import os

            try:
                # 获取项目根目录
                script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                restart_script = os.path.join(script_dir, 'restart.bat')

                if os.path.exists(restart_script):
                    # 在后台执行重启脚本
                    subprocess.Popen(
                        restart_script,
                        shell=True,
                        cwd=script_dir,
                        creationflags=subprocess.CREATE_NEW_CONSOLE
                    )
                    print(f"✅ 重启脚本已执行: {restart_script}")
                else:
                    await interaction.followup.send(f"❌ 找不到重启脚本 `restart.bat`")
                    print(f"⚠️  重启脚本不存在: {restart_script}")

            except Exception as e:
                await interaction.followup.send(f"❌ 重启失败: {str(e)}")
                print(f"❌ 执行重启脚本时出错: {e}")
                import traceback
                traceback.print_exc()

    async def on_ready(self):
        """Bot 准备就绪"""
        print(f"✓ Bot 已准备就绪!")
        print(f"✓ 在 {len(self.guilds)} 个服务器中")
        print(f"✓ 斜杠命令: /reset, /status, /restart")

    async def on_message(self, message: discord.Message):
        """处理接收到的消息"""
        # 忽略自己的消息
        if message.author == self.user:
            return

        # 检查是否被提及
        if self.user not in message.mentions:
            return

        # 检查频道权限
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

        # 处理消息
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

            # 显示"正在输入"状态
            async with message.channel.typing():
                # 创建消息对象
                msg = Message(
                    id=None,
                    direction=MessageDirection.TO_CLAUDE.value,
                    content=content,
                    status=MessageStatus.PENDING.value,
                    discord_channel_id=message.channel.id,
                    discord_message_id=message.id,
                    discord_user_id=message.author.id,
                    username=message.author.display_name,
                    is_dm=is_dm
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

    async def check_responses(self):
        """定期检查 Claude 的响应和消息状态"""
        await self.wait_until_ready()

        while not self.is_closed():
            try:
                current_time = asyncio.get_event_loop().time()

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
                        if not tracking_info.get("notified_pending_timeout") and elapsed_time > 30:
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

                    # 状态 2: PROCESSING 且无 response - 正在调用 Claude Code
                    elif status == MessageStatus.PROCESSING.value and not response:
                        if not tracking_info.get("notified_processing"):
                            # 首次检测到正在处理
                            try:
                                await tracking_info["confirmation_msg"].edit(
                                    content=f"🔄 消息 #{msg_id} 正在处理中...\n"
                                            f"Claude Code 正在工作，请稍候。"
                                )
                                tracking_info["notified_processing"] = True
                                print(f"🔄 [消息 #{msg_id}] 开始调用 Claude Code")
                            except Exception as e:
                                print(f"⚠️ 无法编辑确认消息: {e}")

                    # 状态 3: PROCESSING 且有 response - 收到响应
                    elif status == MessageStatus.PROCESSING.value and response:
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
                            await tracking_info["channel"].send(
                                f"❌ 消息 #{msg_id} 处理失败\n"
                                f"错误: {error_msg}"
                            )
                        except Exception as e:
                            print(f"⚠️ 无法发送失败提示: {e}")
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

    async def on_close(self):
        """Bot 关闭时的清理"""
        if self.response_check_task:
            self.response_check_task.cancel()


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
