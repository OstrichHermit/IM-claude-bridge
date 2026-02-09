"""检查最新消息状态"""
import sqlite3

conn = sqlite3.connect('shared/messages.db')
cursor = conn.cursor()

cursor.execute("""
    SELECT id, status, username, content, response
    FROM messages
    ORDER BY created_at DESC
    LIMIT 1
""")

row = cursor.fetchone()

if row:
    print(f"✅ 最新消息:")
    print(f"   ID: {row[0]}")
    print(f"   状态: {row[1]}")
    print(f"   用户: {row[2]}")
    print(f"   内容: {row[3][:50]}...")
    if row[4]:
        print(f"   响应: {row[4][:100]}...")
    else:
        print(f"   响应: 暂无")
else:
    print("❌ 没有找到消息")

conn.close()
