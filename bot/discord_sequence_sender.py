"""
Discord Bot - 消息序列发送模块
负责按顺序发送消息序列（文本、表情包、工具调用卡片等）
"""
import discord
import asyncio
import os
import traceback
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.message_queue import MessageStatus
from shared.logger import get_logger

log = get_logger("DiscordBot", "discord")


class DiscordSequenceSenderMixin:
    """消息序列发送 Mixin"""

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

                        elif item_type == "file":
                            # 文件发送：从 item_data 获取文件路径列表，发送为 Discord 文件
                            file_paths = item_data.get("file_paths", []) if item_data else []
                            valid_files = []
                            for fp in file_paths:
                                if os.path.exists(fp):
                                    valid_files.append(discord.File(fp))

                            if valid_files:
                                try:
                                    await channel.send(files=valid_files)
                                    log.log(f"✅ [消息 #{message_id}] 已发送 {len(valid_files)} 个文件")
                                except Exception as e:
                                    log.log(f"❌ [消息 #{message_id}] 文件发送失败: {e}")
                            else:
                                log.log(f"⚠️ [消息 #{message_id}] 没有有效的文件可发送")

                        # 标记为已发送
                        self.message_queue.mark_sequence_sent(seq_id)

                        # 控制发送速率，避免触发Discord速率限制
                        await asyncio.sleep(self.config.queue_send_interval)

                    except discord.NotFound as e:
                        # 频道/用户不存在，标记消息为失败并清理
                        log.log(f"❌ 消息 #{message_id} 发送失败: 资源不存在 - {e}")
                        self.message_queue.cleanup_message_sequences(message_id)
                        self.message_queue.update_status(message_id, MessageStatus.FAILED, error=f"资源不存在: {e}")
                        traceback.print_exc()
                    except discord.Forbidden as e:
                        # 没有权限，标记消息为失败并清理
                        log.log(f"❌ 消息 #{message_id} 发送失败: 没有权限 - {e}")
                        self.message_queue.cleanup_message_sequences(message_id)
                        self.message_queue.update_status(message_id, MessageStatus.FAILED, error=f"没有权限: {e}")
                        traceback.print_exc()
                    except Exception as e:
                        log.log(f"❌ 发送序列项失败: 消息#{message_id}, 序列#{seq_index}, 错误: {e}")
                        traceback.print_exc()

                except Exception as e:
                    log.log(f"❌ 处理消息序列失败: 消息#{message_id}, 错误: {e}")
                    traceback.print_exc()

                # 极小延迟，避免无消息时CPU空转
                await asyncio.sleep(0.01)

            except Exception as e:
                log.log(f"❌ 检查消息序列时出错: {e}")
                traceback.print_exc()
                await asyncio.sleep(5)
