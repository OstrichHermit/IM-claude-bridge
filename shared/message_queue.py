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


class FileRequestStatus(Enum):
    """文件请求状态枚举"""
    PENDING = "pending"        # 等待处理
    PROCESSING = "processing"  # 正在处理
    COMPLETED = "completed"    # 已完成
    FAILED = "failed"          # 失败


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
    attachments: Optional[List[AttachmentInfo]] = None  # 附件信息列表
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict:
        """转换为字典"""
        return asdict(self)


@dataclass
class FileRequest:
    """文件发送请求数据类"""
    id: Optional[int]
    file_paths: List[str]  # 文件路径列表（JSON 数组）
    user_id: Optional[int]  # Discord 用户 ID
    channel_id: Optional[int]  # Discord 频道 ID
    status: str = FileRequestStatus.PENDING.value  # 请求状态
    result: Optional[str] = None  # 执行结果（JSON 格式）
    error: Optional[str] = None  # 错误信息
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
    """消息队列管理器"""

    def __init__(self, db_path: str):
        """初始化消息队列"""
        self.db_path = db_path
        self._init_database()

    def _init_database(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 创建消息表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                direction TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT NOT NULL,
                discord_channel_id INTEGER NOT NULL,
                discord_message_id INTEGER NOT NULL,
                discord_user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                response TEXT,
                error TEXT,
                is_dm BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 兼容性处理：为旧数据库添加 is_dm 字段
        try:
            cursor.execute("ALTER TABLE messages ADD COLUMN is_dm BOOLEAN DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # 字段已存在

        # 兼容性处理：为旧数据库添加 is_external 字段（标记外部插入的消息）
        try:
            cursor.execute("ALTER TABLE messages ADD COLUMN is_external BOOLEAN DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # 字段已存在

        # 兼容性处理：为旧数据库添加 tag 字段（消息标签）
        try:
            cursor.execute("ALTER TABLE messages ADD COLUMN tag TEXT DEFAULT 'default'")
        except sqlite3.OperationalError:
            pass  # 字段已存在

        # 兼容性处理：为旧数据库添加 streaming_response 字段（流式响应）
        try:
            cursor.execute("ALTER TABLE messages ADD COLUMN streaming_response TEXT")
        except sqlite3.OperationalError:
            pass  # 字段已存在

        # 兼容性处理：为旧数据库添加 last_stream_update 字段（最后流式更新时间）
        try:
            cursor.execute("ALTER TABLE messages ADD COLUMN last_stream_update TIMESTAMP")
        except sqlite3.OperationalError:
            pass  # 字段已存在

        # 兼容性处理：为旧数据库添加 attachments 字段（附件信息）
        try:
            cursor.execute("ALTER TABLE messages ADD COLUMN attachments TEXT")
        except sqlite3.OperationalError:
            pass  # 字段已存在

        # 兼容性处理：为旧数据库添加 tool_uses 字段（工具调用信息）
        try:
            cursor.execute("ALTER TABLE messages ADD COLUMN tool_uses TEXT")
        except sqlite3.OperationalError:
            pass  # 字段已存在

        # 创建流式响应索引（提高查询性能）
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_last_stream_update
            ON messages(last_stream_update)
        """)

        # 创建工具调用卡片引用表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tool_use_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                tool_use_index INTEGER NOT NULL,
                discord_message_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                is_dm BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(message_id, tool_use_index)
            )
        """)

        # 创建索引以提高查询性能
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tool_use_message_id
            ON tool_use_messages(message_id)
        """)

        # 创建 content block 顺序表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS content_blocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                block_index INTEGER NOT NULL,
                block_type TEXT NOT NULL,
                block_data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(message_id, block_index)
            )
        """)

        # 创建索引以提高查询性能
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_content_block_message_id
            ON content_blocks(message_id)
        """)

        # 创建工具执行结果状态表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tool_use_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                tool_use_index INTEGER NOT NULL,
                success BOOLEAN NOT NULL,
                processed BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(message_id, tool_use_index)
            )
        """)

        # 创建索引以提高查询性能
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tool_use_results_processed
            ON tool_use_results(processed)
        """)

        # 创建会话表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_key TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                session_created BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 兼容性处理：为旧数据库添加 session_created 字段
        try:
            cursor.execute("ALTER TABLE sessions ADD COLUMN session_created BOOLEAN DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # 字段已存在

        # 创建索引以提高查询性能
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_status_direction
            ON messages(status, direction)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_created_at
            ON messages(created_at)
        """)

        # 创建文件请求表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS file_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_paths TEXT NOT NULL,
                user_id INTEGER,
                channel_id INTEGER,
                message TEXT,
                status TEXT NOT NULL,
                result TEXT,
                error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 创建文件请求索引
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_file_requests_status
            ON file_requests(status)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_file_requests_created_at
            ON file_requests(created_at)
        """)

        # 创建文件下载请求表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS file_download_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_message_id INTEGER NOT NULL,
                discord_channel_id INTEGER NOT NULL,
                save_directory TEXT NOT NULL,
                status TEXT NOT NULL,
                downloaded_files TEXT,
                error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 创建文件下载请求索引
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_file_download_requests_status
            ON file_download_requests(status)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_file_download_requests_created_at
            ON file_download_requests(created_at)
        """)

        # 创建消息发送请求表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS message_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                user_id INTEGER,
                channel_id INTEGER,
                use_embed BOOLEAN DEFAULT 1,
                embed_title TEXT,
                embed_color INTEGER,
                tag TEXT DEFAULT 'default',
                status TEXT NOT NULL,
                result TEXT,
                error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 创建消息发送请求索引
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_message_requests_status
            ON message_requests(status)
        """)

        # 兼容性处理：为旧数据库添加 tag 字段（消息请求标签）
        try:
            cursor.execute("ALTER TABLE message_requests ADD COLUMN tag TEXT DEFAULT 'default'")
        except sqlite3.OperationalError:
            pass  # 字段已存在

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_message_requests_created_at
            ON message_requests(created_at)
        """)

        conn.commit()
        conn.close()

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
                response, error, is_dm, is_external, tag, attachments, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            attachments_json,
            message.created_at,
            message.updated_at
        ))

        message_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return message_id

    def get_next_pending(self, direction: MessageDirection) -> Optional[Message]:
        """获取下一个待处理的消息"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, direction, content, status,
                   discord_channel_id, discord_message_id,
                   discord_user_id, username,
                   response, error, is_dm, is_external, tag, attachments, created_at, updated_at
            FROM messages
            WHERE status = ? AND direction = ?
            ORDER BY created_at ASC
            LIMIT 1
        """, (MessageStatus.PENDING.value, direction.value))

        row = cursor.fetchone()
        conn.close()

        if row:
            # 解析附件信息
            attachments = None
            if row[13]:
                try:
                    attachments_data = json.loads(row[13])
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
                tag=row[12] or MessageTag.DEFAULT.value,
                attachments=attachments,
                created_at=row[14],
                updated_at=row[15]
            )
        return None

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
                   response, error, is_dm, is_external, tag, attachments, created_at, updated_at
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
            if row[13]:
                try:
                    attachments_data = json.loads(row[13])
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
                attachments=attachments,
                created_at=row[14],
                updated_at=row[15]
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
        """添加工具调用信息

        Args:
            message_id: 消息 ID
            tool_name: 工具名称
            tool_input: 工具参数
            tool_use_id: 工具调用 ID（可选）

        Returns:
            工具调用的索引（从 0 开始）
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 获取现有的 tool_uses
        cursor.execute("SELECT tool_uses FROM messages WHERE id = ?", (message_id,))
        row = cursor.fetchone()

        tool_uses = []
        if row and row[0]:
            try:
                tool_uses = json.loads(row[0])
            except json.JSONDecodeError:
                tool_uses = []

        # 获取当前索引（即添加后的索引）
        tool_use_index = len(tool_uses)

        # 添加新的工具调用
        tool_use_data = {
            "name": tool_name,
            "input": tool_input
        }
        if tool_use_id:
            tool_use_data["id"] = tool_use_id

        tool_uses.append(tool_use_data)

        # 更新数据库
        now = datetime.now().isoformat()
        cursor.execute("""
            UPDATE messages
            SET tool_uses = ?, updated_at = ?
            WHERE id = ?
        """, (json.dumps(tool_uses, ensure_ascii=False), now, message_id))

        conn.commit()
        conn.close()

        return tool_use_index

    def add_content_block(self, message_id: int, block_index: int, block_type: str, block_data: dict = None):
        """添加 content block 信息（记录 JSON 中的原始顺序）

        Args:
            message_id: 消息 ID
            block_index: Content block 索引（从 0 开始）
            block_type: Content block 类型（text 或 tool_use）
            block_data: Content block 数据（可选）
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        now = datetime.now().isoformat()
        data_json = json.dumps(block_data, ensure_ascii=False) if block_data else None

        cursor.execute("""
            INSERT OR REPLACE INTO content_blocks
            (message_id, block_index, block_type, block_data, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (message_id, block_index, block_type, data_json, now))

        conn.commit()
        conn.close()

    def get_content_blocks(self, message_id: int) -> list:
        """获取消息的所有 content blocks（按原始顺序）

        Args:
            message_id: 消息 ID

        Returns:
            Content block 列表，格式：[{"index": 0, "type": "text", "data": {...}}, ...]
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT block_index, block_type, block_data
            FROM content_blocks
            WHERE message_id = ?
            ORDER BY block_index ASC
        """, (message_id,))

        rows = cursor.fetchall()
        conn.close()

        content_blocks = []
        for row in rows:
            block_index, block_type, block_data = row
            block_info = {
                "index": block_index,
                "type": block_type
            }
            if block_data:
                try:
                    block_info["data"] = json.loads(block_data)
                except json.JSONDecodeError:
                    pass
            content_blocks.append(block_info)

        return content_blocks

    def get_tool_uses(self, message_id: int) -> list:
        """获取消息的所有工具调用

        Args:
            message_id: 消息 ID

        Returns:
            工具调用列表
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT tool_uses FROM messages WHERE id = ?", (message_id,))
        row = cursor.fetchone()
        conn.close()

        if row and row[0]:
            try:
                return json.loads(row[0])
            except json.JSONDecodeError:
                return []

        return []

    def save_tool_use_message_ref(self, message_id: int, tool_use_index: int, discord_message_id: int, channel_id: int, is_dm: bool):
        """保存工具调用卡片的 Discord 消息引用

        Args:
            message_id: 消息 ID
            tool_use_index: 工具调用索引
            discord_message_id: Discord 消息 ID
            channel_id: Discord 频道/私聊 ID
            is_dm: 是否为私聊
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        now = datetime.now().isoformat()
        cursor.execute("""
            INSERT OR REPLACE INTO tool_use_messages
            (message_id, tool_use_index, discord_message_id, channel_id, is_dm, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (message_id, tool_use_index, discord_message_id, channel_id, 1 if is_dm else 0, now))

        conn.commit()
        conn.close()

    def get_tool_use_message_ref(self, message_id: int, tool_use_index: int) -> Optional[dict]:
        """获取工具调用卡片的 Discord 消息引用

        Args:
            message_id: 消息 ID
            tool_use_index: 工具调用索引

        Returns:
            包含 discord_message_id, channel_id, is_dm 的字典，如果不存在则返回 None
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT discord_message_id, channel_id, is_dm
            FROM tool_use_messages
            WHERE message_id = ? AND tool_use_index = ?
        """, (message_id, tool_use_index))

        row = cursor.fetchone()
        conn.close()

        if row:
            return {
                "discord_message_id": row[0],
                "channel_id": row[1],
                "is_dm": bool(row[2])
            }

        return None

    def save_tool_use_result(self, message_id: int, tool_use_index: int, success: bool):
        """保存工具执行结果

        Args:
            message_id: 消息 ID
            tool_use_index: 工具调用索引
            success: 工具执行是否成功
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        now = datetime.now().isoformat()
        cursor.execute("""
            INSERT OR REPLACE INTO tool_use_results
            (message_id, tool_use_index, success, processed, created_at)
            VALUES (?, ?, ?, 0, ?)
        """, (message_id, tool_use_index, 1 if success else 0, now))

        conn.commit()
        conn.close()

    def get_pending_tool_use_results(self) -> List[dict]:
        """获取待处理的工具执行结果

        Returns:
            待处理的工具执行结果列表
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT message_id, tool_use_index, success
            FROM tool_use_results
            WHERE processed = 0
            ORDER BY created_at ASC
        """)

        rows = cursor.fetchall()
        conn.close()

        results = []
        for row in rows:
            results.append({
                "message_id": row[0],
                "tool_use_index": row[1],
                "success": bool(row[2])
            })

        return results

    def mark_tool_use_result_processed(self, message_id: int, tool_use_index: int):
        """标记工具执行结果为已处理

        Args:
            message_id: 消息 ID
            tool_use_index: 工具调用索引
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE tool_use_results
            SET processed = 1
            WHERE message_id = ? AND tool_use_index = ?
        """, (message_id, tool_use_index))

        conn.commit()
        conn.close()

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
            print(f"❌ 请求中止失败: {e}")
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

    def get_processing_messages(self) -> List[Message]:
        """获取所有正在处理的消息

        Returns:
            正在处理的消息列表
        """
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, direction, content, status,
                   discord_channel_id, discord_message_id,
                   discord_user_id, username,
                   response, error, is_dm, is_external, tag, attachments, created_at, updated_at
            FROM messages
            WHERE status IN (?, ?)
            ORDER BY created_at DESC
        """, (MessageStatus.PROCESSING.value, MessageStatus.AI_STARTED.value))

        rows = cursor.fetchall()
        conn.close()

        messages = []
        for row in rows:
            # 解析 attachments JSON
            attachments_json = row[12]
            attachments = []
            if attachments_json:
                try:
                    attachments_list = json.loads(attachments_json)
                    attachments = [AttachmentInfo(**a) for a in attachments_list]
                except:
                    pass

            message = Message(
                id=row[0],
                direction=MessageDirection(row[1]),
                content=row[2],
                status=MessageStatus(row[3]),
                discord_channel_id=row[4],
                discord_message_id=row[5],
                discord_user_id=row[6],
                username=row[7],
                response=row[8],
                error=row[9],
                is_dm=bool(row[10]),
                is_external=bool(row[11]),
                tag=row[12] if row[12] else MessageTag.DEFAULT.value,
                attachments=attachments
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
                   response, error, is_dm, is_external, tag, attachments, created_at, updated_at
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
            if row[13]:
                try:
                    attachments_data = json.loads(row[13])
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
                tag=row[12] or MessageTag.DEFAULT.value,
                attachments=attachments,
                created_at=row[14],
                updated_at=row[15]
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

    def get_or_create_session(
        self,
        base_working_dir: str,
        channel_id: int = None,
        user_id: int = None,
        is_dm: bool = False,
        use_temp_session: bool = False,
        temp_session_key: str = None,
        session_mode: str = "global"
    ) -> tuple[str, str, bool, str]:
        """
        获取或创建会话的工作目录

        Args:
            base_working_dir: 基础工作目录
            channel_id: Discord 频道 ID（频道消息使用）
            user_id: Discord 用户 ID（私聊消息使用）
            is_dm: 是否为私聊消息
            use_temp_session: 是否使用临时会话（外部消息）
            temp_session_key: 临时会话 key（use_temp_session=True 时使用）
            session_mode: 会话模式（"global" 或 "session"）

        Returns:
            (session_key, session_id, session_created, working_directory):
            会话标识、会话 ID、会话是否已创建、工作目录路径
        """
        import os
        from pathlib import Path

        base_path = Path(base_working_dir)

        # ========== 生成 session_key 和工作目录 ==========
        if use_temp_session and temp_session_key:
            # 临时会话（外部消息：task/reminder）- 所有模式下都是独立的
            session_key = temp_session_key
            working_dir = str(base_path)  # 使用基础工作目录
        elif session_mode == "global":
            # global 模式：所有频道/私聊共享一个全局 session
            session_key = "global"
            working_dir = str(base_path)  # 使用基础工作目录
        elif is_dm:
            # session 模式 - 私聊：每个用户的私聊使用独立 session
            session_key = f"dm_{user_id}"
            working_dir = str(base_path)  # 使用基础工作目录（不创建子目录）
        else:
            # session 模式 - 频道：每个频道使用独立 session（该频道所有用户共享）
            session_key = f"channel_{channel_id}"
            working_dir = str(base_path)  # 使用基础工作目录（不创建子目录）

        # 确保工作目录存在
        os.makedirs(working_dir, exist_ok=True)

        # 获取或创建 session_id
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT session_id, session_created FROM sessions WHERE session_key = ?
        """, (session_key,))

        row = cursor.fetchone()

        if row:
            session_id, session_created = row[0], bool(row[1])
            # 更新最后使用时间
            cursor.execute("""
                UPDATE sessions SET last_used_at = ? WHERE session_key = ?
            """, (datetime.now().isoformat(), session_key))
        else:
            # 新会话：立即生成一个新的 session_id（使用 UUID）
            import uuid
            session_id = str(uuid.uuid4())
            session_created = False
            # 插入新记录
            cursor.execute("""
                INSERT INTO sessions (session_key, session_id, session_created, created_at, last_used_at)
                VALUES (?, ?, ?, ?, ?)
            """, (session_key, session_id, 0, datetime.now().isoformat(), datetime.now().isoformat()))

        conn.commit()
        conn.close()

        return session_key, session_id, session_created, working_dir

    def get_claude_session_path(self, working_dir: str) -> str:
        """
        获取 Claude Code 会话文件路径（~/.claude/projects）

        Args:
            working_dir: 工作目录

        Returns:
            Claude Code 会话目录路径
        """
        from pathlib import Path
        import platform

        # Claude Code 将路径转换为项目目录名
        # Windows: D:\path -> F--D--path
        # Unix: /home/user -> D--home-user

        working_path = Path(working_dir).resolve()

        if platform.system() == "Windows":
            # Windows: 盘符转为大写字母前缀
            # D:\path -> F--D--path
            parts = working_path.parts
            drive = parts[0][0].upper()  # D:\ -> D
            rest = "-".join(parts[1:])  # \path\to -> path-to
            project_name = f"F--{drive}--{rest}" if rest else f"F--{drive}"
        else:
            # Unix: /home/user/path -> D--home-user-path
            parts = working_path.parts[1:]  # 去掉开头的 /
            project_name = "D--" + "-".join(parts) if parts else "D--"

        # 获取 Claude projects 目录
        home = Path.home()
        claude_projects_dir = home / ".claude" / "projects"

        return str(claude_projects_dir / project_name)

    def delete_claude_session_files(self, working_dir: str) -> bool:
        """
        删除 Claude Code 的会话文件

        Args:
            working_dir: 工作目录

        Returns:
            是否成功删除
        """
        from pathlib import Path
        import glob

        try:
            claude_session_dir = self.get_claude_session_path(working_dir)
            session_path = Path(claude_session_dir)

            if not session_path.exists():
                return False

            # 删除所有 .jsonl 会话文件
            jsonl_files = list(session_path.glob("*.jsonl"))
            deleted = False

            for jsonl_file in jsonl_files:
                try:
                    jsonl_file.unlink()
                    print(f"[会话清理] 已删除 Claude 会话文件: {jsonl_file.name}")
                    deleted = True
                except Exception as e:
                    print(f"⚠️ 删除会话文件失败: {e}")

            # 删除会话索引文件
            index_file = session_path / "sessions-index.json"
            if index_file.exists():
                try:
                    index_file.unlink()
                    print(f"[会话清理] 已删除会话索引: sessions-index.json")
                except Exception as e:
                    print(f"⚠️ 删除索引文件失败: {e}")

            return deleted

        except Exception as e:
            print(f"❌ 删除 Claude 会话文件时出错: {e}")
            return False

    def get_latest_session_id(self, working_dir: str) -> str:
        """
        获取 Claude Code 最新的 session_id（从会话文件中读取）

        Args:
            working_dir: 工作目录

        Returns:
            最新的 session_id，如果没有则返回 None
        """
        from pathlib import Path
        import json

        try:
            claude_session_dir = self.get_claude_session_path(working_dir)
            session_path = Path(claude_session_dir)

            if not session_path.exists():
                return None

            # 读取 sessions-index.json
            index_file = session_path / "sessions-index.json"
            if not index_file.exists():
                return None

            with open(index_file, 'r', encoding='utf-8') as f:
                index_data = json.load(f)

            # 获取最新的 session_id（列表中的第一个）
            if index_data and isinstance(index_data, list):
                return index_data[0] if index_data else None

            return None

        except Exception as e:
            print(f"⚠️ 获取最新 session_id 失败: {e}")
            return None

    def update_session_id(self, session_key: str, session_id: str):
        """
        更新会话的 session_id

        Args:
            session_key: 会话标识
            session_id: Claude Code 返回的会话 ID
        """
        import sqlite3

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE sessions SET session_id = ?, last_used_at = ?
                WHERE session_key = ?
            """, (session_id, datetime.now().isoformat(), session_key))

            conn.commit()
            conn.close()

            print(f"✅ session_id 已更新: {session_key} -> {session_id}")

        except Exception as e:
            print(f"❌ 更新 session_id 失败: {e}")

    def mark_session_created(self, session_key: str):
        """
        标记会话已创建（第一次使用 --session-id 后）

        Args:
            session_key: 会话标识
        """
        import sqlite3

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE sessions SET session_created = 1, last_used_at = ?
                WHERE session_key = ?
            """, (datetime.now().isoformat(), session_key))

            conn.commit()
            conn.close()

            print(f"✅ 会话已标记为创建: {session_key}")

        except Exception as e:
            print(f"❌ 标记会话创建失败: {e}")

    def cleanup_old_sessions(self, days: int = 7):
        """
        清理超过指定天数未使用的会话

        Args:
            days: 保留天数，默认 7 天
        """
        cutoff_time = datetime.now() - timedelta(days=days)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            DELETE FROM sessions WHERE last_used_at < ?
        """, (cutoff_time.isoformat(),))

        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()

        return deleted_count

    def add_file_request(self, file_request: FileRequest) -> int:
        """添加文件发送请求到队列"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        now = datetime.now().isoformat()
        file_request.created_at = now
        file_request.updated_at = now

        cursor.execute("""
            INSERT INTO file_requests (
                file_paths, user_id, channel_id,
                status, result, error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            json.dumps(file_request.file_paths, ensure_ascii=False),
            file_request.user_id,
            file_request.channel_id,
            file_request.status,
            file_request.result,
            file_request.error,
            file_request.created_at,
            file_request.updated_at
        ))

        request_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return request_id

    def get_next_file_request(self) -> Optional[FileRequest]:
        """获取下一个待处理的文件请求"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, file_paths, user_id, channel_id,
                   status, result, error, created_at, updated_at
            FROM file_requests
            WHERE status = ?
            ORDER BY created_at ASC
            LIMIT 1
        """, (FileRequestStatus.PENDING.value,))

        row = cursor.fetchone()
        conn.close()

        if row:
            return FileRequest(
                id=row[0],
                file_paths=json.loads(row[1]),
                user_id=row[2],
                channel_id=row[3],
                status=row[4],
                result=row[5],
                error=row[6],
                created_at=row[7],
                updated_at=row[8]
            )
        return None

    def update_file_request_status(self, request_id: int, status: FileRequestStatus,
                                   result: Optional[str] = None, error: Optional[str] = None):
        """更新文件请求状态"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        now = datetime.now().isoformat()

        if result is not None:
            cursor.execute("""
                UPDATE file_requests
                SET status = ?, result = ?, updated_at = ?
                WHERE id = ?
            """, (status.value, result, now, request_id))
        elif error is not None:
            cursor.execute("""
                UPDATE file_requests
                SET status = ?, error = ?, updated_at = ?
                WHERE id = ?
            """, (status.value, error, now, request_id))
        else:
            cursor.execute("""
                UPDATE file_requests
                SET status = ?, updated_at = ?
                WHERE id = ?
            """, (status.value, now, request_id))

        conn.commit()
        conn.close()

    def get_file_request(self, request_id: int, timeout: float = 30.0) -> Optional[FileRequest]:
        """
        获取文件请求及其结果（等待处理完成）

        Args:
            request_id: 请求 ID
            timeout: 超时时间（秒），默认 30 秒

        Returns:
            完成的文件请求，如果超时或失败则返回 None
        """
        import time
        start_time = time.time()

        while time.time() - start_time < timeout:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT id, file_paths, user_id, channel_id,
                       status, result, error, created_at, updated_at
                FROM file_requests
                WHERE id = ?
            """, (request_id,))

            row = cursor.fetchone()
            conn.close()

            if row:
                request = FileRequest(
                    id=row[0],
                    file_paths=json.loads(row[1]),
                    user_id=row[2],
                    channel_id=row[3],
                    status=row[4],
                    result=row[5],
                    error=row[6],
                    created_at=row[7],
                    updated_at=row[8]
                )

                # 如果已完成或失败，返回结果
                if request.status in (FileRequestStatus.COMPLETED.value, FileRequestStatus.FAILED.value):
                    return request

            # 等待一段时间后重试
            time.sleep(0.5)

        return None

    def cleanup_old_file_requests(self, retention_hours: int = 24):
        """清理旧的文件请求"""
        if retention_hours == 0:
            return

        cutoff_time = datetime.now() - timedelta(hours=retention_hours)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            DELETE FROM file_requests
            WHERE created_at < ? AND status IN (?, ?)
        """, (cutoff_time.isoformat(), FileRequestStatus.COMPLETED.value, FileRequestStatus.FAILED.value))

        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()

        return deleted_count

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
                print(f"⚠️ 删除 Claude 会话文件时出错: {e}")

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

        return deleted_count
