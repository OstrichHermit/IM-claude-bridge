"""
数据库 Schema 定义 + 迁移逻辑

将建表逻辑独立出去，支持版本化迁移管理
"""
import sqlite3
from typing import Dict, List

# ========== 建表 SQL 模板（按表分组）==========

TABLES: Dict[str, str] = {
    "messages": """
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
    """,

    "tool_use_messages": """
        CREATE TABLE IF NOT EXISTS tool_use_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            tool_use_index INTEGER NOT NULL,
            discord_message_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            is_dm BOOLEAN DEFAULT 0,
            channel_type TEXT DEFAULT 'discord',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(message_id, tool_use_index)
        )
    """,

    "content_blocks": """
        CREATE TABLE IF NOT EXISTS content_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            block_index INTEGER NOT NULL,
            block_type TEXT NOT NULL,
            block_data TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(message_id, block_index)
        )
    """,

    "tool_use_results": """
        CREATE TABLE IF NOT EXISTS tool_use_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            tool_use_index INTEGER NOT NULL,
            success BOOLEAN NOT NULL,
            processed BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(message_id, tool_use_index)
        )
    """,

    "sessions": """
        CREATE TABLE IF NOT EXISTS sessions (
            session_key TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            session_created BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,

    "channel_settings": """
        CREATE TABLE IF NOT EXISTS channel_settings (
            channel_id TEXT PRIMARY KEY,
            mention_required BOOLEAN NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,

    "file_download_requests": """
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
    """,

    "message_requests": """
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
    """,

    "message_sequence": """
        CREATE TABLE IF NOT EXISTS message_sequence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            sequence_index INTEGER NOT NULL,
            content_block_index INTEGER NOT NULL,
            item_type TEXT NOT NULL,
            item_data TEXT NOT NULL,
            tool_use_index INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sent_at
        )
    """,
}

# ========== 索引 SQL（按表分组）==========

INDEXES: Dict[str, List[str]] = {
    "messages": [
        "CREATE INDEX IF NOT EXISTS idx_last_stream_update ON messages(last_stream_update)",
        "CREATE INDEX IF NOT EXISTS idx_status_direction ON messages(status, direction)",
        "CREATE INDEX IF NOT EXISTS idx_created_at ON messages(created_at)",
    ],
    "tool_use_messages": [
        "CREATE INDEX IF NOT EXISTS idx_tool_use_message_id ON tool_use_messages(message_id)",
    ],
    "content_blocks": [
        "CREATE INDEX IF NOT EXISTS idx_content_block_message_id ON content_blocks(message_id)",
    ],
    "tool_use_results": [
        "CREATE INDEX IF NOT EXISTS idx_tool_use_results_processed ON tool_use_results(processed)",
    ],
    "file_download_requests": [
        "CREATE INDEX IF NOT EXISTS idx_file_download_requests_status ON file_download_requests(status)",
        "CREATE INDEX IF NOT EXISTS idx_file_download_requests_created_at ON file_download_requests(created_at)",
    ],
    "message_requests": [
        "CREATE INDEX IF NOT EXISTS idx_message_requests_status ON message_requests(status)",
        "CREATE INDEX IF NOT EXISTS idx_message_requests_created_at ON message_requests(created_at)",
    ],
    "message_sequence": [
        "CREATE INDEX IF NOT EXISTS idx_message_sequence_message_id ON message_sequence(message_id)",
        "CREATE INDEX IF NOT EXISTS idx_message_sequence_status ON message_sequence(status)",
        "CREATE INDEX IF NOT EXISTS idx_message_sequence_sequence_index ON message_sequence(sequence_index)",
    ],
}

# ========== 迁移脚本（按版本管理）==========

MIGRATIONS: List[Dict] = [
    # Version 1: messages 表扩展
    {
        "version": 1,
        "alterations": [
            "ALTER TABLE messages ADD COLUMN is_dm BOOLEAN DEFAULT 0",
            "ALTER TABLE messages ADD COLUMN is_external BOOLEAN DEFAULT 0",
            "ALTER TABLE messages ADD COLUMN tag TEXT DEFAULT 'default'",
            "ALTER TABLE messages ADD COLUMN context_token TEXT",
            "ALTER TABLE messages ADD COLUMN channel_type TEXT DEFAULT 'discord'",
            "ALTER TABLE messages ADD COLUMN streaming_response TEXT",
            "ALTER TABLE messages ADD COLUMN last_stream_update TIMESTAMP",
            "ALTER TABLE messages ADD COLUMN attachments TEXT",
            "ALTER TABLE messages ADD COLUMN tool_uses TEXT",
        ]
    },

    # Version 2: tool_use_messages 表
    {
        "version": 2,
        "alterations": [
            "ALTER TABLE tool_use_messages ADD COLUMN channel_type TEXT DEFAULT 'discord'",
        ]
    },

    # Version 3: sessions 表
    {
        "version": 3,
        "alterations": [
            "ALTER TABLE sessions ADD COLUMN session_created BOOLEAN DEFAULT 0",
        ]
    },

    # Version 4: message_requests 表
    {
        "version": 4,
        "alterations": [
            "ALTER TABLE message_requests ADD COLUMN tag TEXT DEFAULT 'default'",
        ]
    },

    # Version 5: message_sequence 表
    {
        "version": 5,
        "alterations": [
            "ALTER TABLE message_sequence ADD COLUMN tool_use_index INTEGER",
        ]
    },

    # Version 6: channel_settings 表 - 添加 message_splitting
    {
        "version": 6,
        "alterations": [
            "ALTER TABLE channel_settings ADD COLUMN message_splitting BOOLEAN",
        ]
    },
]


class SchemaManager:
    """数据库 Schema 管理器"""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def init_database(self):
        """初始化数据库（简洁版）"""
        conn = sqlite3.connect(self.db_path)
        try:
            # 1. 执行建表
            self._create_tables(conn)
            # 2. 执行索引
            self._create_indexes(conn)
            # 3. 执行迁移
            self._run_migrations(conn)
        finally:
            conn.close()

    def _create_tables(self, conn):
        """执行所有建表语句"""
        for table_name, sql in TABLES.items():
            conn.execute(sql)
        conn.commit()

    def _create_indexes(self, conn):
        """执行所有索引创建语句"""
        for table_name, indexes in INDEXES.items():
            for index_sql in indexes:
                conn.execute(index_sql)
        conn.commit()

    def _run_migrations(self, conn):
        """执行未完成的迁移"""
        # 1. 创建 migrations 表（如果不存在）记录版本
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 2. 读取当前版本
        cursor = conn.execute("SELECT MAX(version) FROM schema_migrations")
        result = cursor.fetchone()
        current_version = result[0] if result and result[0] else 0

        # 3. 执行未执行的迁移
        for migration in MIGRATIONS:
            if migration["version"] > current_version:
                for alter_sql in migration["alterations"]:
                    try:
                        conn.execute(alter_sql)
                    except sqlite3.OperationalError:
                        pass  # 字段已存在

                # 记录迁移版本
                conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES (?)",
                    (migration["version"],)
                )
                conn.commit()
                print(f"✅ Migration {migration['version']} applied")

    def get_current_version(self) -> int:
        """获取当前数据库版本"""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute("SELECT MAX(version) FROM schema_migrations")
            result = cursor.fetchone()
            return result[0] if result and result[0] else 0
        finally:
            conn.close()