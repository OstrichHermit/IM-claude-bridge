"""
消息队列系统
用于 Discord Bot 和 Claude Code 桥接服务之间的通信
"""
import sqlite3
import json
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from dataclasses import dataclass, asdict
from enum import Enum

from shared.logger import get_logger

log = get_logger("MessageQueue", "bridge")


class MessageStatus(Enum):
    """消息状态枚举"""
    PENDING = "pending"      # 等待处理
    QUEUED = "queued"        # 已加入处理队列（防止重复扫描）
    PROCESSING = "processing" # 正在处理
    AI_STARTED = "ai_started" # AI 开始工作（新的中间状态）
    ABORTING = "aborting"     # 正在中止
    COMPLETED = "completed"   # 已完成
    FAILED = "failed"        # 失败
    SKIPPED = "skipped"      # 已跳过（服务重启时清理旧消息）


class MessageDirection(Enum):
    """消息方向枚举"""
    TO_CLAUDE = "to_claude"    # Discord -> Claude
    TO_DISCORD = "to_discord"  # Claude -> Discord


class FileDownloadRequestStatus(Enum):
    """文件下载请求状态枚举"""
    PENDING = "pending"        # 等待处理
    PROCESSING = "processing"  # 正在处理
    COMPLETED = "completed"    # 已完成
    FAILED = "failed"          # 失败


class MessageRequestStatus(Enum):
    """纯文本消息发送请求状态枚举"""
    PENDING = "pending"        # 等待处理
    PROCESSING = "processing"  # 正在处理
    COMPLETED = "completed"    # 已完成
    FAILED = "failed"          # 失败


class MessageTag(Enum):
    """消息标签枚举"""
    DEFAULT = "default"          # 默认标签
    TASK = "task"                # 任务消息
    REMINDER = "reminder"          # 提醒消息


class ChannelType(Enum):
    """频道类型枚举"""
    DISCORD = "discord"          # Discord 频道
    WEIXIN = "weixin"            # 微信频道


@dataclass
class AttachmentInfo:
    """附件信息数据类"""
    id: int  # Discord 附件 ID（唯一标识）
    filename: str  # Discord 原始文件名
    size: int  # 文件大小（字节）
    url: str  # 文件 URL
    local_filename: Optional[str] = None  # 本地实际文件名
    description: Optional[str] = None  # 文件描述


@dataclass
class Message:
    """消息数据类"""
    id: Optional[int]
    direction: str
    content: str
    status: str
    discord_channel_id: int
    discord_message_id: int
    discord_user_id: int
    username: str
    response: Optional[str] = None
    error: Optional[str] = None
    is_dm: bool = False  # 是否为私聊消息
    is_external: bool = False  # 是否为外部插入的消息（非真实 Discord 消息）
    tag: str = MessageTag.DEFAULT.value  # 消息标签
    channel_type: str = ChannelType.DISCORD.value  # 频道类型（discord/weixin）
    context_token: Optional[str] = None  # 微信消息上下文 token（用于回复）
    attachments: Optional[List[AttachmentInfo]] = None  # 附件信息列表
    streaming_response: Optional[str] = None  # 流式响应内容
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict:
        """转换为字典"""
        return asdict(self)


@dataclass
class FileDownloadRequest:
    """文件下载请求数据类"""
    id: Optional[int]
    discord_message_id: int  # Discord 消息 ID
    discord_channel_id: int  # Discord 频道/私聊 ID
    save_directory: str  # 保存目录路径
    status: str  # 请求状态
    downloaded_files: Optional[str] = None  # 已下载文件列表（JSON 格式）
    error: Optional[str] = None  # 错误信息
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict:
        """转换为字典"""
        return asdict(self)


@dataclass
class MessageRequest:
    """纯文本消息发送请求数据类"""
    id: Optional[int] = None
    content: str = ""  # 消息内容
    user_id: Optional[int] = None  # Discord 用户 ID（发送私聊）
    channel_id: Optional[int] = None  # Discord 频道 ID（发送到频道）
    use_embed: bool = True  # 是否使用 Embed 格式
    embed_title: Optional[str] = None  # Embed 标题
    embed_color: Optional[int] = None  # Embed 颜色（十进制）
    tag: str = MessageTag.DEFAULT.value  # 消息标签
    status: str = MessageRequestStatus.PENDING.value  # 请求状态
    result: Optional[str] = None  # 执行结果（JSON 格式）
    error: Optional[str] = None  # 错误信息
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict:
        """转换为字典"""
        return asdict(self)


class MessageQueue:
    """消息队列管理器（精简版，只做编排和委托）"""

    def __init__(self, db_path: str):
        """初始化消息队列"""
        self.db_path = db_path
        self._init_database()

        # 初始化各个 Manager
        from shared.session_manager import SessionManager
        from shared.tool_use_tracker import ToolUseTracker
        from shared.sequence_manager import MessageSequenceManager

        self._sessions = SessionManager(db_path)
        self._tool_uses = ToolUseTracker(db_path)
        self._sequences = MessageSequenceManager(db_path)

    def _init_database(self):
        """初始化数据库表"""
        from shared.schema import SchemaManager
        SchemaManager(self.db_path).init_database()

    # ========== 属性：代理到各 Manager ==========

    @property
    def sessions(self):
        """会话管理器"""
        return self._sessions

    @property
    def tool_uses(self):
        """工具调用追踪器"""
        return self._tool_uses

    @property
    def sequences(self):
        """消息序列管理器"""
        return self._sequences

    def add_message(self, message: Message) -> int:
        """添加新消息到队列"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        now = datetime.now().isoformat()
        message.created_at = now
        message.updated_at = now

        # 将附件信息转换为 JSON 字符串
        attachments_json = None
        if message.attachments:
            attachments_list = [
                {
                    "id": a.id,
                    "filename": a.filename,
                    "local_filename": a.local_filename,
                    "size": a.size,
                    "url": a.url,
                    "description": a.description
                }
                for a in message.attachments
            ]
            attachments_json = json.dumps(attachments_list)

        cursor.execute("""
            INSERT INTO messages (
                direction, content, status,
                discord_channel_id, discord_message_id,
                discord_user_id, username,
                response, error, is_dm, is_external, tag, channel_type, context_token, attachments, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            message.direction,
            message.content,
            message.status,
            message.discord_channel_id,
            message.discord_message_id,
            message.discord_user_id,
            message.username,
            message.response,
            message.error,
            1 if message.is_dm else 0,
            1 if message.is_external else 0,
            message.tag,
            message.channel_type,
            message.context_token,
            attachments_json,
            message.created_at,
            message.updated_at
        ))

        message_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return message_id


    def get_pending_messages_by_session(self) -> dict:
        """
        获取所有 PENDING 消息，按 session_key 分组

        用于并发处理架构：不同 session 的消息可以并发处理

        Returns:
            {session_key: [Message, ...]} 按会话分组的消息字典
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 查询所有 PENDING 消息
        cursor.execute("""
            SELECT id, direction, content, status,
                   discord_channel_id, discord_message_id,
                   discord_user_id, username,
                   response, error, is_dm, is_external, tag, channel_type, context_token, attachments, created_at, updated_at
            FROM messages
            WHERE status = ? AND direction = ?
            ORDER BY created_at ASC
        """, (MessageStatus.PENDING.value, MessageDirection.TO_CLAUDE.value))

        rows = cursor.fetchall()
        conn.close()

        # 按 session_key 分组
        messages_by_session = {}
        for row in rows:
            # 解析消息对象
            attachments = None
            if row[15]:  # attachments 索引变化了
                try:
                    attachments_data = json.loads(row[15])
                    attachments = [
                        AttachmentInfo(
                            id=a["id"],
                            filename=a["filename"],
                            local_filename=a.get("local_filename"),
                            size=a["size"],
                            url=a["url"],
                            description=a.get("description")
                        )
                        for a in attachments_data
                    ]
                except (json.JSONDecodeError, KeyError):
                    pass

            message = Message(
                id=row[0],
                direction=row[1],
                content=row[2],
                status=row[3],
                discord_channel_id=row[4],
                discord_message_id=row[5],
                discord_user_id=row[6],
                username=row[7],
                response=row[8],
                error=row[9],
                is_dm=bool(row[10]),
                is_external=bool(row[11]),
                tag=row[12] or MessageTag.DEFAULT.value,
                channel_type=row[13] or ChannelType.DISCORD.value,
                context_token=row[14],
                attachments=attachments,
                created_at=row[16],
                updated_at=row[17]
            )

            # 计算 session_key
            session_key = self._calculate_session_key(message)

            if session_key not in messages_by_session:
                messages_by_session[session_key] = []
            messages_by_session[session_key].append(message)

        return messages_by_session

    def _calculate_session_key(self, message: Message) -> str:
        """
        计算消息的 session_key

        Args:
            message: 消息对象

        Returns:
            session_key 字符串
        """
        # 外部消息（task/reminder）：使用临时 session
        if message.is_external and message.tag in (MessageTag.TASK.value, MessageTag.REMINDER.value):
            return f"temp_{message.id}"
        # 私聊消息
        elif message.is_dm:
            return f"dm_{message.discord_user_id}"
        # 频道消息
        else:
            return f"channel_{message.discord_channel_id}"

    def update_status(self, message_id: int, status: MessageStatus,
                     response: Optional[str] = None, error: Optional[str] = None):
        """更新消息状态"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        now = datetime.now().isoformat()

        if response is not None:
            cursor.execute("""
                UPDATE messages
                SET status = ?, response = ?, updated_at = ?
                WHERE id = ?
            """, (status.value, response, now, message_id))
        elif error is not None:
            cursor.execute("""
                UPDATE messages
                SET status = ?, error = ?, updated_at = ?
                WHERE id = ?
            """, (status.value, error, now, message_id))
        else:
            cursor.execute("""
                UPDATE messages
                SET status = ?, updated_at = ?
                WHERE id = ?
            """, (status.value, now, message_id))

        conn.commit()
        conn.close()

    def update_streaming_response(self, message_id: int, streaming_response: str):
        """更新流式响应（实时更新部分响应内容）

        Args:
            message_id: 消息 ID
            streaming_response: 流式响应内容（部分响应）
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        now = datetime.now().isoformat()

        cursor.execute("""
            UPDATE messages
            SET streaming_response = ?, last_stream_update = ?, updated_at = ?
            WHERE id = ?
        """, (streaming_response, now, now, message_id))

        conn.commit()
        conn.close()

    def add_tool_use(self, message_id: int, tool_name: str, tool_input: dict, tool_use_id: str = None) -> int:
        """添加工具调用信息（代理到 ToolUseTracker）"""
        return self._tool_uses.add_tool_use(message_id, tool_name, tool_input, tool_use_id)

    def get_tool_uses(self, message_id: int) -> list:
        """获取消息的所有工具调用（代理到 ToolUseTracker）"""
        return self._tool_uses.get_tool_uses(message_id)

    def add_content_block(self, message_id: int, block_index: int, block_type: str, block_data: dict = None):
        """添加 content block 信息（代理到 ToolUseTracker）"""
        self._tool_uses.add_content_block(message_id, block_index, block_type, block_data)

    def get_content_blocks(self, message_id: int) -> list:
        """获取消息的所有 content blocks（代理到 ToolUseTracker）"""
        return self._tool_uses.get_content_blocks(message_id)

    def save_tool_use_message_ref(self, message_id: int, tool_use_index: int, discord_message_id: int, channel_id: int, is_dm: bool, channel_type: str = 'discord'):
        """保存工具调用卡片的 Discord 消息引用（代理到 ToolUseTracker）"""
        self._tool_uses.save_tool_use_message_ref(message_id, tool_use_index, discord_message_id, channel_id, is_dm, channel_type)

    def get_tool_use_message_ref(self, message_id: int, tool_use_index: int) -> Optional[dict]:
        """获取工具调用卡片的 Discord 消息引用（代理到 ToolUseTracker）"""
        return self._tool_uses.get_tool_use_message_ref(message_id, tool_use_index)

    def save_tool_use_result(self, message_id: int, tool_use_index: int, success: bool):
        """保存工具执行结果（代理到 ToolUseTracker）"""
        self._tool_uses.save_tool_use_result(message_id, tool_use_index, success)

    def get_pending_tool_use_results(self, channel_type: str = None) -> List[dict]:
        """获取待处理的工具执行结果（代理到 ToolUseTracker）"""
        return self._tool_uses.get_pending_tool_use_results(channel_type)

    def mark_tool_use_result_processed(self, message_id: int, tool_use_index: int):
        """标记工具执行结果为已处理（代理到 ToolUseTracker）"""
        self._tool_uses.mark_tool_use_result_processed(message_id, tool_use_index)

    def request_abort(self, message_id: int) -> bool:
        """请求中止消息处理

        Args:
            message_id: 消息 ID

        Returns:
            是否成功请求中止
        """
        try:
            self.update_status(message_id, MessageStatus.ABORTING)
            return True
        except Exception as e:
            log.log(f"❌ 请求中止失败: {e}")
            return False

    def is_aborting(self, message_id: int) -> bool:
        """检查消息是否正在中止

        Args:
            message_id: 消息 ID

        Returns:
            消息是否正在中止
        """
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM messages WHERE id = ?", (message_id,))
        row = cursor.fetchone()
        conn.close()
        return row and row[0] == MessageStatus.ABORTING.value

    def get_message_status(self, message_id: int) -> Optional[MessageStatus]:
        """获取消息状态

        Args:
            message_id: 消息 ID

        Returns:
            消息状态枚举值，如果消息不存在则返回 None
        """
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM messages WHERE id = ?", (message_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            try:
                return MessageStatus(row[0])
            except ValueError:
                return None
        return None

    def get_streaming_messages(self, channel_type: str = None, limit: int = 100) -> List[dict]:
        """批量获取有待发送流式响应的消息

        Args:
            channel_type: 频道类型过滤（discord/weixin），None 表示所有频道
            limit: 返回数量限制

        Returns:
            消息列表，每条消息包含 id, username, discord_channel_id, streaming_response, response, status
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        if channel_type:
            cursor.execute("""
                SELECT id, username, discord_channel_id, streaming_response, response, status
                FROM messages
                WHERE status IN (?, ?)
                  AND channel_type = ?
                  AND streaming_response IS NOT NULL
                  AND streaming_response != ''
                ORDER BY created_at ASC
                LIMIT ?
            """, (MessageStatus.PROCESSING.value, MessageStatus.AI_STARTED.value, channel_type, limit))
        else:
            cursor.execute("""
                SELECT id, username, discord_channel_id, streaming_response, response, status
                FROM messages
                WHERE status IN (?, ?)
                  AND streaming_response IS NOT NULL
                  AND streaming_response != ''
                ORDER BY created_at ASC
                LIMIT ?
            """, (MessageStatus.PROCESSING.value, MessageStatus.AI_STARTED.value, limit))

        rows = cursor.fetchall()
        conn.close()

        return [
            {
                "id": row[0],
                "username": row[1],
                "discord_channel_id": row[2],
                "streaming_response": row[3] or "",
                "response": row[4] or "",
                "status": row[5]
            }
            for row in rows
        ]

    def is_ai_response_complete(self, message_id: int) -> bool:
        """检查 AI 响应是否已完成（即 Claude Bridge 输出"处理成功"）

        当状态为 PROCESSING（AI 响应完成，正在等待/正在发送消息）或 COMPLETED/FAILED/SKIPPED 时返回 True

        Args:
            message_id: 消息 ID

        Returns:
            AI 响应是否已完成
        """
        status = self.get_message_status(message_id)
        if status is None:
            return False
        # AI 响应完成的状态：PROCESSING（Claude Bridge 已输出"处理成功"）、COMPLETED、FAILED、SKIPPED
        return status in (
            MessageStatus.PROCESSING,
            MessageStatus.COMPLETED,
            MessageStatus.FAILED,
            MessageStatus.SKIPPED
        )

    def get_processing_messages(self, channel_type: str = None, channel_id: int = None, user_id: int = None) -> List[Message]:
        """获取正在处理的消息

        Args:
            channel_type: 频道类型过滤（discord/weixin），None 表示获取所有频道
            channel_id: 频道 ID 过滤，用于匹配特定频道的消息
            user_id: 用户 ID 过滤，用于匹配特定用户的私聊消息

        Returns:
            正在处理的消息列表
        """
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        conditions = ["status IN (?, ?)"]
        params = [MessageStatus.PROCESSING.value, MessageStatus.AI_STARTED.value]

        if channel_type:
            conditions.append("channel_type = ?")
            params.append(channel_type)

        if channel_id:
            conditions.append("discord_channel_id = ?")
            params.append(channel_id)

        if user_id:
            conditions.append("discord_user_id = ?")
            params.append(user_id)

        query = f"""
            SELECT id, direction, content, status,
                   discord_channel_id, discord_message_id,
                   discord_user_id, username,
                   response, error, is_dm, is_external, tag, channel_type, context_token, attachments, streaming_response, created_at, updated_at
            FROM messages
            WHERE {' AND '.join(conditions)}
            ORDER BY created_at DESC
        """
        cursor.execute(query, params)

        rows = cursor.fetchall()
        conn.close()

        messages = []
        for row in rows:
            # 解析 attachments JSON
            attachments_json = row[15]
            attachments = []
            if attachments_json:
                try:
                    attachments_list = json.loads(attachments_json)
                    attachments = [AttachmentInfo(**a) for a in attachments_list]
                except:
                    pass

            message = Message(
                id=row[0],
                direction=row[1],
                content=row[2],
                status=row[3],
                discord_channel_id=row[4],
                discord_message_id=row[5],
                discord_user_id=row[6],
                username=row[7],
                response=row[8],
                error=row[9],
                is_dm=bool(row[10]),
                is_external=bool(row[11]),
                tag=row[12] if row[12] else MessageTag.DEFAULT.value,
                channel_type=row[13] if row[13] else ChannelType.DISCORD.value,
                context_token=row[14],
                attachments=attachments,
                streaming_response=row[16],
                created_at=row[17],
                updated_at=row[18]
            )
            messages.append(message)

        return messages

    def get_response(self, discord_message_id: int) -> Optional[Message]:
        """获取 Discord 消息的响应"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, direction, content, status,
                   discord_channel_id, discord_message_id,
                   discord_user_id, username,
                   response, error, is_dm, is_external, tag, channel_type, context_token, attachments, created_at, updated_at
            FROM messages
            WHERE discord_message_id = ? AND direction = ?
            AND status IN (?, ?)
            ORDER BY created_at DESC
            LIMIT 1
        """, (discord_message_id, MessageDirection.TO_CLAUDE.value,
              MessageStatus.COMPLETED.value, MessageStatus.PROCESSING.value))

        row = cursor.fetchone()
        conn.close()

        if row:
            # 解析附件信息
            attachments = None
            if row[15]:
                try:
                    attachments_data = json.loads(row[15])
                    attachments = [
                        AttachmentInfo(
                            id=a["id"],
                            filename=a["filename"],
                            local_filename=a.get("local_filename"),
                            size=a["size"],
                            url=a["url"],
                            description=a.get("description")
                        )
                        for a in attachments_data
                    ]
                except (json.JSONDecodeError, KeyError):
                    pass

            return Message(
                id=row[0],
                direction=row[1],
                content=row[2],
                status=row[3],
                discord_channel_id=row[4],
                discord_message_id=row[5],
                discord_user_id=row[6],
                username=row[7],
                response=row[8],
                error=row[9],
                is_dm=bool(row[10]),
                is_external=bool(row[11]),
                tag=row[12] if row[12] else MessageTag.DEFAULT.value,
                channel_type=row[13] if row[13] else ChannelType.DISCORD.value,
                context_token=row[14],
                attachments=attachments,
                created_at=row[16],
                updated_at=row[17]
            )
        return None

    def cleanup_old_messages(self, retention_hours: int = 24):
        """清理旧消息"""
        if retention_hours == 0:
            return  # 0 表示永久保留

        cutoff_time = datetime.now() - timedelta(hours=retention_hours)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            DELETE FROM messages
            WHERE created_at < ? AND status = ?
        """, (cutoff_time.isoformat(), MessageStatus.COMPLETED.value))

        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()

        return deleted_count

    # ========== 频道设置管理（mention_required 按频道独立管理） ==========

    def get_channel_mention_required(self, channel_id: int, default: bool = True) -> bool:
        """获取指定频道的 mention_required 设置

        Args:
            channel_id: 频道 ID
            default: 频道未配置时的默认值

        Returns:
            该频道的 mention_required 值，未配置时返回 default
        """
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT mention_required FROM channel_settings WHERE channel_id = ?",
                (str(channel_id),)
            )
            row = cursor.fetchone()
        finally:
            conn.close()

        if row is None:
            return default
        return bool(row[0])

    def set_channel_mention_required(self, channel_id: int, value: bool):
        """设置指定频道的 mention_required 值

        Args:
            channel_id: 频道 ID
            value: 是否需要 @
        """
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO channel_settings (channel_id, mention_required, message_splitting, updated_at)
                   VALUES (?, ?, NULL, CURRENT_TIMESTAMP)
                   ON CONFLICT(channel_id) DO UPDATE SET
                       mention_required = excluded.mention_required,
                       updated_at = CURRENT_TIMESTAMP""",
                (str(channel_id), int(value))
            )
            conn.commit()
        finally:
            conn.close()

    def remove_channel_mention_required(self, channel_id: int):
        """删除指定频道的 mention_required 设置（恢复全局默认）

        Args:
            channel_id: 频道 ID
        """
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM channel_settings WHERE channel_id = ?",
                (str(channel_id),)
            )
            conn.commit()
        finally:
            conn.close()

    # ========== 频道设置管理（message_splitting 按频道独立管理） ==========

    def get_channel_message_splitting(self, channel_id: int, default: bool = True) -> bool:
        """获取指定频道的 message_splitting 设置

        Args:
            channel_id: 频道 ID
            default: 频道未配置时的默认值

        Returns:
            该频道的 message_splitting 值，未配置时返回 default
        """
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT message_splitting FROM channel_settings WHERE channel_id = ?",
                (str(channel_id),)
            )
            row = cursor.fetchone()
        finally:
            conn.close()

        if row is None or row[0] is None:
            return default
        return bool(row[0])

    def set_channel_message_splitting(self, channel_id: int, value: bool):
        """设置指定频道的 message_splitting 值

        Args:
            channel_id: 频道 ID
            value: 是否启用换行分割
        """
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO channel_settings (channel_id, mention_required, message_splitting, updated_at)
                   VALUES (?, 1, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(channel_id) DO UPDATE SET
                       message_splitting = excluded.message_splitting,
                       updated_at = CURRENT_TIMESTAMP""",
                (str(channel_id), int(value))
            )
            conn.commit()
        finally:
            conn.close()

    def remove_channel_message_splitting(self, channel_id: int):
        """删除指定频道的 message_splitting 设置（恢复全局默认）

        Args:
            channel_id: 频道 ID
        """
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE channel_settings SET message_splitting = NULL WHERE channel_id = ?",
                (str(channel_id),)
            )
            conn.commit()
        finally:
            conn.close()

    def get_or_create_session(
        self,
        base_working_dir: str,
        channel_id: int = None,
        user_id: int = None,
        is_dm: bool = False,
        use_temp_session: bool = False,
        temp_session_key: str = None
    ) -> tuple[str, str, bool, str]:
        """获取或创建会话的工作目录（代理到 SessionManager）"""
        return self._sessions.get_or_create_session(
            base_working_dir, channel_id, user_id, is_dm, use_temp_session, temp_session_key
        )

    def get_claude_session_path(self, working_dir: str) -> str:
        """获取 Claude Code 会话文件路径（代理到 SessionManager）"""
        return self._sessions.get_claude_session_path(working_dir)

    def delete_claude_session_files(self, working_dir: str) -> bool:
        """删除 Claude Code 的会话文件（代理到 SessionManager）"""
        return self._sessions.delete_claude_session_files(working_dir)

    def get_latest_session_id(self, working_dir: str) -> str:
        """获取 Claude Code 最新的 session_id（代理到 SessionManager）"""
        return self._sessions.get_latest_session_id(working_dir)

    def update_session_id(self, session_key: str, session_id: str):
        """更新会话的 session_id（代理到 SessionManager）"""
        self._sessions.update_session_id(session_key, session_id)

    def mark_session_created(self, session_key: str):
        """标记会话已创建（代理到 SessionManager）"""
        self._sessions.mark_session_created(session_key)

    def cleanup_old_sessions(self, days: int = 7):
        """清理超过指定天数未使用的会话（代理到 SessionManager）"""
        return self._sessions.cleanup_old_sessions(days)

    def delete_session(self, session_key: str, working_dir: str = None) -> bool:
        """删除指定会话（代理到 SessionManager）"""
        return self._sessions.delete_session(session_key, working_dir)

    def add_file_download_request(self, download_request: FileDownloadRequest) -> int:
        """添加文件下载请求到队列"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        now = datetime.now().isoformat()
        download_request.created_at = now
        download_request.updated_at = now

        cursor.execute("""
            INSERT INTO file_download_requests (
                discord_message_id, discord_channel_id, save_directory,
                status, downloaded_files, error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            download_request.discord_message_id,
            download_request.discord_channel_id,
            download_request.save_directory,
            download_request.status,
            download_request.downloaded_files,
            download_request.error,
            download_request.created_at,
            download_request.updated_at
        ))

        request_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return request_id

    def get_next_file_download_request(self) -> Optional[FileDownloadRequest]:
        """获取下一个待处理的文件下载请求"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, discord_message_id, discord_channel_id, save_directory,
                   status, downloaded_files, error, created_at, updated_at
            FROM file_download_requests
            WHERE status = ?
            ORDER BY created_at ASC
            LIMIT 1
        """, (FileDownloadRequestStatus.PENDING.value,))

        row = cursor.fetchone()
        conn.close()

        if row:
            return FileDownloadRequest(
                id=row[0],
                discord_message_id=row[1],
                discord_channel_id=row[2],
                save_directory=row[3],
                status=row[4],
                downloaded_files=row[5],
                error=row[6],
                created_at=row[7],
                updated_at=row[8]
            )
        return None

    def update_file_download_request_status(self, request_id: int, status: FileDownloadRequestStatus,
                                           downloaded_files: Optional[str] = None, error: Optional[str] = None):
        """更新文件下载请求状态"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        now = datetime.now().isoformat()

        if downloaded_files is not None:
            cursor.execute("""
                UPDATE file_download_requests
                SET status = ?, downloaded_files = ?, updated_at = ?
                WHERE id = ?
            """, (status.value, downloaded_files, now, request_id))
        elif error is not None:
            cursor.execute("""
                UPDATE file_download_requests
                SET status = ?, error = ?, updated_at = ?
                WHERE id = ?
            """, (status.value, error, now, request_id))
        else:
            cursor.execute("""
                UPDATE file_download_requests
                SET status = ?, updated_at = ?
                WHERE id = ?
            """, (status.value, now, request_id))

        conn.commit()
        conn.close()

    def get_file_download_request(self, request_id: int, timeout: float = 60.0) -> Optional[FileDownloadRequest]:
        """
        获取文件下载请求及其结果（等待处理完成）

        Args:
            request_id: 请求 ID
            timeout: 超时时间（秒），默认 60 秒

        Returns:
            完成的文件下载请求，如果超时或失败则返回 None
        """
        import time
        start_time = time.time()

        while time.time() - start_time < timeout:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT id, discord_message_id, discord_channel_id, save_directory,
                       status, downloaded_files, error, created_at, updated_at
                FROM file_download_requests
                WHERE id = ?
            """, (request_id,))

            row = cursor.fetchone()
            conn.close()

            if row:
                request = FileDownloadRequest(
                    id=row[0],
                    discord_message_id=row[1],
                    discord_channel_id=row[2],
                    save_directory=row[3],
                    status=row[4],
                    downloaded_files=row[5],
                    error=row[6],
                    created_at=row[7],
                    updated_at=row[8]
                )

                # 如果已完成或失败，返回结果
                if request.status in (FileDownloadRequestStatus.COMPLETED.value, FileDownloadRequestStatus.FAILED.value):
                    return request

            # 等待一段时间后重试
            time.sleep(0.5)

        return None

    def cleanup_old_file_download_requests(self, retention_hours: int = 24):
        """清理旧的文件下载请求"""
        if retention_hours == 0:
            return

        cutoff_time = datetime.now() - timedelta(hours=retention_hours)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            DELETE FROM file_download_requests
            WHERE created_at < ? AND status IN (?, ?)
        """, (cutoff_time.isoformat(), FileDownloadRequestStatus.COMPLETED.value, FileDownloadRequestStatus.FAILED.value))

        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()

        return deleted_count

    def delete_session(self, session_key: str, working_dir: str = None) -> bool:
        """
        删除指定会话（包括数据库记录和 Claude Code 会话文件）

        Args:
            session_key: 会话标识
            working_dir: 工作目录（可选），用于删除 Claude Code 会话文件

        Returns:
            是否成功删除
        """
        import os
        from pathlib import Path

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            DELETE FROM sessions WHERE session_key = ?
        """, (session_key,))

        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()

        # 如果提供了工作目录，同时删除 Claude Code 的会话文件
        if working_dir and deleted:
            try:
                self.delete_claude_session_files(working_dir)
            except Exception as e:
                log.log(f"⚠️ 删除 Claude 会话文件时出错: {e}")

        return deleted

    def add_message_request(self, message_request: MessageRequest) -> int:
        """添加消息发送请求到队列"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        now = datetime.now().isoformat()
        message_request.created_at = now
        message_request.updated_at = now

        cursor.execute("""
            INSERT INTO message_requests (
                content, user_id, channel_id, use_embed,
                embed_title, embed_color, tag, status, result, error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            message_request.content,
            message_request.user_id,
            message_request.channel_id,
            1 if message_request.use_embed else 0,
            message_request.embed_title,
            message_request.embed_color,
            message_request.tag,
            message_request.status,
            message_request.result,
            message_request.error,
            message_request.created_at,
            message_request.updated_at
        ))

        request_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return request_id

    def get_next_message_request(self) -> Optional[MessageRequest]:
        """获取下一个待处理的消息发送请求"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, content, user_id, channel_id, use_embed,
                   embed_title, embed_color, tag, status, result, error, created_at, updated_at
            FROM message_requests
            WHERE status = ?
            ORDER BY created_at ASC
            LIMIT 1
        """, (MessageRequestStatus.PENDING.value,))

        row = cursor.fetchone()
        conn.close()

        if row:
            return MessageRequest(
                id=row[0],
                content=row[1],
                user_id=row[2],
                channel_id=row[3],
                use_embed=bool(row[4]),
                embed_title=row[5],
                embed_color=row[6],
                tag=row[7] or MessageTag.DEFAULT.value,
                status=row[8],
                result=row[9],
                error=row[10],
                created_at=row[11],
                updated_at=row[12]
            )
        return None

    def update_message_request_status(self, request_id: int, status: MessageRequestStatus,
                                   result: Optional[str] = None, error: Optional[str] = None):
        """更新消息发送请求状态"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        now = datetime.now().isoformat()

        if result is not None:
            cursor.execute("""
                UPDATE message_requests
                SET status = ?, result = ?, updated_at = ?
                WHERE id = ?
            """, (status.value, result, now, request_id))
        elif error is not None:
            cursor.execute("""
                UPDATE message_requests
                SET status = ?, error = ?, updated_at = ?
                WHERE id = ?
            """, (status.value, error, now, request_id))
        else:
            cursor.execute("""
                UPDATE message_requests
                SET status = ?, updated_at = ?
                WHERE id = ?
            """, (status.value, now, request_id))

        conn.commit()
        conn.close()

    def get_message_request(self, request_id: int, timeout: float = 30.0) -> Optional[MessageRequest]:
        """
        获取消息发送请求及其结果（等待处理完成）

        Args:
            request_id: 请求 ID
            timeout: 超时时间（秒），默认 30 秒

        Returns:
            完成的消息请求，如果超时或失败则返回 None
        """
        import time
        start_time = time.time()

        while time.time() - start_time < timeout:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT id, content, user_id, channel_id, use_embed,
                       embed_title, embed_color, tag, status, result, error, created_at, updated_at
                FROM message_requests
                WHERE id = ?
            """, (request_id,))

            row = cursor.fetchone()
            conn.close()

            if row:
                request = MessageRequest(
                    id=row[0],
                    content=row[1],
                    user_id=row[2],
                    channel_id=row[3],
                    use_embed=bool(row[4]),
                    embed_title=row[5],
                    embed_color=row[6],
                    tag=row[7] or MessageTag.DEFAULT.value,
                    status=row[8],
                    result=row[9],
                    error=row[10],
                    created_at=row[11],
                    updated_at=row[12]
                )

                # 如果已完成或失败，返回结果
                if request.status in (MessageRequestStatus.COMPLETED.value, MessageRequestStatus.FAILED.value):
                    return request

            # 等待一段时间后重试
            time.sleep(0.5)

        return None

    def cleanup_old_message_requests(self, retention_hours: int = 24):
        """清理旧的消息发送请求"""
        if retention_hours == 0:
            return

        cutoff_time = datetime.now() - timedelta(hours=retention_hours)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            DELETE FROM message_requests
            WHERE created_at < ? AND status IN (?, ?)
        """, (cutoff_time.isoformat(), MessageRequestStatus.COMPLETED.value, MessageRequestStatus.FAILED.value))

        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()

    # ========== 消息序列相关方法 ==========

    def add_message_sequence(self, message_id: int, sequence_index: int, content_block_index: int,
                            item_type: str, item_data: dict, tool_use_index: int = None):
        """添加消息序列项（代理到 MessageSequenceManager）"""
        self._sequences.add_message_sequence(message_id, sequence_index, content_block_index, item_type, item_data, tool_use_index)

    def get_pending_message_sequences(self, message_id: int, limit: int = 10) -> list:
        """获取待发送的消息序列（代理到 MessageSequenceManager）"""
        return self._sequences.get_pending_message_sequences(message_id, limit)

    def get_messages_with_pending_sequences(self, channel_type: str, limit: int = 1) -> List[dict]:
        """获取有待发送序列的消息列表（代理到 MessageSequenceManager）"""
        return self._sequences.get_messages_with_pending_sequences(channel_type, limit)

    def mark_sequence_sent(self, sequence_id: int):
        """标记消息序列项为已发送（代理到 MessageSequenceManager）"""
        self._sequences.mark_sequence_sent(sequence_id)

    def get_max_sequence_index(self, message_id: int) -> int:
        """获取指定消息的最大 sequence_index（代理到 MessageSequenceManager）"""
        return self._sequences.get_max_sequence_index(message_id)

    def get_message_sequences_stats(self, message_id: int) -> dict:
        """获取消息序列统计信息（代理到 MessageSequenceManager）"""
        return self._sequences.get_message_sequences_stats(message_id)

    def cleanup_message_sequences(self, message_id: int):
        """清理已发送的消息序列（代理到 MessageSequenceManager）"""
        return self._sequences.cleanup_message_sequences(message_id)
