"""
微信 Bot 命令处理模块
处理用户命令
"""
import os
import time
import subprocess
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.message_queue import Message, MessageDirection, MessageStatus, MessageTag, ChannelType
from shared.logger import get_logger

log = get_logger("WeixinBot", "weixin")


class WeixinCommandsMixin:
    """命令处理 Mixin"""

    async def _handle_command(self, from_user_id: str, command: str, account_bot_id: str):
        """处理命令消息

        Args:
            from_user_id: 发送者用户 ID
            command: 命令文本（如 "/new", "/status" 等）
            account_bot_id: 微信账号 bot_id（用于获取 client）
        """
        import time

        # 获取客户端
        client = self.clients.get(account_bot_id)
        if not client:
            await self._send_direct_message(from_user_id, account_bot_id, "❌ 客户端未初始化，请稍后重试")
            return

        # 解析命令和参数
        parts = command.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        # /new - 重置当前用户的会话
        if cmd == "/new":
            await self._cmd_new(from_user_id, account_bot_id)

        # /status - 查看当前会话状态
        elif cmd == "/status":
            await self._cmd_status(from_user_id, account_bot_id)

        # /stop - 停止服务（需要 60 秒内再次确认）
        elif cmd == "/stop":
            await self._cmd_stop(from_user_id, account_bot_id)

        # /restart - 重启服务
        elif cmd == "/restart":
            await self._cmd_restart(from_user_id, account_bot_id)

        # /abort - 中止当前正在处理的响应
        elif cmd == "/abort":
            await self._cmd_abort(from_user_id, account_bot_id)

        # 未知命令
        else:
            help_text = (
                "**📋 可用命令**\n"
                "`/new` - 重置当前会话\n"
                "`/status` - 查看会话状态\n"
                "`/abort` - 中止当前响应\n"
                "`/stop` - 停止服务（需确认）\n"
                "`/restart` - 重启服务"
            )
            await self._send_direct_message(from_user_id, account_bot_id, help_text)

    async def _cmd_new(self, from_user_id: str, account_bot_id: str):
        """重置当前用户的会话"""
        # 从配置中获取 user_id
        user_id_int = self.username_to_userid.get(from_user_id)
        if user_id_int is None:
            await self._send_direct_message(from_user_id, account_bot_id, f"**⚠️ 未找到用户配置**\n\n用户 `{from_user_id}` 的配置不存在")
            return

        # 获取当前会话
        session_key, old_session_id, _, working_dir = self.message_queue.get_or_create_session(
            self.config.working_directory,
            channel_id=None,
            user_id=user_id_int,
            is_dm=True,
            use_temp_session=False,
            temp_session_key=None
        )

        # /new 前自动发送提示词
        if self.config.auto_trigger_before_new_enabled:
            preset_msg = self.config.auto_trigger_before_new_message
            if preset_msg:
                auto_msg = Message(
                    id=None,
                    direction=MessageDirection.TO_CLAUDE.value,
                    content=preset_msg,
                    status=MessageStatus.PENDING.value,
                    discord_channel_id=0,
                    discord_message_id=0,
                    discord_user_id=user_id_int,
                    username=from_user_id,
                    is_dm=True,
                    tag=MessageTag.DEFAULT.value,
                    channel_type=ChannelType.WEIXIN.value,
                    attachments=[]
                )
                auto_message_id = self.message_queue.add_message(auto_msg)
                log.log(f"[自动触发] 已发送预设消息 #{auto_message_id} 到当前会话: {preset_msg[:50]}...")

                # 等待消息处理完成后再删除会话，否则消息会被路由到新会话
                max_wait = 120
                waited = 0
                while waited < max_wait:
                    await asyncio.sleep(1)
                    waited += 1
                    status = self.message_queue.get_message_status(auto_message_id)
                    if status in (MessageStatus.COMPLETED, MessageStatus.FAILED):
                        log.log(f"[自动触发] 消息 #{auto_message_id} 已处理完成 (状态: {status.value}, 等待 {waited}秒)")
                        break
                else:
                    log.log(f"[自动触发] 消息 #{auto_message_id} 等待超时 ({max_wait}秒)，继续执行 /new")

        # 删除会话
        deleted = self.message_queue.delete_session(session_key, working_dir)

        # 重新获取会话（应该生成新的 session_id）
        session_key, new_session_id, session_created, _ = self.message_queue.get_or_create_session(
            self.config.working_directory,
            channel_id=None,
            user_id=user_id_int,
            is_dm=True,
            use_temp_session=False,
            temp_session_key=None
        )

        if deleted:
            msg = (
                f"**✅ 会话已重置**\n\n"
                f"旧 Session ID: `{old_session_id[:8]}...` (已删除)\n"
                f"新 Session ID: `{new_session_id[:8]}...`\n\n"
                f"下次对话将使用新的会话 ID 创建全新上下文。"
            )
            log.log(f"[会话重置] 用户 {from_user_id} 重置了私聊会话")
            log.log(f"[会话重置] Session Key: {session_key}")
            log.log(f"[会话重置] 旧 Session ID: {old_session_id} -> 新 Session ID: {new_session_id}")
        else:
            msg = (
                f"**⚠️ 没有活跃会话**\n\n"
                f"当前 Session ID: `{new_session_id[:8]}...`"
            )

        await self._send_direct_message(from_user_id, account_bot_id, msg)

        # /new 后自动触发对话
        if self.config.auto_trigger_after_new_enabled:
            preset_msg = self.config.auto_trigger_after_new_message
            if preset_msg:
                auto_msg = Message(
                    id=None,
                    direction=MessageDirection.TO_CLAUDE.value,
                    content=preset_msg,
                    status=MessageStatus.PENDING.value,
                    discord_channel_id=0,
                    discord_message_id=0,
                    discord_user_id=user_id_int,
                    username=from_user_id,
                    is_dm=True,
                    tag=MessageTag.DEFAULT.value,
                    channel_type=ChannelType.WEIXIN.value,
                    attachments=[]
                )
                auto_message_id = self.message_queue.add_message(auto_msg)
                log.log(f"[自动触发] 已发送预设消息 #{auto_message_id} 到新会话: {preset_msg[:50]}...")

    async def _cmd_status(self, from_user_id: str, account_bot_id: str):
        """查看当前会话状态"""
        # 从配置中获取 user_id
        user_id_int = self.username_to_userid.get(from_user_id)
        if user_id_int is None:
            await self._send_direct_message(from_user_id, account_bot_id, f"**⚠️ 未找到用户配置**\n\n用户 `{from_user_id}` 的配置不存在")
            return

        # 获取会话信息
        session_key, session_id, session_created, working_dir = self.message_queue.get_or_create_session(
            self.config.working_directory,
            channel_id=None,
            user_id=user_id_int,
            is_dm=True,
            use_temp_session=False,
            temp_session_key=None
        )

        msg = (
            f"**📊 IM-Claude-Bridge 状态**\n\n"
            f"**📋 会话类型**\n私聊会话\n\n"
            f"**{'✅' if session_created else '⏳'} 当前会话**\n"
            f"Session ID: `{session_id[:8] if session_id else '未生成'}...`\n"
            f"状态: {'已创建' if session_created else '未创建'}\n\n"
            f"**📂 工作目录**\n`{working_dir}`\n\n"
            f"**💬 对话模式**\n不需要 @（私聊）"
        )

        await self._send_direct_message(from_user_id, account_bot_id, msg)

    async def _cmd_stop(self, from_user_id: str, account_bot_id: str):
        """停止服务（需要 60 秒内再次确认）"""
        import time
        import subprocess
        import os

        current_time = time.time()

        # 检查是否有未过期的停止请求
        if from_user_id in self.stop_requests:
            request_time = self.stop_requests[from_user_id]
            time_diff = current_time - request_time

            if time_diff <= 60:  # 60 秒内再次使用 /stop
                # 确认停止
                del self.stop_requests[from_user_id]

                msg = "**🛑 正在停止服务**\n\n服务将在几秒钟后停止。"
                await self._send_direct_message(from_user_id, account_bot_id, msg)
                log.log(f"[停止命令] 用户 {from_user_id} 确认停止服务")

                # 执行停止脚本
                try:
                    script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                    stop_script = os.path.join(script_dir, 'stop.bat')

                    if os.path.exists(stop_script):
                        subprocess.Popen(
                            ["cmd", "/c", stop_script],
                            cwd=script_dir,
                            creationflags=subprocess.CREATE_NO_WINDOW
                        )
                        log.log(f"✅ 停止命令已执行: stop.bat")
                    else:
                        msg = f"**❌ 文件未找到**\n\n找不到 `stop.bat` 文件"
                        await self._send_direct_message(from_user_id, account_bot_id, msg)

                except Exception as e:
                    msg = f"**❌ 停止失败**\n\n错误: `{str(e)}`"
                    await self._send_direct_message(from_user_id, account_bot_id, msg)
                    log.log(f"❌ 执行停止命令时出错: {e}")

                return

        # 第一次使用 /stop，记录请求
        self.stop_requests[from_user_id] = current_time

        msg = (
            "**⚠️ 确认停止服务**\n\n"
            "此操作将停止 Bot 和 Bridge，服务将不再响应消息。\n\n"
            "如需确认，请在 **60 秒内** 再次使用 `/stop` 命令"
        )
        await self._send_direct_message(from_user_id, account_bot_id, msg)

    async def _cmd_restart(self, from_user_id: str, account_bot_id: str):
        """重启服务"""
        import subprocess
        import os

        msg = "**🔄 正在重启服务**\n\n请稍候，服务将在几秒钟后重新启动。"
        await self._send_direct_message(from_user_id, account_bot_id, msg)
        log.log(f"[重启命令] 用户 {from_user_id} 触发了服务重启")

        try:
            script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            restart_script = os.path.join(script_dir, 'restart.bat')

            if os.path.exists(restart_script):
                subprocess.Popen(
                    ["cmd", "/c", restart_script],
                    cwd=script_dir,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                log.log(f"✅ 重启命令已执行: restart.bat")
            else:
                msg = "**❌ 文件未找到**\n\n找不到 `restart.bat` 文件"
                await self._send_direct_message(from_user_id, account_bot_id, msg)

        except Exception as e:
            msg = f"**❌ 重启失败**\n\n错误: `{str(e)}`"
            await self._send_direct_message(from_user_id, account_bot_id, msg)
            log.log(f"❌ 执行重启命令时出错: {e}")

    async def _cmd_abort(self, from_user_id: str, account_bot_id: str):
        """中止当前正在处理的响应"""
        # 查找正在处理的消息（匹配发送命令的私聊或群聊）
        processing_messages = self.message_queue.get_processing_messages(
            channel_type=ChannelType.WEIXIN.value,
            user_id=from_user_id
        )

        if not processing_messages:
            msg = "**⚠️ 没有正在处理的响应**\n\n当前没有正在处理的 Claude 响应。"
            await self._send_direct_message(from_user_id, account_bot_id, msg)
            return

        # 请求中止第一个处理中的消息
        message_to_abort = processing_messages[0]
        success = self.message_queue.request_abort(message_to_abort.id)

        if success:
            # 停止正在输入状态
            await self.stop_typing_indicator(message_to_abort.id)

            msg = (
                f"**🛑 已请求中止**\n\n"
                f"已请求中止消息 `#{message_to_abort.id}` 的处理\n"
                f"Claude 响应将在几秒内停止..."
            )
            await self._send_direct_message(from_user_id, account_bot_id, msg)
            log.log(f"[中止命令] 用户 {from_user_id} 请求中止消息 #{message_to_abort.id}")
        else:
            msg = "**❌ 中止请求失败**\n\n中止请求失败，请稍后重试。"
            await self._send_direct_message(from_user_id, account_bot_id, msg)

    async def _send_direct_message(self, to_user_id: str, account_bot_id: str, text: str):
        """直接发送消息到微信（绕过消息队列）

        Args:
            to_user_id: 接收者用户 ID
            account_bot_id: 微信账号 bot_id
            text: 消息文本
        """
        client = self.clients.get(account_bot_id)
        if not client:
            raise Exception(f"账号 {account_bot_id} 的客户端未初始化")

        context_token = self.context_tokens.get(to_user_id) or ""
        if not context_token:
            raise Exception(f"context_token is required but missing for user {to_user_id}")

        await client.send_message(
            to_user_id=to_user_id,
            text=text,
            context_token=context_token
        )