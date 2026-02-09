"""
Discord Bot 主程序
接收 Discord 消息并转发给 Claude Code
"""
import discord
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

        # 注册命令
        await self.add_commands()

        # 启动响应检查任务
        self.response_check_task = asyncio.create_task(self.check_responses())

        # 发送启动通知
        await self.send_startup_notification()

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

        embed.add_field(name="📝 会话模式", value=f"`{self.config.session_mode}`", inline=True)
        embed.add_field(name="📂 工作目录", value=f"`{self.config.working_directory}`", inline=True)
        embed.add_field(name="⏱️  超时时间", value=f"{self.config.claude_timeout} 秒", inline=True)

        embed.add_field(name="📋 可用命令", value="`!reset` - 重置会话\n`!status` - 查看状态\n`!restart` - 重启服务", inline=False)

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
        """注册 Bot 命令"""

        @self.command(name='reset')
        async def reset_command(ctx: commands.Context):
            """重置当前频道的 Claude 会话"""
            # 检查用户权限
            if self.config.allowed_users:
                if ctx.author.id not in self.config.allowed_users:
                    await ctx.send(f"❌ {ctx.author.mention}，您没有权限执行此操作。")
                    return

            # 获取会话 key
            session_key = self.message_queue.get_session_key(
                self.config.session_mode,
                ctx.channel.id,
                ctx.author.id
            )

            if session_key:
                # 删除会话
                deleted = self.message_queue.delete_session(session_key)
                if deleted:
                    await ctx.send(
                        f"✅ {ctx.author.mention}，会话已重置！\n"
                        f"下次对话将开始新的会话，使用新的工作目录。"
                    )
                    print(f"[会话重置] 用户 {ctx.author.display_name} 重置了会话: {session_key}")
                else:
                    await ctx.send(
                        f"⚠️ {ctx.author.mention}，没有找到活跃的会话。"
                    )
            else:
                await ctx.send(
                    f"ℹ️ {ctx.author.mention}，当前会话模式为 `{self.config.session_mode}`，无需重置。"
                )

        @self.command(name='status')
        async def status_command(ctx: commands.Context):
            """查看当前会话状态"""
            session_key = self.message_queue.get_session_key(
                self.config.session_mode,
                ctx.channel.id,
                ctx.author.id
            )

            mode_desc = {
                'channel': '每个频道独立会话',
                'user': '每个用户独立会话',
                'global': '全局共享会话',
                'none': '无会话保持'
            }

            embed = discord.Embed(
                title="📊 Claude Bridge 状态",
                color=discord.Color.blue()
            )
            embed.add_field(name="会话模式", value=f"`{self.config.session_mode}` - {mode_desc.get(self.config.session_mode, '未知')}", inline=False)
            embed.add_field(name="当前会话", value=f"`{session_key}`" if session_key else "`无`", inline=False)
            embed.add_field(name="工作目录", value=f"`{self.config.working_directory}`", inline=False)

            await ctx.send(embed=embed)

        @self.command(name='restart')
        async def restart_command(ctx: commands.Context):
            """重启 Discord Bridge 服务"""
            # 检查用户权限
            if self.config.allowed_users:
                if ctx.author.id not in self.config.allowed_users:
                    await ctx.send(f"❌ {ctx.author.mention}，您没有权限执行此操作。")
                    return

            # 发送确认消息
            await ctx.send(
                f"🔄 {ctx.author.mention}，正在重启 Discord Bridge 服务...\n"
                f"请稍候，服务将在几秒钟后重新启动。"
            )
            print(f"[重启命令] 用户 {ctx.author.display_name} 触发了服务重启")

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
                    await ctx.send(f"❌ 找不到重启脚本 `restart.bat`")
                    print(f"⚠️  重启脚本不存在: {restart_script}")

            except Exception as e:
                await ctx.send(f"❌ 重启失败: {str(e)}")
                print(f"❌ 执行重启脚本时出错: {e}")
                import traceback
                traceback.print_exc()

    async def on_ready(self):
        """Bot 准备就绪"""
        print(f"✓ Bot 已准备就绪!")
        print(f"✓ 在 {len(self.guilds)} 个服务器中")
        print(f"✓ 命令前缀: @{self.user.name} ")
        print(f"✓ 可用命令: !reset, !status, !restart")

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
