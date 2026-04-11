"""
微信 Bot 轮询任务模块
处理工具执行结果检查和 typing indicator
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.logger import get_logger

log = get_logger("WeixinBot", "weixin")


class WeixinPollersMixin:
    """轮询任务 Mixin"""

    async def check_tool_use_results(self):
        """定期检查工具执行结果并发送工具调用通知"""
        while self.running:
            try:
                # 检查是否启用微信工具调用通知
                if not self.config.weixin_tool_use_notification_enabled:
                    # 禁用时跳过处理，但继续运行循环以便配置更改后能生效
                    await asyncio.sleep(5)
                    continue

                # 获取待处理的工具执行结果（只处理微信频道的）
                pending_results = self.message_queue.get_pending_tool_use_results(channel_type='weixin')

                for result in pending_results:
                    message_id = result['message_id']
                    tool_use_index = result['tool_use_index']
                    success = result['success']

                    try:
                        # 从数据库获取消息信息
                        import sqlite3
                        conn = sqlite3.connect(self.config.database_path)
                        cursor = conn.cursor()
                        cursor.execute("""
                                       SELECT username, discord_channel_id, context_token
                                       FROM messages
                                       WHERE id = ?
                                       """, (message_id,))
                        row = cursor.fetchone()
                        conn.close()

                        if not row:
                            # 找不到消息信息，标记为已处理
                            self.message_queue.mark_tool_use_result_processed(message_id, tool_use_index)
                            continue

                        username, channel_id, msg_context_token = row

                        # 根据用户名选择正确的账号
                        if not self.accounts:
                            continue

                        # 检查 username 是配置的用户名还是原始 wxid
                        target_account = None
                        to_user_id = username  # 默认使用原始 username

                        if username in self.username_to_wxid:
                            # username 是配置的用户名（如 "用户名"）
                            # 从用户名获取对应的 wxid
                            target_wxid = self.username_to_wxid.get(username)
                            # 找到包含该 wxid 的账号
                            for account in self.accounts:
                                if account.wxid == target_wxid:
                                    target_account = account
                                    break
                            # 使用配置的用户名（send_message 会自动转换为 wxid）
                            to_user_id = username
                        elif len(self.accounts) == 1:
                            # 只有一个账号，直接使用它
                            target_account = self.accounts[0]
                            # username 是原始 wxid（外部联系人），直接使用
                            to_user_id = username
                        else:
                            # 多个账号，无法确定使用哪个
                            # 检查是否有 context_token，如果有则使用第一个有 token 的账号
                            if self.context_tokens.get(username) is not None:
                                # 有 context_token，使用第一个账号
                                target_account = self.accounts[0]
                                to_user_id = username
                            else:
                                # 没有 context_token，跳过这条消息
                                # 标记为已处理，避免重复处理
                                self.message_queue.mark_tool_use_result_processed(message_id, tool_use_index)
                                continue

                        if not target_account:
                            # 标记为已处理
                            self.message_queue.mark_tool_use_result_processed(message_id, tool_use_index)
                            continue

                        client = self.clients.get(target_account.bot_id)
                        if not client:
                            # 标记为已处理
                            self.message_queue.mark_tool_use_result_processed(message_id, tool_use_index)
                            continue

                        # 获取 context_token
                        # 优先从缓存获取，如果没有则使用消息保存的 context_token
                        context_token = self.context_tokens.get(username) or msg_context_token or ""

                        if not context_token:
                            # 标记为已处理，避免重复处理
                            self.message_queue.mark_tool_use_result_processed(message_id, tool_use_index)
                            continue

                        # 获取工具调用信息
                        tool_uses = self.message_queue.get_tool_uses(message_id)
                        if tool_use_index >= len(tool_uses):
                            # 标记为已处理
                            self.message_queue.mark_tool_use_result_processed(message_id, tool_use_index)
                            continue

                        tool_use = tool_uses[tool_use_index]
                        tool_name = tool_use.get('name', '')
                        tool_input = tool_use.get('input', {})

                        # 构建工具调用通知文本
                        status_emoji = "✅" if success else "❌"

                        # 从配置文件读取工具 emoji 映射
                        TOOL_EMOJIS = self.config.tool_emoji_mapping

                        # 检查是否是 MCP 工具
                        is_mcp = tool_name.startswith('mcp__')

                        if is_mcp:
                            # MCP 工具：提取服务器名和工具名
                            parts = tool_name.split('__')
                            if len(parts) >= 3:
                                mcp_server = parts[1]
                                mcp_tool = parts[2]

                                emoji = TOOL_EMOJIS.get(tool_name)
                                if emoji is None:
                                    emoji = TOOL_EMOJIS.get(mcp_server, "🔧")

                                tool_title = f"{emoji} MCP {mcp_server}"
                                tool_desc = mcp_tool
                            else:
                                emoji = TOOL_EMOJIS.get(tool_name, "🔧")
                                tool_title = f"{emoji} {tool_name}"
                                tool_desc = "无参数"
                        else:
                            emoji = TOOL_EMOJIS.get(tool_name, "🔧")
                            tool_title = f"{emoji} {tool_name}"

                            # 智能显示参数（为每个工具定制显示内容）
                            tool_desc = None

                            if tool_name == 'Read':
                                tool_desc = tool_input.get('file_path', '无路径')
                            elif tool_name == 'Write':
                                tool_desc = tool_input.get('file_path', '无路径')
                            elif tool_name == 'Edit':
                                tool_desc = tool_input.get('file_path', '无路径')
                            elif tool_name == 'Glob':
                                pattern = tool_input.get('pattern', '无 pattern')
                                path = tool_input.get('path', '')
                                if path:
                                    tool_desc = f"{path}: {pattern}"
                                else:
                                    tool_desc = pattern
                            elif tool_name == 'Grep':
                                tool_desc = tool_input.get('pattern', '无 pattern')
                            elif tool_name == 'Bash':
                                cmd = tool_input.get('command', '')
                                if len(cmd) > 100:
                                    cmd = cmd[:97] + "..."
                                tool_desc = cmd
                            elif tool_name == 'WebSearch':
                                tool_desc = tool_input.get('query', '无 query')
                            elif tool_name == 'Skill':
                                tool_desc = tool_input.get('skill', '无 skill')
                            elif tool_name == 'Agent':
                                desc = tool_input.get('description', '')
                                subagent = tool_input.get('subagent_type', 'general-purpose')
                                tool_desc = f"{subagent}: {desc}"
                            elif tool_name == 'EnterPlanMode':
                                tool_desc = "进入计划模式"
                            elif tool_name == 'ExitPlanMode':
                                tool_desc = "退出计划模式"
                            elif 'prompt' in tool_input:
                                tool_desc = tool_input['prompt']
                                if len(tool_desc) > 50:
                                    tool_desc = tool_desc[:47] + "..."
                            else:
                                tool_desc = "无参数"

                        # 构建通知文本（只显示状态 emoji，不显示"成功"/"失败"文字）
                        notification_text = f"{status_emoji} {tool_title}"

                        if tool_desc and tool_desc != "无参数":
                            notification_text += f"\n{tool_desc}"

                        # 确保 typing_ticket 存在（如果不存在，自动获取）
                        if username not in self.typing_tickets:
                            try:
                                wxid = self.username_to_wxid.get(username, username)
                                config_result = await client.get_config(
                                    ilink_user_id=wxid,
                                    context_token=context_token or ""
                                )
                                typing_ticket = config_result.get("typing_ticket", "")
                                if typing_ticket:
                                    self.typing_tickets[username] = typing_ticket
                            except Exception as e:
                                pass

                        # 发送通知到微信
                        try:
                            await client.send_message(
                                to_user_id=to_user_id,
                                text=notification_text,
                                context_token=context_token
                            )
                            log.log(f"🔧 [消息 #{message_id}] 已发送工具调用通知: {tool_name} - {'成功' if success else '失败'}")
                        except Exception as send_error:
                            log.log(f"❌ [消息 #{message_id}] 发送工具调用通知失败: {send_error}")
                            # 标记为已处理，避免无限重试
                            self.message_queue.mark_tool_use_result_processed(message_id, tool_use_index)
                            continue

                    except Exception as e:
                        log.log(f"❌ 发送工具调用通知失败: 消息#{message_id}, 工具#{tool_use_index}, 错误: {e}")

                    # 标记为已处理
                    self.message_queue.mark_tool_use_result_processed(message_id, tool_use_index)

                # 等待一段时间再检查（1秒）
                await asyncio.sleep(1)

            except Exception as e:
                log.log(f"❌ 检查工具执行结果时出错: {e}")
                await asyncio.sleep(5)

    async def _maintain_typing_indicator(self, client: "WeixinClient", ilink_user_id: str, typing_ticket: str, stop_event: asyncio.Event):
        """
        维持 typing indicator（正在输入状态）

        使用持续刷新模式，每 8 秒刷新一次（微信 typing ticket 默认持续 10 秒）

        Args:
            client: 微信客户端
            ilink_user_id: 用户 ID（原始 wxid）
            typing_ticket: typing 票据
            stop_event: 停止事件
        """
        retry_count = 0
        max_retries = 3
        retry_delay = 5

        try:
            while self.running and not stop_event.is_set():
                try:
                    # 微信 typing indicator 默认持续 10 秒
                    # 我们每 8 秒刷新一次，确保有足够余量避免中断
                    await client.send_typing(
                        ilink_user_id=ilink_user_id,
                        typing_ticket=typing_ticket,
                        status=1  # 1 = 正在输入
                    )
                    # 使用 wait_for 来响应停止事件，最多等待 8 秒
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=8)
                        # stop_event 被设置，退出循环
                        break
                    except asyncio.TimeoutError:
                        # 8 秒超时，继续下一轮循环
                        pass

                    # 成功完成一次循环，重置重试计数
                    retry_count = 0

                except asyncio.CancelledError:
                    # 任务被取消，正常退出
                    break
                except Exception as e:
                    retry_count += 1

                    if retry_count >= max_retries:
                        break
                    # 使用 wait_for 来响应停止事件
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=retry_delay)
                        break
                    except asyncio.TimeoutError:
                        pass

        except asyncio.CancelledError:
            # 任务被取消，正常退出
            pass

    def start_typing_indicator(self, message_id: int, from_user_id: str, account_bot_id: str):
        """
        启动指定消息对应的 typing indicator 任务

        Args:
            message_id: 消息记录在数据库中的唯一 ID
            from_user_id: 用户名（如"用户名"）
            account_bot_id: 微信账号 bot_id
        """
        client = self.clients.get(account_bot_id)
        if not client:
            return

        # 获取用户的原始 wxid
        wxid = self.username_to_wxid.get(from_user_id)
        if not wxid:
            return

        # 检查是否已经有 typing ticket
        typing_ticket = self.typing_tickets.get(from_user_id)
        if not typing_ticket:
            return

        # 创建停止事件
        stop_event = asyncio.Event()

        # 创建 typing indicator 任务
        typing_task = asyncio.create_task(
            self._maintain_typing_indicator(client, wxid, typing_ticket, stop_event)
        )

        # 保存到 pending_messages
        self.pending_messages[message_id] = {
            "typing_task": typing_task,
            "typing_stop_event": stop_event,
            "typing_active": True,
            "from_user_id": from_user_id,
            "account_bot_id": account_bot_id
        }

    async def stop_typing_indicator(self, message_id: int):
        """
        停止指定消息对应的 typing indicator 任务

        Args:
            message_id: 消息记录在数据库中的唯一 ID
        """
        # 从 pending_messages 字典中找到这个消息记录并取消它的 typing_task
        if message_id in self.pending_messages:
            msg_info = self.pending_messages[message_id]
            task = msg_info.get("typing_task")
            stop_event = msg_info.get("typing_stop_event")
            from_user_id = msg_info.get("from_user_id")
            account_bot_id = msg_info.get("account_bot_id")

            # 检查是否已经在停止状态
            if not msg_info.get("typing_active", False):
                # 已经停止，静默返回
                return

            # 首先设置停止事件，这会立即停止 _maintain_typing_indicator 循环
            if stop_event:
                stop_event.set()

            # 然后发送取消状态给微信 API
            if from_user_id and account_bot_id:
                client = self.clients.get(account_bot_id)
                typing_ticket = self.typing_tickets.get(from_user_id)
                if client and typing_ticket:
                    try:
                        wxid = self.username_to_wxid.get(from_user_id)
                        if wxid:
                            await client.send_typing(
                                ilink_user_id=wxid,
                                typing_ticket=typing_ticket,
                                status=2  # 2 = 取消输入
                            )
                    except Exception as e:
                        pass

            # 取消任务（如果还在运行）
            if task and not task.done():
                task.cancel()  # 这会触发 _maintain_typing_indicator 中的 CancelledError
                try:
                    await task  # 等待任务完全停止
                except asyncio.CancelledError:
                    pass

            # 更新状态为已停止
            msg_info["typing_active"] = False
            msg_info["typing_task"] = None
        else:
            # 消息不在缓存中，可能已经被清理，静默返回
            pass