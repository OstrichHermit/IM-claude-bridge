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

    async def setup_hook(self):
        """Bot 启动后的钩子"""
        print(f"Bot 已启动，登录为 {self.user}")

        # 注册命令
        await self.add_commands()

        # 启动响应检查任务
        self.response_check_task = asyncio.create_task(self.check_responses())

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

    async def on_ready(self):
        """Bot 准备就绪"""
        print(f"✓ Bot 已准备就绪!")
        print(f"✓ 在 {len(self.guilds)} 个服务器中")
        print(f"✓ 命令前缀: @{self.user.name} ")
        print(f"✓ 可用命令: !reset, !status")

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

                # 添加到消息队列
                message_id = self.message_queue.add_message(msg)

                print(f"[消息 #{message_id}] 收到来自 {message.author.display_name} 的消息: {content[:50]}... ({'私聊' if is_dm else '频道'})")

                # 发送确认消息
                await message.reply(
                    f"✅ 消息已接收！正在转发给 Claude Code...\n"
                    f"消息 ID: {message_id}"
                )

                # 更新消息状态为处理中
                self.message_queue.update_status(message_id, MessageStatus.PROCESSING)

        except Exception as e:
            print(f"❌ 处理消息时出错: {e}")
            import traceback
            traceback.print_exc()
            await message.channel.send(f"❌ 处理消息时出错: {str(e)}")

    async def check_responses(self):
        """定期检查 Claude 的响应"""
        await self.wait_until_ready()

        while not self.is_closed():
            try:
                # 直接查询数据库获取待发送的响应
                import sqlite3
                conn = sqlite3.connect(self.config.database_path)
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT m.id, m.discord_channel_id, m.discord_message_id,
                           m.response, m.username, m.content, m.is_dm, m.discord_user_id
                    FROM messages m
                    WHERE m.direction = ? AND m.status = ?
                    ORDER BY m.created_at ASC
                """, (MessageDirection.TO_CLAUDE.value, MessageStatus.PROCESSING.value))

                rows = cursor.fetchall()
                conn.close()

                for row in rows:
                    msg_id, channel_id, original_msg_id, response, username, content, is_dm, user_id = row

                    if response:  # 如果有响应
                        try:
                            # 区分私聊和频道消息
                            if is_dm:
                                # 私聊：通过用户获取 DM 频道（使用 fetch_user 从 API 获取）
                                user = self.get_user(user_id)
                                if not user:
                                    try:
                                        user = await self.fetch_user(user_id)
                                    except discord.NotFound:
                                        print(f"⚠️  找不到用户 {user_id}")
                                        continue
                                    except discord.HTTPException as e:
                                        print(f"⚠️  获取用户 {user_id} 失败: {e}")
                                        continue
                                # 创建或获取 DM 频道
                                channel = await user.create_dm()
                            else:
                                # 服务器频道：直接获取频道
                                channel = self.get_channel(channel_id)
                                if not channel:
                                    print(f"⚠️  找不到频道 {channel_id}")
                                    continue

                            # Discord Embed 字段值长度限制为 1024 字符
                            # 描述长度限制为 4096 字符
                            max_desc_length = 4000  # Embed 描述留一些余量
                            max_field_length = 1000  # Embed 字段留一些余量

                            # 创建 Embed
                            embed = discord.Embed(
                                title=f"✨ Claude Code 的回复",
                                description=f"消息 ID: {msg_id}",
                                color=discord.Color.green()
                            )

                            # 分割长响应
                            if len(response) <= max_desc_length:
                                # 短消息，直接放在描述中
                                embed.description = f"**消息 ID: {msg_id}**\n\n{response}"
                                await channel.send(embed=embed)
                            else:
                                # 长消息，分割成多个字段
                                chunks = []
                                current_chunk = ""
                                lines = response.split('\n')

                                for line in lines:
                                    # 尝试按行分割
                                    if len(current_chunk) + len(line) + 1 <= max_field_length:
                                        current_chunk += line + '\n'
                                    else:
                                        if current_chunk:
                                            chunks.append(current_chunk)
                                        current_chunk = line + '\n'

                                if current_chunk:
                                    chunks.append(current_chunk)

                                # 第一个分块放在描述中
                                if chunks:
                                    embed.description = f"**消息 ID: {msg_id}**\n\n{chunks[0]}"
                                    chunks.pop(0)

                                # 后续分块作为字段添加（最多 25 个字段）
                                for i, chunk in enumerate(chunks[:25], 1):
                                    embed.add_field(
                                        name=f"续 ({i}/{len(chunks)})" if len(chunks) > 1 else "续",
                                        value=chunk,
                                        inline=False
                                    )

                                await channel.send(embed=embed)

                                # 如果还有剩余内容（超过 25 个字段），需要额外的 Embed
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
                            self.message_queue.update_status(
                                msg_id,
                                MessageStatus.COMPLETED
                            )

                            print(f"[消息 #{msg_id}] 已发送响应到 Discord")

                        except Exception as e:
                            print(f"❌ 发送响应时出错: {e}")
                            import traceback
                            traceback.print_exc()
                            self.message_queue.update_status(
                                msg_id,
                                MessageStatus.FAILED,
                                error=str(e)
                            )

                # 等待一段时间再检查
                await asyncio.sleep(self.config.poll_interval / 1000)

            except Exception as e:
                print(f"❌ 检查响应时出错: {e}")
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
