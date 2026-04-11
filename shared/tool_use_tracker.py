"""
工具调用追踪器

负责追踪工具调用信息，包括：
- 工具调用记录（add_tool_use, get_tool_uses）
- Content Block 管理（add_content_block, get_content_blocks）
- 工具消息卡片引用（save_tool_use_message_ref, get_tool_use_message_ref）
- 工具执行结果（save_tool_use_result, get_pending_tool_use_results, mark_tool_use_result_processed）
"""
import sqlite3
import json
from datetime import datetime
from typing import Optional, List, Dict

from shared.logger import get_logger

log = get_logger("ToolUseTracker", "bridge")


class ToolUseTracker:
    """工具调用追踪器"""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_connection(self):
        """获取数据库连接"""
        return sqlite3.connect(self.db_path)

    # ========== 工具调用记录 ==========

    def add_tool_use(self, message_id: int, tool_name: str, tool_input: dict, tool_use_id: str = None) -> int:
        """
        添加工具调用信息

        Args:
            message_id: 消息 ID
            tool_name: 工具名称
            tool_input: 工具参数
            tool_use_id: 工具调用 ID（可选）

        Returns:
            工具调用的索引（从 0 开始）
        """
        conn = self._get_connection()
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

    def get_tool_uses(self, message_id: int) -> List[dict]:
        """
        获取消息的所有工具调用

        Args:
            message_id: 消息 ID

        Returns:
            工具调用列表
        """
        conn = self._get_connection()
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

    # ========== Content Block 管理 ==========

    def add_content_block(self, message_id: int, block_index: int, block_type: str, block_data: dict = None):
        """
        添加 content block 信息（记录 JSON 中的原始顺序）

        Args:
            message_id: 消息 ID
            block_index: Content block 索引（从 0 开始）
            block_type: Content block 类型（text 或 tool_use）
            block_data: Content block 数据（可选）
        """
        conn = self._get_connection()
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

    def get_content_blocks(self, message_id: int) -> List[Dict]:
        """
        获取消息的所有 content blocks（按原始顺序）

        Args:
            message_id: 消息 ID

        Returns:
            Content block 列表，格式：[{"index": 0, "type": "text", "data": {...}}, ...]
        """
        conn = self._get_connection()
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

    # ========== 工具消息卡片引用 ==========

    def save_tool_use_message_ref(self, message_id: int, tool_use_index: int, discord_message_id: int,
                                     channel_id: int, is_dm: bool, channel_type: str = 'discord'):
        """
        保存工具调用卡片的 Discord 消息引用

        Args:
            message_id: 消息 ID
            tool_use_index: 工具调用索引
            discord_message_id: Discord 消息 ID
            channel_id: Discord 频道/私聊 ID
            is_dm: 是否为私聊
            channel_type: 频道类型（'discord' 或 'weixin'）
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        now = datetime.now().isoformat()
        cursor.execute("""
            INSERT OR REPLACE INTO tool_use_messages
            (message_id, tool_use_index, discord_message_id, channel_id, is_dm, channel_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (message_id, tool_use_index, discord_message_id, channel_id, 1 if is_dm else 0, channel_type, now))

        conn.commit()
        conn.close()

    def get_tool_use_message_ref(self, message_id: int, tool_use_index: int) -> Optional[Dict]:
        """
        获取工具调用卡片的 Discord 消息引用

        Args:
            message_id: 消息 ID
            tool_use_index: 工具调用索引

        Returns:
            包含 discord_message_id, channel_id, is_dm, channel_type 的字典，如果不存在则返回 None
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT discord_message_id, channel_id, is_dm, channel_type
            FROM tool_use_messages
            WHERE message_id = ? AND tool_use_index = ?
        """, (message_id, tool_use_index))

        row = cursor.fetchone()
        conn.close()

        if row:
            return {
                "discord_message_id": row[0],
                "channel_id": row[1],
                "is_dm": bool(row[2]),
                "channel_type": row[3] or 'discord'
            }

        return None

    # ========== 工具执行结果 ==========

    def save_tool_use_result(self, message_id: int, tool_use_index: int, success: bool):
        """
        保存工具执行结果

        Args:
            message_id: 消息 ID
            tool_use_index: 工具调用索引
            success: 工具执行是否成功
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        now = datetime.now().isoformat()
        cursor.execute("""
            INSERT OR REPLACE INTO tool_use_results
            (message_id, tool_use_index, success, processed, created_at)
            VALUES (?, ?, ?, 0, ?)
        """, (message_id, tool_use_index, 1 if success else 0, now))

        conn.commit()
        conn.close()

    def get_pending_tool_use_results(self, channel_type: str = None) -> List[Dict]:
        """
        获取待处理的工具执行结果

        Args:
            channel_type: 可选，按频道类型过滤（'discord' 或 'weixin'）

        Returns:
            待处理的工具执行结果列表
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        if channel_type:
            # JOIN tool_use_messages 来过滤频道类型
            cursor.execute("""
                SELECT r.message_id, r.tool_use_index, r.success
                FROM tool_use_results r
                INNER JOIN tool_use_messages m ON r.message_id = m.message_id AND r.tool_use_index = m.tool_use_index
                WHERE r.processed = 0 AND m.channel_type = ?
                ORDER BY r.created_at ASC
            """, (channel_type,))
        else:
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
        """
        标记工具执行结果为已处理

        Args:
            message_id: 消息 ID
            tool_use_index: 工具调用索引
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE tool_use_results
            SET processed = 1
            WHERE message_id = ? AND tool_use_index = ?
        """, (message_id, tool_use_index))

        conn.commit()
        conn.close()