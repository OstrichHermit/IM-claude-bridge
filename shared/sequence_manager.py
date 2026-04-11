"""
消息序列管理器

负责消息序列的管理，包括：
- 添加消息序列（add_message_sequence）
- 获取待发送序列（get_pending_message_sequences）
- 获取有待发送序列的消息（get_messages_with_pending_sequences）
- 标记序列已发送（mark_sequence_sent）
- 序列统计和清理
"""
import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Optional

from shared.logger import get_logger

log = get_logger("MessageSequenceManager", "bridge")


class MessageSequenceManager:
    """消息序列管理器"""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_connection(self):
        """获取数据库连接"""
        return sqlite3.connect(self.db_path)

    def add_message_sequence(self, message_id: int, sequence_index: int, content_block_index: int,
                              item_type: str, item_data: dict, tool_use_index: int = None):
        """
        添加消息序列项

        Args:
            message_id: 消息ID
            sequence_index: 序列索引（从0开始）
            content_block_index: content block索引
            item_type: 类型（text或tool_use）
            item_data: 数据（字典）
            tool_use_index: 工具调用索引（仅当item_type为tool_use时有效）
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # 检查是否已存在完全相同的记录（避免streaming过程中重复插入）
        cursor.execute("""
            SELECT id FROM message_sequence
            WHERE message_id = ? AND sequence_index = ? AND content_block_index = ? AND item_type = ?
        """, (message_id, sequence_index, content_block_index, item_type))

        if cursor.fetchone():
            # 已存在，跳过
            conn.close()
            return

        now = datetime.now().isoformat()
        data_json = json.dumps(item_data, ensure_ascii=False)

        # 插入新记录
        cursor.execute("""
            INSERT INTO message_sequence
            (message_id, sequence_index, content_block_index, item_type, item_data, tool_use_index, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
        """, (message_id, sequence_index, content_block_index, item_type, data_json, tool_use_index, now))

        conn.commit()
        conn.close()

    def get_pending_message_sequences(self, message_id: int, limit: int = 10) -> List[Dict]:
        """
        获取待发送的消息序列

        Args:
            message_id: 消息ID
            limit: 最多返回多少条

        Returns:
            消息序列列表，格式：[{"id": ..., "sequence_index": ..., "item_type": ..., "item_data": ..., "tool_use_index": ...}, ...]
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, sequence_index, content_block_index, item_type, item_data, tool_use_index
            FROM message_sequence
            WHERE message_id = ? AND status = 'pending'
            ORDER BY sequence_index ASC
            LIMIT ?
        """, (message_id, limit))

        rows = cursor.fetchall()
        conn.close()

        sequences = []
        for row in rows:
            seq_id, seq_index, block_index, item_type, item_data_json, tool_use_index = row
            item_data = json.loads(item_data_json) if item_data_json else {}
            sequences.append({
                "id": seq_id,
                "sequence_index": seq_index,
                "content_block_index": block_index,
                "item_type": item_type,
                "item_data": item_data,
                "tool_use_index": tool_use_index
            })

        return sequences

    def get_messages_with_pending_sequences(self, channel_type: str, limit: int = 1) -> List[Dict]:
        """
        获取有待发送序列的消息列表

        Args:
            channel_type: 频道类型（'discord' 或 'weixin'）
            limit: 最多返回多少条消息，默认 1

        Returns:
            消息列表，每条消息包含：id, discord_channel_id, discord_user_id, is_dm, channel_type, username, context_token
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT DISTINCT m.id, m.discord_channel_id, m.discord_user_id, m.is_dm, m.channel_type, m.username, m.context_token
            FROM message_sequence ms
            INNER JOIN messages m ON ms.message_id = m.id
            WHERE ms.status = 'pending' AND m.channel_type = ?
            ORDER BY m.id ASC
            LIMIT ?
        """, (channel_type, limit))

        rows = cursor.fetchall()
        conn.close()

        messages = []
        for row in rows:
            messages.append({
                "id": row[0],
                "discord_channel_id": row[1],
                "discord_user_id": row[2],
                "is_dm": bool(row[3]),
                "channel_type": row[4],
                "username": row[5],
                "context_token": row[6]
            })

        return messages

    def mark_sequence_sent(self, sequence_id: int):
        """
        标记消息序列项为已发送

        Args:
            sequence_id: 序列项ID
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        now = datetime.now().isoformat()

        cursor.execute("""
            UPDATE message_sequence
            SET status = 'sent', sent_at = ?
            WHERE id = ?
        """, (now, sequence_id))

        conn.commit()
        conn.close()

    def get_max_sequence_index(self, message_id: int) -> int:
        """
        获取指定消息的最大 sequence_index

        Args:
            message_id: 消息ID

        Returns:
            最大的 sequence_index，如果没有记录则返回 -1
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT MAX(sequence_index)
            FROM message_sequence
            WHERE message_id = ?
        """, (message_id,))

        result = cursor.fetchone()
        max_index = result[0] if result[0] is not None else -1

        conn.close()
        return max_index

    def get_message_sequences_stats(self, message_id: int) -> Dict:
        """
        获取消息序列统计信息

        Args:
            message_id: 消息ID

        Returns:
            统计信息字典：{"total": 总数, "pending": 待发送数, "sent": 已发送数}
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # 获取总数
        cursor.execute("""
            SELECT COUNT(*) FROM message_sequence WHERE message_id = ?
        """, (message_id,))
        total = cursor.fetchone()[0]

        # 获取待发送数
        cursor.execute("""
            SELECT COUNT(*) FROM message_sequence WHERE message_id = ? AND status = 'pending'
        """, (message_id,))
        pending = cursor.fetchone()[0]

        # 获取已发送数
        cursor.execute("""
            SELECT COUNT(*) FROM message_sequence WHERE message_id = ? AND status = 'sent'
        """, (message_id,))
        sent = cursor.fetchone()[0]

        conn.close()

        return {
            "total": total,
            "pending": pending,
            "sent": sent
        }

    def cleanup_message_sequences(self, message_id: int):
        """
        清理已发送的消息序列（当消息完成时）

        Args:
            message_id: 消息ID
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            DELETE FROM message_sequence WHERE message_id = ?
        """, (message_id,))

        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()

        return deleted_count