"""
检查消息队列中的消息记录
"""
import sqlite3
from datetime import datetime

db_path = "./shared/messages.db"

try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 查询最近的消息
    cursor.execute("""
        SELECT id, direction, status, username,
               substr(content, 1, 50) as content_preview,
               substr(response, 1, 50) as response_preview,
               created_at
        FROM messages
        ORDER BY created_at DESC
        LIMIT 10
    """)

    rows = cursor.fetchall()

    if not rows:
        print("数据库中没有消息记录")
    else:
        print(f"\n最近 {len(rows)} 条消息记录：\n")
        print(f"{'ID':<5} {'方向':<12} {'状态':<12} {'用户':<15} {'内容预览':<30} {'响应预览':<30} {'时间'}")
        print("-" * 140)

        for row in rows:
            msg_id, direction, status, username, content, response, created_at = row
            content_preview = content if content else "N/A"
            response_preview = response if response else "N/A"

            print(f"{msg_id:<5} {direction:<12} {status:<12} {username:<15} {content_preview:<30} {response_preview:<30} {created_at}")

    conn.close()

except Exception as e:
    print(f"错误: {e}")
