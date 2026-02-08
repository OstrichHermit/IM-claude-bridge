#!/usr/bin/env python3
"""
Discord Claude Bridge - 队列清理脚本
清理旧消息和重置队列
"""
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timedelta


def get_database_path():
    """获取数据库路径"""
    project_root = Path(__file__).parent.parent.parent.parent
    db_path = project_root / "discord-claude-bridge" / "shared" / "messages.db"
    return db_path


def show_status():
    """显示队列状态"""
    db_path = get_database_path()

    if not db_path.exists():
        print(f"❌ 数据库不存在: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 统计各状态消息数量
    cursor.execute("""
        SELECT status, COUNT(*) as count
        FROM messages
        GROUP BY status
    """)

    print("\n📊 队列状态:")
    print("-" * 40)
    total = 0
    for status, count in cursor.fetchall():
        print(f"  {status}: {count}")
        total += count
    print(f"  总计: {total}")

    # 查看最近的消息
    cursor.execute("""
        SELECT id, status, direction, created_at
        FROM messages
        ORDER BY created_at DESC
        LIMIT 5
    """)

    print("\n📝 最近的消息:")
    print("-" * 40)
    for msg_id, status, direction, created_at in cursor.fetchall():
        print(f"  [{msg_id}] {status} | {direction} | {created_at}")

    conn.close()


def clean_old_messages(retention_hours=24):
    """清理旧消息"""
    db_path = get_database_path()

    if not db_path.exists():
        print(f"❌ 数据库不存在: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cutoff_time = datetime.now() - timedelta(hours=retention_hours)

    # 删除旧消息
    cursor.execute("""
        DELETE FROM messages
        WHERE created_at < ? AND status = 'completed'
    """, (cutoff_time.isoformat(),))

    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()

    print(f"✅ 已清理 {deleted_count} 条旧消息（{retention_hours} 小时前）")


def reset_pending_messages():
    """重置卡住的消息（processing -> pending）"""
    db_path = get_database_path()

    if not db_path.exists():
        print(f"❌ 数据库不存在: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 查找 processing 状态超过 10 分钟的消息
    cutoff_time = datetime.now() - timedelta(minutes=10)

    cursor.execute("""
        UPDATE messages
        SET status = 'pending'
        WHERE status = 'processing' AND updated_at < ?
    """, (cutoff_time.isoformat(),))

    reset_count = cursor.rowcount
    conn.commit()
    conn.close()

    print(f"✅ 已重置 {reset_count} 条卡住的消息")


def clear_all_messages(confirm=False):
    """清空所有消息"""
    if not confirm:
        response = input("⚠️  确定要清空所有消息吗？(yes/no): ")
        if response.lower() != "yes":
            print("❌ 操作已取消")
            return

    db_path = get_database_path()

    if not db_path.exists():
        print(f"❌ 数据库不存在: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 删除所有消息
    cursor.execute("DELETE FROM messages")
    deleted_count = cursor.rowcount

    # 删除所有会话
    cursor.execute("DELETE FROM sessions")

    conn.commit()
    conn.close()

    print(f"✅ 已清空所有消息（{deleted_count} 条）和会话")


def main():
    """主函数"""
    if len(sys.argv) < 2:
        print("用法:")
        print("  python clean_queue.py status                    # 显示队列状态")
        print("  python clean_queue.py clean [hours]             # 清理旧消息（默认 24 小时）")
        print("  python clean_queue.py reset                     # 重置卡住的消息")
        print("  python clean_queue.py clear                     # 清空所有消息")
        return

    command = sys.argv[1]

    if command == "status":
        show_status()
    elif command == "clean":
        retention_hours = int(sys.argv[2]) if len(sys.argv) > 2 else 24
        clean_old_messages(retention_hours)
    elif command == "reset":
        reset_pending_messages()
    elif command == "clear":
        clear_all_messages()
    else:
        print(f"❌ 未知命令: {command}")


if __name__ == "__main__":
    main()
