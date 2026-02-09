"""检查 pending 状态的消息"""
import sqlite3

conn = sqlite3.connect('shared/messages.db')
cursor = conn.cursor()

cursor.execute("""
    SELECT id, status, username, substr(content, 1, 50), created_at
    FROM messages
    WHERE status IN ('pending', 'processing')
    ORDER BY created_at DESC
    LIMIT 10
""")

rows = cursor.fetchall()

if rows:
    print(f"找到 {len(rows)} 条 pending/processing 消息:\n")
    for r in rows:
        print(f"ID: {r[0]}, Status: {r[1]}, User: {r[2]}")
        print(f"  Content: {r[3]}...")
        print(f"  Time: {r[4]}\n")
else:
    print("没有 pending/processing 消息")

conn.close()
