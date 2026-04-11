"""
会话管理器

负责 Claude Code 会话的管理，包括：
- 创建/获取会话
- 清理会话文件
- 会话状态跟踪
"""
import os
import sqlite3
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
import platform

from shared.logger import get_logger

log = get_logger("SessionManager", "bridge")


class SessionManager:
    """会话管理器"""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_connection(self):
        """获取数据库连接"""
        return sqlite3.connect(self.db_path)

    def get_or_create_session(
        self,
        base_working_dir: str,
        channel_id: int = None,
        user_id: int = None,
        is_dm: bool = False,
        use_temp_session: bool = False,
        temp_session_key: str = None
    ) -> tuple[str, str, bool, str]:
        """
        获取或创建会话的工作目录（固定使用 session 模式）

        Args:
            base_working_dir: 基础工作目录
            channel_id: Discord 频道 ID（频道消息使用）
            user_id: Discord 用户 ID（私聊消息使用）
            is_dm: 是否为私聊消息
            use_temp_session: 是否使用临时会话（外部消息）
            temp_session_key: 临时会话 key（use_temp_session=True 时使用）

        Returns:
            (session_key, session_id, session_created, working_directory):
            会话标识、会话 ID、会话是否已创建、工作目录路径
        """
        base_path = Path(base_working_dir)

        # ========== 生成 session_key 和工作目录 ==========
        if use_temp_session and temp_session_key:
            # 临时会话（外部消息：task/reminder）- 独立 session
            session_key = temp_session_key
            working_dir = str(base_path)  # 使用基础工作目录
        elif is_dm:
            # 私聊：每个用户的私聊使用独立 session
            session_key = f"dm_{user_id}"
            working_dir = str(base_path)  # 使用基础工作目录（不创建子目录）
        else:
            # 频道：每个频道使用独立 session（该频道所有用户共享）
            session_key = f"channel_{channel_id}"
            working_dir = str(base_path)  # 使用基础工作目录（不创建子目录）

        # 确保工作目录存在
        os.makedirs(working_dir, exist_ok=True)

        # 获取或创建 session_id
        conn = self._get_connection()
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
                    log.log(f"[会话清理] 已删除 Claude 会话文件: {jsonl_file.name}")
                    deleted = True
                except Exception as e:
                    log.log(f"⚠️ 删除会话文件失败: {e}")

            # 删除会话索引文件
            index_file = session_path / "sessions-index.json"
            if index_file.exists():
                try:
                    index_file.unlink()
                    log.log(f"[会话清理] 已删除会话索引: sessions-index.json")
                except Exception as e:
                    log.log(f"⚠️ 删除索引文件失败: {e}")

            return deleted

        except Exception as e:
            log.log(f"❌ 删除 Claude 会话文件时出错: {e}")
            return False

    def get_latest_session_id(self, working_dir: str) -> str:
        """
        获取 Claude Code 最新的 session_id（从会话文件中读取）

        Args:
            working_dir: 工作目录

        Returns:
            最新的 session_id，如果没有则返回 None
        """
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
            log.log(f"⚠️ 获取最新 session_id 失败: {e}")
            return None

    def update_session_id(self, session_key: str, session_id: str):
        """
        更新会话的 session_id

        Args:
            session_key: 会话标识
            session_id: Claude Code 返回的会话 ID
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE sessions SET session_id = ?, last_used_at = ?
                WHERE session_key = ?
            """, (session_id, datetime.now().isoformat(), session_key))

            conn.commit()
            conn.close()

            log.log(f"✅ session_id 已更新: {session_key} -> {session_id}")

        except Exception as e:
            log.log(f"❌ 更新 session_id 失败: {e}")

    def mark_session_created(self, session_key: str):
        """
        标记会话已创建（第一次使用 --session-id 后）

        Args:
            session_key: 会话标识
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE sessions SET session_created = 1, last_used_at = ?
                WHERE session_key = ?
            """, (datetime.now().isoformat(), session_key))

            conn.commit()
            conn.close()

            log.log(f"✅ 会话已标记为创建: {session_key}")

        except Exception as e:
            log.log(f"❌ 标记会话创建失败: {e}")

    def cleanup_old_sessions(self, days: int = 7):
        """
        清理超过指定天数未使用的会话

        Args:
            days: 保留天数，默认 7 天
        """
        cutoff_time = datetime.now() - timedelta(days=days)

        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            DELETE FROM sessions WHERE last_used_at < ?
        """, (cutoff_time.isoformat(),))

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
        conn = self._get_connection()
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